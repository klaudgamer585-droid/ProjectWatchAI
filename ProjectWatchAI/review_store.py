"""Reviewer decisions: validation, eligibility and durable, revisioned persistence.

No Streamlit here. Every function takes an open ``sqlite3.Connection`` so tests can use disposable
storage and simulate failures. A decision is one transaction that

* appends an immutable row to ``manual_decision_history`` (the revision),
* updates the *current* decision tables the rest of the app already reads
  (``manual_categories``, ``manual_review_metadata``, ``contract_assessments``).

Nothing here touches the YOLO training store. Training corrections are saved by ``yolo_feedback`` and
never call this module, so one outcome cannot change the other.
"""
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

from contract_assessment import (NEUTRAL_REVIEW_CATEGORY, PHOTOS_BEFORE_COMPLETION, COMPLETION_TIMING_GROUPS,
                                 assess_contract, completion_timing_group, reported_day)
from evidence_policy import evidence_fingerprint
import evidence_language as language

PHILIPPINE_TIME = timezone(timedelta(hours=8), "PHT")
REASON_MIN, REASON_MAX = 8, 600
REVIEWER_MAX = 80
# Names that would hide who decided. A reviewer must identify themselves.
PLACEHOLDER_REVIEWERS = {"local reviewer", "reviewer", "unknown", "unrecorded", "n/a", "na", "none", "anonymous", "-"}

# Every stored category a reviewer may choose, in the order the form lists them.
DECISION_CATEGORIES = (
    PHOTOS_BEFORE_COMPLETION,
    NEUTRAL_REVIEW_CATEGORY,
    "Completed · manual review",
    *COMPLETION_TIMING_GROUPS,
    "Possible unfinished work shown after reported completion",
    "Completion not reported",
    "Reported status unavailable",
    "No photo evidence",
    "Photo evidence unavailable",
    "Photos inconclusive",
)


class DecisionError(ValueError):
    """A decision that is not allowed. ``fields`` maps a form field to the message shown under it."""

    def __init__(self, fields):
        self.fields = dict(fields)
        super().__init__("; ".join(self.fields.values()))


class StaleEvidenceError(DecisionError):
    """The saved evidence changed after the reviewer opened it, so the decision was not recorded."""

    def __init__(self):
        super().__init__({"evidence": "The saved evidence for this contract changed after you opened it, so nothing was "
                                      "saved. Reload the contract, check the current photos, then save again."})


def ensure_schema(database):
    database.execute("""CREATE TABLE IF NOT EXISTS manual_decision_history (
        revision INTEGER PRIMARY KEY AUTOINCREMENT, contract_id TEXT NOT NULL, category TEXT NOT NULL,
        previous_category TEXT, automatic_category TEXT, reason TEXT NOT NULL, reviewer TEXT NOT NULL,
        evidence_hash TEXT NOT NULL, decided_at TEXT NOT NULL
    )""")
    database.execute("CREATE INDEX IF NOT EXISTS manual_decision_contract ON manual_decision_history(contract_id, revision)")


def eligible_review_categories(record, categories=DECISION_CATEGORIES):
    """Target categories the saved evidence allows for this contract, excluding the current one.

    A decision is about the contract's *current* (newest dated) evidence, so a historical or
    undated photo never widens the choices.
    """
    photos = record["photos"]
    assessment = record["assessment"]
    completion = reported_day((record.get("dates") or {}).get("Reported completion"))
    capture_days = [day for item in photos if (day := reported_day(item.get("capturedAt")))]
    captured = max(capture_days, default=None)
    allowed = {"Photos inconclusive", NEUTRAL_REVIEW_CATEGORY} if photos else set()
    if completion and capture_days and max(capture_days) < completion:
        allowed.add(PHOTOS_BEFORE_COMPLETION)
    if captured and completion and captured >= completion:
        offset = (captured - completion).days
        allowed.add("Completed · manual review")
        allowed.add(completion_timing_group(offset))
        if offset > 0:
            allowed.add("Possible unfinished work shown after reported completion")
    if not photos:
        allowed.add("No photo evidence" if record.get("galleryPhotoCount") == 0 else "Photo evidence unavailable")
    if not record.get("status"):
        allowed.add("Reported status unavailable")
    elif record["status"].casefold() != "completed":
        allowed.add("Completion not reported")
    return [category for category in categories if category in allowed
            and category != assessment["completion_evidence_category"]]


def automatic_category(record):
    """The automatic photo assessment, kept separate from any manual decision that replaced it.

    Returns (raw category, note). Older manual decisions did not keep the automatic value, so it is
    recomputed from the saved observations with the current rules and labelled as such.
    """
    assessment = record.get("assessment") or {}
    if not language.is_manual(assessment):
        return assessment.get("completion_evidence_category"), None
    if saved := assessment.get("automatic_evidence_category"):
        return saved, None
    try:
        return (assess_contract(record)["completion_evidence_category"],
                "Recomputed from saved observations with the current rules; the original automatic value was not stored.")
    except (KeyError, TypeError, ValueError):
        return None, "The automatic assessment before this manual decision is unavailable."


def clean_reviewer(value):
    return " ".join(str(value or "").split())


def validate_decision(record, category, reason, reviewer):
    """Field -> message for everything wrong with a decision. Empty dict means it can be saved."""
    problems = {}
    allowed = eligible_review_categories(record)
    if not category:
        problems["category"] = "Choose a target category."
    elif category not in allowed:
        problems["category"] = "This category is not available for the saved evidence. Choose one from the list."
    reason = (reason or "").strip()
    if not reason:
        problems["reason"] = "Enter a reason for this category change."
    elif len(reason) < REASON_MIN:
        problems["reason"] = f"Describe the reason in at least {REASON_MIN} characters, for example which photo or date you relied on."
    elif len(reason) > REASON_MAX:
        problems["reason"] = f"Shorten the reason to {REASON_MAX} characters or fewer."
    reviewer = clean_reviewer(reviewer)
    if not reviewer:
        problems["reviewer"] = "Enter your name so this decision is attributed to you."
    elif reviewer.casefold() in PLACEHOLDER_REVIEWERS:
        problems["reviewer"] = "Enter your own name. A generic label cannot be attributed to a person."
    elif len(reviewer) > REVIEWER_MAX:
        problems["reviewer"] = f"Shorten the name to {REVIEWER_MAX} characters or fewer."
    return problems


def decided_record(record, category, reason, reviewer, decided_at, evidence_hash):
    previous = record["assessment"]
    automatic = previous.get("automatic_evidence_category") or (
        None if language.is_manual(previous) else previous.get("completion_evidence_category"))
    updated = {
        **record, "assessment": {
            **previous, "completion_evidence_category": category,
            "completion_assessment": f"Manual review · {category}", "manual_category_note": reason,
            "manual_review_provenance": {"evidence_hash": evidence_hash, "reviewed_at": decided_at, "reviewer": reviewer},
            **({"automatic_evidence_category": automatic} if automatic else {}),
        },
    }
    updated.pop("regional_review", None)
    return updated, automatic


def save_decision(database, record, category, reason, reviewer, *, now=None):
    """Validate, then record one decision revision atomically. Returns the stored revision as a dict.

    Raises DecisionError (nothing written) for a field problem, StaleEvidenceError when the saved
    evidence is no longer what the reviewer saw, and sqlite3.Error/OSError when storage fails. In every
    failure case the database is left exactly as it was.
    """
    if problems := validate_decision(record, category, reason, reviewer):
        raise DecisionError(problems)
    contract = record["contractId"]
    reason, reviewer = reason.strip(), clean_reviewer(reviewer)
    decided_at = (now or datetime.now(PHILIPPINE_TIME)).isoformat()
    updated, automatic = decided_record(record, category, reason, reviewer, decided_at, evidence_fingerprint(record))
    ensure_schema(database)
    try:
        database.execute("BEGIN IMMEDIATE")
        row = database.execute("SELECT summary FROM contract_assessments WHERE contract_id = ?", (contract,)).fetchone()
        if row is None:
            raise DecisionError({"evidence": "This contract has no saved review to attach a decision to. "
                                             "Analyze the contract first."})
        stored = json.loads(row[0])
        if evidence_fingerprint(stored) != evidence_fingerprint(record):
            raise StaleEvidenceError()
        previous = stored.get("assessment", {}).get("completion_evidence_category")
        cursor = database.execute(
            "INSERT INTO manual_decision_history(contract_id, category, previous_category, automatic_category, reason, "
            "reviewer, evidence_hash, decided_at) VALUES (?,?,?,?,?,?,?,?)",
            (contract, category, previous, automatic, reason, reviewer, evidence_fingerprint(record), decided_at))
        revision = cursor.lastrowid
        database.execute("INSERT OR REPLACE INTO manual_categories VALUES (?, ?, ?)", (contract, category, reason))
        database.execute("INSERT OR REPLACE INTO manual_review_metadata VALUES (?, ?, ?, ?)",
                         (contract, evidence_fingerprint(record), decided_at, reviewer))
        database.execute("UPDATE contract_assessments SET category = ?, reviewed_at = ?, summary = ? WHERE contract_id = ?",
                         (category, decided_at, json.dumps(updated, ensure_ascii=False), contract))
        database.commit()
    except BaseException:
        database.rollback()
        raise
    return read_revision(database, revision)


def _row(row):
    keys = ("revision", "contract_id", "category", "previous_category", "automatic_category", "reason", "reviewer",
            "evidence_hash", "decided_at")
    return dict(zip(keys, row))


_COLUMNS = ("revision, contract_id, category, previous_category, automatic_category, reason, reviewer, "
            "evidence_hash, decided_at")


def read_revision(database, revision):
    """Read one revision back from storage. This is what a confirmation shows, not what the form held."""
    row = database.execute(f"SELECT {_COLUMNS} FROM manual_decision_history WHERE revision = ?", (revision,)).fetchone()
    return _row(row) if row else None


def decision_history(database, contract, limit=20):
    """Newest first, each with its per-contract number ('Decision 3')."""
    try:
        rows = database.execute(
            f"SELECT {_COLUMNS} FROM manual_decision_history WHERE contract_id = ? ORDER BY revision DESC", (contract,)).fetchall()
    except sqlite3.OperationalError:  # database created before revision history existed
        return []
    total = len(rows)
    return [{**_row(row), "number": total - index} for index, row in enumerate(rows[:limit])]


def decision_metadata(database, contract):
    """Current decision attribution (evidence hash, time, reviewer), or None."""
    try:
        row = database.execute("SELECT evidence_hash, reviewed_at, reviewer FROM manual_review_metadata WHERE contract_id = ?",
                               (contract,)).fetchone()
    except sqlite3.OperationalError:  # database created before decisions were attributed
        return None
    return dict(zip(("evidence_hash", "reviewed_at", "reviewer"), row)) if row else None


def decision_warnings(record, metadata, *, review_stale=False):
    """Plain-language reasons a recorded decision may no longer match the evidence. Presentation input only."""
    warnings = []
    if not language.is_manual(record.get("assessment") or {}):
        return warnings
    if metadata is None:
        warnings.append("This decision was recorded before reviewer and evidence details were kept, so it cannot be "
                        "checked against the current evidence.")
    elif metadata["evidence_hash"] != evidence_fingerprint(record):
        warnings.append("The evidence changed after this decision was recorded. The decision is kept; check that it "
                        "still matches the current photos and record.")
    if review_stale:
        warnings.append("The automatic assessment is out of date because rules or detectors changed. "
                        "The reviewer decision is kept when the review is rerun.")
    return warnings
