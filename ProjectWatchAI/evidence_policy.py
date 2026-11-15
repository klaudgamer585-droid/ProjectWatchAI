"""Versioned evidence policy, model provenance, and explicit release gates."""
import hashlib
import json
import math
from functools import lru_cache
from pathlib import Path

ASSESSMENT_VERSION = "2026-11-15.5"
INFERENCE_VERSION = 2
EVIDENCE_LIMITATION = (
    "Visible features support only the photographed scope. This is not verification of "
    "full contract completion, quality, quantities, spending, or actual completion date."
)
# These checkpoints have a provisional evaluation status.
PROVISIONAL_SCOPE_MODELS = {"Roads", "Buildings and Facilities", "Stainless steel water tanks"}

@lru_cache(maxsize=128)
def _digest(path, size, modified):
    with open(path, "rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()

def checkpoint_digest(path):
    path = Path(path).resolve()
    stat = path.stat()
    return _digest(str(path), stat.st_size, stat.st_mtime_ns)

def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()

def model_quality(detector, weights):
    """Approval is bound to bytes, not a mutable filename or training completion."""
    path = Path(weights)
    approval = path.with_suffix(".approval.json")
    try:
        report = json.loads(approval.read_text(encoding="utf-8"))
        if (report.get("checkpoint_sha256") == checkpoint_digest(path)
                and report.get("accepted") is True and report.get("detector") == detector
                and not release_failures(report)):
            return "validated"
    except (OSError, ValueError):
        pass
    try:
        registry = json.loads(Path(__file__).with_name("audited_checkpoints.json").read_text(encoding="utf-8"))
        if registry["provisional_scope_checkpoints"].get(detector) == checkpoint_digest(path):
            return "provisional"
    except (OSError, ValueError, KeyError):
        pass
    return "review_only"

def evidence_fingerprint(record):
    return fingerprint({k: v for k, v in record.items()
                        if k not in {"assessment", "regional_review", "review_provenance"}})

def release_failures(report):
    """Conservative engineering release gate; not a claim of completion accuracy."""
    failures = []
    for key, minimum in {"test_contracts": 10, "positive_images": 20, "negative_images": 20,
                         "precision": .9, "recall": .8, "mask_map50": .5}.items():
        value = report.get(key, 0)
        if (type(value) not in (int, float) or not math.isfinite(value) or value < minimum):
            failures.append(f"{key} must be at least {minimum}")
    if report.get("annotations_reviewed") is not True:
        failures.append("Held-out annotations require review for this detector's current label definition")
    if report.get("split_leakage") is not False:
        failures.append("Contract and image split isolation must pass")
    return failures
