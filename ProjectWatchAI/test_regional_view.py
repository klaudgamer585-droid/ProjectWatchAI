"""Phase 6: Regional review (saved reviews) — coverage, definitions, drilldowns, comparison, highlights.

Pure helpers are tested directly. The page is rendered through the real script (AppTest) against a disposable
SQLite file with known fixtures; no network, no real reviews. Every count shown is checked against the records
that its drilldown lists, so an aggregate that cannot be traced to its records fails here.
"""
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import regional_view as rv
from contract_assessment import assess_contract
from test_audit_regressions import photo, record
from test_states_ui import all_text

HERE = Path(__file__).parent
REGION, OTHER_REGION = "Region III", "Region I"
RECENT, OLD = "2026-11-16T10:00:00+08:00", "2026-11-10T09:00:00+08:00"


def fixture(contract, *, contractor="Alpha Builders", photos=None, budget=None, completion="2025-01-01",
            status="Completed", gallery=None, scope="paved road surface"):
    photos = [photo("2025-01-02", scope)] if photos is None else photos
    item = record(photos)
    item.update(contractId=contract, coordinates=None, description=f"Fixture {contract}", contractor=contractor,
                status=status, budgetPHP=budget if budget is not None else {"Reported project budget": "1000000"})
    item["dates"] = {"Reported completion": completion, "Contract expiry": "2025-01-05"} if completion else {}
    if gallery is not None:
        item["galleryPhotoCount"] = gallery
    item["assessment"] = assess_contract(item)
    return item


def fixtures():
    """Known set, 9 reviews. Expected counts are asserted by name below."""
    failed = fixture("FX-FAILED", contractor="Beta Works")
    failed["photos"][0]["yolo"][0].update(status="unavailable", quality="unavailable")
    failed["assessment"] = assess_contract(failed)
    cue = fixture("FX-CUE", contractor="Beta Works", scope="Under construction")
    cue["photos"][0]["yolo"][0]["detector"] = "Under construction"
    cue["assessment"] = assess_contract(cue)
    return [
        fixture("FX-A1"),
        fixture("FX-A2", contractor="alpha   builders", budget={"Reported project budget": "250000.50",
                "Approved budget for the contract": "500000", "Award amount": "600000"}),
        failed,
        cue,
        fixture("FX-NOPHOTO", contractor="Gamma Corp", photos=[], gallery=0),
        fixture("FX-NOCONTRACTOR", contractor=None, completion=None, budget={}),
        fixture("FX-ZERO", contractor="Delta Inc", budget={"Reported project budget": "0"}),
        fixture("FX-ONGOING", contractor="Delta Inc", status="On-going"),
        fixture("FX-ELSEWHERE", contractor="Alpha Builders"),
    ]


def entry(item, *, stale=False, reviewed_at=RECENT):
    return {"record": item, "reviewed_at": reviewed_at, "stale": stale}


class PureTests(unittest.TestCase):
    def setUp(self):
        self.items = fixtures()
        self.entries = [entry(item, stale=item["contractId"] in {"FX-A1", "FX-CUE"},
                              reviewed_at=OLD if item["contractId"] == "FX-A1" else RECENT) for item in self.items]

    def ids(self, focus):
        return {e["record"]["contractId"] for e in self.entries if rv.focus_matches(e, focus)}

    def test_every_count_equals_its_drilldown(self):
        summary = rv.coverage(self.entries, None)
        pairs = {"flagged": summary["flagged"], "stale": summary["stale"], "unavailable": rv.unavailable_total(summary),
                 "no_photos": summary["no_photos"], "inconclusive": summary["inconclusive"],
                 "no_completion_date": summary["no_completion_date"], "no_budget": summary["no_budget"],
                 "manual": summary["manual"], "all": summary["included"]}
        for focus, count in pairs.items():
            self.assertEqual(len(self.ids(focus)), count, focus)
        for focus, label in rv.focus_choices(self.entries):
            self.assertTrue(label.endswith(f" · {len(self.ids(focus)):,}"), (focus, label))

    def test_known_fixture_counts(self):
        s = rv.coverage(self.entries, None)
        self.assertEqual((s["included"], s["stale"], s["no_photos"], s["manual"]), (9, 2, 1, 0))
        self.assertEqual(s["analysis_unavailable"], 1)
        self.assertEqual(self.ids("unavailable"), {"FX-FAILED"})
        self.assertEqual(self.ids("no_photos"), {"FX-NOPHOTO"})
        self.assertEqual(self.ids("no_completion_date"), {"FX-NOCONTRACTOR"})
        self.assertEqual(self.ids("no_budget"), {"FX-NOCONTRACTOR"})   # zero is a known amount; missing is not
        self.assertIn("FX-ZERO", self.ids("all") - self.ids("no_budget"))

    def test_contractors_are_distinct_named_and_unreported_is_separate(self):
        s = rv.coverage(self.entries, None)
        self.assertEqual(s["contractors"], 5 - 1 + 0)  # Alpha (3 spellings→1), Beta, Gamma, Delta = 4
        self.assertEqual(s["without_contractor"], 1)

    def test_date_range_and_small_sample(self):
        s = rv.coverage(self.entries, None)
        self.assertEqual(rv.date_range_text(s), "10 Nov 2026 to 16 Nov 2026")
        self.assertTrue(s["small_sample"])                       # 9 reviews: below the display threshold of 10
        self.assertFalse(rv.coverage(self.entries + [entry(fixture("FX-TEN"))], None)["small_sample"])
        small = rv.coverage(self.entries[:3], None)
        self.assertTrue(small["small_sample"])
        self.assertEqual(rv.date_range_text(rv.coverage([], None)), "Dates unavailable")

    def test_status_and_category_drilldowns_cover_every_record_once(self):
        counts = rv.status_counts(self.entries)
        self.assertEqual(sum(counts.values()), len(self.entries))
        for status, count in counts.items():
            self.assertEqual(len(self.ids(f"status:{status}")), count)
        for raw, count in rv.category_counts(self.entries).items():
            self.assertEqual(len(self.ids(f"category:{raw}")), count)

    def test_unknown_focus_matches_nothing_and_contract_focus_one(self):
        self.assertEqual(self.ids("nonsense"), set())
        self.assertEqual(self.ids("contract:FX-A2"), {"FX-A2"})
        self.assertEqual(rv.focus_label("nonsense"), "All included saved reviews")

    def test_budget_summary_keeps_unknown_apart_from_zero(self):
        b = rv.budget_summary(self.entries)
        self.assertEqual(b["with_amount"], 8)
        self.assertEqual(b["without_amount"], 1)
        self.assertEqual(str(b["total"]), "6250000.50")        # 6 x 1,000,000 + 250,000.50; the zero adds nothing
        none = rv.budget_summary([entry(fixture("X", budget={}))])
        self.assertIsNone(none["total"])

    def test_gap_checkable_requires_both_amounts(self):
        self.assertFalse(rv.budget_gap_checkable(fixture("X", budget={})))
        self.assertFalse(rv.budget_gap_checkable(fixture("X", budget={"Approved budget for the contract": "0", "Award amount": "5"})))
        self.assertFalse(rv.budget_gap_checkable(fixture("X", budget={"Approved budget for the contract": "100"})))
        self.assertTrue(rv.budget_gap_checkable(fixture("X", budget={"Approved budget for the contract": "100", "Award amount": "0"})))

    def test_csv_has_one_row_per_record_and_is_formula_safe(self):
        hostile = fixture("FX-EVIL")
        hostile["description"] = '=HYPERLINK("http://x")'
        hostile["contractor"] = "+cmd"
        text = rv.records_csv(self.entries + [entry(hostile)])
        lines = text.strip().splitlines()
        self.assertEqual(len(lines), len(self.entries) + 2)
        self.assertIn("'=HYPERLINK", text)
        self.assertIn("'+cmd", text)
        self.assertEqual(rv.records_csv([]), "")

    def test_queue_text_only_when_recorded(self):
        self.assertIsNone(rv.coverage(self.entries, None)["queue"])
        self.assertEqual(rv.coverage(self.entries, {"completed": 9, "pending": 2})["queue"]["pending"], 2)

    def test_hand_damaged_record_does_not_break_helpers(self):
        broken = {"record": {"contractId": "FX-BAD"}, "reviewed_at": "garbage", "stale": False}
        s = rv.coverage([broken], None)
        self.assertEqual(s["included"], 1)
        self.assertIsNone(s["first"])
        self.assertTrue(rv.focus_matches(broken, "no_budget"))


def seed(path, items, *, region=REGION, year="2024", reviewed_at=RECENT, pending=()):
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS contract_assessments (contract_id TEXT PRIMARY KEY, category TEXT NOT NULL, "
                   "reviewed_at TEXT NOT NULL, summary TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS regional_queue (filters TEXT NOT NULL, position INTEGER NOT NULL, "
                   "contract_id TEXT NOT NULL, staged TEXT, done INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (filters, position))")
        filters = json.dumps({"region": region, "year": year}, sort_keys=True)
        start = db.execute("SELECT COALESCE(MAX(position), 0) FROM regional_queue WHERE filters = ?", (filters,)).fetchone()[0]
        for index, item in enumerate(items, start + 1):
            db.execute("INSERT OR REPLACE INTO contract_assessments VALUES (?,?,?,?)",
                       (item["contractId"], item["assessment"]["completion_evidence_category"], reviewed_at, json.dumps(item)))
            db.execute("INSERT INTO regional_queue VALUES (?,?,?,NULL,1)", (filters, index, item["contractId"]))
        for index, contract in enumerate(pending, start + len(items) + 1):
            db.execute("INSERT INTO regional_queue VALUES (?,?,?,NULL,0)", (filters, index, contract))


class PageCase(unittest.TestCase):
    items = None
    pending = ()

    def setUp(self):
        import streamlit as st
        from streamlit.testing.v1 import AppTest
        st.cache_data.clear()
        self.tmp = tempfile.TemporaryDirectory(dir=HERE)
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "regional.db"
        items = fixtures() if self.items is None else self.items
        import app
        signature = app.current_model_signature()
        for item in items:
            item["review_provenance"] = {"models": signature}      # current unless a test says otherwise
        for item in items:
            if item["contractId"] in {"FX-A1", "FX-CUE"}:
                item["assessment"]["assessment_version"] = "older-rules"   # the two out-of-date reviews
        seed(self.db, [i for i in items if i["contractId"] != "FX-ELSEWHERE"], pending=self.pending)
        seed(self.db, [i for i in items if i["contractId"] == "FX-ELSEWHERE"], region=OTHER_REGION, year="2022")
        patcher = patch.dict(os.environ, {"PROJECTWATCH_PHOTO_REVIEW_DB": str(self.db), "PROJECTWATCH_REVIEWER": "tester"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.at = AppTest.from_file(str(HERE / "app.py"), default_timeout=60)
        self.at.session_state["primary_navigation"] = "Regional review"
        self.render()
        self.at.selectbox(key="dashboard_region").set_value(REGION)   # regions are listed alphabetically
        self.render()

    def render(self):
        self.at.run()
        self.assertFalse(self.at.exception, [e.value for e in self.at.exception])
        return self.at

    def click(self, key):
        self.at.button(key=key).click()
        return self.render()

    def labels(self):
        return [b.label for b in self.at.button]

    def subheaders(self):
        return [s.value for s in self.at.subheader]

    def table(self):
        return next(d for d in self.at.dataframe if "Reviewer decision" in getattr(d.value, "columns", []))

    def metric(self, label):
        return next(m.value for m in self.at.metric if m.label == label)


class CoveragePageTests(PageCase):
    def test_page_leads_with_coverage_for_the_selected_region_and_year(self):
        text = all_text(self.at)
        self.assertEqual(self.subheaders()[0], "What these figures cover")
        self.assertIn(REGION, text)
        self.assertEqual(self.metric("Saved reviews included"), "8")        # the Region I review is not included
        self.assertEqual(self.metric("Saved between"), "16 Nov 2026")
        self.assertEqual(self.metric("Out of date"), "2")
        self.assertEqual(self.metric("Evidence unavailable"), "1")
        self.assertIn("not a census", text)
        self.assertIn("never reviewed, or whose review is still queued", text)

    def test_year_selection_changes_what_is_included_and_resets_the_filter(self):
        self.click("regional_show_flagged")
        self.assertEqual(self.at.session_state["regional_focus"], "flagged")
        self.at.selectbox(key="dashboard_region").set_value(OTHER_REGION)
        self.render()
        self.assertEqual(self.at.session_state["regional_focus"], "all")
        self.assertEqual(self.metric("Saved reviews included"), "1")
        self.assertIn("Small sample · 1 saved review", all_text(self.at))

    def test_small_sample_notice_names_the_threshold_as_a_convention(self):
        text = all_text(self.at)
        self.assertIn("Small sample · 8 saved reviews", text)
        self.assertIn("This threshold is a display convention", text)

    def test_queue_is_stated_when_recorded(self):
        self.assertIn("8 contracts reviewed · 0 still queued and not included", all_text(self.at))
        self.assertNotIn("1 contracts reviewed", all_text(self.at))

    def test_separate_definitions_for_reviewed_contractors_and_flagged(self):
        text = all_text(self.at)
        self.assertEqual(self.metric("Reviewed contracts"), "8")
        self.assertEqual(self.metric("Contractors"), "4")        # Alpha (two spellings), Beta, Gamma, Delta
        self.assertIn("1 more contract has no reported contractor", text)
        self.assertIn("not a finding of wrongdoing", text)
        self.assertIn("distinct reported contractor names", text)

    def test_section_order_puts_comparison_after_coverage(self):
        order = self.subheaders()
        for before, after in [("What these figures cover", "Reviewed contracts, contractors and flagged contracts"),
                              ("Reviewed contracts, contractors and flagged contracts", "Photo evidence categories"),
                              ("Photo evidence categories", "Recorded facts"),
                              ("Recorded facts", "Supporting contracts"),
                              ("Supporting contracts", "Contractor comparison"),
                              ("Contractor comparison", "Evidence highlights")]:
            self.assertLess(order.index(before), order.index(after), order)

    def test_missing_evidence_situations_are_listed_separately(self):
        text = all_text(self.at)
        self.assertIn("1 have no photos in the source record", text)
        self.assertIn("1 have photo analysis that failed (retry needed)", text)
        self.assertIn("none is evidence of unfinished work", text)


class DrilldownPageTests(PageCase):
    def listed(self):
        return set(self.table().value["Contract"])

    def test_default_lists_every_included_review(self):
        self.assertEqual(self.metric("Reviewed contracts"), str(len(self.listed())))
        self.assertNotIn("FX-ELSEWHERE", self.listed())

    def test_flagged_count_opens_exactly_the_flagged_contracts_with_visible_filter(self):
        flagged = int(self.metric("Flagged contracts"))
        self.click("regional_show_flagged")
        text = all_text(self.at)
        self.assertIn(f"Filter · Flagged contracts · {flagged:,} of 8 included saved reviews", text)
        self.assertEqual(len(self.listed()), flagged)
        self.assertTrue(set(self.table().value["Flags"]) <= set(range(1, 20)))
        self.assertIn("Clear filter", self.labels())
        self.click("regional_clear_focus")
        self.assertEqual(len(self.listed()), 8)
        self.assertNotIn("Clear filter", self.labels())

    def test_choosing_a_count_says_where_the_list_is(self):
        self.click("regional_show_flagged")
        self.assertTrue(any("listed under Supporting contracts" in str(toast.value) for toast in self.at.toast))
        self.assertIn("Filter active · Flagged contracts", all_text(self.at))
        self.assertIn("#supporting-contracts", all_text(self.at))
        self.click("regional_clear_focus")
        self.assertNotIn("Filter active", all_text(self.at))

    def test_every_category_row_opens_its_contracts_and_counts_add_up(self):
        buttons = [b for b in self.at.button if b.key and b.key.startswith("regional_cat_")]
        self.assertTrue(buttons)
        shown = 0
        for index in range(len(buttons)):
            label = [b for b in self.at.button if b.key == f"regional_cat_{index}"][0].label
            count = int(label.rsplit("·", 1)[1].split()[0].replace(",", ""))
            shown += count
            self.click(f"regional_cat_{index}")
            self.assertEqual(len(self.listed()), count, label)
            self.assertIn("showing", [b for b in self.at.button if b.key == f"regional_cat_{index}"][0].label)
        self.assertEqual(shown, 8)   # categories partition the included reviews

    def test_category_rows_name_their_kind_in_words_not_colour_alone(self):
        import evidence_language as language
        words = {style[0] for style in language.KIND_STYLE.values()}
        buttons = [b for b in self.at.button if b.key and b.key.startswith("regional_cat_")]
        self.assertTrue(buttons)
        for button in buttons:
            self.assertTrue(any(button.label.startswith(f"{word} · ") for word in words), button.label)
            self.assertRegex(button.label, r" · \d[\d,]* contracts?( · showing)?$")

    def test_status_and_missing_data_counts_open_their_records(self):
        self.click("regional_no_budget")
        self.assertEqual(self.listed(), {"FX-NOCONTRACTOR"})
        self.click("regional_no_completion")
        self.assertEqual(self.listed(), {"FX-NOCONTRACTOR"})
        self.click("regional_status_0")
        self.assertIn("Filter · Reported status", all_text(self.at))

    def test_selectbox_offers_the_same_counts_as_buttons(self):
        box = self.at.selectbox(key="regional_focus")
        self.assertTrue(any(o.startswith("Flagged contracts · ") or o.startswith("All included") for o in box.options))
        flagged = [o for o in box.options if o.startswith("Flagged contracts")]
        if flagged:
            box.set_value("flagged")
            self.render()
            self.assertIn(flagged[0].rsplit("· ", 1)[1], all_text(self.at))

    def test_stale_and_unavailable_counts_open_records(self):
        box = self.at.selectbox(key="regional_focus")
        box.set_value("stale")
        self.render()
        self.assertEqual(self.listed(), {"FX-A1", "FX-CUE"})
        self.at.selectbox(key="regional_focus").set_value("unavailable")
        self.render()
        self.assertEqual(self.listed(), {"FX-FAILED"})

    def test_text_alternative_table_and_download_exist(self):
        self.assertTrue(any(e.label == "Table view of evidence categories" for e in self.at.expander))
        self.assertEqual(len(self.at.get("download_button")), 1)


class ComparisonPageTests(PageCase):
    def comparison(self):
        return next(d for d in self.at.dataframe if "Ranking score" in getattr(d.value, "columns", []))

    def test_comparison_states_counts_formula_limits_and_timing_assumption_beside_the_table(self):
        text = all_text(self.at)
        self.assertRegex(text, r"4 contractors compared · 8 included contracts · \d+ scored · \d+ with No score")
        self.assertIn("Ranking score = total points ÷ √(number of scored contracts)", text)
        self.assertIn("not a measured construction delay", text)
        self.assertIn("says nothing about quality or conduct", text)
        self.assertIn("No score means", text)
        self.assertIn("It is not zero", text)

    def test_no_score_is_text_never_zero_and_unscored_are_not_averaged(self):
        frame = self.comparison().value
        gamma = frame[frame["Contractor"] == "Gamma Corp"].iloc[0]          # only a no-photo contract
        self.assertEqual(gamma["Scored"], 0)
        self.assertEqual(gamma["No score"], 1)
        self.assertEqual(gamma["Total points"], "No score")
        self.assertEqual(gamma["Ranking score"], "No score")
        self.assertEqual(gamma["Sample"], "No score")

    def test_small_samples_are_marked_and_counts_reconcile(self):
        frame = self.comparison().value
        self.assertTrue((frame.loc[(frame["Scored"] > 0) & (frame["Scored"] < rv.SMALL_SCORED), "Sample"] == "Small sample").all())
        self.assertEqual(int(frame["Included contracts"].sum()), 8)       # includes the holding row for no contractor
        self.assertEqual(int((frame["Scored"] + frame["No score"]).sum()), 8)
        self.assertIn("1 included contract has no reported contractor", all_text(self.at))

    def test_unknown_budget_is_counted_apart_from_zero_and_gaps(self):
        frame = self.comparison().value
        alpha = frame[frame["Contractor"].str.casefold().str.contains("alpha")].iloc[0]
        self.assertEqual(alpha["Included contracts"], 2)                   # two spellings are one contractor
        self.assertEqual(alpha["Budget gap not checkable"], 1)             # FX-A1 has no approved/award amounts
        self.assertEqual(alpha["Large ABC–award gaps"], 1)                 # FX-A2: 600,000 vs 500,000 = +20%
        delta = frame[frame["Contractor"] == "Delta Inc"].iloc[0]
        self.assertEqual(delta["Budget gap not checkable"], 2)

    def test_scoring_calculation_is_unchanged(self):
        from project_review import contractor_performance
        records = [e["record"] for e in (entry(i) for i in fixtures() if i["contractId"] != "FX-ELSEWHERE")]
        expected = {g["name"]: sorted((r["Contract"], r["Points"]) for r in g["contracts"]) for g in contractor_performance(records)}
        self.assertEqual(expected["Gamma Corp"], [("FX-NOPHOTO", None)])
        frame = self.comparison().value
        for group in contractor_performance(records):
            points = [r["Points"] for r in group["contracts"] if r["Points"] is not None]
            row = frame[frame["Contractor"] == group["name"]].iloc[0]
            self.assertEqual(row["Scored"], len(points))


class HighlightsPageTests(PageCase):
    def report(self, contract="FX-A2"):
        return {"summary": "x", "batch_summaries": [f"Alpha Builders · On time (+2 points). Fact about {contract}"],
                "completed_batches": 1, "total_batches": 1, "errors": [], "category_counts": {},
                "selected": [{"contract": contract, "text": f"Alpha Builders · On time (+2 points). Fact about {contract}"},
                             {"contract": "FX-GONE", "text": "A contract that is no longer included."}],
                "generated_for": 8}

    def test_facts_are_available_before_any_highlight_is_requested(self):
        self.assertIn("Generate evidence highlights", self.labels())
        text = all_text(self.at)
        self.assertIn("No highlights have been generated for these saved reviews yet.", text)
        self.assertIn("Recorded facts", self.subheaders())
        self.assertIn("Reported project budget · ₱", text)

    def test_highlights_link_each_fact_to_its_contract(self):
        with patch("project_review.summarize_regional_evidence", return_value=self.report()):
            self.click("regional_generate_highlights")
        text = all_text(self.at)
        self.assertIn("Fact about FX-A2", text)
        self.assertIn("Contract FX-GONE is no longer included", text)
        self.click("regional_hl_0")
        self.assertEqual(self.at.session_state["regional_focus"], "contract:FX-A2")
        self.assertEqual(set(self.table().value["Contract"]), {"FX-A2"})
        self.assertIn("Filter · Contract · FX-A2 · 1 of 8", all_text(self.at))

    def test_generated_highlights_are_saved_and_reload(self):
        with patch("project_review.summarize_regional_evidence", return_value=self.report()):
            self.click("regional_generate_highlights")
        with closing(sqlite3.connect(self.db)) as db:
            saved = db.execute("SELECT report FROM regional_highlights").fetchall()
        self.assertEqual(len(saved), 1)
        self.assertIn("generated_at", json.loads(saved[0][0]))
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(str(HERE / "app.py"), default_timeout=60)
        at.session_state["primary_navigation"] = "Regional review"
        at.run()
        at.selectbox(key="dashboard_region").set_value(REGION)
        at.run()
        self.assertIn("Fact about FX-A2", all_text(at))

    def test_failure_keeps_every_recorded_fact_and_explains_next_step(self):
        import requests
        before = (self.metric("Reviewed contracts"), self.metric("Flagged contracts"), len(self.table().value))
        with patch("project_review.summarize_regional_evidence", side_effect=requests.ConnectionError("down")):
            self.click("regional_generate_highlights")
        text = all_text(self.at)
        self.assertIn("Evidence highlights are unavailable.", text)
        self.assertIn("recorded facts, counts and scores above are unaffected", text)
        self.assertEqual((self.metric("Reviewed contracts"), self.metric("Flagged contracts"), len(self.table().value)), before)
        self.assertEqual(len(self.at.error), 1)                              # one real failure, nothing else red
        self.assertIn("Generate evidence highlights", self.labels())          # retry is the same action
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM regional_highlights").fetchone()[0], 0)

    def test_a_failed_run_does_not_claim_anything_was_generated(self):
        import requests
        with patch("project_review.summarize_regional_evidence", side_effect=requests.ConnectionError("down")):
            self.click("regional_generate_highlights")
        self.assertNotIn("Generated ", all_text(self.at))
        report = {**self.report(), "selected": [], "batch_summaries": [], "completed_batches": 0, "errors": ["Batch 1: x"]}
        with patch("project_review.summarize_regional_evidence", return_value=report):
            self.click("regional_generate_highlights")
        self.assertNotIn("Generated ", all_text(self.at))

    def test_model_returning_nothing_is_a_failure_not_an_empty_success(self):
        report = {**self.report(), "selected": [], "batch_summaries": [], "completed_batches": 0, "total_batches": 1,
                  "errors": ["Batch 1: unreadable"]}
        with patch("project_review.summarize_regional_evidence", return_value=report):
            self.click("regional_generate_highlights")
        self.assertIn("did not return any highlights", all_text(self.at))

    def test_older_highlights_without_links_say_so_instead_of_pretending(self):
        old = {"summary": "s", "batch_summaries": ["An older recorded fact."], "completed_batches": 1, "total_batches": 1,
               "errors": [], "category_counts": {}}
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS regional_highlights (filters TEXT PRIMARY KEY, report TEXT NOT NULL)")
            db.execute("INSERT INTO regional_highlights VALUES (?, ?)",
                       (json.dumps({"region": REGION, "year": "2024"}, sort_keys=True), json.dumps(old)))
        self.at.selectbox(key="dashboard_year_" + REGION).set_value("2024")
        self.render()
        text = all_text(self.at)
        self.assertIn("An older recorded fact.", text)
        self.assertIn("Supporting contracts were not recorded for these older highlights", text)

    def test_diagnostics_are_in_operator_details_only(self):
        with patch("project_review.summarize_regional_evidence", return_value={**self.report(), "errors": ["Batch 2: model offline"]}):
            self.click("regional_generate_highlights")
        details = [e for e in self.at.expander if e.label == "Operator details"]
        self.assertEqual(len(details), 1)
        inside = " ".join(str(t.value) for t in details[0].text)
        self.assertIn("Highlights model ·", inside)
        self.assertIn("Batch error · Batch 2: model offline", inside)
        self.assertNotIn("model offline", "\n".join(str(t.value) for t in self.at.text if t not in list(details[0].text)))


class EmptyRoutePageTests(unittest.TestCase):
    def render(self, items, **kw):
        import streamlit as st
        from streamlit.testing.v1 import AppTest
        st.cache_data.clear()
        tmp = tempfile.TemporaryDirectory(dir=HERE)
        self.addCleanup(tmp.cleanup)
        db = Path(tmp.name) / "r.db"
        if items is not None:
            seed(db, items, **kw)
        patcher = patch.dict(os.environ, {"PROJECTWATCH_PHOTO_REVIEW_DB": str(db)})
        patcher.start()
        self.addCleanup(patcher.stop)
        at = AppTest.from_file(str(HERE / "app.py"), default_timeout=60)
        at.session_state["primary_navigation"] = "Regional review"
        at.run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        return at

    def test_no_saved_reviews_routes_to_admin_regional_reviews(self):
        at = self.render(None)
        self.assertIn("No saved regional reviews yet.", all_text(at))
        at.button(key="regional_empty_to_admin").click()
        at.run()
        self.assertEqual(at.session_state["primary_navigation"], "Admin")
        self.assertEqual(at.session_state["admin_section"], "Regional reviews")

    def test_queued_but_unreviewed_work_is_named_in_the_empty_state(self):
        at = self.render([], pending=["Q-1", "Q-2"])
        text = all_text(at)
        self.assertIn("2 contracts are queued and waiting to be reviewed", text)
        self.assertEqual(len(at.error) + len(at.warning), 0)
        self.assertIn("Go to Admin · Regional reviews", [b.label for b in at.button])

    def test_one_review_is_a_small_sample_not_an_error(self):
        at = self.render([fixture("ONLY")])
        text = all_text(at)
        self.assertIn("Small sample · 1 saved review", text)
        self.assertEqual(len(at.error), 0)
        self.assertEqual(at.metric[0].value, "1")


if __name__ == "__main__":
    unittest.main()
