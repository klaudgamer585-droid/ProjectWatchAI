"""Contract review workspace, shared by the saved-review page (Admin) and the contract detail (Explore).

Section order follows the plan (§5B): identity and source, reported facts, assessment and limitations,
latest dated evidence, reviewer decision, timeline and budget, provenance and history. Streamlit
columns stack in source order on compact screens, so the evidence viewer always comes before the
decision form.

This module renders and orchestrates. It does not decide anything about evidence: rules live in
contract_assessment, wording in evidence_language, persistence in review_store. App-specific work (the
database connection, detector overlays, the training form, queue navigation) is passed in through
``Services`` so this module never imports app.py.
"""
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Callable, Optional
from urllib.parse import urlsplit

import streamlit as st

import evidence_language as language
import review_store as store
from evidence_policy import evidence_fingerprint
from review_evidence import flag_links, photo_citations, photo_groups
from ui import (category_badge, empty_state, error_state, escape_markdown, evidence_overview, field_feedback, loading,
                notice, photo_with_fallback, show_state)

DRAFTS = "review_drafts"            # contract -> {"category", "reason", "reviewer"}
VIEWS = "review_views"             # contract -> {"group", "photo", "view"}
RECEIPTS = "decision_receipts"     # contract -> revision id of the last successful save in this session
REVIEWER = "reviewer_name"         # remembered across contracts in this session
RESET = "review_form_reset"        # contracts whose form must start empty after a successful save
SEEN = "review_seen_evidence"      # contract -> evidence fingerprint the reviewer was shown
MAX_LINKS = 6                      # photo links shown per flag before "+N more"


@dataclass
class Services:
    open_db: Callable[[], sqlite3.Connection]                  # read-write; used only to save a decision
    project_url: Callable[[str], str]
    read_db: Optional[Callable[[], sqlite3.Connection]] = None  # read-only; used to show confirmations and history
    overlay: Optional[Callable[[dict, int], dict]] = None     # photo, number -> {detector: image} (empty = unavailable)
    training_form: Optional[Callable[[dict, int], None]] = None
    review_stale: bool = False
    reviewer_default: str = ""
    next_contract: Optional[Callable[[], None]] = None        # set when a queue has a next contract; callback
    after_save: Optional[Callable[[dict], None]] = None       # called with the stored revision, before the rerun
    satellite: Optional[Callable[[], None]] = None
    actions: Optional[Callable[[], None]] = None              # extra identity-level actions (e.g. Rerun review)
    documents: Optional[Callable[[], None]] = None
    namespace: str = "review"                                 # widget-key prefix, so two pages never collide


def _reader(services):
    return (services.read_db or services.open_db)()


def _store(name):
    return st.session_state.setdefault(name, set() if name == RESET else {})


def _peso(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return "Unavailable"
    return f"₱{amount:,.2f}" if amount.is_finite() else "Unavailable"


def _date(value):
    return language.format_timestamp(value) if value else "Unavailable"


def _numbers(numbers):
    return ", ".join(str(number) for number in numbers)


def forget_seen_evidence(contract):
    _store(SEEN).pop(contract, None)


def _seed(key, saved, value_if_missing, options=None):
    """Put a remembered value into a widget key before the widget exists, only when it is still valid."""
    if key in st.session_state:
        return
    value = saved if saved is not None else value_if_missing
    if options is not None and value not in options:
        return
    if value is not None:
        st.session_state[key] = value


# ---------------------------------------------------------------------------------------------
# 1. Identity and source, 2. Reported facts
# ---------------------------------------------------------------------------------------------

def render_identity(record, services, *, reviewed_at=None, source_checked=None):
    contract = record["contractId"]
    title, link = st.columns([3, 1], vertical_alignment="center")
    with title:
        st.subheader(contract)
    with link:
        st.link_button("Open source record", services.project_url(contract), icon=":material/open_in_new:")
    if description := record.get("description"):
        st.text(description)
    times = []
    if source_checked:
        times.append(f"Source record checked {language.format_timestamp(source_checked)}")
    if reviewed_at:
        times.append(f"Saved review {language.format_timestamp(reviewed_at)}")
    if times:
        st.caption(" · ".join(times))
    if services.actions:
        services.actions()


def render_reported_facts(record, *, live_detail=None, live_notes=()):
    """Facts as published by DPWH (or saved with the review). Never presented as a photo finding."""
    with st.container(border=True):
        st.markdown("**Reported facts**")
        st.caption("From the DPWH record via BetterGov. These are reported values, not photo findings.")
        facts = st.columns(4)
        facts[0].markdown("Contractor")
        facts[0].text(record.get("contractor") or "Unavailable")
        facts[1].markdown("Reported progress")
        progress = record.get("progress")
        facts[1].text(f"{progress}" if progress not in (None, "") else "Unavailable")
        facts[2].markdown("Reported project budget")
        facts[2].text(_peso((record.get("budgetPHP") or {}).get("Reported project budget")))
        facts[3].markdown("Photos in this review")
        count = record.get("galleryPhotoCount")
        facts[3].text(f"{len(record.get('photos') or []):,} of {count:,} in the gallery" if isinstance(count, int)
                      else f"{len(record.get('photos') or []):,}")
        for note in live_notes:
            notice("The live record differs from the saved review", note, icon=":material/difference:")


def live_record_differences(record, detail, completion):
    """Plain sentences for fields where the live source record no longer matches what the review saved."""
    notes = []
    saved_status = record.get("status")
    live_status = (detail or {}).get("status")
    if live_status and saved_status and live_status != saved_status:
        notes.append(f"Reported status was “{escape_markdown(saved_status)}” when this review was saved and is "
                     f"“{escape_markdown(live_status)}” now.")
    saved_completion = (record.get("dates") or {}).get("Reported completion")
    if completion and saved_completion and str(completion)[:10] != str(saved_completion)[:10]:
        notes.append(f"Reported completion was {_date(saved_completion)} when this review was saved and is "
                     f"{_date(completion)} now.")
    return notes


# ---------------------------------------------------------------------------------------------
# 3. Assessment and limitations: linked flags
# ---------------------------------------------------------------------------------------------

def _select_photo(contract, number, groups):
    group = next((g for g in groups if number in g["numbers"]), None)
    views = _store(VIEWS)
    key = (group or {}).get("key") or "unknown"
    views.setdefault(contract, {}).update(photo=number, group=key)
    # Widgets read their value from these keys on the next run.
    st.session_state[f"review_group_{contract}"] = key
    st.session_state[f"review_photo_{contract}"] = number


def _photo_links(record, groups, numbers, *, prefix, services):
    contract = record["contractId"]
    photos = record["photos"]
    shown = numbers[:MAX_LINKS]
    columns = st.columns(min(len(shown) + (1 if len(numbers) > MAX_LINKS else 0), MAX_LINKS + 1) or 1)
    for column, number in zip(columns, shown):
        label = f"Photo {number} · {language.format_timestamp(photos[number - 1].get('capturedAt'))}"
        column.button(label, key=f"{services.namespace}_{prefix}_{contract}_{number}", type="tertiary",
                      icon=":material/photo:", on_click=_select_photo, args=(contract, number, groups),
                      help="Show this photo in the evidence viewer below.")
    if len(numbers) > MAX_LINKS:
        columns[-1].caption(f"+{len(numbers) - MAX_LINKS} more photos")


def render_flags(record, groups, services):
    links = flag_links(record)
    if not links:
        return
    contract = record["contractId"]
    attention = [item for item in links if item["discrepancy"]]
    notes = [item for item in links if not item["discrepancy"]]
    for title, icon, items, prefix in (
            ("Record checks needing attention", ":orange[:material/warning:]", attention, "flagA"),
            ("Evidence gaps and review notes", ":material/info:", notes, "flagN")):
        if not items:
            continue
        with st.container(border=True):
            st.markdown(f"{icon} **{title} · {len(items)}**")
            for index, item in enumerate(items):
                st.markdown(f"- {escape_markdown(item['text'])}")
                if item.get("why"):
                    st.caption(item["why"])
                for label, value in item.get("dates") or ():
                    st.caption(f"{label} · {value}")
                for label, value in item.get("amounts") or ():
                    st.caption(f"{label} · {value}")
                if item.get("photos"):
                    st.caption("Supporting photos")
                    _photo_links(record, groups, item["photos"], prefix=f"{prefix}{index}", services=services)
                if item.get("counter_photos") or item.get("counter_note"):
                    st.markdown(":material/compare: **Counterevidence**")
                    if item.get("counter_note"):
                        st.caption(item["counter_note"])
                    if item.get("counter_photos"):
                        _photo_links(record, groups, item["counter_photos"], prefix=f"{prefix}{index}c", services=services)


def render_conflict(record, groups, services):
    """Both sides of a same-date conflict, each linked to its photos. The rule's outcome is not changed."""
    conflict = language.same_date_conflict(record)
    if not conflict:
        return
    with st.container(border=True):
        st.markdown(":material/compare: **Latest-date conflict · both observations are shown**")
        st.caption("Visible scope detections")
        _photo_links(record, groups, conflict["scope_photos"], prefix="conflictS", services=services)
        st.caption("Construction cues (counterevidence to the scope detections)")
        _photo_links(record, groups, conflict["cue_photos"], prefix="conflictC", services=services)


# ---------------------------------------------------------------------------------------------
# 4. Latest dated evidence: group, photo, image/overlay, observations
# ---------------------------------------------------------------------------------------------

def _step_photo(contract, numbers, step):
    current = st.session_state.get(f"review_photo_{contract}", numbers[0])
    index = numbers.index(current) if current in numbers else 0
    st.session_state[f"review_photo_{contract}"] = numbers[max(0, min(len(numbers) - 1, index + step))]


def _source_text(photo):
    host = urlsplit(photo.get("url") or "").netloc or "unavailable"
    parts = [f"DPWH gallery via BetterGov ({host})"]
    if photo.get("componentId"):
        parts.append(f"component {photo['componentId']}")
    if photo.get("purpose"):
        parts.append(str(photo["purpose"]))
    parts = [escape_markdown(part) for part in parts]
    return " · ".join(parts)


def render_evidence(record, groups, services):
    contract = record["contractId"]
    photos = record["photos"]
    st.subheader("Latest dated evidence")
    if not photos:
        empty_state("No photos are saved with this review.",
                    "A reviewer decision can still be recorded from the reported facts.", icon=":material/hide_image:")
        return
    views = _store(VIEWS).setdefault(contract, {})
    keys = [g["key"] or "unknown" for g in groups]
    group_key, photo_key, view_key = (f"review_group_{contract}", f"review_photo_{contract}", f"review_view_{contract}")
    _seed(group_key, views.get("group"), keys[0], keys)
    if st.session_state.get(group_key) not in keys:
        st.session_state[group_key] = keys[0]
    labels = {(g["key"] or "unknown"): g["label"] for g in groups}
    group_key_value = st.selectbox("Photo group", keys, key=group_key, format_func=labels.get,
                                   help="The newest dated group sets the current assessment. Earlier and undated "
                                        "photos stay here for inspection.")
    group = next(g for g in groups if (g["key"] or "unknown") == group_key_value)
    numbers = group["numbers"]
    _seed(photo_key, views.get("photo"), numbers[0], numbers)
    if st.session_state.get(photo_key) not in numbers:
        st.session_state[photo_key] = numbers[0]
    chooser = st.container()
    previous, following = st.columns(2)
    position = numbers.index(st.session_state[photo_key])
    previous.button("Previous photo", key=f"{services.namespace}_prev_photo_{contract}", icon=":material/chevron_left:",
                    disabled=position == 0, on_click=_step_photo, args=(contract, numbers, -1), use_container_width=True)
    number = chooser.selectbox("Photo", numbers, key=photo_key, format_func=lambda n: f"Photo {n}")
    following.button("Next photo", key=f"{services.namespace}_next_photo_{contract}", icon=":material/chevron_right:",
                     disabled=position == len(numbers) - 1, on_click=_step_photo, args=(contract, numbers, 1),
                     use_container_width=True)
    views.update(group=group_key_value, photo=number)
    st.caption(f"Photo {position + 1} of {len(numbers)} in this group · {len(photos)} saved with this review")

    photo = photos[number - 1]
    role = group["role"]
    citations = photo_citations(record).get(number, [])
    with st.container(border=True):
        st.markdown(f"**Photo {number} · {language.format_timestamp(photo.get('capturedAt'))}**")
        if role == "newest":
            st.caption(f"Newest dated group · {len(numbers)} photo{'s' if len(numbers) != 1 else ''} · this group sets the "
                       "current assessment")
        elif role == "earlier":
            show_state("historical")
        else:
            show_state("unknown_date")
        _render_image(photo, number, contract, services)
        st.caption(f"Source · {_source_text(photo)}")
        if photo.get("scope"):
            st.text(f"Reported work · {photo['scope']}")
        _render_observations(photo)
        for item in citations:
            icon = {"scope": ":material/visibility:", "cue": ":material/construction:",
                    "counter": ":material/compare:", "failed": ":material/error:"}.get(item["kind"], ":material/link:")
            st.caption(f"{icon} {item['text']}")
        st.link_button("Open original photo", photo["url"], icon=":material/open_in_new:")
    if services.training_form:
        services.training_form(record, number)


def _render_image(photo, number, contract, services):
    view_key = f"review_view_{contract}"
    views = _store(VIEWS).setdefault(contract, {})
    choices = ["Original photo", "Detector overlay"]
    _seed(view_key, views.get("view"), choices[0], choices)
    if st.session_state.get(view_key) not in choices:
        st.session_state[view_key] = choices[0]
    view = st.radio("Image view", choices, key=view_key, horizontal=True,
                    help="The original photo is always available. The overlay redraws the detector's observation on it.")
    views["view"] = view
    alt = f"Project photo {number}, captured {language.format_timestamp(photo.get('capturedAt'))}"
    if view == "Detector overlay":
        # Computed only on request: an overlay can mean running a detector on the photo.
        overlay_images = (services.overlay(photo, number) or {}) if services.overlay else {}
        if overlay_images:
            chosen = next(iter(overlay_images))
            if len(overlay_images) > 1:
                chosen = st.selectbox("Overlay detector", list(overlay_images), key=f"review_overlay_{contract}")
            st.image(overlay_images[chosen], caption="Detector observation overlay. Outlines mark what the detector matched; "
                                                     "they are not verification marks.")
        else:
            st.caption(":material/layers_clear: No overlay is available for this photo. The original is shown instead. "
                       "Overlays need an installed detector and a photo the source can still return.")
            photo_with_fallback(photo.get("url"), alt, f"Original photo {number}")
    else:
        photo_with_fallback(photo.get("url"), alt, f"Original photo {number}")


def _render_observations(photo):
    findings = photo.get("yolo") or []
    st.markdown("**What the detectors reported for this photo**")
    if not findings:
        st.caption("No detector results are saved for this photo. This is not evidence that nothing is visible.")
        return
    detected = False
    for finding in findings:
        label, use = language.eligibility(finding.get("quality"))
        st.text(language.finding_text(finding))
        st.caption(f"Detector eligibility · {label}", help=use)
        detected = detected or finding.get("status") == "detected"
    if detected:
        st.caption(language.MODEL_SCORE_NOTE)


# ---------------------------------------------------------------------------------------------
# 5. Reviewer decision
# ---------------------------------------------------------------------------------------------

def _clear_receipt(contract):
    """A new save attempt retires the last confirmation, so a failure never sits beside an old 'saved'."""
    _store(RECEIPTS).pop(contract, None)


def _draft(contract):
    return _store(DRAFTS).setdefault(contract, {})


def _receipt_block(record, services):
    contract = record["contractId"]
    revision = _store(RECEIPTS).get(contract)
    if revision is None:
        return
    try:
        with closing(_reader(services)) as database:
            saved = store.read_revision(database, revision)
    except (OSError, sqlite3.Error):
        error_state("The saved confirmation could not be read back.", "The review database did not respond.",
                    kept="Open the history below to check whether the decision was recorded.")
        return
    if not saved:
        error_state("The decision is not in the review database.", "Nothing was recorded for this confirmation.",
                    kept="Your draft is still here. Select Save decision to try again.")
        return
    with st.container(border=True):
        reason = saved["reason"] if len(saved["reason"]) <= 120 else saved["reason"][:117] + "…"
        st.success(f"Decision saved for {escape_markdown(contract)}", icon=":material/check_circle:")
        category_badge(saved["category"])
        st.text(f"Reason · {reason}")
        st.caption(f"Recorded by {escape_markdown(saved['reviewer'])} · {language.format_timestamp(saved['decided_at'])} · "
                   f"revision {saved['revision']}")
        st.caption("A later correction is saved as a new revision. Earlier decisions stay in the history below.")
        if services.next_contract:
            st.button("Next contract", key=f"{services.namespace}_next_contract_{contract}",
                      on_click=services.next_contract, icon=":material/arrow_forward:",
                      help="Go to the next contract in this group. Your saved decision is not changed.")
        else:
            st.caption("There are no other contracts waiting in this group.")


def render_decision(record, services):
    contract = record["contractId"]
    st.subheader("Reviewer decision")
    previous = st.session_state.get("review_active_contract")
    if previous and previous != contract:
        _clear_receipt(previous)  # a confirmation belongs to the moment it was saved, not to later visits
    st.session_state["review_active_contract"] = contract
    assessment = record["assessment"]
    current = assessment["completion_evidence_category"]
    with st.container(border=True):
        st.caption("Current category")
        category_badge(current)
        _receipt_block(record, services)
        options = store.eligible_review_categories(record)
        draft = _draft(contract)
        if not options:
            st.caption("No other category matches the saved evidence, so there is nothing to change.")
            return
        st.markdown("**Record a decision**")
        category_key, reason_key, reviewer_key = (f"manual_category_choice_{contract}", f"manual_category_reason_{contract}",
                                                  f"manual_category_reviewer_{contract}")
        if contract in _store(RESET):
            # Set (not popped) so the browser's widget state follows: an empty form after a save.
            st.session_state[category_key] = None
            st.session_state[reason_key] = ""
            _store(RESET).discard(contract)
        _seed(category_key, draft.get("category"), None, options)
        _seed(reason_key, draft.get("reason"), "")
        _seed(reviewer_key, draft.get("reviewer"), st.session_state.get(REVIEWER) or services.reviewer_default or "")
        category = st.selectbox("Target category", options, format_func=language.category_label, index=None,
                                placeholder="Choose a category", key=category_key,
                                help="Only categories the saved evidence allows are listed. Nothing is preselected.")
        category_feedback = st.empty()  # feedback sits directly under its field
        reason = st.text_area("Reason for the decision", key=reason_key, height=96, max_chars=store.REASON_MAX,
                              placeholder="e.g. Photo 3 shows the full road length; capture date checked")
        reason_feedback = st.empty()
        reviewer = st.text_input("Reviewer", key=reviewer_key, max_chars=store.REVIEWER_MAX,
                                 help="Your name. It is saved with the decision.")
        reviewer_feedback = st.empty()
        _store(SEEN).setdefault(contract, evidence_fingerprint(record))
        draft.update(category=category, reason=reason, reviewer=reviewer)
        if reviewer.strip():
            st.session_state[REVIEWER] = reviewer.strip()
        evidence_feedback = st.empty()
        if st.button("Save decision", type="primary", key=f"{services.namespace}_save_decision_{contract}",
                     on_click=_clear_receipt, args=(contract,)):
            problems = store.validate_decision(record, category, reason, reviewer)
            for field_name, slot in (("category", category_feedback), ("reason", reason_feedback),
                                     ("reviewer", reviewer_feedback)):
                if field_name in problems:
                    with slot.container():
                        field_feedback(problems[field_name])
            if problems:
                return  # everything typed stays in the form
            seen = _store(SEEN)
            current_hash = evidence_fingerprint(record)
            if seen.get(contract) not in (None, current_hash):
                # The saved evidence changed after this form was shown (a rerun or another session).
                # Nothing is saved; the page now shows the current evidence and the draft is kept.
                seen[contract] = current_hash
                with evidence_feedback.container():
                    error_state("The decision was not saved.",
                                "The saved evidence for this contract changed after you opened it.",
                                kept="Your draft is still here. Check the photos and flags shown now, then select "
                                     "Save decision again to record it against the current evidence.")
                return
            try:
                with loading("Saving decision"), closing(services.open_db()) as database:
                    saved = store.save_decision(database, record, category, reason, reviewer)
            except store.DecisionError as error:
                with evidence_feedback.container():
                    error_state("The decision was not saved.", escape_markdown(error.fields.get("evidence", str(error))),
                                kept="Your draft is still here.")
                return
            except (OSError, sqlite3.Error):
                # The primary button is the retry action; the draft was written back above.
                with evidence_feedback.container():
                    error_state("Decision could not be saved.", "The saved review database did not accept it.",
                                kept="Your draft is still here and nothing was recorded. Select Save decision to retry.")
                return
            _store(RECEIPTS)[contract] = saved["revision"]
            _store(DRAFTS).pop(contract, None)
            _store(SEEN).pop(contract, None)
            _store(RESET).add(contract)  # cleared before the widgets are created on the next run
            if services.after_save:
                services.after_save(saved)
            st.rerun()


# ---------------------------------------------------------------------------------------------
# 6. Timeline and budget, 7. Provenance and history
# ---------------------------------------------------------------------------------------------

def render_timeline_budget(dates, amounts, services):
    st.subheader("Timeline and budget")
    timeline, budget = st.columns(2)
    with timeline:
        st.markdown("**Reported dates**")
        st.caption("Each date is a reported value. Dates shown together are not a measured sequence of events.")
        if dates:
            st.table({"Reported date": {label: _date(value) for label, value in dates.items()}})
        else:
            st.caption("No reported dates are saved with this review.")
    with budget:
        st.markdown("**Reported amounts**")
        for label, value in (amounts or {}).items():
            st.text(f"{label} · {_peso(value)}")
        if not any(value not in (None, "") for value in (amounts or {}).values()):
            st.caption("No reported amounts are saved with this review.")
        if services.documents:
            services.documents()
        if services.satellite:
            services.satellite()


def render_history(record, reviewed_at, services):
    contract = record["contractId"]
    st.subheader("Provenance and history")
    provenance = record.get("review_provenance") or {}
    with st.container(border=True):
        st.markdown("**How the automatic assessment was produced**")
        if provenance:
            st.caption(f"Assessment rules {provenance.get('assessment_version', 'unrecorded')} · detector pipeline "
                       f"{provenance.get('inference_version', 'unrecorded')} · reviewed "
                       f"{language.format_timestamp(provenance.get('reviewed_at') or reviewed_at)}")
        else:
            st.caption("This is an older review with no recorded provenance. Rerun it to record one.")
    try:
        with closing(_reader(services)) as database:
            decisions = store.decision_history(database, contract)
            metadata = store.decision_metadata(database, contract)
            try:
                earlier = database.execute("SELECT revision, category, reviewed_at, archived_at FROM review_history "
                                           "WHERE contract_id = ? ORDER BY revision DESC LIMIT 20", (contract,)).fetchall()
            except sqlite3.OperationalError:  # no archived reviews have been kept in this database yet
                earlier = []
    except (OSError, sqlite3.Error):
        error_state("History could not be read.", "The saved review database did not respond.",
                    kept="No saved decision or review was changed.")
        return
    for warning in store.decision_warnings(record, metadata, review_stale=services.review_stale):
        notice("Check this decision against the current evidence", warning, icon=":material/history:")
    with st.container(border=True):
        st.markdown("**Reviewer decisions**")
        if decisions:
            st.dataframe([{"Decision": item["number"], "Revision": item["revision"],
                           "Category": language.category_label(item["category"]),
                           "Changed from": language.category_label(item["previous_category"]) if item["previous_category"] else "—",
                           "Reason": item["reason"], "Reviewer": item["reviewer"],
                           "Recorded": language.format_timestamp(item["decided_at"])} for item in decisions],
                         hide_index=True)
            st.caption("Revisions are never overwritten. Saving a new decision adds a revision; there is no undo.")
        elif language.is_manual(record.get("assessment") or {}):
            st.caption("The current decision was recorded before revisions were kept, so its history is not available.")
        else:
            st.caption("No reviewer decision has been recorded for this contract.")
    with st.container(border=True):
        st.markdown("**Earlier automatic reviews**")
        if earlier:
            # reviewed_at is stored in PHT; archived_at is SQLite CURRENT_TIMESTAMP (UTC).
            st.dataframe([{"Revision": revision, "Category": language.category_label(category), "Stored value": category,
                           "Reviewed": language.format_timestamp(reviewed),
                           "Archived": language.format_timestamp(archived, naive_tz=language.timezone.utc)}
                          for revision, category, reviewed, archived in earlier], hide_index=True)
        else:
            st.caption("No earlier saved review of this contract was archived.")


# ---------------------------------------------------------------------------------------------
# The whole workspace
# ---------------------------------------------------------------------------------------------

def render_contract_review(record, *, reviewed_at, services, automatic, automatic_note, dates, amounts,
                           source_checked=None, live_notes=()):
    """Render every section for one saved contract review, in plan order."""
    groups = photo_groups(record["photos"])
    render_identity(record, services, reviewed_at=reviewed_at, source_checked=source_checked)
    if services.namespace == "live":
        st.caption("This is the saved review of the contract. Opening a contract never reruns or changes it; only "
                   "Save decision and Rerun review write to it.")
    render_reported_facts(record, live_notes=live_notes)
    st.subheader("Assessment and limitations")
    if services.review_stale:
        show_state("stale")
    evidence_overview(record, record["assessment"], automatic, reviewed_at=reviewed_at, automatic_note=automatic_note)
    render_flags(record, groups, services)
    render_conflict(record, groups, services)
    # Keyed container: ui.py stacks these two columns below 1100 px, with evidence first.
    with st.container(key=f"{services.namespace}_review_split"):
        evidence, decision = st.columns([3, 2], gap="large")
        with evidence:
            render_evidence(record, groups, services)
        with decision:
            render_decision(record, services)
    render_timeline_budget(dates, amounts, services)
    render_history(record, reviewed_at, services)
