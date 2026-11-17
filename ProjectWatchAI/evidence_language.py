"""Display wording for stored evidence values.

Stored categories, outcomes, flags and score levels are provenance: they are saved in SQLite,
used by the scoring and requeue rules, and fingerprinted into caches. This module only maps
them to qualified, user-facing wording. Every mapping is reversible so widgets can keep the raw
value as their option value (``format_func``) and any label can be resolved back to its raw value.
No function here changes an evidence rule, threshold or score.
"""
import re
from datetime import date, datetime, timedelta, timezone

from contract_assessment import (
    COMPLETION_TIMING_GROUPS, NEUTRAL_REVIEW_CATEGORY, PHOTOS_BEFORE_COMPLETION, PRE_COMPLETION_EQUIPMENT,
    BUDGET_FLAG_PERCENT, CONSTRUCTION_EQUIPMENT, SCAFFOLDING_LABELS, flag_is_discrepancy, reported_day,
)
from yolo_feedback import UNFINISHED_TARGETS

LIMITATION_TEXT = (
    "Photos show only the photographed scope. They do not verify full completion, quality, "
    "quantities, spending or the actual completion date."
)
TIMING_NOTE = "Photo timing shows when a photo was taken. It is not a measured construction delay."
MODEL_SCORE_NOTE = ("Model scores describe how strongly a detector matched a visible feature. "
                    "They are not completion probabilities and are not comparable across detectors.")
MANUAL_COMPLETE = "Completed · manual review"
SCOPE_PREFIX = "Visible scope evidence"
LEGACY_PREFIX = f"{SCOPE_PREFIX} · legacy saved label: "
STORED_PREFIX = "Saved label: "

# Kinds drive the shared badge treatment. Each pairs a word with its icon; colour is never the only cue.
KIND_STYLE = {
    "scope": ("Automatic observation", ":material/visibility:", "blue"),
    "manual": ("Reviewer decision", ":material/person:", "violet"),
    "review": ("Needs review", ":material/rate_review:", "orange"),
    "cue": ("Needs review", ":material/construction:", "orange"),
    "gap": ("Evidence gap", ":material/help:", "gray"),
    "failed": ("Analysis failed", ":material/error:", "red"),
    "source": ("Source record", ":material/description:", "gray"),
}

_TIMING_DISPLAY = (
    "photo captured on reported completion date",
    "photo captured 1–7 days after reported completion",
    "photo captured 8–31 days after reported completion",
    "photo captured more than 31 days after reported completion",
)

# raw stored value -> (display label, kind, one-sentence explanation)
CATEGORIES = {
    **{raw: (f"{SCOPE_PREFIX} · {timing}", "scope",
             "A qualifying detector found the contract's scope in the newest dated photo group. "
             "This supports the photographed scope only.")
       for raw, timing in zip(COMPLETION_TIMING_GROUPS, _TIMING_DISPLAY)},
    MANUAL_COMPLETE: ("Manual decision · marked complete by reviewer", "manual",
                      "A reviewer recorded this decision. It is attributed to the reviewer, not to the detectors."),
    NEUTRAL_REVIEW_CATEGORY: ("Further review needed · latest photos not conclusive", "review",
                              "The newest dated photos were analyzed but did not meet a scope or construction-cue rule."),
    "Photos inconclusive": ("Photos inconclusive", "review",
                            "These photos do not provide a clear assessment. Check the observations and review manually."),
    PHOTOS_BEFORE_COMPLETION: ("All dated photos precede reported completion", "gap",
                               "Every dated photo was taken before the reported completion date, so they are historical context only."),
    "Possible unfinished work shown after reported completion": (
        "Possible unfinished work · photo captured after reported completion", "cue",
        "Construction cues outweighed scope detections in the newest dated photos. Review the cited photos."),
    "Possible unfinished work shown on reported completion date": (
        "Possible unfinished work · photo captured on reported completion date", "cue",
        "Construction cues outweighed scope detections in photos from the reported completion date."),
    "Completion not reported": ("Completion not reported in source", "source",
                                "The source record has no reported completion, so photos cannot be compared with it."),
    "Reported status unavailable": ("Reported status unavailable", "source",
                                    "The source record does not state a project status."),
    "No photo evidence": ("No photos in source record", "gap",
                          "No photos are available in this source record. This does not show that work is absent."),
    "Photo evidence unavailable": ("Photos could not be retrieved", "gap",
                                   "Photos could not be retrieved from the source. Retry the review; earlier evidence is kept."),
    "Photo analysis unavailable · retry needed": ("Photo analysis failed · retry needed", "failed",
                                                  "Detectors could not analyze the newest photos. This is not evidence either way."),
}
LEGACY_SCOPE_PREFIXES = ("Completion evidence found", "Completion evidence captured", "Completion supported",
                         "Visible scope supported")
_DISPLAY_TO_RAW = {label: raw for raw, (label, _, _) in CATEGORIES.items()}
assert len(_DISPLAY_TO_RAW) == len(CATEGORIES), "Category labels must be unique"


def category_label(raw):
    """Qualified display label for a stored category. Unknown values stay visible, never blank."""
    if raw is None or raw == "":
        return "No saved assessment"
    if raw in CATEGORIES:
        return CATEGORIES[raw][0]
    if raw.startswith(LEGACY_SCOPE_PREFIXES):
        return LEGACY_PREFIX + raw
    if raw in _DISPLAY_TO_RAW or raw.startswith((LEGACY_PREFIX, STORED_PREFIX)) or raw == "No saved assessment":
        return STORED_PREFIX + raw  # an unknown stored value that would collide with a display label
    return raw


def category_value(label):
    """Inverse of category_label."""
    if label in _DISPLAY_TO_RAW:
        return _DISPLAY_TO_RAW[label]
    if label == "No saved assessment":
        return None
    for prefix in (LEGACY_PREFIX, STORED_PREFIX):
        if label.startswith(prefix):
            return label.removeprefix(prefix)
    return label


def category_kind(raw):
    if raw in CATEGORIES:
        return CATEGORIES[raw][1]
    return "scope" if (raw or "").startswith(LEGACY_SCOPE_PREFIXES) else "gap"


def category_explanation(raw):
    if raw in CATEGORIES:
        return CATEGORIES[raw][2]
    if (raw or "").startswith(LEGACY_SCOPE_PREFIXES):
        return "Saved under an older label. Read it as visible scope evidence only, and rerun the review to refresh it."
    return "This saved category has no current description. Rerun the review to refresh it."


def category_badge(raw):
    """(text, icon, color) for st.badge. The text carries the meaning; the colour is supplementary."""
    _, icon, color = KIND_STYLE[category_kind(raw)]
    return category_label(raw), icon, color


OUTCOMES = {
    "Reported completed · possible unfinished work": "Reported completed · possible unfinished work in latest photos",
    "Reported completed · recent photo evidence supports the contract scope":
        "Reported completed · latest photos show visible scope evidence",
    "Reported completed · completion date unavailable": "Reported completed · completion date unavailable",
    "Reported completed · photo evidence inconclusive": "Reported completed · latest photos inconclusive",
    "Reported not completed": "Not reported completed",
    "Completion status unknown": "Reported status unknown",
    "Marked completed by manual review": "Manual decision · marked complete by reviewer",
}


def outcome_label(raw):
    if raw in OUTCOMES:
        return OUTCOMES[raw]
    if raw and raw.startswith("Manual review · "):
        label = category_label(raw.removeprefix("Manual review · "))
        return label if label.startswith("Manual decision · ") else f"Manual decision · {label}"
    return raw or "Unavailable"


# Flags: (pattern, display, explanation). Raw flags stay stored; patterns use the stable prefixes.
_FLAGS = (
    (r"Award amount exceeds approved budget for the contract",
     f"Award amount is at least {BUDGET_FLAG_PERCENT}% above the approved budget",
     "Compared with a positive approved budget for the contract (ABC) in the same record. A difference "
     "does not establish spending or misconduct; confirm both amounts cover the same scope."),
    (r"Reported project budget exceeds approved budget for the contract",
     f"Reported project budget is at least {BUDGET_FLAG_PERCENT}% above the approved budget",
     "Compared with a positive approved budget for the contract (ABC). Check that both amounts cover the same scope."),
    (r"Reported progress is outside 0–100% \((.+)\)", r"Reported physical progress is outside 0–100% (\1)",
     "Value as published in the source record."),
    (r"Reported completed with physical progress below 100% \((.+)\)",
     r"Reported completed while physical progress is below 100% (\1)", "Both values come from the source record."),
    (r"Reported not yet started with physical progress above 0% \((.+)\)",
     r"Reported not yet started while physical progress is above 0% (\1)", "Both values come from the source record."),
    (r"Reported completion precedes reported start", "Reported completion date is before the reported start date",
     "Dates as published in the source record."),
    (r"Contract expiry precedes reported start", "Contract expiry date is before the reported start date",
     "Dates as published in the source record."),
    (r"Reported completion is after contract expiry", "Reported completion date is after contract expiry",
     "Dates as published in the source record. This compares reported dates, not photo timing."),
    (r"Reported complete with no completion date", "Reported completed without a completion date",
     "Photo timing cannot be compared without a reported completion date."),
    (r"Reported complete with no gallery photos", "Reported completed; source lists no gallery photos",
     "Missing photos do not show that work is absent."),
    (r"All dated sampled photos precede reported completion", "All dated photos were captured before reported completion",
     "They remain historical context and cannot show the state at the reported completion date."),
    (re.escape(PRE_COMPLETION_EQUIPMENT),
     "Construction equipment in the latest building photo, captured before reported completion · follow-up needed",
     "The photo predates the reported completion date; it is a prompt for follow-up, not a finding of unfinished work."),
    (r"Some latest photo checks failed; retry incomplete analysis", "Some checks on the latest photos failed · retry needed",
     "Completed checks are shown. Failed checks are not evidence either way."),
    (r"Latest photo analysis unavailable; retry required", "Analysis of the latest photos failed · retry needed",
     "Failed analysis is not evidence either way. Earlier saved evidence is kept."),
    (r"Sampled photos meet the visual evidence rule while the contract is not reported completed",
     "Latest photos show visible scope while the source does not report completion",
     "Visible scope does not establish completion; compare with the reported status."),
    (r"Possible unfinished work in photos on or after reported completion",
     "Possible unfinished work in latest photos captured on or after reported completion",
     "Construction cues are detector observations. Review the cited photos before drawing a conclusion."),
    (r"Latest-date photos contain both.*", "Latest dated photos show both scope evidence and construction cues",
     "Review each relevant photo; neither observation settles the assessment alone."),
    (r"Latest photo evidence is inconclusive\. Review the photos manually\.",
     "Latest photos are inconclusive · manual review needed",
     "These photos do not provide a clear assessment."),
    (r"Manual decision retained; underlying evidence changed or original evidence provenance is unavailable",
     "Manual decision retained · evidence changed since the decision, or its provenance is unavailable",
     "Re-review the decision against the current evidence. The prior decision, reason and reviewer are kept."),
)
_FLAG_PATTERNS = tuple((re.compile(pattern + r"\Z"), display, why) for pattern, display, why in _FLAGS)


def flag_text(raw):
    """(display text, explanation) for a stored flag. Unknown flags are shown as stored."""
    for pattern, display, why in _FLAG_PATTERNS:
        if match := pattern.match(raw):
            return match.expand(display), why
    return raw, ""


def grouped_flags(flags):
    """Deduplicate and split flags into record inconsistencies and evidence/review notes."""
    unique = list(dict.fromkeys(flags or ()))
    return ([(raw, *flag_text(raw)) for raw in unique if flag_is_discrepancy(raw)],
            [(raw, *flag_text(raw)) for raw in unique if not flag_is_discrepancy(raw)])


ELIGIBILITY = {
    "validated": ("Validated detector", "Can support visible-scope categories. It still does not certify a complete contract."),
    "provisional": ("Provisional detector", "Supports qualified visible-scope evidence only."),
    # Policy audit A1: under the current rules review_only detections CAN set a category on their own
    # (quality only feeds experimental_evidence_photos). Describe that behaviour; do not promise gating.
    "review_only": ("Experimental · not validated",
                    "Not validated. Under the current rules its detections can still set a photo assessment, "
                    "so treat findings from it as unconfirmed and review the photos."),
    "missing": ("Not installed", "No checkpoint is installed, so this feature could not be assessed."),
    "unavailable": ("Analysis failed", "The detector could not run on this photo. Retry the review."),
}


def eligibility(quality):
    return ELIGIBILITY.get(quality, ("Eligibility unknown", "This observation has no recorded detector eligibility."))


def finding_text(finding):
    """Plain description of one saved detector result, keeping missing, failed and negative distinct."""
    detector = finding.get("detector") or "Detector"
    status = finding.get("status")
    if status == "detected":
        detections = finding.get("detections")
        if detections:
            parts = [f"{d.get('label', 'feature')} (model score {float(d.get('confidence', 0)):.0%})" for d in detections]
            return f"{detector}: detected " + ", ".join(parts)
        observation = (finding.get("observation") or "").removeprefix("YOLO detected ").rstrip(".")
        # Legacy prose kept rounded "(NN% confidence)" text; reword it as a model score, value unchanged.
        observation = re.sub(r"\((\d+(?:\.\d+)?)% confidence\)", r"(model score \1%, rounded)", observation)
        return f"{detector}: detected {observation or 'a feature'} (legacy record)"
    if status == "no detection":
        return f"{detector}: no target detected. This does not show that the work is absent."
    if status == "unavailable":
        if finding.get("quality") == "missing":
            return f"{detector}: not installed. This feature could not be assessed."
        return f"{detector}: analysis failed. Retry needed."
    return f"{detector}: result unknown."


def photo_timing_text(days):
    if days is None:
        return "Photo capture date unknown or not comparable with reported completion"
    if days == 0:
        return "Photo captured on the reported completion date"
    unit = "day" if abs(days) == 1 else "days"
    return f"Photo captured {abs(days):,} {unit} {'after' if days > 0 else 'before'} reported completion"


SCORE_LEVELS = {
    "Early": "Reported complete before contract expiry",
    "On time": "Reported complete on contract expiry date",
    "1–7 days late": "Reported complete 1–7 days after contract expiry",
    "8–31 days late": "Reported complete 8–31 days after contract expiry",
    "More than 31 days late": "Reported complete more than 31 days after contract expiry",
    "Overdue and incomplete": "Not reported complete · contract expiry passed",
    "Pending": "Pending · contract expiry not reached",
    "Unscored": "No score",
    "Unscored · no qualifying photo evidence": "No score · no qualifying photo evidence",
}
_LEVEL_TO_RAW = {label: raw for raw, label in SCORE_LEVELS.items()}


def score_level_label(raw):
    return SCORE_LEVELS.get(raw, raw)


def score_level_value(label):
    return _LEVEL_TO_RAW.get(label, label)


# Free-text relabelling for application-generated strings (saved highlight text, contract summaries).
_TEXT_MAP = {
    **{raw: label for raw, (label, _, _) in CATEGORIES.items() if raw != label},
    **{f"Manual review · {raw}": outcome_label(f"Manual review · {raw}") for raw in CATEGORIES},
    **{raw: label for raw, label in OUTCOMES.items() if raw != label},
    # Highlight facts are written as "<contractor> · <level> (<points>)" by project_review.
    **{f" · {raw} (": f" · {label} (" for raw, label in SCORE_LEVELS.items()},
    **{f" · {raw}. ": f" · {label}. " for raw, label in SCORE_LEVELS.items()},
}
def _text_pattern(raw):
    label = _TEXT_MAP[raw]
    # A label that extends its raw value must not be extended again on a second pass.
    return re.escape(raw) + (f"(?!{re.escape(label[len(raw):])})" if label.startswith(raw) else "")


_TEXT_PATTERN = re.compile("|".join(_text_pattern(raw) for raw in sorted(_TEXT_MAP, key=len, reverse=True)))


def relabel_text(text):
    """Replace stored category/outcome strings inside generated text; idempotent and single-pass."""
    if not text:
        return text
    return _TEXT_PATTERN.sub(lambda match: _TEXT_MAP[match.group(0)], text)


def is_manual(assessment):
    return bool("manual_category_note" in (assessment or {}) or (assessment or {}).get("manual_completion_evidence"))


# --- Dates -------------------------------------------------------------------------------------
PHILIPPINE_TIME = timezone(timedelta(hours=8), "PHT")
_DATE_ONLY = re.compile(r"\d{4}-\d{2}-\d{2}\Z")


def format_timestamp(value, *, naive_tz=PHILIPPINE_TIME):
    """Date-only values stay date-only; timestamps are shown in Philippine time with "PHT".

    ``naive_tz`` says how a timestamp without an offset was written: application and BetterGov
    times are Philippine time; SQLite ``CURRENT_TIMESTAMP`` (``review_history.archived_at``) is UTC.
    """
    if value is None or value == "":
        return "Date unknown"
    if hasattr(value, "to_pydatetime"):  # pandas Timestamp / NaT
        if value != value:
            return "Date unknown"
        value = value.to_pydatetime()
    if isinstance(value, date) and not isinstance(value, datetime):
        return f"{value:%d %b %Y}"
    if not isinstance(value, datetime):
        text = str(value).strip()
        if _DATE_ONLY.match(text):
            try:
                return f"{date.fromisoformat(text):%d %b %Y}"
            except ValueError:
                return "Date unknown"
        try:
            value = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return "Date unknown"
    if value.tzinfo is None:
        value = value.replace(tzinfo=naive_tz)
    return f"{value.astimezone(PHILIPPINE_TIME):%d %b %Y, %H:%M} PHT"


# --- Photo roles within the newest-date rule ---------------------------------------------------
def latest_capture_day(photos):
    return max((day for photo in photos if (day := reported_day(photo.get("capturedAt")))), default=None)


def photo_role(photos, index, latest_day=None):
    """'latest date', 'historical' or 'date unknown' for photos[index] (presentation of the newest-date rule)."""
    latest_day = latest_day if latest_day is not None else latest_capture_day(photos)
    day = reported_day(photos[index].get("capturedAt"))
    if day is None:
        return "date unknown"
    return "latest date" if day == latest_day else "historical"


def photo_caption(photos, index, latest_day=None):
    photo = photos[index]
    return (f"Photo {index + 1} · {format_timestamp(photo.get('capturedAt'))} · "
            f"{photo_role(photos, index, latest_day)}")


# --- Standardized evidence states ---------------------------------------------------------------
# One treatment per state: (kind, icon, title, explanation). Titles follow plan §8 microcopy.
STATES = {
    "no_photos": ("gap", ":material/hide_image:", "No photos are available in this source record.",
                  "This does not show that work is unfinished or absent."),
    "retrieval_failed": ("failed", ":material/cloud_off:", "Photos could not be retrieved.",
                         "Retry the review or open the source record. Any earlier saved evidence is kept."),
    "detector_missing": ("gap", ":material/extension_off:", "Detector not installed.",
                         "This feature could not be assessed. Operators can check Admin › Model status."),
    "analysis_failed": ("failed", ":material/error:", "Photo analysis failed.",
                        "Failed analysis is not evidence either way. Retry the review."),
    "inconclusive": ("review", ":material/rate_review:", "These photos do not provide a clear assessment.",
                     "Check the observations and the photos, then record a reviewer decision if needed."),
    "no_detection": ("gap", ":material/search_off:", "No target detected.",
                     "This does not show that the work is absent; the feature may be out of frame or not visible."),
    "stale": ("review", ":material/update:",
              "Saved review needs a refresh because evidence or assessment rules changed.",
              "The last review is kept until a rerun succeeds."),
    "historical": ("gap", ":material/history:", "Historical photo — excluded from the current latest-date assessment.",
                   "Only the newest dated photo group can set the current assessment. It stays inspectable."),
    "unknown_date": ("gap", ":material/event_busy:", "Capture date unknown.",
                     "This photo cannot set the latest-date assessment."),
    "same_date_conflict": ("cue", ":material/compare:",
                           "Latest dated photos show both scope evidence and construction cues.",
                           "Review each cited photo; neither observation settles the assessment alone."),
    "no_saved_assessment": ("source", ":material/pending:", "No saved photo assessment.",
                            "Photos below come straight from the source and have not been assessed and saved. "
                            "Admin › Contract reviews can analyze this contract."),
}


def state(name):
    """(kind, icon, colour slot, title, explanation) for a standardized evidence state."""
    kind, icon, title, explanation = STATES[name]
    return kind, icon, KIND_STYLE[kind][2], title, explanation


def category_state(raw):
    """The standardized state that explains a stored category, when one applies."""
    return {"No photo evidence": "no_photos", "Photo evidence unavailable": "retrieval_failed",
            "Photo analysis unavailable · retry needed": "analysis_failed", "Photos inconclusive": "inconclusive",
            NEUTRAL_REVIEW_CATEGORY: "inconclusive"}.get(raw)


# --- Same-date conflict (presentation only; the rule itself is unchanged) ------------------------
CUE_DETECTORS = {"Under construction", "Temporary scaffolding", "Construction equipment", "Building under construction",
                 *(target[0] for target in UNFINISHED_TARGETS.values())}
CUE_LABELS = {"under construction", "possible unfinished building work", *SCAFFOLDING_LABELS, *CONSTRUCTION_EQUIPMENT,
              *(target[1] for target in UNFINISHED_TARGETS.values())}


def _detected_labels(finding):
    if finding.get("status") != "detected":
        return []
    if finding.get("detections"):
        return [str(d.get("label", "")) for d in finding["detections"]]
    return [label.strip().removeprefix("and ") for label, _ in re.findall(
        r"([\w -]+) \((\d+(?:\.\d+)?)% confidence\)", (finding.get("observation") or "").removeprefix("YOLO detected "))]


def same_date_conflict(record):
    """Photos in the newest dated group with scope detections and with construction cues, if both occur.

    Returns None without a conflict, else {"scope_photos", "cue_photos", "kept"}; "kept" says what the
    saved assessment retained ("scope", "cues" or "neither"). Detections below a scope threshold are still
    listed: this is a prompt for review, not a recalculation of the rule.
    """
    photos = record.get("photos") or []
    latest = latest_capture_day(photos)
    if latest is None:
        return None
    scope, cues = set(), set()
    for number, photo in enumerate(photos, start=1):
        if reported_day(photo.get("capturedAt")) != latest:
            continue
        for finding in photo.get("yolo", []):
            for label in _detected_labels(finding):
                is_cue = finding.get("detector") in CUE_DETECTORS or label.casefold() in CUE_LABELS
                (cues if is_cue else scope).add(number)
    if not scope or not cues:
        return None
    assessment = record.get("assessment") or {}
    kept = ("scope" if assessment.get("completion_supporting_photo") is not None else
            "cues" if assessment.get("possible_unfinished_work_photos") else "neither")
    return {"scope_photos": sorted(scope), "cue_photos": sorted(cues), "kept": kept}


EXPERIMENTAL_BASIS_NOTE = "Based on an experimental detector that has not passed validation."


def experimental_basis(assessment):
    """True when the assessment's supporting or cue photos include photos whose detections came from
    review_only (experimental) detectors. Presentation only; the rule is unchanged (policy audit A1)."""
    assessment = assessment or {}
    experimental = set(assessment.get("experimental_evidence_photos") or ())
    basis = set(assessment.get("possible_unfinished_work_photos") or ())
    if (supporting := assessment.get("completion_supporting_photo")) is not None:
        basis.add(supporting)
    basis.update(assessment.get("scope_supporting_photos") or ())
    return bool(experimental & basis)


def conflict_text(conflict):
    def photos(numbers):
        return ("photo " if len(numbers) == 1 else "photos ") + ", ".join(map(str, numbers))
    # Policy audit A4: the rule keeps one side and drops the other without a flag; say which side it kept.
    kept = {"scope": "The rule kept the visible scope evidence (the construction cues did not score higher) "
                     "and recorded no flag for the cues.",
            "cues": "The rule kept the construction cues (they scored higher than the scope detections) "
                    "and recorded no flag for the scope detections.",
            "neither": "The rule kept neither observation, so the assessment needs review."}[conflict["kept"]]
    return (f"Scope detections: {photos(conflict['scope_photos'])}. "
            f"Construction cues: {photos(conflict['cue_photos'])}. {kept}")


# --- Contractor score cells ---------------------------------------------------------------------
SCORE_NO_SCORE = "No score"


def score_text(value, *, signed=False, decimals=None):
    """'No score' for missing (never zero); fixed formatting for numbers (no 3.000000)."""
    if value is None or value != value:
        return SCORE_NO_SCORE
    if decimals is not None:
        return f"{value:+.{decimals}f}" if signed else f"{value:.{decimals}f}"
    return f"{int(round(value)):+d}" if signed else f"{int(round(value))}"
