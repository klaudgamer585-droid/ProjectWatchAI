"""Test a ProjectWatch YOLO checkpoint on an uploaded image."""

from hashlib import sha256
from evidence_policy import INFERENCE_VERSION, checkpoint_digest, model_quality
from csv import DictReader
from importlib import reload
from io import BytesIO
from datetime import datetime, timezone
import json
import re
from subprocess import TimeoutExpired

from PIL import Image, ImageOps
import streamlit as st

import app
import evidence_language
import model_readiness

# Streamlit can rerun the tester while retaining an older dashboard module.
if not hasattr(app, "UNDER_CONSTRUCTION_LABELS"):
    reload(app)

from app import BUILDING_STAGE_WEIGHTS, UNDER_CONSTRUCTION_LABELS, YOLO_MODELS, load_photo_model, photo_checkpoint_options
from photo_outline import outline_photo
from road_surface import MAX_PHOTO_BYTES
# Shared with the review workspace so both training forms validate and explain themselves identically.
from training_correction import (EXCLUDED_FROM_TRAINING, SOURCE_ID, source_id_problem, source_note,  # noqa: F401 (re-exported)
                                 validate_correction)
from yolo_feedback import UNDER_CONSTRUCTION_TRAINING, active_weights, feedback_folder, has_training_dataset, latest_candidate, read_correction, save_correction, train_candidate

NOT_INSTALLED = "Not installed — this detector cannot assess photos yet."
NO_CERTIFICATION = "No detector status certifies that a contract is complete."
UPLOAD_LABEL = "Upload a road or infrastructure photo (JPG or PNG, up to 12 MB and 25 megapixels)"
_DETECTION = re.compile(r"(?:and )?(.+?) \((\d+(?:\.\d+)?)% confidence\)")


def prediction_text(description):
    """Plain wording for describe_image output: model scores, not confidence in completion."""
    description = description or ""
    if description.startswith("YOLO detected "):
        body = description.removeprefix("YOLO detected ").split(". ", 1)[0].rstrip(".")
        found = [f"{label.strip()} · model score {score}% for a visible feature"
                 for label, score in _DETECTION.findall(body)]
        text = "Detected " + "; ".join(found) if found else f"Detected {body}"
        if "may overlap" in description:
            text += ". Predictions may overlap; review the overlay."
        return text
    if description.startswith("YOLO found no "):
        return "No target detected. This does not show that the feature is absent."
    return "Analysis failed. The detector did not return a usable result."


def training_status(run, detector, *, failed, epochs):
    """(status, note) for a training run. A finished run is never presented as the active model.

    Exclusive states: Training in progress, Training error, Evaluation required, Candidate ready,
    Evaluation did not pass, Approved active. Only the last is the active model; none is reached by training alone.
    """
    if failed:
        return "Training error", "The run stopped with an error. The active model is unchanged. Next: check the log, then train again."
    if not (run / "ready").is_file():
        note = ("All 10 epochs are recorded; held-out validation is still running. " if epochs >= 10 else "") \
            + "The active model is unchanged while a candidate trains."
        return "Training in progress", note
    candidate = run / "runs/corrected/weights/best.pt"
    active = active_weights(detector, YOLO_MODELS[detector][0])
    state = model_readiness.candidate_state(
        detector, candidate, active, model_quality(detector, active) if active.is_file() else None)
    return model_readiness.CANDIDATE_STATES[state]


@st.fragment(run_every=3)
def training_progress():
    runs = {}
    for detector in YOLO_MODELS:
        logs = list(feedback_folder(detector).glob("training-*/training.log"))
        if logs:
            runs[detector] = max(logs, key=lambda path: path.stat().st_mtime)
    if not runs:
        from ui import empty_state
        empty_state("No training run yet.", "Training progress appears here once a candidate run starts in Test and train.",
                    icon=":material/model_training:")
        return
    st.caption("Training outcome states: Evaluation required (trained, not evaluated) · Candidate ready (evaluation passed, "
               "not promoted) · Approved active (promoted and in use). Training alone never reaches Approved active.")
    selected = st.selectbox("Training run", sorted(runs, key=lambda name: runs[name].stat().st_mtime, reverse=True))
    log = runs[selected]
    run = log.parent
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    finished = (run / "ready").is_file()
    results = run / "runs/corrected/results.csv"
    epochs = 0
    if results.is_file():
        with results.open(encoding="utf-8", newline="") as file:
            epochs = sum(row.get("epoch", "").strip().isdigit() for row in DictReader(file, skipinitialspace=True))
    failed = any("Traceback (most recent call last)" in line for line in lines)
    status, note = training_status(run, selected, failed=failed and not finished, epochs=epochs)
    st.progress(min(epochs / 10, 1.0), text=f"{status} · {epochs} of 10 epochs recorded")
    from ui import freshness
    # Activity reflects the log file, not a decorative pulse: "Working" only while the run is unfinished.
    freshness("Log last written", datetime.fromtimestamp(log.stat().st_mtime, tz=timezone.utc),
              running=not finished and not failed)
    st.caption(note)
    for line in lines:
        if line.startswith("Held-out mask mAP50:"):
            st.caption(line)
    with st.expander("Latest log output"):
        st.code("\n".join(lines[-12:]) or "Waiting for training output.", language=None)


def main():
    st.set_page_config(page_title="BantAIProyekto | Model tester", page_icon=str(app.PROJECT_DIR / "static/brand/favicon-64.png"),
                       layout="wide")
    from ui import apply_theme, brand_logo, error_state, escape_markdown, field_feedback, loading, notice
    apply_theme()
    brand_logo()
    st.title("Model tester")
    st.caption("BantAIProyekto · Inspect detections and save labelled corrections. Training here never changes the active model.")
    default_view = 1 if st.query_params.get("view") == "training" else 0
    if st.radio("Tester view", ["Test and train", "Training progress"], index=default_view, horizontal=True) == "Training progress":
        training_progress()
        return
    detector = st.selectbox("Detector", list(YOLO_MODELS))
    weights, classes, target, image_size = YOLO_MODELS[detector]
    seed_weights = weights
    if detector == UNDER_CONSTRUCTION_TRAINING:
        classes = UNDER_CONSTRUCTION_LABELS
        seed_weights = BUILDING_STAGE_WEIGHTS
    st.caption(f"Detection target · {target}")
    with st.expander("Train with saved corrections"):
        st.caption("Fine-tune a separate checkpoint with your corrections and the existing training photos.")
        can_train = has_training_dataset(detector)
        if not can_train:
            st.caption("Fine-tuning requires a labeled training and evaluation dataset for this detector.")
        device = st.selectbox("Training device", ["GPU", "CPU"])
        if st.button("Train a candidate", disabled=not seed_weights.is_file() or not can_train, key="train_candidate"):
            try:
                with loading("Training a candidate checkpoint"):
                    train_candidate(detector, seed_weights, classes, image_size, "0" if device == "GPU" else "cpu")
                # Neutral, not green: a trained candidate is not an approval.
                notice("Evaluation required · candidate saved, not active.",
                       "Training does not change the active model. Evaluate the candidate, then promote it as a separate "
                       "step. You can select it below to test it on a different photo.",
                       icon=":material/model_training:")
            except (OSError, ValueError, TimeoutExpired) as error:
                error_state("Training stopped.", escape_markdown(str(error)),
                            kept="The active model is unchanged. Saved corrections are kept.")
    candidate = latest_candidate(detector)
    checkpoints = photo_checkpoint_options(detector)
    weights = checkpoints["Current model"]
    if candidate:
        st.caption(f"Training log · {candidate[1]}")
    if len(checkpoints) > 1:
        selected = st.radio("Checkpoint", list(checkpoints), horizontal=True, key=f"checkpoint_{detector}")
        weights = checkpoints[selected]
    checkpoint_available = weights.is_file()
    # Eligibility sits next to the checkpoint choice, for every state including a missing checkpoint.
    if checkpoint_available:
        eligibility, use = evidence_language.eligibility(model_quality(detector, weights))
        st.caption(f"Decision eligibility · {eligibility}. {use} {NO_CERTIFICATION}")
    else:
        notice("No checkpoint for this detector.",
               f"{NOT_INSTALLED} You can still save labels now and run detection once a checkpoint is available.",
               icon=":material/block:")
    uploaded = st.file_uploader(UPLOAD_LABEL, type=["jpg", "jpeg", "png"])
    if uploaded is None:
        return
    if uploaded.size > MAX_PHOTO_BYTES:
        field_feedback("Choose a photo of 12 MB or less.")
        return
    try:
        raw = uploaded.getvalue()
        digest = sha256(raw).hexdigest()
        with Image.open(BytesIO(raw)) as source:
            if source.width * source.height > 25_000_000:
                field_feedback("Choose a photo with at most 25 megapixels.")
                return
            photo = ImageOps.exif_transpose(source).convert("RGB")
        photo.thumbnail((960, 960))
        key = (detector, digest, str(weights), (INFERENCE_VERSION, checkpoint_digest(weights)) if checkpoint_available else None)
        if st.button("Run YOLO", type="primary", disabled=not checkpoint_available, key="run_yolo"):
            with loading("Analyzing photo"):
                model = load_photo_model(detector, str(weights.resolve()), (INFERENCE_VERSION, checkpoint_digest(weights)))
                st.session_state["prediction"] = (key, model.describe_image(photo))
    except (Image.DecompressionBombError, ImportError, OSError, RuntimeError, ValueError) as error:
        error_state("Analysis failed.", "The detector could not process this photo.",
                    kept="Saved labels are unchanged. Choose another photo or checkpoint to retry.")
        st.text(f"Details · {error}")  # exception text is not trusted Markdown
        return
    saved = read_correction(detector, digest)
    if saved and saved["label"] and detector == UNDER_CONSTRUCTION_TRAINING:
        saved = {**saved, "label": next(iter(classes))}
    if saved:
        st.success(f"Saved label · {classes[saved['label']] if saved['label'] else 'No target visible'}. "
                   "This is a training example only; no contract was changed.")
    original, result = st.columns(2)
    original.image(photo, caption="Original photo")
    prediction = st.session_state.get("prediction")
    if not checkpoint_available:
        result.caption(NOT_INSTALLED)
    elif prediction and prediction[0] == key:
        description, overlay = prediction[1]
        result.text(prediction_text(description))
        result.caption(f"{evidence_language.eligibility(model_quality(detector, weights))[0]}. "
                       f"{evidence_language.MODEL_SCORE_NOTE}")
        if overlay:
            result.image(overlay, caption="Detector observation overlay; not a verification mark.")
    with st.expander("Label what is visible in this photo"):
        st.caption("This label is a training example for the detector. It does not change any contract's category.")
        labels = [None, *classes]
        current = saved["label"] if saved else None
        label = st.selectbox("Correct label", labels, index=labels.index(current),
                             format_func=lambda value: classes[value] if value else "No target visible",
                             key=f"label_{detector}_{digest}")
        points = []
        append = False
        if label:
            st.caption("Click around the feature in order, then select Use outline.")
            polygons = saved["polygons"] if saved and saved["label"] == label else []
            if detector == UNDER_CONSTRUCTION_TRAINING and polygons:
                append = st.checkbox("Add another outline", value=True, key=f"append_outline_{digest}")
            polygon = polygons[-1] if polygons and not append else []
            initial = [polygon[index:index + 2] for index in range(0, len(polygon), 2)]
            outline_key = f"outline_{detector}_{digest}_{len(polygons) if append else 0}"
            state = st.session_state.get(outline_key)
            draft = state.get("points", initial) if isinstance(state, dict) else getattr(state, "points", initial)
            points = outline_photo(photo, outline_key, draft)
        outline_feedback = st.empty()
        source_contract = st.text_input("Source contract/project ID (required for training)", value=(saved or {}).get("contractId", ""),
                                        key=f"source_contract_{digest}", placeholder="e.g. 24CK0181")
        source_problem = source_id_problem(source_contract)
        if source_problem:
            field_feedback(source_problem)
        else:
            note = source_note(source_contract)
            # Directly under the field: an omitted source is allowed but explained, not an alert.
            field_feedback(note, kind="info") if note == EXCLUDED_FROM_TRAINING else st.caption(note)
        if st.button("Save label", type="primary", key=f"save_label_{digest}", disabled=bool(source_problem)):
            problems = validate_correction(detector, label, points, source_contract, classes=classes, photo=photo)
            if problems:
                with outline_feedback.container():
                    for message in problems.values():
                        field_feedback(message)
                return
            try:
                with loading("Saving label"):
                    save_correction(detector, digest, photo, label, points, append=append,
                                    contract_id=source_contract.strip() or None)
                st.rerun()
            except (OSError, ValueError) as error:
                error_state("The label could not be saved.", escape_markdown(str(error)),
                            kept="Your label and outline are still here.")


if __name__ == "__main__":
    main()
