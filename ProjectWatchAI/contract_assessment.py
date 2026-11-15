"""Compute contract evidence and discrepancy flags from published records."""

import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from statistics import mean

from yolo_feedback import UNFINISHED_TARGETS
from evidence_policy import ASSESSMENT_VERSION, EVIDENCE_LIMITATION, PROVISIONAL_SCOPE_MODELS

BUILDING_CONFIDENCE = 24
BRIDGE_CONFIDENCE = 50
ROAD_CONFIDENCE = 35
BUDGET_FLAG_PERCENT = 10
CONSTRUCTION_EQUIPMENT = ("excavator", "bulldozer", "road roller", "concrete mixer truck", "tower crane")
PHOTO_TIMEZONE = timezone(timedelta(hours=8))
UNKNOWN_CAPTURE = datetime.min.replace(tzinfo=timezone.utc)
NEUTRAL_REVIEW_CATEGORY = "Neutral · further review needed"
COMPLETION_EVIDENCE_FOUND = "Completion evidence found"
COMPLETION_TIMING_GROUPS = tuple(
    f"{COMPLETION_EVIDENCE_FOUND} · {timing}"
    for timing in ("on reported date", "1–7 days later", "8–31 days later", "more than 31 days later")
)


def completion_timing_group(days):
    if days == 0:
        return COMPLETION_TIMING_GROUPS[0]
    if days <= 7:
        return COMPLETION_TIMING_GROUPS[1]
    if days <= 31:
        return COMPLETION_TIMING_GROUPS[2]
    return COMPLETION_TIMING_GROUPS[3]
SCAFFOLDING_LABELS = ("temporary scaffolding", "temporary metal scaffolding", "temporary timber shoring")


def flag_is_discrepancy(flag):
    """Coverage/experimental-model warnings are review gaps, not established inconsistencies."""
    return flag.startswith(("Award amount exceeds", "Reported project budget exceeds",
        "Reported completion precedes", "Contract expiry precedes", "Reported completion is after",
        "Reported progress", "Reported completed with physical", "Reported not yet started with",
        "Possible unfinished work", "Latest-date photos contain both"))


def reported_day(value):
    try:
        text = str(value)
        if len(text) == 10:
            return date.fromisoformat(text)
        parsed = datetime.fromisoformat(text)
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=PHOTO_TIMEZONE)).astimezone(PHOTO_TIMEZONE).date()
    except ValueError:
        return None


def photo_capture_order(photo):
    try:
        captured = datetime.fromisoformat(str(photo.get("capturedAt")))
    except ValueError:
        return UNKNOWN_CAPTURE
    return captured if captured.tzinfo else captured.replace(tzinfo=PHOTO_TIMEZONE)


def amount(value):
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except InvalidOperation:
        return None


def status_and_date_flags(record):
    flags = []
    status = str(record.get("status") or "").strip().casefold()
    raw_progress = record.get("progress")
    progress = amount(str(raw_progress).strip().removesuffix("%").strip())
    if progress is not None:
        if not 0 <= progress <= 100:
            flags.append(f"Reported progress is outside 0–100% ({progress}%)")
        elif status == "completed" and progress < 100:
            flags.append(f"Reported completed with physical progress below 100% ({progress}%)")
        elif status == "not yet started" and progress > 0:
            flags.append(f"Reported not yet started with physical progress above 0% ({progress}%)")
    dates = record["dates"]
    start = reported_day(dates.get("Reported start"))
    completion = reported_day(dates.get("Reported completion"))
    expiry = reported_day(dates.get("Contract expiry"))
    if start and completion and completion < start:
        flags.append("Reported completion precedes reported start")
    if start and expiry and expiry < start:
        flags.append("Contract expiry precedes reported start")
    if expiry and completion and completion > expiry:
        flags.append("Reported completion is after contract expiry")
    return flags


PHOTOS_BEFORE_COMPLETION = "Photos precede reported completion"
PRE_COMPLETION_EQUIPMENT = "Construction equipment in latest building photo before reported completion · follow-up needed"


def completion_evidence_category(reported_complete, record, latest_support, flags, needs_photo_review):
    if reported_day(record["dates"].get("Reported completion")) is None:
        return "Completion not reported"
    if "All dated sampled photos precede reported completion" in flags:
        return PHOTOS_BEFORE_COMPLETION
    if needs_photo_review:
        return NEUTRAL_REVIEW_CATEGORY
    if not reported_complete:
        return "Completion not reported" if record.get("status") else "Reported status unavailable"
    if record.get("galleryPhotoCount") == 0:
        return "No photo evidence"
    if latest_support is None:
        return "Photo evidence unavailable" if not record["photos"] else "Photos inconclusive"
    return completion_timing_group(latest_support)


def assess_contract(record):
    drainage_canal_title = re.search(r"\bdrainage[\s-]+canals?\b", record.get("description") or "", re.IGNORECASE)
    dates = record["dates"]
    start = reported_day(dates.get("Reported start"))
    completion = reported_day(dates.get("Reported completion"))
    expiry = reported_day(dates.get("Contract expiry"))
    budget = record["budgetPHP"]
    flags = []
    comparisons = {}
    for label, left, right in (
        ("award_minus_approved", "Award amount", "Approved budget for the contract"),
        ("reported_minus_approved", "Reported project budget", "Approved budget for the contract"),
    ):
        a, b = amount(budget.get(left)), amount(budget.get(right))
        comparisons[label] = str(a - b) if a is not None and b is not None else None
        if a is not None and b is not None and b > 0 and (a - b) * 100 >= b * BUDGET_FLAG_PERCENT:
            flags.append(f"{left} exceeds {right.lower()}")
    flags.extend(status_and_date_flags(record))
    reported_complete = str(record.get("status") or "").strip().casefold() == "completed"
    if reported_complete and completion is None:
        flags.append("Reported complete with no completion date")
    if reported_complete and record.get("galleryPhotoCount") == 0:
        flags.append("Reported complete with no gallery photos")

    scores, work_cues, scoped_photos, photo_offsets = [], [], [], []
    scope_confidence = {}
    work_confidence = {}
    quality_gaps = []
    usable_by_photo = {}
    detected_by_photo = {}
    work_evidence = {}
    building_photo_scores = []
    strongest_detections = {}
    road_scores = {}
    photos_by_label = Counter()
    for number, photo in enumerate(record["photos"], start=1):
        captured = reported_day(photo.get("capturedAt"))
        offset = (captured - completion).days if captured and completion else None
        photo_offsets.append(offset)
        scope = (photo.get("scope") or "").casefold()
        hits, equipment_hits = {}, {}
        usable_by_photo[number] = any(f.get("status") in {"detected", "no detection"} for f in photo["yolo"])
        detected_by_photo[number] = any(f.get("status") == "detected" for f in photo["yolo"])
        for finding in photo["yolo"]:
            if finding.get("quality", "provisional" if finding.get("detector") in PROVISIONAL_SCOPE_MODELS else "review_only") == "review_only":
                if finding.get("status") == "detected":
                    quality_gaps.append(number)
            if finding.get("status") == "detected":
                detections = finding.get("detections")
                if detections is None:
                    # Read old reviews without silently treating rounded legacy scores as raw values.
                    detections = [{"label": label.strip().removeprefix("and "), "confidence": float(score) / 100}
                        for label, score in re.findall(r"([\w -]+) \((\d+(?:\.\d+)?)% confidence\)",
                            finding.get("observation", "").removeprefix("YOLO detected "))]
                for detection in detections:
                    label, score = detection["label"], float(detection["confidence"]) * 100
                    if not 0 <= score <= 100:
                        continue
                    target = equipment_hits if finding.get("detector") == "Construction equipment" else hits
                    target[label] = max(score, target.get(label, 0))
        for label, score in hits.items():
            strongest_detections[label] = max(score, strongest_detections.get(label, 0))
            photos_by_label[label] += 1
        if hits:
            scores.append(max(hits.values()))
        category = photo.get("category") or record.get("category")
        building_photo = category == "Buildings and Facilities" or (
            category == "Water Provision and Storage"
            and bool(re.search(r"\b(?:buildings?|pump\s*houses?|pumping stations?|treatment plants?)\b", scope))
        )
        work_check = any(
            finding.get("detector") in {"Temporary scaffolding", "Under construction"}
            and finding["status"] in {"detected", "no detection"}
            for finding in photo["yolo"]
        )
        unfinished_target = UNFINISHED_TARGETS.get(category)
        scaffold_score = max(hits.get(label, 0) for label in SCAFFOLDING_LABELS)
        equipment_score = max(max(hits.get(label, 0), equipment_hits.get(label, 0))
                              for label in CONSTRUCTION_EQUIPMENT)
        unfinished_score = hits.get("Under construction", 0)
        if unfinished_target:
            unfinished_score = max(unfinished_score, hits.get(unfinished_target[1], 0))
        if building_photo or work_check:
            unfinished_score = max(unfinished_score, hits.get("possible unfinished building work", 0))

        scope_scores = []
        if category == "Roads" or re.search(r"\b(?:roads?|asphalt overlay|concrete paving)\b", scope):
            road_score = max(hits.get("paved road surface", 0), hits.get("unpaved road surface", 0))
            road_scores[number] = road_score
            if road_score >= ROAD_CONFIDENCE:
                scope_scores.append(road_score)
        for applies, label, threshold in (
            (category == "Roads" and "slope protection" in (scope + " " + (record.get("description") or "").casefold()), "slope protection", 50),
            (building_photo, "building structure", BUILDING_CONFIDENCE),
            (category == "Bridges", "bridge", BRIDGE_CONFIDENCE),
            (category == "Water Provision and Storage", "elevated water tank", 1),
            (category == "Water Provision and Storage", "stainless steel water tank", 1),
            (category == "Flood Control and Drainage", "concrete riverbank protection", 1),
            (category == "Flood Control and Drainage" and drainage_canal_title, "lined drainage channel", 1),
            ("grating" in scope, "street drain grate", 70),
            ("stainless" in scope, "stainless steel water tank", 70),
            ("revetment" in scope, "concrete riverbank protection", 70),
        ):
            if applies and hits.get(label, 0) >= threshold:
                scope_scores.append(hits[label])
        work_score = max(unfinished_score, scaffold_score)
        best_scope = max(scope_scores, default=0)
        work_visible = work_score > best_scope and (best_scope > 0 or work_score >= 50)
        if building_photo:
            building_photo_scores.append({
                "photo": number, "building_confidence_pct": hits.get("building structure"),
                "days_from_reported_completion": offset,
                "construction_cue": "detected" if work_visible else "none detected" if work_check else "unchecked",
            })
        if offset is not None and offset >= 0:
            if work_score > 0:
                work_cues.append(number)
                work_confidence[number] = work_score
                work_evidence[number] = {"unfinished_work_detected": bool(unfinished_score),
                                         "scaffolding_detected": bool(scaffold_score),
                                         "equipment_detected": bool(equipment_score)}
            if scope_scores:
                scoped_photos.append(number)
                scope_confidence[number] = best_scope
    dated_offsets = [offset for offset in photo_offsets if offset is not None]
    if reported_complete and dated_offsets and max(dated_offsets) < 0:
        flags.append("All dated sampled photos precede reported completion")
    dated_photos = sorted(
        (number for number, photo in enumerate(record["photos"], start=1) if photo_capture_order(photo) != UNKNOWN_CAPTURE),
        key=lambda number: photo_capture_order(record["photos"][number - 1]),
        reverse=True,
    )
    capture_days = {
        number: reported_day(record["photos"][number - 1].get("capturedAt")) for number in dated_photos
    }
    recent_days = set(list(dict.fromkeys(capture_days.values()))[:1])
    recent = [number for number in dated_photos if capture_days[number] in recent_days]
    latest = recent[0] if recent else None
    if reported_complete and latest is not None and photo_offsets[latest - 1] is not None and photo_offsets[latest - 1] < 0:
        latest_photo = record["photos"][latest - 1]
        if any(row["photo"] == latest for row in building_photo_scores) and any(
            finding.get("detector") in {"Construction equipment", "Under construction"} and finding["status"] == "detected"
            and any(f"{label} (" in finding.get("observation", "") for label in CONSTRUCTION_EQUIPMENT)
            for finding in latest_photo["yolo"]
        ):
            flags.append(PRE_COMPLETION_EQUIPMENT)
    decision_day = capture_days.get(latest)
    work_cues = [number for number in work_cues if capture_days.get(number) == decision_day and decision_day is not None]
    work_evidence = {number: work_evidence[number] for number in work_cues}
    scoped_photos = [n for n in scoped_photos if capture_days.get(n) == decision_day]
    best_scope = max((scope_confidence[n] for n in scoped_photos), default=0)
    best_work = max((work_confidence[n] for n in work_cues), default=0)
    if best_work > best_scope and (best_scope > 0 or best_work >= 50):
        scoped_photos = []
    else:
        work_cues = []
        work_evidence = {}
    supporting = next(
        (number for number in recent if capture_days[number] == decision_day and number in scoped_photos), None,
    )
    latest_support = photo_offsets[supporting - 1] if supporting is not None else None
    best_recent_road = max((road_scores.get(number, 0) for number in recent if photo_offsets[number - 1] is not None and photo_offsets[number - 1] >= 0), default=0)
    evidence_reason = None
    if latest_support is None and 0 < best_recent_road < ROAD_CONFIDENCE:
        evidence_reason = (
            f"Best recent road-surface detection on or after reported completion was "
            f"{best_recent_road}%. The threshold is {ROAD_CONFIDENCE}%."
        )
    check_indices = recent or list(usable_by_photo)
    partial_failure = any(f.get("status") == "unavailable" and f.get("quality") != "missing"
                          for n in check_indices for f in record["photos"][n - 1]["yolo"])
    latest_checks_failed = bool(check_indices) and not any(usable_by_photo[n] for n in check_indices)
    needs_photo_review = completion is not None and bool(recent) and latest_support is None and not work_cues and not latest_checks_failed
    quality_gaps = sorted(set(quality_gaps).intersection(recent))
    if partial_failure and not latest_checks_failed:
        flags.append("Some latest photo checks failed; retry incomplete analysis")
    if latest_checks_failed:
        flags.append("Latest photo analysis unavailable; retry required")
    after_completion_work = [number for number in work_cues if photo_offsets[number - 1] > 0]
    if not reported_complete and latest_support is not None:
        flags.append("Sampled photos meet the visual evidence rule while the contract is not reported completed")
    if reported_complete and work_cues and latest_support is None:
        flags.append("Possible unfinished work in photos on or after reported completion")
        outcome = "Reported completed · possible unfinished work"
    elif reported_complete and latest_support is not None:
        outcome = "Reported completed · recent photo evidence supports the contract scope"
    elif reported_complete and completion is None:
        outcome = "Reported completed · completion date unavailable"
    elif reported_complete:
        outcome = "Reported completed · photo evidence inconclusive"
    elif str(record.get("status") or "").casefold() in {"on-going", "not yet started", "terminated", "for procurement"}:
        outcome = "Reported not completed"
    else:
        outcome = "Completion status unknown"
    has_inconsistencies = bool(flags)
    if needs_photo_review:
        flags.append("Latest photo evidence is inconclusive. Review the photos manually.")
    evidence_category = completion_evidence_category(
        reported_complete, record, latest_support, flags, needs_photo_review,
    )
    if latest_checks_failed and completion is not None:
        evidence_category = "Photo analysis unavailable · retry needed"
    elif reported_complete and work_cues:
        evidence_category = "Possible unfinished work shown after reported completion"
        if after_completion_work:
            days = max(photo_offsets[number - 1] for number in after_completion_work)
            unit = "day" if days == 1 else "days"
            evidence_reason = f"Latest photo showing possible unfinished work was taken {days} {unit} after reported completion."
        else:
            evidence_category = "Possible unfinished work shown on reported completion date"
            evidence_reason = "A photo taken on the reported completion date showed possible unfinished work."
    completion_photo_timing = None
    if reported_complete and latest_support is not None:
        if latest_support > 0:
            days = "day" if latest_support == 1 else "days"
            completion_photo_timing = (
                f"Visible scope evidence · captured {latest_support} {days} after reported completion"
            )
        else:
            completion_photo_timing = "Visible scope evidence · captured on the reported completion date"
    return {
        "assessment_version": ASSESSMENT_VERSION,
        "evidence_limitation": EVIDENCE_LIMITATION,
        "experimental_evidence_photos": quality_gaps,
        "retry_required": partial_failure or latest_checks_failed or record.get("galleryPhotoCount") is None,
        "legacy_detection_format": any("detections" not in f for p in record["photos"] for f in p["yolo"] if f.get("status") == "detected"),
        "completion_evidence_category": evidence_category,
        "decision_day_scope_confidence_pct": round(best_scope, 2) if best_scope else None,
        "decision_day_unfinished_confidence_pct": round(best_work, 2) if best_work else None,
        "completion_photo_timing": completion_photo_timing,
        "completion_evidence_reason": evidence_reason,
        "latest_evidence_photo": latest,
        "completion_supporting_photo": supporting,
        "completion_support_days_from_reported_completion": latest_support,
        "latest_photo_days_from_reported_completion": photo_offsets[latest - 1] if latest is not None else None,
        "reported_status_summary": (
            "Reported completed with inconsistencies" if reported_complete and any(map(flag_is_discrepancy, flags)) else
            "Reported completed" if reported_complete else record.get("status") or "Unknown"
        ),
        "completion_assessment": outcome, "flags": flags,
        "discrepancy_flags": [flag for flag in flags if flag_is_discrepancy(flag)],
        "mean_best_detection_confidence_pct": round(mean(scores), 1) if scores else None,
        "strongest_detection_by_label_pct": strongest_detections,
        "sampled_photos_by_detected_feature": dict(photos_by_label),
        "photos_with_detections": len(scores), "possible_unfinished_work_photos": work_cues,
        "unfinished_work_evidence": work_evidence,
        "scope_supporting_photos": scoped_photos, "budget_differences_php": comparisons,
        "photo_days_from_reported_completion": photo_offsets,
        "photo_timing_flags": [
            flag for flag in flags if flag in {
                "All dated sampled photos precede reported completion",
                PRE_COMPLETION_EQUIPMENT,
                "Possible unfinished work in photos on or after reported completion",
            }
        ],
        "building_photo_checks": building_photo_scores,
        "reported_start_to_completion_days": (completion - start).days if start and completion else None,
        "reported_completion_minus_expiry_days": (completion - expiry).days if completion and expiry else None,
    }
