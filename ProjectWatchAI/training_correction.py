"""Training corrections: label what is visible in one photo so a detector can learn from it.

This module is deliberately blind to contract decisions. It imports neither the review store nor the
dashboard, and writes only through ``yolo_feedback.save_correction``. A saved training example can
therefore not change any contract's category, and a saved decision cannot change a training label.
Both forms have their own validation, their own save button and their own outcome message.
"""
import re
from hashlib import sha256

import requests
import streamlit as st

import yolo_feedback
from yolo_feedback import UNDER_CONSTRUCTION_TRAINING, outline_problem

EXCLUDED_FROM_TRAINING = "Excluded from training until source is identified."
ELIGIBLE_NOTE = ("Eligible for training once checks pass: contract IDs and duplicate photos are checked "
                 "against held-out evaluation data when training runs.")
DECISION_UNCHANGED = "This contract's decision was not changed."
SOURCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
LABEL_NOT_CHOSEN = "Choose what is visible, or choose No target visible."
_PHOTO_CACHE_LIMIT = 8


def source_note(source_id):
    """Inline training-eligibility explanation for the source field."""
    if not (source_id or "").strip():
        return EXCLUDED_FROM_TRAINING
    return ELIGIBLE_NOTE


def source_id_problem(source_id):
    """Field feedback for the source ID; None when it is empty (allowed, excluded) or well formed."""
    value = (source_id or "").strip()
    if not value or SOURCE_ID.fullmatch(value):
        return None
    if re.search(r"[\s,;]", value):
        return "Enter one contract or project ID. Label photos from other contracts separately."
    return "Contract and project IDs use letters, numbers and hyphens only."


def validate_correction(detector, label, points, source_id, *, classes, photo=None):
    """Field -> message for everything wrong with a training example. Empty dict means it can be saved.

    ``label`` None means "No target visible": valid, and no outline is needed. A chosen label needs an
    outline that ``yolo_feedback.outline_problem`` accepts (pass ``photo`` to also check its area). An
    empty source ID is valid: the example is saved but excluded from training. Nothing here looks at
    contract categories or reviewer decisions.
    """
    problems = {}
    if label is not None:
        if label not in classes:
            problems["label"] = "This label is not available for the chosen detector. Choose one from the list."
        elif problem := outline_problem(points, photo.size if photo is not None else None):
            problems["outline"] = problem
    if problem := source_id_problem(source_id):
        problems["source"] = problem
    return problems


def photo_digest(photo):
    return sha256(f"{photo.size}".encode() + photo.tobytes()).hexdigest()


def _drafts():
    return st.session_state.setdefault("training_drafts", {})


def _remember_photo(cache_key, photo):
    cache = st.session_state.setdefault("training_photos", {})
    cache[cache_key] = photo
    while len(cache) > _PHOTO_CACHE_LIMIT:
        cache.pop(next(iter(cache)))


def _seed(key, value):
    """Give a widget its starting value once; an existing session value (the live draft) always wins."""
    if key not in st.session_state:
        st.session_state[key] = value


def render_training_correction(record, photo_number, *, detectors, classes_for,
                               under_construction=UNDER_CONSTRUCTION_TRAINING, key_prefix="training"):
    """The one training-correction form, for the review workspace and the Explore detail.

    ``record`` is a saved review (contractId, photos); ``photo_number`` is the 1-based photo selected in
    the evidence viewer; ``classes_for(detector)`` maps each label to its display text.
    """
    from photo_outline import outline_photo
    from road_surface import download_photo
    from ui import error_state, escape_markdown, field_feedback, loading

    contract = record["contractId"]
    source_photo = record["photos"][photo_number - 1]
    url = source_photo["url"]
    prefix = f"{key_prefix}_{contract}_{photo_number}"
    drafts = _drafts()
    receipts = st.session_state.setdefault("training_receipts", {})

    with st.container(border=True):
        st.markdown("**Training correction**")
        st.caption(f"Label what is visible in photo {photo_number} so the detector can learn from it. "
                   "This does not change this contract's category or decision. "
                   "Record decisions in the separate Reviewer decision form.")
        if not detectors:
            st.caption("No detector is available for this photo.")
            return
        detector = st.selectbox("Training detector", detectors, key=f"{prefix}_detector")
        draft = drafts.setdefault((contract, photo_number, detector), {})

        cache_key = (contract, photo_number, url)
        if st.button("Load photo for labeling", key=f"{prefix}_load"):
            try:
                with loading("Retrieving photo"):
                    _remember_photo(cache_key, download_photo(url))
            except (requests.RequestException, OSError, ValueError):
                error_state("The photo could not be loaded.", "The photo source did not respond.",
                            kept="Saved labels and the review are unchanged. Select Load photo for labeling to retry.")
                return
        photo = st.session_state.get("training_photos", {}).get(cache_key)
        if photo is None:
            return

        classes = classes_for(detector)
        digest = photo_digest(photo)
        saved = yolo_feedback.read_correction(detector, digest)
        if saved and saved["label"] is not None and saved["label"] not in classes:
            saved = {**saved, "label": next(iter(classes))}
        if saved:
            st.caption("A training example for this photo and detector is already saved. Saving again replaces it.")
        no_target = ""
        options = [no_target, *classes]
        label_key = f"{prefix}_label_{detector}"
        if label_key not in st.session_state:
            if "label" in draft:  # the draft survives switching photos and back
                st.session_state[label_key] = draft["label"]
            elif saved:
                st.session_state[label_key] = saved["label"] or no_target
        label_choice = st.selectbox(
            "What is visible", options, key=label_key, index=None,
            format_func=lambda value: classes[value] if value else "No target visible",
            placeholder="Choose what is visible")
        label_feedback = st.empty()
        label = label_choice or None  # "" means No target visible
        draft["label"] = label_choice

        points, append = [], False
        outline_feedback = st.empty()
        if label:
            if detector == under_construction and saved and saved["label"] == label:
                st.caption(f"Saved outlines · {len(saved['polygons'])}")
                append = st.checkbox("Add another outline", value=True, key=f"{prefix}_append_{detector}")
            polygon = saved["polygons"][-1] if saved and saved["label"] == label and not append else []
            outline_key = f"{prefix}_outline_{detector}_{label}_{len(saved['polygons']) if append and saved else 0}"
            initial = [polygon[i:i + 2] for i in range(0, len(polygon), 2)]
            if draft.get("outline_label") == label and draft.get("points") and not polygon:
                initial = draft["points"]  # the draft survives switching photos and back
            state = st.session_state.get(outline_key)
            current = state.get("points", initial) if isinstance(state, dict) else getattr(state, "points", initial)
            st.caption("Click around the feature and select Use outline.")
            points = outline_photo(photo, outline_key, current)
            draft["outline_label"], draft["points"] = label, points
        else:
            st.image(photo)

        source_key = f"{prefix}_source"
        # This photo came from this contract, so its source is known unless the reviewer clears it.
        initial_source = saved.get("contractId", "") if saved else contract
        _seed(source_key, draft.get("source", initial_source))
        source = st.text_input("Source contract/project ID (needed for training)", key=source_key,
                               placeholder="e.g. 24CK0181")
        draft["source"] = source
        source_feedback = st.empty()
        problem = source_id_problem(source)
        with source_feedback.container():
            if problem:
                field_feedback(problem)
            elif source_note(source) == EXCLUDED_FROM_TRAINING:
                field_feedback(EXCLUDED_FROM_TRAINING, kind="info")
            else:
                st.caption(source_note(source))

        if st.button("Save training example", type="primary", key=f"{prefix}_save", disabled=bool(problem)):
            problems = validate_correction(detector, label, points, source, classes=classes, photo=photo)
            if label_choice is None:
                problems = {"label": LABEL_NOT_CHOSEN, **problems}
            if problems:
                for field, placeholder in (("label", label_feedback), ("outline", outline_feedback),
                                           ("source", source_feedback)):
                    if field in problems:
                        with placeholder.container():
                            field_feedback(problems[field])
            else:
                try:
                    with loading("Saving training example"):
                        yolo_feedback.save_correction(detector, digest, photo, label, points, append=append,
                                        contract_id=source.strip() or None)
                except (OSError, ValueError) as error:
                    error_state("The training example could not be saved.", escape_markdown(str(error)),
                                kept="Nothing was saved. Your label and outline are still here.")
                else:
                    receipts[(contract, photo_number, detector)] = {
                        "label": classes[label] if label else "No target visible",
                        "note": source_note(source), "source": source.strip() or None}
        # The confirmation is read back from the saved correction, not taken from what the form held.
        receipt = receipts.get((contract, photo_number, detector))
        if receipt and yolo_feedback.read_correction(detector, digest):
            message = (f"Training example saved for photo {photo_number} · {receipt['label']}. "
                       f"{DECISION_UNCHANGED} {receipt['note']}")
            st.success(escape_markdown(message))
