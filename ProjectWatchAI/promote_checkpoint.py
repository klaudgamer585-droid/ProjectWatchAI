"""Promote only an evaluated checkpoint; retain a rollback copy and provenance."""
import argparse
import json
from pathlib import Path
from shutil import copy2
from datetime import datetime, timezone
from evidence_policy import checkpoint_digest, release_failures
from yolo_feedback import feedback_folder


def promote(detector, candidate):
    candidate = Path(candidate)
    report = json.loads(candidate.with_suffix(".evaluation.json").read_text(encoding="utf-8"))
    if (report.get("detector") != detector or report.get("checkpoint_sha256") != checkpoint_digest(candidate)
            or release_failures(report)):
        raise ValueError("Candidate has not passed its hash-bound release gate. See evaluation report.")
    folder = feedback_folder(detector)
    folder.mkdir(parents=True, exist_ok=True)
    active = folder / "active.pt"
    if active.is_file():
        rollback = folder / "rollback"
        rollback.mkdir(exist_ok=True)
        copy2(active, rollback / f"{checkpoint_digest(active)}.pt")
        approval = active.with_suffix(".approval.json")
        if approval.is_file():
            copy2(approval, rollback / f"{checkpoint_digest(active)}.approval.json")
    pending = folder / "promotion.pending.pt"
    copy2(candidate, pending)
    pending.replace(active)
    report.update(accepted=True, promoted_at=datetime.now(timezone.utc).isoformat())
    active.with_suffix(".approval.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return active


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("detector")
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args()
    print(promote(args.detector, args.candidate))
