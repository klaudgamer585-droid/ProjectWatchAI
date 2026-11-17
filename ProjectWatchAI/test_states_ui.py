"""Phase 3 shared interface states: empty, error, field feedback, loading, activity and the emphasis budget.

Every rendered test uses a disposable SQLite file and a mocked BetterGov API; nothing touches real data.
"""
import os
import re
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from test_evidence_language import seed_review_db
from test_explore_ui import FakeApi

HERE = Path(__file__).parent


def primary_buttons(at):
    return [b.label for b in at.button if b.proto.type == "primary"]


def flatten(node):
    """Render-order list of (kind, key-or-text) for leaf elements."""
    children = getattr(node, "children", None)
    if isinstance(children, dict) and children:
        return [item for index in sorted(children) for item in flatten(children[index])]
    kind = getattr(node, "type", "")
    if kind in {"text_input", "text_area"}:
        return [(kind, node.key)]
    if kind == "button":
        return [(kind, node.label)]
    if kind == "markdown":
        return [(kind, str(node.value))]
    return [(kind, "")]


def all_text(at):
    parts = []
    for kind in ("markdown", "caption", "text", "info", "warning", "success", "error", "subheader"):
        parts += [str(element.value) for element in getattr(at, kind)]
    return "\n".join(parts).replace("\\", "")


class ContractIdParsingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app
        cls.parse = staticmethod(app.parse_contract_ids)

    def test_harmless_differences_are_normalized(self):
        ids, problem = self.parse("  24ck0181,\n23je0090 ;; 24CK0181 ,")
        self.assertIsNone(problem)
        self.assertEqual(ids, ("24CK0181", "23JE0090"))  # case, spaces, separators and duplicates

    def test_more_than_three_is_rejected_and_names_extras(self):
        ids, problem = self.parse("A1, B2, C3, D4, E5")
        self.assertEqual(ids, ())
        self.assertIn("Enter up to 3 contract IDs", problem)
        self.assertIn("D4, E5", problem)  # never silently dropped

    def test_invalid_characters_are_rejected_clearly(self):
        ids, problem = self.parse("24CK0181, 23JE*0090")
        self.assertEqual(ids, ())
        self.assertIn("23JE*0090", problem)

    def test_empty_input_is_not_an_error(self):
        self.assertEqual(self.parse(" , \n"), ((), None))


class RenderedStateTests(unittest.TestCase):
    def setUp(self):
        import streamlit as st
        from streamlit.testing.v1 import AppTest
        st.cache_data.clear()
        self.tmp = tempfile.TemporaryDirectory(dir=HERE)
        self.db = Path(self.tmp.name) / "fixture.db"
        self.api = FakeApi()
        self.patches = [patch.dict(os.environ, {"PROJECTWATCH_PHOTO_REVIEW_DB": str(self.db),
                                                "PROJECTWATCH_REVIEWER": "fixture reviewer"}),
                        patch("requests.get", side_effect=self.api)]
        for item in self.patches:
            item.start()
        self.at = AppTest.from_file(str(HERE / "app.py"), default_timeout=60)

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def render(self):
        self.at.run()
        self.assertFalse(self.at.exception, [e.value for e in self.at.exception])
        return self.at

    # --- Regional review -------------------------------------------------------------------------
    def test_regional_empty_state_routes_to_admin_regional_reviews(self):
        self.at.session_state["primary_navigation"] = "Regional review"
        at = self.render()
        self.assertIn("No saved regional reviews yet.", all_text(at))
        self.assertEqual(len(at.info) + len(at.warning) + len(at.error), 0)  # neutral, not an alert
        at.button(key="regional_empty_to_admin").click()
        at = self.render()
        self.assertEqual(at.session_state["primary_navigation"], "Admin")
        self.assertEqual(at.session_state["admin_section"], "Regional reviews")
        self.assertIn("Define your review", all_text(at))

    # --- Explore ---------------------------------------------------------------------------------
    def test_explore_no_results_is_an_empty_state_with_reset(self):
        at = self.render()
        at.text_input(key="live_search").input("nomatch")
        at = self.render()
        self.assertIn("No contracts match these filters.", all_text(at))
        self.assertEqual(len(at.info), 0)
        self.assertIn("Reset filters", [b.label for b in at.button])

    def test_source_outage_is_an_error_that_keeps_filters_and_offers_retry(self):
        at = self.render()
        at.text_input(key="live_search").input("road")
        at = self.render()
        import requests
        import streamlit as st
        st.cache_data.clear()
        with patch("requests.get", side_effect=requests.ConnectionError("down")):
            at = self.render()
        errors = " ".join(e.value for e in at.error).replace("\\", "")
        self.assertIn("The source is unavailable.", errors)
        self.assertIn("filters and page are kept", errors)
        self.assertEqual(at.text_input(key="live_search").value, "road")
        self.assertIn("Refresh results", [b.label for b in at.button])

    def test_explore_has_one_primary_action_and_static_freshness(self):
        at = self.render()
        self.assertEqual(primary_buttons(at), ["Open contract"])
        self.assertTrue(any("Source checked" in c.value and ":material/schedule:" in c.value for c in at.caption))

    # --- Admin · decision form -------------------------------------------------------------------
    def open_scope_contract(self):
        seed_review_db(self.db)
        self.at.session_state["primary_navigation"] = "Admin"
        self.render()
        self.at.selectbox(key="saved_review_category").set_value("Completion evidence found · 1–7 days later")
        return self.render()

    def click(self, label):
        self.at.button[[b.label for b in self.at.button].index(label)].click()
        return self.render()

    def test_reason_feedback_sits_under_reason_field_and_keeps_category(self):
        at = self.open_scope_contract()
        at.selectbox(key="manual_category_choice_FIXTURE-SCOPE").set_value("Photos inconclusive")
        at = self.click("Save decision")
        values = [str(m.value) for m in at.markdown]
        feedback = [i for i, value in enumerate(values) if "Enter a reason for this category change" in value]
        self.assertEqual(len(feedback), 1)
        self.assertIn(":red[:material/error:", values[feedback[0]])  # icon + words, not colour alone
        self.assertNotIn("Choose a decision category", " ".join(values))  # only the missing field is flagged
        # Placement: directly after the reason field and before the submit button, in render order.
        order = flatten(at.main)
        reason = order.index(("text_area", "manual_category_reason_FIXTURE-SCOPE"))
        message = next(i for i, (kind, value) in enumerate(order)
                       if kind == "markdown" and "Enter a reason for this category change" in value)
        submit = order.index(("button", "Save decision"))
        self.assertEqual(message, reason + 1)
        self.assertLess(message, submit)
        self.assertEqual(at.selectbox(key="manual_category_choice_FIXTURE-SCOPE").value, "Photos inconclusive")

    def test_save_failure_keeps_draft(self):
        at = self.open_scope_contract()
        # Simulate a SQLite failure inside the real save path, in-process, on the disposable fixture.
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("CREATE TRIGGER fail_manual BEFORE INSERT ON manual_categories "
                       "BEGIN SELECT RAISE(ABORT, 'simulated failure'); END")
        at.selectbox(key="manual_category_choice_FIXTURE-SCOPE").set_value("Photos inconclusive")
        at.text_area(key="manual_category_reason_FIXTURE-SCOPE").input("Scope not clear in photo 1")
        at = self.click("Save decision")
        errors = " ".join(e.value for e in at.error).replace("\\", "")
        self.assertIn("Decision could not be saved.", errors)
        self.assertIn("Your draft is still here", errors)
        self.assertEqual(at.selectbox(key="manual_category_choice_FIXTURE-SCOPE").value, "Photos inconclusive")
        self.assertEqual(at.text_area(key="manual_category_reason_FIXTURE-SCOPE").value, "Scope not clear in photo 1")
        self.assertEqual(len(at.success), 0)  # no green after a failure
        with closing(sqlite3.connect(self.db)) as db:
            self.assertIsNone(db.execute(
                "SELECT 1 FROM manual_categories WHERE contract_id='FIXTURE-SCOPE'").fetchone())

    def test_reviewed_workspace_has_one_primary_decision_action(self):
        at = self.open_scope_contract()
        # Training's primary action appears only once a photo is loaded, in its own task area.
        self.assertEqual(primary_buttons(at), ["Save decision"])

    # --- Admin · contract IDs --------------------------------------------------------------------
    def test_contract_id_feedback_lists_extra_ids(self):
        self.at.session_state["primary_navigation"] = "Admin"
        self.at.session_state["admin_section"] = "Contract reviews"
        at = self.render()
        at.text_area(key="admin_contract_ids").input("a1, b2\nc3, d4")
        at = self.render()
        text = all_text(at)
        self.assertIn("Enter up to 3 contract IDs. Remove 1 to continue: D4.", text)
        self.assertNotIn("Build contract summaries", [b.label for b in at.button])  # nothing runs on a partial list


class SourceRulesTests(unittest.TestCase):
    """Static guards for the emphasis budget and labelled loading."""

    def setUp(self):
        self.app = (HERE / "app.py").read_text(encoding="utf-8")
        self.tester = (HERE / "yolo_test_app.py").read_text(encoding="utf-8")

    def test_loading_helper_replaces_generic_spinners(self):
        for source in (self.app, self.tester):
            self.assertNotIn("st.spinner(", source)
        stages = re.findall(r'loading\(f?"([^"]+)"', self.app + self.tester)
        self.assertGreaterEqual(len(stages), 8)
        for stage in stages:
            self.assertNotRegex(stage, r"(?i)minutes|seconds|almost")  # no claimed durations

    def test_alerts_reserved_for_failures(self):
        for source in (self.app, self.tester):
            self.assertNotIn("st.warning(", source)
            self.assertNotIn("st.info(", source)
        # Green only for saved confirmations.
        for message in re.findall(r'st\.success\(([^\n]+)', self.app + self.tester):
            self.assertRegex(message, r"(?i)saved|refreshed for|message")

    def test_ui_helpers_keep_their_signatures(self):
        import inspect
        import ui
        self.assertEqual(list(inspect.signature(ui.empty_state).parameters)[:2], ["title", "body"])
        self.assertIn("kept", inspect.signature(ui.error_state).parameters)
        self.assertIn("running", inspect.signature(ui.freshness).parameters)


class TesterSourceIdTests(unittest.TestCase):
    def test_source_id_feedback(self):
        import yolo_test_app as tester
        self.assertIsNone(tester.source_id_problem(""))
        self.assertIsNone(tester.source_id_problem(" 24CK0181 "))
        self.assertIn("one contract", tester.source_id_problem("24CK0181, 23JE0090"))
        self.assertIn("letters, numbers", tester.source_id_problem("24CK*0181"))


if __name__ == "__main__":
    unittest.main()
