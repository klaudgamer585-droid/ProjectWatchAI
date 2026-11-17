"""Phase 5: reviewer decisions are validated, attributed, revisioned and atomic. Disposable SQLite only."""
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from copy import deepcopy
from pathlib import Path

import review_store as store
from contract_assessment import NEUTRAL_REVIEW_CATEGORY, assess_contract
from test_audit_regressions import photo, record

HERE = Path(__file__).parent


def make_database(path):
    """The tables app.open_assessment_db creates, without importing the Streamlit app."""
    database = sqlite3.connect(path)
    database.executescript("""
        CREATE TABLE contract_assessments (contract_id TEXT PRIMARY KEY, category TEXT NOT NULL,
            reviewed_at TEXT NOT NULL, summary TEXT NOT NULL);
        CREATE TABLE manual_categories (contract_id TEXT PRIMARY KEY, category TEXT NOT NULL, note TEXT NOT NULL);
        CREATE TABLE manual_review_metadata (contract_id TEXT PRIMARY KEY, evidence_hash TEXT NOT NULL,
            reviewed_at TEXT NOT NULL, reviewer TEXT NOT NULL);
        CREATE TABLE review_history (revision INTEGER PRIMARY KEY, contract_id TEXT NOT NULL, category TEXT NOT NULL,
            reviewed_at TEXT NOT NULL, summary TEXT NOT NULL, archived_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TRIGGER archive_review_update BEFORE UPDATE ON contract_assessments WHEN OLD.summary != NEW.summary BEGIN
            INSERT INTO review_history(contract_id,category,reviewed_at,summary)
            VALUES (OLD.contract_id,OLD.category,OLD.reviewed_at,OLD.summary); END;
    """)
    store.ensure_schema(database)
    database.commit()
    return database


def saved_record(day="2025-01-02", label="paved road surface"):
    item = record([photo(day, label)])
    item["contractId"] = "S-1"
    item["assessment"] = assess_contract(item)
    return item


def put(database, item):
    database.execute("INSERT OR REPLACE INTO contract_assessments VALUES (?,?,?,?)",
                     (item["contractId"], item["assessment"]["completion_evidence_category"],
                      "2026-11-10T09:00:00+08:00", json.dumps(item)))
    database.commit()


def counts(database):
    return {table: database.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("manual_decision_history", "manual_categories", "manual_review_metadata", "review_history")}


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=HERE)
        self.path = Path(self.tmp.name) / "review.db"
        self.db = make_database(self.path)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.db.close)
        self.item = saved_record()
        put(self.db, self.item)


class ValidationTests(StoreCase):
    def test_every_field_is_reported_separately(self):
        problems = store.validate_decision(self.item, None, "", "")
        self.assertEqual(set(problems), {"category", "reason", "reviewer"})

    def test_reason_must_say_something(self):
        for reason in ("", "   ", "ok", "x" * (store.REASON_MAX + 1)):
            self.assertIn("reason", store.validate_decision(self.item, "Photos inconclusive", reason, "A. Reviewer"), reason)
        self.assertEqual(store.validate_decision(self.item, "Photos inconclusive", "Scope not visible in photo 1", "A. Reviewer"), {})

    def test_reviewer_must_be_a_person_not_a_placeholder(self):
        for name in ("", "  ", "local reviewer", "Unknown", "n/a", "x" * (store.REVIEWER_MAX + 1)):
            self.assertIn("reviewer", store.validate_decision(self.item, "Photos inconclusive", "Scope not visible", name), name)

    def test_only_eligible_targets_are_accepted(self):
        eligible = store.eligible_review_categories(self.item)
        self.assertNotIn(self.item["assessment"]["completion_evidence_category"], eligible)  # the current category
        self.assertNotIn("No photo evidence", eligible)  # this contract has photos
        problems = store.validate_decision(self.item, "No photo evidence", "Scope not visible", "A. Reviewer")
        self.assertIn("category", problems)

    def test_historical_photo_cannot_widen_the_choices(self):
        # The newest dated photo precedes completion, so a later (older-in-the-list) photo must not unlock later groups.
        item = record([photo("2025-01-02", "paved road surface"), photo("2024-12-20")])
        item["assessment"] = assess_contract(item)
        self.assertEqual(store.eligible_review_categories(item), store.eligible_review_categories(item))
        early = record([photo("2024-12-20")])
        early["assessment"] = assess_contract(early)
        self.assertNotIn("Completed · manual review", store.eligible_review_categories(early))


class SaveTests(StoreCase):
    def decide(self, item=None, category="Photos inconclusive", reason="Scope not clear in photo 1", reviewer="A. Reviewer"):
        return store.save_decision(self.db, item or self.item, category, reason, reviewer)

    def test_save_writes_revision_current_decision_and_summary_together(self):
        saved = self.decide()
        self.assertEqual((saved["contract_id"], saved["category"], saved["reviewer"]), ("S-1", "Photos inconclusive", "A. Reviewer"))
        self.assertEqual(saved["previous_category"], self.item["assessment"]["completion_evidence_category"])
        self.assertEqual(saved["automatic_category"], self.item["assessment"]["completion_evidence_category"])
        self.assertEqual(self.db.execute("SELECT category, note FROM manual_categories").fetchone(),
                         ("Photos inconclusive", "Scope not clear in photo 1"))
        self.assertEqual(self.db.execute("SELECT reviewer FROM manual_review_metadata").fetchone(), ("A. Reviewer",))
        category, summary = self.db.execute("SELECT category, summary FROM contract_assessments").fetchone()
        self.assertEqual(category, "Photos inconclusive")
        self.assertEqual(json.loads(summary)["assessment"]["manual_category_note"], "Scope not clear in photo 1")
        self.assertEqual(store.read_revision(self.db, saved["revision"]), saved)  # what the confirmation shows

    def test_validation_failure_writes_nothing(self):
        before = counts(self.db)
        with self.assertRaises(store.DecisionError) as raised:
            self.decide(reason="")
        self.assertIn("reason", raised.exception.fields)
        self.assertEqual(counts(self.db), before)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM manual_decision_history").fetchone()[0], 0)

    def test_persistence_failure_rolls_everything_back(self):
        self.db.execute("CREATE TRIGGER fail_summary BEFORE UPDATE ON contract_assessments "
                        "BEGIN SELECT RAISE(ABORT, 'simulated failure'); END")
        self.db.commit()
        before = self.db.execute("SELECT category, summary FROM contract_assessments").fetchone()
        with self.assertRaises(sqlite3.Error):
            self.decide()
        self.assertEqual(counts(self.db)["manual_decision_history"], 0)
        self.assertEqual(counts(self.db)["manual_categories"], 0)
        self.assertEqual(counts(self.db)["manual_review_metadata"], 0)
        self.assertEqual(self.db.execute("SELECT category, summary FROM contract_assessments").fetchone(), before)
        self.assertFalse(self.db.in_transaction)
        # The same call succeeds once storage recovers: nothing is stuck half-written.
        self.db.execute("DROP TRIGGER fail_summary")
        self.db.commit()
        self.assertEqual(self.decide()["revision"], 1)

    def test_decision_is_refused_when_the_saved_evidence_changed_underneath(self):
        newer = deepcopy(self.item)
        newer["description"] = "Rerun found different evidence"
        put(self.db, {**newer, "assessment": self.item["assessment"]})
        with self.assertRaises(store.StaleEvidenceError):
            self.decide()
        self.assertEqual(counts(self.db)["manual_decision_history"], 0)

    def test_decision_needs_a_saved_review(self):
        orphan = saved_record()
        orphan["contractId"] = "NOT-SAVED"
        with self.assertRaises(store.DecisionError):
            self.decide(orphan)

    def test_a_correction_is_a_new_revision_and_keeps_the_automatic_category(self):
        automatic = self.item["assessment"]["completion_evidence_category"]
        first = self.decide(category="Photos inconclusive", reason="First look at the photos")
        saved = json.loads(self.db.execute("SELECT summary FROM contract_assessments").fetchone()[0])
        second = store.save_decision(self.db, saved, NEUTRAL_REVIEW_CATEGORY, "Second look, photo 2 is clearer", "B. Reviewer")
        self.assertEqual((first["revision"], second["revision"]), (1, 2))
        self.assertEqual(second["previous_category"], "Photos inconclusive")
        self.assertEqual(second["automatic_category"], automatic)  # never records the first manual decision as automatic
        history = store.decision_history(self.db, "S-1")
        self.assertEqual([item["number"] for item in history], [2, 1])
        self.assertEqual([item["reviewer"] for item in history], ["B. Reviewer", "A. Reviewer"])
        self.assertEqual(history[1]["reason"], "First look at the photos")  # the first decision is not overwritten
        self.assertEqual(self.db.execute("SELECT note FROM manual_categories").fetchone()[0], "Second look, photo 2 is clearer")

    def test_reviewer_name_is_normalised(self):
        self.assertEqual(self.decide(reviewer="  A.   Reviewer ")["reviewer"], "A. Reviewer")


class WarningTests(StoreCase):
    def test_decision_warns_when_evidence_changed_after_it(self):
        self.decide = store.save_decision(self.db, self.item, "Photos inconclusive", "Scope not clear in photo 1", "A. Reviewer")
        saved = json.loads(self.db.execute("SELECT summary FROM contract_assessments").fetchone()[0])
        metadata = store.decision_metadata(self.db, "S-1")
        self.assertEqual(store.decision_warnings(saved, metadata), [])
        changed = {**saved, "description": "Changed after the decision"}
        warnings = store.decision_warnings(changed, metadata, review_stale=True)
        self.assertEqual(len(warnings), 2)
        self.assertIn("evidence changed after this decision", warnings[0])

    def test_legacy_decision_without_metadata_says_so(self):
        legacy = deepcopy(self.item)
        legacy["assessment"]["manual_category_note"] = "old"
        warnings = store.decision_warnings(legacy, None)
        self.assertIn("recorded before reviewer and evidence details were kept", warnings[0])

    def test_no_warning_without_a_manual_decision(self):
        self.assertEqual(store.decision_warnings(self.item, None, review_stale=False), [])


class SchemaTests(unittest.TestCase):
    def test_old_database_without_history_tables_reads_cleanly(self):
        with tempfile.TemporaryDirectory(dir=HERE) as tmp:
            database = sqlite3.connect(Path(tmp) / "old.db")
            self.assertEqual(store.decision_history(database, "X"), [])
            self.assertIsNone(store.decision_metadata(database, "X"))
            database.close()


if __name__ == "__main__":
    unittest.main()
