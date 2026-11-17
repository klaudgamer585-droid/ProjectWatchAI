"""Links between photos, flags and the record facts behind them. Pure presentation logic.

No Streamlit and no evidence rule: every link is read from what the saved assessment already
contains (photo numbers, flags, dates, amounts). Nothing here changes a category, threshold or score,
and no record is mutated. Missing keys in older records simply produce no link.
"""
import re
from collections import defaultdict

import evidence_language as language
from contract_assessment import (PRE_COMPLETION_EQUIPMENT, amount, flag_is_discrepancy, reported_day,
                                 status_and_date_flags)

_BUDGET_FLAGS = {
    "Award amount exceeds approved budget for the contract": ("Award amount", "Approved budget for the contract"),
    "Reported project budget exceeds approved budget for the contract":
        ("Reported project budget", "Approved budget for the contract"),
}
_DATE_FLAGS = {
    "Reported completion precedes reported start": ("Reported start", "Reported completion"),
    "Contract expiry precedes reported start": ("Reported start", "Contract expiry"),
    "Reported completion is after contract expiry": ("Reported completion", "Contract expiry"),
    "Reported complete with no completion date": ("Reported completion",),
}
_PROGRESS_FLAG = re.compile(r"Reported (?:progress is outside 0–100%|completed with physical progress below 100%|"
                            r"not yet started with physical progress above 0%) \((.+)\)\Z")
_PRE_COMPLETION = "All dated sampled photos precede reported completion"
_UNFINISHED = "Possible unfinished work in photos on or after reported completion"
_INCONCLUSIVE = "Latest photo evidence is inconclusive. Review the photos manually."
_PARTIAL_FAILURE = "Some latest photo checks failed; retry incomplete analysis"
_TOTAL_FAILURE = "Latest photo analysis unavailable; retry required"
_SCOPE_WHILE_OPEN = "Sampled photos meet the visual evidence rule while the contract is not reported completed"
_BOTH_PREFIX = "Latest-date photos contain both"
_NO_GALLERY = "Reported complete with no gallery photos"


# --- Photo groups ---------------------------------------------------------------------------------
def _plural(count):
    return f"{count} photo" if count == 1 else f"{count} photos"


def photo_groups(photos):
    """Photo numbers (1-based, into the original list) grouped by capture day in Philippine time.

    Newest dated group first, older days descending, photos without a usable date last. The newest
    group is the latest ``reported_day``, which is the day the assessment treats as current.
    """
    by_day = defaultdict(list)
    for number, photo in enumerate(photos or (), start=1):
        by_day[reported_day((photo or {}).get("capturedAt"))].append(number)
    dated = sorted((day for day in by_day if day is not None), reverse=True)
    groups = []
    for index, day in enumerate(dated):
        shown = language.format_timestamp(day)
        role = "newest" if index == 0 else "earlier"
        prefix = "Newest dated group" if index == 0 else "Earlier"
        groups.append({"key": day.isoformat(), "day": day, "role": role,
                       "label": f"{prefix} · {shown} · {_plural(len(by_day[day]))}", "numbers": sorted(by_day[day])})
    if None in by_day:
        groups.append({"key": None, "day": None, "role": "unknown",
                       "label": f"Date unknown · {_plural(len(by_day[None]))}", "numbers": sorted(by_day[None])})
    return groups


def _newest_numbers(photos):
    groups = photo_groups(photos)
    return list(groups[0]["numbers"]) if groups and groups[0]["role"] == "newest" else []


def _photos_text(numbers):
    return ("photo " if len(numbers) == 1 else "photos ") + ", ".join(map(str, numbers))


def _usable(photo):
    return [f for f in (photo or {}).get("yolo") or () if isinstance(f, dict)]


def _all_failed(photo):
    findings = _usable(photo)
    return bool(findings) and all(f.get("status") == "unavailable" for f in findings)


def _any_failed(photo):
    return any(f.get("status") == "unavailable" and f.get("quality") != "missing" for f in _usable(photo))


def _failed_numbers(photos, numbers, test):
    return [n for n in numbers if 1 <= n <= len(photos) and test(photos[n - 1])]


def _safe_conflict(record):
    try:
        return language.same_date_conflict(record)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def _valid_numbers(values, photos):
    return sorted({int(n) for n in values or () if isinstance(n, int) and 1 <= n <= len(photos)})


# --- Flag links -----------------------------------------------------------------------------------
def _peso(value):
    number = amount(value) if value is not None else None
    return f"₱{number:,.2f}" if number is not None else "Unavailable"


def _date_entry(label, record, key=None):
    return (label, language.format_timestamp((record.get("dates") or {}).get(key or label)))


def _flags_of(record):
    assessment = record.get("assessment") or {}
    flags = list(assessment.get("flags") or ())
    try:
        flags += status_and_date_flags({**record, "dates": record.get("dates") or {}})
    except (KeyError, TypeError, ValueError, AttributeError):
        pass
    return list(dict.fromkeys(flags))


def _link(raw, record):
    photos = record.get("photos") or []
    assessment = record.get("assessment") or {}
    text, why = language.flag_text(raw)
    link = {"raw": raw, "text": text, "why": why, "discrepancy": flag_is_discrepancy(raw),
            "photos": [], "counter_photos": [], "dates": [], "amounts": [], "counter_note": None}
    conflict = _safe_conflict(record)
    scope_photos = list(conflict["scope_photos"]) if conflict else []
    cue_photos = list(conflict["cue_photos"]) if conflict else []
    newest = _newest_numbers(photos)

    def counter(numbers, what):
        if numbers:
            link["counter_photos"] = list(numbers)
            link["counter_note"] = f"{what} also appear in {_photos_text(numbers)} on the same date."

    if raw in _BUDGET_FLAGS:
        left, right = _BUDGET_FLAGS[raw]
        budget = record.get("budgetPHP") or {}
        link["amounts"] = [(left, _peso(budget.get(left))), (right, _peso(budget.get(right)))]
        a, b = amount(budget.get(left)), amount(budget.get(right))
        if a is not None and b is not None and b > 0:
            link["amounts"].append(("Difference", f"₱{a - b:,.2f} ({(a - b) * 100 / b:.1f}% above the approved budget)"))
    elif raw in _DATE_FLAGS:
        link["dates"] = [_date_entry(label, record) for label in _DATE_FLAGS[raw]]
    elif match := _PROGRESS_FLAG.match(raw):
        link["amounts"] = [("Reported progress", str(record.get("progress") if record.get("progress") is not None
                                                      else match.group(1)))]
        if record.get("status"):
            link["amounts"].append(("Reported status", str(record["status"])))
    elif raw == _PRE_COMPLETION:
        dated = [n for n in range(1, len(photos) + 1) if reported_day(photos[n - 1].get("capturedAt"))]
        link["photos"] = dated
        link["dates"] = [_date_entry("Reported completion", record)]
        if newest:
            link["dates"].append(("Latest photo", language.format_timestamp(reported_day(photos[newest[0] - 1].get("capturedAt")))))
    elif raw == _UNFINISHED:
        link["photos"] = _valid_numbers(assessment.get("possible_unfinished_work_photos"), photos)
        counter(scope_photos, "Visible scope detections")
        link["dates"] = [_date_entry("Reported completion", record)]
    elif raw == PRE_COMPLETION_EQUIPMENT:
        latest = assessment.get("latest_evidence_photo")
        link["photos"] = _valid_numbers([latest], photos)
        link["dates"] = [_date_entry("Reported completion", record)]
        counter([n for n in scope_photos if n not in link["photos"]], "Visible scope detections")
    elif raw == _INCONCLUSIVE:
        link["photos"] = newest
    elif raw == _PARTIAL_FAILURE:
        link["photos"] = _failed_numbers(photos, newest, _any_failed)
    elif raw == _TOTAL_FAILURE:
        link["photos"] = _failed_numbers(photos, newest, _all_failed)
    elif raw == _SCOPE_WHILE_OPEN:
        link["photos"] = _valid_numbers([assessment.get("completion_supporting_photo")], photos)
        link["amounts"] = [("Reported status", str(record.get("status") or "Unavailable"))]
        counter(cue_photos, "Construction cues")
    elif raw.startswith(_BOTH_PREFIX):
        link["photos"] = scope_photos
        counter(cue_photos, "Construction cues")
    elif raw == _NO_GALLERY:
        link["amounts"] = [("Source gallery photo count", str(record.get("galleryPhotoCount", "Unavailable")))]
    return link


def flag_links(record):
    """One entry per distinct flag (saved flags, then record date/status flags), with its supporting facts."""
    return [_link(raw, record) for raw in _flags_of(record)]


# --- Photo citations ------------------------------------------------------------------------------
def photo_citations(record):
    """{photo number: [{"kind", "text"}]} — how the saved assessment cites each photo, for the photo panel."""
    photos = record.get("photos") or []
    assessment = record.get("assessment") or {}
    cites = defaultdict(list)

    def add(number, kind, text):
        item = {"kind": kind, "text": text}
        if 1 <= number <= len(photos) and item not in cites[number]:
            cites[number].append(item)

    scope = _valid_numbers([assessment.get("completion_supporting_photo"), *(assessment.get("scope_supporting_photos") or ())], photos)
    for number in scope:
        add(number, "scope", "Cited as visible scope evidence for the assessment")
    for number in _valid_numbers(assessment.get("possible_unfinished_work_photos"), photos):
        add(number, "cue", "Cited as a possible unfinished-work cue (a detector observation, not a finding)")
    if conflict := _safe_conflict(record):
        for number in conflict["scope_photos"]:
            others = [n for n in conflict["cue_photos"] if n != number]
            if number in conflict["cue_photos"]:
                add(number, "counter", "Counterevidence: construction cues are also detected in this photo")
            if others:
                add(number, "counter", f"Counterevidence: construction cues also appear in {_photos_text(others)} on the same date")
        for number in conflict["cue_photos"]:
            others = [n for n in conflict["scope_photos"] if n != number]
            if number in conflict["scope_photos"]:
                add(number, "counter", "Counterevidence: visible scope detections are also detected in this photo")
            if others:
                add(number, "counter", f"Counterevidence: visible scope detections also appear in {_photos_text(others)} on the same date")
    for number in _failed_numbers(photos, _newest_numbers(photos), _all_failed):
        add(number, "failed", "No detector check completed on this photo; this is not evidence either way")
    for link in flag_links(record):
        for number in link["photos"]:
            add(number, "flag", link["text"])
    return {number: items for number, items in sorted(cites.items())}
