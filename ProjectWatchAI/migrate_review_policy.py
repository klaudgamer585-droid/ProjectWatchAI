"""Reassess saved observations with an SQLite backup and immutable prior revisions.

Does not rerun YOLO or replace manual decisions. Legacy inference remains stale.
"""
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from app import PHOTO_REVIEW_DB, open_assessment_db, assess_with_manual_review


def migrate(existing_backup=None):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = Path(existing_backup) if existing_backup else PHOTO_REVIEW_DB.with_name(f"{PHOTO_REVIEW_DB.stem}.before-policy-{stamp}.sqlite3")
    if existing_backup:
        if not backup.is_file() or backup.resolve() == PHOTO_REVIEW_DB.resolve():
            raise ValueError("A distinct existing backup is required")
        with closing(sqlite3.connect(f"file:{backup.as_posix()}?mode=ro", uri=True)) as saved:
            if saved.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("Backup integrity check failed")
    else:
        with closing(sqlite3.connect(f"file:{PHOTO_REVIEW_DB.as_posix()}?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(backup)) as destination:
                source.backup(destination)
    changed = 0
    with closing(open_assessment_db()) as db:
        rows = db.execute("SELECT contract_id, summary FROM contract_assessments").fetchall()
    # No long write transaction while reading manual decisions from another connection.
    updates = []
    for contract, payload in rows:
        record = json.loads(payload)
        assessment = assess_with_manual_review(record)
        record["assessment"] = assessment
        record.pop("regional_review", None)
        updated = json.dumps(record, ensure_ascii=False)
        if updated != payload:
            updates.append((assessment["completion_evidence_category"], updated, contract, payload))
    with closing(open_assessment_db()) as db, db:
        for update in updates:
            changed += db.execute("UPDATE contract_assessments SET category=?, summary=? WHERE contract_id=? AND summary=?", update).rowcount
    return {"backup": str(backup), "updated": changed, "reviewed": len(rows),
            "note": "Original inference timestamps retained; stale inference still requires a live rerun."}


if __name__ == "__main__":
    print(json.dumps(migrate(), indent=2))
