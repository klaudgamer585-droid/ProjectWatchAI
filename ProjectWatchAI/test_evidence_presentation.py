"""Phase 2 (P0 evidence presentation): shared state/time/conflict helpers and the views that use them.

Pure helpers are tested directly; views run through AppTest against disposable SQLite copies and a
mocked BetterGov API. Nothing here touches real reviews, datasets or checkpoints.
"""
import json
import os
import re
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import evidence_language as language
from contract_assessment import assess_contract
from test_audit_regressions import photo, record
from test_evidence_language import FORBIDDEN, seed_review_db
from test_explore_ui import PROJECTS, FakeApi, fake_response

HERE = Path(__file__).parent


def conflict_record():
    """Latest date (2025-01-02) has scope evidence in photo 2 and a construction cue in photo 3."""
    r = record([photo("2024-12-01", "Under construction", .9, "Under construction"),
                photo("2025-01-02", "paved road surface", .9),
                photo("2025-01-02", "Under construction", .6, "Under construction")])
    r["assessment"] = assess_contract(r)
    return r


class TimestampTests(unittest.TestCase):
    def test_date_only_stays_date_only(self):
        self.assertEqual(language.format_timestamp("2025-01-02"), "02 Jan 2025")
        self.assertEqual(language.format_timestamp(date(2025, 1, 2)), "02 Jan 2025")
        self.assertNotIn("PHT", language.format_timestamp("2025-01-02"))

    def test_timestamps_are_philippine_time(self):
        self.assertEqual(language.format_timestamp("2026-11-16T10:00:00+08:00"), "16 Nov 2026, 10:00 PHT")
        self.assertEqual(language.format_timestamp("2026-11-16T02:00:00Z"), "16 Nov 2026, 10:00 PHT")
        # SQLite CURRENT_TIMESTAMP is naive UTC.
        self.assertEqual(language.format_timestamp("2026-11-16 02:00:00", naive_tz=timezone.utc), "16 Nov 2026, 10:00 PHT")
        # Application timestamps without an offset are Philippine time.
        self.assertEqual(language.format_timestamp(datetime(2026, 11, 16, 10, 0)), "16 Nov 2026, 10:00 PHT")

    def test_unknown_says_unknown(self):
        for value in (None, "", "not a date", "2025-13-40"):
            self.assertEqual(language.format_timestamp(value), "Date unknown")

    def test_photo_roles(self):
        photos = [{"capturedAt": "2025-01-02"}, {"capturedAt": "2024-12-01"}, {"capturedAt": None}]
        self.assertEqual([language.photo_role(photos, i) for i in range(3)], ["latest date", "historical", "date unknown"])
        self.assertEqual(language.photo_caption(photos, 1), "Photo 2 · 01 Dec 2024 · historical")
        self.assertEqual(language.photo_caption(photos, 2), "Photo 3 · Date unknown · date unknown")


class StateTests(unittest.TestCase):
    REQUIRED = ("no_photos", "retrieval_failed", "detector_missing", "analysis_failed", "inconclusive",
                "no_detection", "stale", "historical", "unknown_date", "same_date_conflict", "no_saved_assessment")

    def test_every_listed_state_has_one_distinct_treatment(self):
        titles = set()
        for name in self.REQUIRED:
            kind, icon, color, title, why = language.state(name)
            self.assertTrue(icon.startswith(":material/") and title and why)
            self.assertNotRegex(title + why, FORBIDDEN)
            titles.add(title)
        self.assertEqual(len(titles), len(self.REQUIRED))
        # Failures are red; gaps are not; nothing missing is described as a negative finding.
        self.assertEqual(language.state("analysis_failed")[2], "red")
        self.assertNotEqual(language.state("no_photos")[2], "red")
        self.assertIn("does not show that the work is absent", language.state("no_detection")[4])

    def test_category_states(self):
        self.assertEqual(language.category_state("No photo evidence"), "no_photos")
        self.assertEqual(language.category_state("Photo evidence unavailable"), "retrieval_failed")
        self.assertEqual(language.category_state("Photo analysis unavailable · retry needed"), "analysis_failed")
        self.assertIsNone(language.category_state("Completion evidence found · 1–7 days later"))

    def test_score_text_never_zero_for_missing(self):
        self.assertEqual(language.score_text(None), "No score")
        self.assertEqual(language.score_text(float("nan"), signed=True), "No score")
        self.assertEqual(language.score_text(3.0, signed=True), "+3")
        self.assertEqual(language.score_text(2.121320, decimals=2), "2.12")
        self.assertEqual(language.score_text(0, signed=True), "+0")

    def test_legacy_confidence_prose_becomes_model_score(self):
        text = language.finding_text({"detector": "Roads", "status": "detected",
                                      "observation": "YOLO detected paved road surface (40% confidence)."})
        self.assertIn("model score 40%", text)
        self.assertNotIn("confidence", text)


class ConflictTests(unittest.TestCase):
    def test_same_date_conflict_cites_photos_and_kept_side(self):
        r = conflict_record()
        before = json.dumps(r["assessment"], sort_keys=True)
        conflict = language.same_date_conflict(r)
        self.assertEqual(conflict, {"scope_photos": [2], "cue_photos": [3], "kept": "scope"})
        text = language.conflict_text(conflict)
        self.assertIn("Scope detections: photo 2", text)
        self.assertIn("Construction cues: photo 3", text)
        self.assertIn("rule kept the visible scope evidence", text)
        self.assertEqual(json.dumps(r["assessment"], sort_keys=True), before)  # presentation never edits the rule result

    def test_kept_cues_when_cues_win(self):
        r = record([photo("2025-01-02", "paved road surface", .4), photo("2025-01-02", "Under construction", .9, "Under construction")])
        r["assessment"] = assess_contract(r)
        self.assertEqual(language.same_date_conflict(r)["kept"], "cues")

    def test_historical_cue_is_not_a_conflict(self):
        r = record([photo("2024-12-01", "Under construction", .9, "Under construction"), photo("2025-01-02", "paved road surface")])
        r["assessment"] = assess_contract(r)
        self.assertIsNone(language.same_date_conflict(r))
        self.assertIsNone(language.same_date_conflict(record([])))

    def test_experimental_basis_note(self):
        r = record([photo("2025-01-02", "paved road surface", quality="review_only")])
        a = assess_contract(r)
        self.assertTrue(language.experimental_basis(a))
        validated = assess_contract(record([photo("2025-01-02", "paved road surface")]))
        self.assertFalse(language.experimental_basis(validated))


def add_states(path):
    """Extra disposable rows: same-date conflict, stale review, no photos, experimental basis, history."""
    conflict = conflict_record()
    conflict.update(contractId="FIXTURE-CONFLICT", coordinates=None)
    stale = record([photo("2025-01-02", "paved road surface")])
    stale.update(contractId="FIXTURE-STALE", coordinates=None)
    stale["assessment"] = {**assess_contract(stale), "assessment_version": "old"}
    empty = record([])
    empty.update(contractId="FIXTURE-EMPTY", coordinates=None)
    empty["assessment"] = assess_contract(empty)
    experimental = record([photo("2025-01-02", "paved road surface", quality="review_only")])
    experimental.update(contractId="FIXTURE-EXPERIMENTAL", coordinates=None)
    experimental["assessment"] = assess_contract(experimental)
    with closing(sqlite3.connect(path)) as db, db:
        for item in (conflict, stale, empty, experimental):
            db.execute("INSERT INTO contract_assessments VALUES (?,?,?,?)",
                       (item["contractId"], item["assessment"]["completion_evidence_category"],
                        "2026-11-16T10:00:00+08:00", json.dumps(item)))
        db.execute("CREATE TABLE IF NOT EXISTS review_history (contract_id TEXT, revision INTEGER, category TEXT, "
                   "reviewed_at TEXT, archived_at TEXT, summary TEXT)")
        db.execute("INSERT INTO review_history (contract_id, revision, category, reviewed_at, archived_at, summary) "
                   "VALUES ('FIXTURE-CONFLICT', 1, 'Completion evidence found · 1–7 days later', "
                   "'2026-11-15T09:00:00+08:00', '2026-11-16 01:00:00', '{}')")
    return conflict, stale, empty, experimental


class AdminPresentationTests(unittest.TestCase):
    def setUp(self):
        from streamlit.testing.v1 import AppTest
        self.tmp = tempfile.TemporaryDirectory(dir=HERE)
        self.db = Path(self.tmp.name) / "fixture.db"
        seed_review_db(self.db)
        self.records = add_states(self.db)
        self.env = patch.dict(os.environ, {"PROJECTWATCH_PHOTO_REVIEW_DB": str(self.db)})
        self.env.start()
        self.at = AppTest.from_file(str(HERE / "app.py"), default_timeout=60)
        self.at.session_state["primary_navigation"] = "Admin"

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def text(self):
        parts = []
        for kind in ("markdown", "caption", "text", "info", "warning", "error", "subheader"):
            parts += [str(e.value) for e in getattr(self.at, kind)]
        return "\n".join(parts).replace("\\", "")

    def open(self, contract):
        self.at.run()
        self.assertFalse(self.at.exception, [e.value for e in self.at.exception])
        with closing(sqlite3.connect(self.db)) as db:
            group = db.execute("SELECT category FROM contract_assessments WHERE contract_id=?", (contract,)).fetchone()[0]
        self.at.selectbox(key="saved_review_category").set_value(group).run()
        self.at.selectbox(key=f"saved_review_contract_{group}").set_value(contract).run()
        self.assertFalse(self.at.exception, [e.value for e in self.at.exception])
        return self.text()

    def test_conflict_history_and_pht_times(self):
        text = self.open("FIXTURE-CONFLICT")
        self.assertIn("Latest dated photos show both scope evidence and construction cues.", text)
        self.assertIn("Scope detections: photo 2. Construction cues: photo 3.", text)
        self.assertIn("Saved review time · 16 Nov 2026, 10:00 PHT", text)
        self.assertNotIn("2026-11-16T10:00:00+08:00", text)
        history = [df.value for df in self.at.dataframe if "Revision" in getattr(df.value, "columns", [])]
        self.assertTrue(history)
        row = history[0].iloc[0]
        self.assertEqual(row["Reviewed"], "15 Nov 2026, 09:00 PHT")
        self.assertEqual(row["Archived"], "16 Nov 2026, 09:00 PHT")  # UTC CURRENT_TIMESTAMP shown in PHT
        self.assertEqual(row["Stored value"], "Completion evidence found · 1–7 days later")
        self.assertEqual(language.category_value(row["Category"]), row["Stored value"])  # round trip

    def test_stale_review_state(self):
        self.assertIn("Saved review needs a refresh because evidence or assessment rules changed.", self.open("FIXTURE-STALE"))

    def test_no_photos_state(self):
        text = self.open("FIXTURE-EMPTY")
        self.assertIn("No photos are available in this source record.", text)
        self.assertIn("does not show that work is unfinished or absent", text)

    def test_experimental_basis_shown_beside_assessment(self):
        text = self.open("FIXTURE-EXPERIMENTAL")
        self.assertIn(language.EXPERIMENTAL_BASIS_NOTE, text)
        self.assertIn("Experimental · not validated", text)
        self.assertNotIn("cannot set a category", text)

    def test_failed_analysis_state(self):
        self.assertIn("Photo analysis failed.", self.open("FIXTURE-FAILED"))

    def test_legacy_manual_record_still_renders(self):
        text = self.open("FIXTURE-MANUAL")
        self.assertIn("Reviewer and time were not recorded for this older decision.", text)
        self.assertIn("Manual reviewer decision", text)


class ExploreDetailTests(unittest.TestCase):
    def setUp(self):
        import streamlit as st
        from streamlit.testing.v1 import AppTest
        st.cache_data.clear()
        self.tmp = tempfile.TemporaryDirectory(dir=HERE)
        self.db = Path(self.tmp.name) / "reviews.db"
        seed_review_db(self.db)
        saved = record([photo("2025-01-02", "paved road surface")])
        saved.update(contractId="C-1", coordinates=None)
        saved["assessment"] = assess_contract(saved)
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("INSERT INTO contract_assessments VALUES ('C-1', ?, '2026-11-10T09:00:00+08:00', ?)",
                       (saved["assessment"]["completion_evidence_category"], json.dumps(saved)))
        self.api = FakeApi()
        self.patches = [patch.dict(os.environ, {"PROJECTWATCH_PHOTO_REVIEW_DB": str(self.db)}),
                        patch("requests.get", side_effect=self.api)]
        for item in self.patches:
            item.start()
        self.at = AppTest.from_file(str(HERE / "app.py"), default_timeout=60)
        self.at.session_state["primary_navigation"] = "Explore projects"

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def open(self, contract):
        self.at.session_state["live_contract"] = contract
        self.at.run()
        self.assertFalse(self.at.exception, [e.value for e in self.at.exception])
        parts = []
        for kind in ("markdown", "caption", "text", "subheader", "warning", "info", "error"):
            parts += [str(e.value) for e in getattr(self.at, kind)]
        return "\n".join(parts).replace("\\", "")

    def test_saved_assessment_is_separate_and_read_only(self):
        before = self.db.read_bytes()
        text = self.open("C-1")
        for heading in ("Reported project status", "Photo evidence assessment", "Manual reviewer decision"):
            self.assertIn(heading, text)
        self.assertIn("never reruns or changes it", text)
        self.assertIn("Saved review time · 10 Nov 2026, 09:00 PHT", text)
        self.assertIn("No photos are available in this source record.", text)  # live gallery is empty
        self.assertEqual(self.db.read_bytes(), before)

    def test_unreviewed_contract_says_no_saved_assessment(self):
        text = self.open("C-2")
        self.assertIn("No saved photo assessment.", text)
        self.assertIn("Reported status · On-Going (from the source record; not a photo assessment)", text)
        self.assertNotIn("Manual reviewer decision", text)

    def test_gallery_retrieval_failure_is_distinct_from_no_photos(self):
        def failing(url, params=None, timeout=None, **kwargs):
            if url.endswith("/images"):
                import requests
                raise requests.ConnectionError("down")
            return self.api(url, params=params, timeout=timeout)
        with patch("requests.get", side_effect=failing):
            text = self.open("C-2")
        self.assertIn("Photos could not be retrieved.", text)
        self.assertNotIn("No photos are available in this source record.", text)


class ContractorTableTests(unittest.TestCase):
    def test_scores_dates_and_reported_status_header(self):
        from streamlit.testing.v1 import AppTest

        def view():
            import app
            from test_audit_regressions import photo as p, record as r
            from contract_assessment import assess_contract as assess
            scored = r([p("2025-01-02", "paved road surface")])
            scored.update(contractId="S-1", contractor="Alpha Builders")
            scored["dates"]["Contract expiry"] = "2025-01-05"
            unscored = r([])
            unscored.update(contractId="S-2", contractor="Bayanihan Construction", status=None)
            for item in (scored, unscored):
                item["assessment"] = assess(item)
            app.show_contractor_performance([scored, unscored])

        at = AppTest.from_function(view, default_timeout=60).run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        ranking = at.dataframe[0].value
        ranking = ranking.data if hasattr(ranking, "data") else ranking
        by_name = ranking.set_index("Contractor")
        self.assertEqual(by_name.loc["Bayanihan Construction", "Ranking score"], "No score")
        self.assertRegex(by_name.loc["Alpha Builders", "Ranking score"], r"^-?\d+\.\d{2}$")
        self.assertRegex(by_name.loc["Alpha Builders", "Total points"], r"^[+-]\d+$")
        projects = at.dataframe[1].value
        self.assertIn("Reported status", projects.columns)
        self.assertNotIn("Status", projects.columns)
        self.assertEqual(projects.iloc[0]["Reported completion"], "01 Jan 2025")
        self.assertTrue(all(re.fullmatch(r"[+-]\d+|No score", str(v)) for v in projects["Points"]))


if __name__ == "__main__":
    unittest.main()
