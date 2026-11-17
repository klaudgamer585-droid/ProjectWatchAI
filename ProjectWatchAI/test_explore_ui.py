"""Explore page (plan §5A, P1a) rendered through AppTest with a mocked BetterGov API and a disposable DB."""
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

HERE = Path(__file__).parent
CURRENT_YEAR = str(datetime.now(timezone(timedelta(hours=8))).year)
PROJECTS = [
    {"contractId": f"C-{n}", "description": f"Road project {n} *not markdown*", "status": status,
     "completionDate": f"2025-01-0{n}", "budget": 1000000 * n, "location": {"region": "Region III"}}
    for n, status in ((1, "Completed"), (2, "On-Going"), (3, "Not Yet Started"))
]


def fake_response(payload):
    response = Mock(status_code=200, headers={"Content-Type": "application/json"})
    response.raise_for_status = Mock()
    response.json = Mock(return_value={"data": payload})
    return response


class FakeApi:
    """Records project-list requests; returns three contracts (or none when searching 'nomatch')."""

    # Opt-in photos-only fixture (other suites import FakeApi and expect empty galleries):
    # C-1 has photos, C-2 has none, the gallery check for C-3 fails.
    PHOTO_FIXTURE = {"C-1": 2, "C-2": 0}

    def __init__(self):
        self.list_calls = []
        self.image_calls = []
        self.down = False      # source outage switch
        self.projects = PROJECTS
        self.photo_counts = None  # None: every gallery is empty

    def __call__(self, url, params=None, timeout=None, **kwargs):
        import requests
        if self.down:
            raise requests.ConnectionError("source down (fixture)")
        path = url.removeprefix("https://api.dpwh.bettergov.ph")
        if path == "/projects":
            self.list_calls.append(dict(params or {}))
            data = [] if (params or {}).get("search") == "nomatch" else self.projects
            return fake_response({"data": data, "pagination": {"totalCount": len(data) * 3, "totalPages": 3 if data else 0}})
        if path.endswith("/images"):
            contract = path.split("/")[2]
            self.image_calls.append(contract)
            counts = self.photo_counts
            if counts is not None and contract not in counts:
                raise requests.ConnectionError("gallery down (fixture)")
            count = (counts or {}).get(contract, 0)
            return fake_response({"images": [], "pagination": {"totalCount": count, "totalPages": int(count > 0)},
                                  "summary": {}})
        contract = path.rsplit("/", 1)[-1]
        project = next(p for p in PROJECTS if p["contractId"] == contract)
        return fake_response({**project, "contractor": "Alpha Builders", "imageSummary": {"hasImages": False}})


class ExploreTests(unittest.TestCase):
    def setUp(self):
        import streamlit as st
        from streamlit.testing.v1 import AppTest
        st.cache_data.clear()
        self.tmp = tempfile.TemporaryDirectory(dir=HERE)
        self.db = Path(self.tmp.name) / "reviews.db"
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("CREATE TABLE contract_assessments (contract_id TEXT PRIMARY KEY, category TEXT NOT NULL, "
                       "reviewed_at TEXT NOT NULL, summary TEXT NOT NULL)")
            db.execute("INSERT INTO contract_assessments VALUES ('C-1', 'Completion evidence found · 1–7 days later', "
                       "'2026-11-10T09:00:00+08:00', ?)", (json.dumps({"contractId": "C-1"}),))
        self.api = FakeApi()
        self.patches = [patch.dict(os.environ, {"PROJECTWATCH_PHOTO_REVIEW_DB": str(self.db)}),
                        patch("requests.get", side_effect=self.api)]
        for item in self.patches:
            item.start()
        self.at = AppTest.from_file(str(HERE / "app.py"), default_timeout=60)

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def run_app(self):
        self.at.run()
        self.assertFalse(self.at.exception, [e.value for e in self.at.exception])
        return self.at

    def button(self, key):
        return self.at.button(key=key)

    def table(self):
        return self.at.dataframe[0].value

    def captions(self):
        return " ".join(c.value for c in self.at.caption).replace("\\", "")  # captions are markdown-escaped

    def active_filters(self):
        """Text of the Active filters chip row (st.html), tags stripped and entities decoded."""
        import html
        import re
        body = next(h.proto.body for h in self.at.get("html") if "bant-filters" in h.proto.body)
        return html.unescape(re.sub(r"<[^>]+>", " ", body))

    def chips(self):
        import html
        import re
        body = next(h.proto.body for h in self.at.get("html") if "bant-filters" in h.proto.body)
        return [html.unescape(chip) for chip in re.findall(r'class="bant-chip"[^>]*>([^<]*)<', body)]

    def table_ids(self):
        frame = self.table()
        frame = frame.data if hasattr(frame, "data") else frame
        return [value.split(" · ", 1)[0] for value in frame["Contract ID / project"]]

    def test_search_first_and_more_filters(self):
        at = self.run_app()
        self.assertEqual([s.value for s in at.subheader][:2], ["Find a contract", "9 matching contracts"])
        self.assertEqual(at.text_input(key="live_search").label, "Contract ID or project description")
        more = [e for e in at.expander if e.label == "More filters"]
        self.assertEqual(len(more), 1)
        self.assertEqual({w.key for w in more[0].selectbox}, {"live_category", "live_status", "live_limit"})
        self.assertEqual(at.selectbox(key="live_year").value, CURRENT_YEAR)
        self.assertEqual(self.chips(), [f"Fiscal year {CURRENT_YEAR} (default)", "All regions"])
        self.assertIn("Active filters", self.active_filters())
        self.assertIn(f"Showing every contract for fiscal year {CURRENT_YEAR}", self.captions())  # initial state
        self.assertFalse(any("matching" in m.label for m in at.metric))  # metrics no longer precede search

    def test_table_columns_and_saved_evidence_labels(self):
        self.run_app()
        frame = self.table()
        frame = frame.data if hasattr(frame, "data") else frame  # Styler or DataFrame
        # Plan §5A order, with ID and project in one pinned column.
        self.assertEqual(list(frame.columns), ["Contract ID / project", "Region", "Reported status", "Photo evidence", "Last reviewed"])
        ids = [value.split(" · ", 1)[0] for value in frame["Contract ID / project"]]
        evidence = dict(zip(ids, frame["Photo evidence"]))
        self.assertEqual(evidence["C-1"], "Visible scope evidence · photo captured 1–7 days after reported completion")
        self.assertEqual(evidence["C-2"], "Not reviewed")
        self.assertEqual(dict(zip(ids, frame["Last reviewed"]))["C-1"], "10 Nov 2026")

    def test_reset_restores_default_year(self):
        at = self.run_app()
        at.selectbox(key="live_region").set_value("Region III")
        at.selectbox(key="live_year").set_value("2020")
        self.run_app()
        self.assertEqual(self.api.list_calls[-1]["year"], "2020")
        self.button("reset_live_filters").click()
        at = self.run_app()
        self.assertEqual(at.selectbox(key="live_year").value, CURRENT_YEAR)
        self.assertIsNone(at.selectbox(key="live_region").value)
        # The first run's request (current year) is served from the API cache, so check the stated filters.
        self.assertEqual(self.chips(), [f"Fiscal year {CURRENT_YEAR} (default)", "All regions"])
        self.assertNotIn("2020", self.active_filters())

    def test_open_contract_and_back_keep_filters_and_page(self):
        at = self.run_app()
        self.assertTrue(self.button("open_live_contract").disabled)
        at.selectbox(key="live_region").set_value("Region III")
        self.run_app()
        [b for b in self.at.button if b.label == "Next"][0].click()
        at = self.run_app()
        self.assertEqual(self.api.list_calls[-1]["page"], 2)
        at.selectbox(key="live_open_choice").set_value("C-2")
        self.run_app()
        self.button("open_live_contract").click()
        at = self.run_app()
        self.assertEqual(at.subheader[0].value, "C-2")
        self.assertIn("Back to results", [b.label for b in at.button])
        self.assertIn("Road project 2 *not markdown*", [t.value for t in at.text])  # escaped external text
        self.assertTrue(any("No saved review for this contract yet" in c.value for c in at.caption))
        self.button("live_back_to_results").click()
        at = self.run_app()
        self.assertNotIn("Back to results", [b.label for b in at.button])
        self.assertEqual(at.selectbox(key="live_region").value, "Region III")
        self.assertEqual(self.api.list_calls[-1]["page"], 2)
        self.assertEqual(self.api.list_calls[-1]["region"], "Region III")

    def test_empty_state_lists_filters_and_offers_reset(self):
        at = self.run_app()
        at.text_input(key="live_search").input("nomatch")
        at = self.run_app()
        # Shared neutral empty state (phase 3), not an alert box.
        self.assertTrue(any("No contracts match these filters" in m.value for m in at.markdown))
        self.assertEqual(len(at.info), 0)
        self.assertIn("Active filters", self.captions())
        self.assertIn("nomatch", self.captions())
        self.button("reset_live_filters_empty").click()
        at = self.run_app()
        self.assertEqual(at.text_input(key="live_search").value, "")

    def test_no_timed_refresh_and_manual_refresh_available(self):
        at = self.run_app()
        self.assertIn("Refresh results", [b.label for b in at.button])
        self.assertTrue(any("Source checked" in c.value for c in at.caption))
        source = (HERE / "app.py").read_text(encoding="utf-8")
        self.assertNotIn("@st.fragment(run_every=REFRESH_INTERVAL_SECONDS)", source)

    # --- Phase 4 -------------------------------------------------------------------------------------
    def metrics(self):
        return {m.label: m.value for m in self.at.metric}

    def test_photos_only_keeps_matching_checked_and_shown_counts_apart(self):
        self.api.photo_counts = FakeApi.PHOTO_FIXTURE
        at = self.run_app()
        at.toggle(key="live_photos_only").set_value(True)
        at = self.run_app()
        self.assertEqual(at.subheader[1].value, "9 matching contracts before photo check")
        self.assertEqual(self.metrics(), {"Matching records": "9", "Checked on this page": "3",
                                          "With photos, shown": "1", "Could not be checked": "1"})
        self.assertEqual(self.table_ids(), ["C-1"])
        self.assertIn("Page 1 lists 3 of 9 matching records (before photo check)", self.captions())
        self.assertTrue(any("could not be verified for 1 contracts" in e.value for e in at.error))
        self.assertIn("Only contracts with photos", self.chips())

    def test_photo_checks_never_run_without_photos_only(self):
        self.run_app()
        self.assertEqual(self.api.image_calls, [])  # result rows are not decorated with extra lookups

    def test_back_restores_selection_and_shows_one_time_context_note(self):
        at = self.run_app()
        at.selectbox(key="live_open_choice").set_value("C-2")
        self.run_app()
        self.button("open_live_contract").click()
        self.run_app()
        self.button("live_back_to_results").click()
        at = self.run_app()
        self.assertEqual(at.selectbox(key="live_open_choice").value, "C-2")
        selection = json.loads(at.dataframe[0].proto.selection_default)
        self.assertEqual(self.table_ids()[selection["selection"]["rows"][0]], "C-2")
        self.assertTrue(any(r"You were viewing C\-2" in m.value for m in at.markdown))
        at = self.run_app()  # an ordinary rerun keeps the selection and drops the one-time note
        self.assertEqual(at.selectbox(key="live_open_choice").value, "C-2")
        self.assertFalse(any("You were viewing" in m.value for m in at.markdown))

    def test_reruns_keep_search_choice_view_and_page(self):
        at = self.run_app()
        at.text_input(key="live_search").input("road")
        at = self.run_app()
        [b for b in at.button if b.label == "Next"][0].click()
        at = self.run_app()
        at.selectbox(key="live_open_choice").set_value("C-3")
        at.toggle(key="live_detail_columns").set_value(True)
        self.run_app()
        for _ in range(3):
            at = self.run_app()
        self.assertEqual(at.text_input(key="live_search").value, "road")
        self.assertEqual(at.selectbox(key="live_open_choice").value, "C-3")
        self.assertTrue(at.toggle(key="live_detail_columns").value)
        self.assertEqual(self.api.list_calls[-1]["page"], 2)

    def test_detail_columns_follow_the_planned_hierarchy(self):
        at = self.run_app()
        at.toggle(key="live_detail_columns").set_value(True)
        self.run_app()
        frame = self.table()
        frame = frame.data if hasattr(frame, "data") else frame
        self.assertEqual(list(frame.columns), ["Contract ID / project", "Region", "Reported status", "Photo evidence",
                                               "Last reviewed", "Reported completion", "Reported budget", "Category"])
        row = frame[frame["Contract ID / project"].str.startswith("C-2 ")].iloc[0]
        self.assertEqual((row["Reported completion"], row["Category"]), ("02 Jan 2025", "—"))  # no invented category
        self.assertIn("2,000,000", row["Reported budget"])

    def test_list_view_is_a_keyboard_card_alternative_with_full_names(self):
        long_name = "CONSTRUCTION OF FLOOD CONTROL STRUCTURE ALONG THE RIVER " * 6
        self.api.projects = [{**PROJECTS[0], "description": long_name}, *PROJECTS[1:]]
        at = self.run_app()
        self.assertTrue(any(long_name[:60] in option and option.endswith("…")
                            for option in at.selectbox(key="live_open_choice").options))
        at.radio(key="live_view").set_value("List")
        at = self.run_app()
        self.assertEqual(len(at.dataframe), 0)
        self.assertIn(long_name.strip(), [t.value for t in at.text])  # full text, literal
        labels = [b.label for b in at.button]
        self.assertIn("Open contract C-1", labels)
        self.button("open_card_C-1").click()
        at = self.run_app()
        self.assertEqual(at.subheader[0].value, "C-1")
        self.button("live_back_to_results").click()
        at = self.run_app()
        self.assertEqual(at.radio(key="live_view").value, "List")
        self.assertTrue(any(r"C\-1" in m.value and "last opened" in m.value for m in at.markdown))

    def test_list_view_renders_in_steps(self):
        self.api.projects = [{**PROJECTS[0], "contractId": f"L-{n:02d}"} for n in range(30)]
        at = self.run_app()
        at.radio(key="live_view").set_value("List")
        at = self.run_app()
        self.assertEqual(sum(b.label.startswith("Open contract L-") for b in at.button), 25)
        self.button("live_list_more").click()
        at = self.run_app()
        self.assertEqual(sum(b.label.startswith("Open contract L-") for b in at.button), 30)

    def test_outage_keeps_last_results_labelled_with_their_check_time(self):
        self.run_app()
        self.api.down = True
        self.button("refresh_live_results").click()
        at = self.run_app()
        errors = " ".join(e.value for e in at.error).replace("\\", "")
        self.assertIn("The source is unavailable.", errors)
        self.assertIn("results below are from the last successful check", errors)
        self.assertEqual(sorted(self.table_ids()), ["C-1", "C-2", "C-3"])
        self.assertIn("Source last checked", self.captions())

    def test_outage_without_previous_results_offers_retry_and_keeps_filters(self):
        self.api.down = True
        at = self.run_app()
        self.assertEqual(len(at.dataframe), 0)
        self.assertIn("filters and page are kept", " ".join(e.value for e in at.error))
        self.api.down = False
        self.button("refresh_live_results_failed").click()
        self.run_app()
        self.assertEqual(sorted(self.table_ids()), ["C-1", "C-2", "C-3"])

    def test_search_text_in_chips_is_literal(self):
        at = self.run_app()
        at.text_input(key="live_search").input("<b>x</b> *y*")
        self.run_app()
        self.assertIn("Search “<b>x</b> *y*”", self.chips())


if __name__ == "__main__":
    unittest.main()
