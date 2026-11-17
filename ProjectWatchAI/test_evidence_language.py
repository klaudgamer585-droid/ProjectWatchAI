"""P0 presentation-mapping tests. All writes use disposable SQLite files."""
import json
import os
import re
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import evidence_language as language
from contract_assessment import COMPLETION_TIMING_GROUPS, assess_contract, flag_is_discrepancy
from project_review import CONTRACT_SCORE_LEVELS
from test_audit_regressions import photo, record

HERE = Path(__file__).parent
ASSESSMENT_SOURCE = (HERE / "contract_assessment.py").read_text(encoding="utf-8")
APP_SOURCE = (HERE / "app.py").read_text(encoding="utf-8")
# Affirmative certainty/accusation wording; disclaimers such as "does not certify" are allowed.
FORBIDDEN = re.compile(r"completion evidence found|(?<!not )certif|verified complete|proves|fraud|corrupt|best contractor", re.I)


def emitted_categories():
    """Every literal category the rules or the app can store."""
    found = set(COMPLETION_TIMING_GROUPS)
    found |= set(re.findall(r'return "([^"]+)"', ASSESSMENT_SOURCE))
    found |= set(re.findall(r'evidence_category = "([^"]+)"', ASSESSMENT_SOURCE))
    found |= set(re.findall(r'(?:NEUTRAL_REVIEW_CATEGORY|PHOTOS_BEFORE_COMPLETION) = "([^"]+)"', ASSESSMENT_SOURCE))
    found |= set(re.findall(r'allowed\.add\("([^"]+)"\)', APP_SOURCE))
    found |= {"Completed · manual review"}
    return found


class CategoryMappingTests(unittest.TestCase):
    def test_every_emitted_category_has_a_qualified_label(self):
        for raw in emitted_categories():
            with self.subTest(raw=raw):
                self.assertIn(raw, language.CATEGORIES)
                self.assertNotRegex(language.category_label(raw), FORBIDDEN)
                self.assertTrue(language.category_explanation(raw))

    def test_automatic_positive_findings_say_visible_scope(self):
        for raw in COMPLETION_TIMING_GROUPS:
            label = language.category_label(raw)
            self.assertTrue(label.startswith("Visible scope evidence · photo captured"), label)
            self.assertEqual(language.category_kind(raw), "scope")

    def test_labels_are_unique_and_round_trip(self):
        values = [*language.CATEGORIES, "Completion evidence captured · later", "Some future category", None,
                  # unknown stored values that collide with display text must stay distinguishable
                  "Visible scope evidence · photo captured 1–7 days after reported completion",
                  "Saved label: odd", language.LEGACY_PREFIX + "x", "No saved assessment"]
        labels = [language.category_label(value) for value in values]
        self.assertEqual(len(set(labels)), len(labels))
        for value, label in zip(values, labels):
            self.assertEqual(language.category_value(label), value, label)

    def test_missing_failed_inconclusive_unknown_are_distinct(self):
        states = ["No photo evidence", "Photo evidence unavailable", "Photo analysis unavailable · retry needed",
                  "Photos inconclusive", "Neutral · further review needed", "Reported status unavailable", None]
        self.assertEqual(len({language.category_label(state) for state in states}), len(states))
        self.assertEqual(language.category_kind("Photo analysis unavailable · retry needed"), "failed")
        self.assertNotEqual(language.category_kind("No photo evidence"), "failed")

    def test_manual_is_not_presented_as_automatic(self):
        self.assertEqual(language.category_kind("Completed · manual review"), "manual")
        self.assertTrue(language.category_label("Completed · manual review").startswith("Manual decision"))
        self.assertEqual(language.outcome_label("Manual review · Completed · manual review"),
                         "Manual decision · marked complete by reviewer")
        self.assertEqual(language.outcome_label("Manual review · Photos inconclusive"),
                         "Manual decision · Photos inconclusive")

    def test_badges_never_use_green(self):
        for raw in [*language.CATEGORIES, None, "unknown"]:
            self.assertNotEqual(language.category_badge(raw)[2], "green")


class OutcomeFlagAndTextTests(unittest.TestCase):
    def test_every_outcome_is_mapped(self):
        for raw in re.findall(r'outcome = "([^"]+)"', ASSESSMENT_SOURCE):
            with self.subTest(raw=raw):
                self.assertIn(raw, language.OUTCOMES)
                self.assertNotRegex(language.outcome_label(raw), FORBIDDEN)

    def test_every_literal_flag_is_explained(self):
        literals = set(re.findall(r'flags\.append\("([^"]+)"\)', ASSESSMENT_SOURCE))
        literals |= {"Award amount exceeds approved budget for the contract",
                     "Reported project budget exceeds approved budget for the contract",
                     "Reported progress is outside 0–100% (120%)",
                     "Reported completed with physical progress below 100% (80%)",
                     "Reported not yet started with physical progress above 0% (5%)",
                     "Construction equipment in latest building photo before reported completion · follow-up needed",
                     "Manual decision retained; underlying evidence changed or original evidence provenance is unavailable"}
        for raw in literals:
            with self.subTest(raw=raw):
                text, why = language.flag_text(raw)
                self.assertTrue(why, raw)
                self.assertNotRegex(text + why, FORBIDDEN)
        self.assertIn("(80%)", language.flag_text("Reported completed with physical progress below 100% (80%)")[0])

    def test_budget_flag_states_basis_and_no_misconduct(self):
        text, why = language.flag_text("Award amount exceeds approved budget for the contract")
        self.assertIn("10%", text)
        self.assertIn("does not establish spending or misconduct", why)

    def test_real_budget_flags_from_rules_are_mapped(self):
        r = record([photo("2025-01-02", "paved road surface")])
        r["budgetPHP"] = {"Award amount": 1100000, "Reported project budget": 1100000,
                          "Approved budget for the contract": 1000000}
        for flag in assess_contract(r)["flags"]:
            self.assertTrue(language.flag_text(flag)[1], flag)

    def test_flags_are_grouped_and_deduplicated(self):
        flags = ["Reported completion precedes reported start", "Reported completion precedes reported start",
                 "Latest photo analysis unavailable; retry required", "Unmapped future flag"]
        inconsistencies, notes = language.grouped_flags(flags)
        self.assertEqual([raw for raw, *_ in inconsistencies], ["Reported completion precedes reported start"])
        self.assertEqual([raw for raw, *_ in notes], [flags[2], flags[3]])
        self.assertTrue(all(flag_is_discrepancy(raw) for raw, *_ in inconsistencies))
        self.assertEqual(notes[1][1], "Unmapped future flag")  # unknown flags stay visible as stored

    def test_finding_states_are_distinct_and_negative_is_not_absence(self):
        base = {"detector": "Roads"}
        texts = {
            "detected": language.finding_text({**base, "status": "detected",
                                               "detections": [{"label": "paved road surface", "confidence": .42}]}),
            "none": language.finding_text({**base, "status": "no detection"}),
            "missing": language.finding_text({**base, "status": "unavailable", "quality": "missing"}),
            "failed": language.finding_text({**base, "status": "unavailable", "quality": "unavailable"}),
            "unknown": language.finding_text({**base}),
        }
        self.assertEqual(len(set(texts.values())), 5)
        self.assertIn("model score 42%", texts["detected"])
        self.assertIn("does not show that the work is absent", texts["none"])
        self.assertIn("not installed", texts["missing"])
        self.assertIn("Retry", texts["failed"])
        legacy = language.finding_text({**base, "status": "detected", "observation": "YOLO detected road (40% confidence)."})
        self.assertIn("legacy", legacy)

    def test_eligibility_never_certifies(self):
        for quality in ("validated", "provisional", "review_only", "missing", "unavailable", None):
            label, use = language.eligibility(quality)
            self.assertNotRegex(label + use, FORBIDDEN)
        self.assertIn("does not certify", language.eligibility("validated")[1])
        # Policy audit A1: review_only detections can set a category under the current rules, so the
        # wording must not promise otherwise; it must say the detector is not validated.
        label, use = language.eligibility("review_only")
        self.assertNotIn("cannot set", (label + use).casefold())
        self.assertIn("not validated", (label + use).casefold())
        self.assertIn("can still set a photo assessment", use)

    def test_photo_timing_is_not_delay(self):
        for days, expected in ((None, "unknown"), (0, "on the reported completion date"),
                               (1, "1 day after"), (45, "45 days after"), (-3, "3 days before")):
            text = language.photo_timing_text(days)
            self.assertIn(expected, text)
            self.assertNotRegex(text, r"(?i)late|delay")
        self.assertIn("not a measured construction delay", language.TIMING_NOTE)

    def test_score_levels_round_trip_and_no_score_is_not_zero(self):
        raws = [*CONTRACT_SCORE_LEVELS, "Unscored · no qualifying photo evidence"]
        labels = [language.score_level_label(raw) for raw in raws]
        self.assertEqual(len(set(labels)), len(labels))
        for raw, label in zip(raws, labels):
            self.assertEqual(language.score_level_value(label), raw)
            self.assertNotRegex(label, r"(?i)\blate\b")
        self.assertTrue(language.score_level_label("Unscored").startswith("No score"))

    def test_relabel_text_is_single_pass_and_idempotent(self):
        raw = ("TEST: Completion evidence found · 1–7 days later. Reported completed · recent photo evidence "
               "supports the contract scope. Completion not reported. Builder Co · 1–7 days late (+1 points).")
        once = language.relabel_text(raw)
        self.assertEqual(language.relabel_text(once), once)
        self.assertNotIn("Completion evidence found", once)
        self.assertIn("Visible scope evidence · photo captured 1–7 days after reported completion", once)
        self.assertIn("Completion not reported in source.", once)
        self.assertIn("Reported complete 1–7 days after contract expiry (+1 points)", once)
        self.assertIsNone(language.relabel_text(None))


class StoredValueTests(unittest.TestCase):
    """Presentation never changes stored values; manual decisions keep the automatic observation."""

    def setUp(self):
        import app
        self.app = app
        self.tmp = tempfile.TemporaryDirectory(dir=HERE)
        self.patches = [patch.object(app, "PHOTO_REVIEW_DB", Path(self.tmp.name) / "test.db"),
                        patch.object(app, "current_model_signature", return_value="test-models")]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def test_manual_decision_keeps_raw_values_and_automatic_category(self):
        r = record([photo("2025-01-02", "paved road surface")])
        r["assessment"] = assess_contract(r)
        automatic = r["assessment"]["completion_evidence_category"]
        self.app.save_contract_assessments([r], require_saved=True)
        # The widget hands back the raw value selected through format_func.
        chosen = language.category_value(language.category_label("Completed · manual review"))
        self.app.save_manual_category(r, chosen, "Reviewer inspected scope", "Test Reviewer")
        with closing(self.app.open_assessment_db()) as db:
            stored, summary = db.execute("SELECT category, summary FROM contract_assessments").fetchone()
            manual = db.execute("SELECT category FROM manual_categories").fetchone()[0]
        self.assertEqual((stored, manual), ("Completed · manual review", "Completed · manual review"))
        saved = json.loads(summary)
        self.assertEqual(saved["assessment"]["automatic_evidence_category"], automatic)
        self.assertEqual(self.app.automatic_category(saved), (automatic, None))
        rerun = self.app.assess_with_manual_review(r)
        self.assertEqual(rerun["completion_evidence_category"], "Completed · manual review")
        self.assertEqual(rerun["automatic_evidence_category"], automatic)
        # A second decision must not record the first manual decision as "automatic".
        self.app.save_manual_category(saved, "Photos inconclusive", "Second look", "Test Reviewer")
        with closing(self.app.open_assessment_db()) as db:
            again = json.loads(db.execute("SELECT summary FROM contract_assessments").fetchone()[0])
        self.assertEqual(again["assessment"]["automatic_evidence_category"], automatic)

    def test_legacy_manual_decision_recomputes_automatic_with_note(self):
        r = record([photo("2025-01-02", "paved road surface")])
        r["assessment"] = {**assess_contract(r), "completion_evidence_category": "Completed · manual review",
                           "manual_category_note": "old decision"}
        category, note = self.app.automatic_category(r)
        self.assertEqual(category, "Completion evidence found · 1–7 days later")
        self.assertIn("Recomputed", note)


def seed_review_db(path):
    """Disposable fixture: one automatic positive, one failed analysis, one legacy manual decision."""
    import sqlite3
    positive = record([photo("2025-01-02", "paved road surface")])
    positive.update(contractId="FIXTURE-SCOPE", coordinates=None, description="Road *markdown* :red[text] <b>x</b>")
    failed = record([photo("2025-01-02")])
    failed["photos"][0]["yolo"][0].update(status="unavailable", quality="unavailable")
    failed.update(contractId="FIXTURE-FAILED", coordinates=None)
    manual = record([photo("2025-01-03")])
    manual.update(contractId="FIXTURE-MANUAL", coordinates=None)
    rows = []
    for item in (positive, failed, manual):
        item["assessment"] = assess_contract(item)
    manual["assessment"].update(completion_evidence_category="Completed · manual review", manual_category_note="Legacy note")
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE contract_assessments (contract_id TEXT PRIMARY KEY, category TEXT NOT NULL, "
                   "reviewed_at TEXT NOT NULL, summary TEXT NOT NULL)")
        db.execute("CREATE TABLE manual_categories (contract_id TEXT PRIMARY KEY, category TEXT NOT NULL, note TEXT NOT NULL)")
        for item in (positive, failed, manual):
            rows.append((item["contractId"], item["assessment"]["completion_evidence_category"],
                         "2026-11-16T10:00:00+08:00", json.dumps(item)))
        db.executemany("INSERT INTO contract_assessments VALUES (?,?,?,?)", rows)
        db.execute("INSERT INTO manual_categories VALUES ('FIXTURE-MANUAL','Completed · manual review','Legacy note')")
    return positive, failed, manual


class RenderedAppTests(unittest.TestCase):
    """Runs the real Streamlit script with AppTest against a disposable database."""

    def setUp(self):
        from streamlit.testing.v1 import AppTest
        self.tmp = tempfile.TemporaryDirectory(dir=HERE)
        self.db = Path(self.tmp.name) / "fixture.db"
        seed_review_db(self.db)
        self.env = patch.dict(os.environ, {"PROJECTWATCH_PHOTO_REVIEW_DB": str(self.db),
                                           "PROJECTWATCH_REVIEWER": "fixture reviewer"})
        self.env.start()
        self.at = AppTest.from_file(str(HERE / "app.py"), default_timeout=60)
        self.at.session_state["primary_navigation"] = "Admin"

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def rendered_text(self):
        parts = []
        for kind in ("markdown", "caption", "text", "info", "warning", "success", "error", "subheader", "title"):
            parts += [str(element.value) for element in getattr(self.at, kind)]
        return "\n".join(parts)

    def open_group(self, raw):
        self.at.run()
        self.assertFalse(self.at.exception, [e.value for e in self.at.exception])
        self.at.selectbox(key="saved_review_category").set_value(raw).run()
        self.assertFalse(self.at.exception, [e.value for e in self.at.exception])

    def test_saved_review_separates_status_observation_and_decision(self):
        raw = "Completion evidence found · 1–7 days later"
        self.open_group(raw)
        group = self.at.selectbox(key="saved_review_category")
        self.assertEqual(group.value, raw)  # widget value stays the stored value
        self.assertTrue(any(option.startswith("Visible scope evidence · photo captured 1–7 days after") for option in group.options))
        self.assertFalse(any("Completion evidence found" in option for option in group.options))
        text = self.rendered_text()
        for heading in ("Reported project status", "Photo evidence assessment", "Manual reviewer decision"):
            self.assertIn(heading, text)
        self.assertIn("No manual decision recorded", text)
        self.assertIn(language.LIMITATION_TEXT, text)
        self.assertIn("Photo captured 1 day after reported completion", text)
        self.assertNotIn("Completion evidence found", text)
        # External description is shown as plain text, not interpreted.
        self.assertIn("Road *markdown* :red[text] <b>x</b>", [t.value for t in self.at.text])

    def test_failed_analysis_is_distinct(self):
        self.open_group("Photo analysis unavailable · retry needed")
        text = self.rendered_text()
        self.assertIn("Photo analysis failed · retry needed", text)
        self.assertIn("Analysis of the latest photos failed · retry needed", text)
        self.assertIn("Roads: analysis failed. Retry needed.", text)

    def test_legacy_manual_decision_shows_both_values(self):
        self.open_group("Completed · manual review")
        text = self.rendered_text()
        self.assertIn("Manual decision · marked complete by reviewer", text)
        self.assertIn("Further review needed · latest photos not conclusive", text)  # recomputed automatic value
        self.assertIn("Recomputed from saved observations", text)
        self.assertIn("Reviewer and time were not recorded", text)

    def test_decision_form_validates_then_saves_raw_value(self):
        self.open_group("Completion evidence found · 1–7 days later")
        choice = self.at.selectbox(key="manual_category_choice_FIXTURE-SCOPE")
        self.assertIsNone(choice.value)  # no preselected outcome
        self.assertNotIn("Completion evidence found", " ".join(choice.options))
        choice.set_value("Photos inconclusive")
        self.at.button[[b.label for b in self.at.button].index("Save decision")].click().run()
        self.assertIn("Enter a reason for this category change", self.rendered_text())  # phase 3 field feedback
        self.assertEqual(self.at.selectbox(key="manual_category_choice_FIXTURE-SCOPE").value, "Photos inconclusive")
        self.at.text_area(key="manual_category_reason_FIXTURE-SCOPE").input("Scope not clear in photo 1")
        self.at.button[[b.label for b in self.at.button].index("Save decision")].click().run()
        self.assertFalse(self.at.exception, [e.value for e in self.at.exception])
        import sqlite3
        with closing(sqlite3.connect(self.db)) as db:
            manual = db.execute("SELECT category, note FROM manual_categories WHERE contract_id='FIXTURE-SCOPE'").fetchone()
            reviewer = db.execute("SELECT reviewer FROM manual_review_metadata WHERE contract_id='FIXTURE-SCOPE'").fetchone()
        self.assertEqual(manual, ("Photos inconclusive", "Scope not clear in photo 1"))
        self.assertEqual(reviewer, ("fixture reviewer",))
        self.assertIn("Decision saved for FIXTURE", self.rendered_text())


if __name__ == "__main__":
    unittest.main()
