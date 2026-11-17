"""Detector readiness for the Admin model-status view: availability, eligibility, next action, inspectable hashes.

Pure helpers: no Streamlit and no import of app.py. The caller injects the checkpoint path, its quality
(``evidence_policy.model_quality``) and its digest, so tests can use disposable files and never load a model.

The four readiness states are kept distinct and are never merged:
missing (no checkpoint) · experimental (``review_only``) · provisional · validated.
"""
import json
from pathlib import Path

import evidence_language as language
from evidence_policy import checkpoint_digest, release_failures

READINESS = {
    "missing": "Missing",
    "review_only": "Experimental",
    "provisional": "Provisional",
    "validated": "Validated",
}
FAILED_CATEGORY = "Photo analysis unavailable · retry needed"   # contract_assessment's category when every latest check failed
RELEASE_SCRIPTS = (
    ("evaluate_checkpoint.py", "python evaluate_checkpoint.py --detector <detector> --checkpoint <candidate best.pt> --job <training folder>"),
    ("promote_checkpoint.py", "python promote_checkpoint.py <detector> <candidate best.pt>"),
)


def display_path(path, project):
    try:
        return Path(path).resolve().relative_to(Path(project).resolve()).as_posix()
    except (ValueError, OSError):
        return str(path)


def next_action(state, path_text):
    """One practical next step per readiness state. Wording follows evidence_language.ELIGIBILITY."""
    if state == "missing":
        return (f"Add the checkpoint file at {path_text}, then reload this page. Until then this detector cannot "
                "assess photos.")
    if state == "review_only":
        return ("Treat its findings as unconfirmed: under the current rules they can still set a photo assessment, so "
                "review the photos. To validate it, evaluate a candidate and promote it (release checks below).")
    if state == "provisional":
        return ("Supports qualified visible-scope evidence only. To validate it, run a held-out evaluation with "
                "reviewed annotations, then promote the evaluated checkpoint (release checks below).")
    if state == "validated":
        return "No action needed. Approval is tied to this checkpoint's SHA256, so re-evaluate before replacing it."
    return "Check the checkpoint and its approval file."


# Training outcome states. They are mutually exclusive and describe different things; a finished training run is
# never "Approved active", and nothing here changes the active model.
CANDIDATE_STATES = {
    "evaluation_required": (
        "Evaluation required",
        "Training finished and the checkpoint is saved, but it has not been evaluated. Next: run "
        "evaluate_checkpoint.py on it. The active model is unchanged."),
    "candidate_ready": (
        "Candidate ready",
        "Evaluation passed. It is not the active model until it is promoted. Next: promote it with "
        "promote_checkpoint.py, a separate explicit step."),
    "evaluation_failed": (
        "Evaluation did not pass",
        "The evaluation did not pass the release gate, or its report does not match this checkpoint. The active "
        "model is unchanged. Next: address the failures in the evaluation report and evaluate again."),
    "approved_active": (
        "Approved active",
        "This checkpoint passed evaluation and was promoted; its approval is bound to the checkpoint hash. "
        "Next: none."),
}


def candidate_state(detector, candidate, active=None, active_quality=None):
    """State of a trained candidate checkpoint. Needs evidence on disk: a hash-bound, passing evaluation report."""
    candidate = Path(candidate)
    if (active is not None and Path(active).is_file() and candidate.is_file() and active_quality == "validated"
            and checkpoint_digest(active) == checkpoint_digest(candidate)):
        return "approved_active"
    try:
        report = json.loads(candidate.with_suffix(".evaluation.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "evaluation_required"
    try:
        passed = (isinstance(report, dict) and report.get("accepted") is True and report.get("detector") == detector
                  and report.get("checkpoint_sha256") == checkpoint_digest(candidate) and not release_failures(report))
    except OSError:
        passed = False
    return "candidate_ready" if passed else "evaluation_failed"


def _candidate(detector, candidate, active, active_quality):
    """Facts about a trained candidate: where it is, whether an evaluation report sits beside it, its state."""
    if not candidate:
        return None
    checkpoint = Path(candidate)
    state = candidate_state(detector, checkpoint, active, active_quality)
    return {"path": checkpoint, "evaluation_report": checkpoint.with_suffix(".evaluation.json").is_file(),
            "state": state, "label": CANDIDATE_STATES[state][0], "note": CANDIDATE_STATES[state][1]}


def inspect_detector(detector, target, path, project, *, quality=None, digest=None, candidate=None):
    """One detector's readiness. ``quality`` and ``digest`` are only used when the checkpoint file exists."""
    path = Path(path)
    installed = path.is_file()
    state = quality if installed and quality in READINESS else "review_only" if installed else "missing"
    label, use = language.eligibility("missing" if state == "missing" else state)
    shown = display_path(path, project)
    return {
        "detector": detector, "target": target, "installed": installed, "state": state,
        "readiness": READINESS[state], "eligibility": label, "use": use,
        "availability": "Installed" if installed else "Not installed",
        "next_action": next_action(state, shown), "path": path, "display_path": shown,
        "size_bytes": path.stat().st_size if installed else None, "sha256": digest if installed else None,
        "approval_report": installed and path.with_suffix(".approval.json").is_file(),
        "candidate": _candidate(detector, candidate, path if installed else None, quality),
    }


def summary(rows):
    counts = {state: sum(row["state"] == state for row in rows) for state in READINESS}
    available = sum(row["installed"] for row in rows)
    return {"available": available, "total": len(rows), "validated": counts["validated"],
            "provisional": counts["provisional"], "experimental": counts["review_only"], "missing": counts["missing"],
            "text": f"{available} of {len(rows)} detectors available · {counts['validated']} validated"}


def no_checkpoint_note(rows):
    """(title, body) when no detector can run, else None. States what happens to reviews and what to do next."""
    if not rows or any(row["installed"] for row in rows):
        return None
    failed = language.category_label(FAILED_CATEGORY)
    return ("No detector checkpoints are installed.",
            "Photos cannot be analysed yet. A contract with a reported completion date that is reviewed now is saved "
            f"as “{failed}”, which is not evidence either way. After installing a checkpoint, recheck those contracts "
            f"under Rerun reviews by choosing the “{failed}” category.")


def release_commands(project):
    """Release commands that exist in this checkout. Missing scripts are reported, never shown as runnable."""
    present, absent = [], []
    for script, command in RELEASE_SCRIPTS:
        (present if (Path(project) / script).is_file() else absent).append((script, command))
    return present, absent
