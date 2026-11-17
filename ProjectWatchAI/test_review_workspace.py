"""Phase 5: the contract review workspace, rendered through the real Streamlit script (AppTest).

All storage is disposable: a temporary review database (PROJECTWATCH_PHOTO_REVIEW_DB) and a temporary
training-feedback folder. Nothing touches real reviews or the training store.
"""
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import evidence_language as language
import review_store as store
from contract_assessment import assess_contract
from test_audit_regressions import photo, record
from test_evidence_language import seed_review_db
from test_states_ui import all_text

HERE = Path(__file__).parent
GROUP = "Completion evidence found · 1–7 days later"   # raw stored value of the fixtures' automatic category
SCOPE = "FIXTURE-SCOPE"


def add_review(path, item, reviewed_at="2026-11-16T09:00:00+08:00"):
    item["assessment"] = assess_contract(item)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("INSERT OR REPLACE INTO contract_assessments VALUES (?,?,?,?)",
                   (item["contractId"], item["assessment"]["completion_evidence_category"], reviewed_at, json.dumps(item)))
    return item


def multi_record():
    """Newest day has both scope (60%) and a construction cue (55%); one older photo; one undated photo."""
    cue = photo("2025-01-02", "Under construction", .55, "Under construction")
    scope = photo("2025-01-02", "paved road surface", .6)
    scope["url"] += "/scope"
    cue["url"] += "/cue"
    older = photo("2024-12-20")
    older["url"] += "/older"
    undated = photo("not-a-date")
    undated["capturedAt"] = None
    undated["url"] += "/undated"
    item = record([scope, cue, older, undated])
    item.update(contractId="FIXTURE-MULTI", coordinates=None, description="Road with mixed evidence")
    return item


def flagged_record():
    """A completed contract whose newest photo shows a construction cue, with a budget flag too."""
    item = record([photo("2025-01-02", "Under construction", .9, "Under construction")])
    item.update(contractId="FIXTURE-FLAGS", coordinates=None, description="Road with flags")
    item["budgetPHP"] = {"Award amount": "1200000", "Approved budget for the contract": "1000000"}
    return item


def queued_record(contract):
    item = record([photo("2025-01-03", "paved road surface")])
    item.update(contractId=contract, coordinates=None, description=f"Queued {contract}")
    return item


class WorkspaceCase(unittest.TestCase):
    reviewer = "Test Reviewer"

    def setUp(self):
        from streamlit.testing.v1 import AppTest
        self.tmp = tempfile.TemporaryDirectory(dir=HERE)
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "fixture.db"
        seed_review_db(self.db)
        self.multi = add_review(self.db, multi_record())
        add_review(self.db, queued_record("FIXTURE-NEXT"), "2026-11-15T09:00:00+08:00")
        add_review(self.db, flagged_record(), "2026-11-14T09:00:00+08:00")
        self.feedback = Path(self.tmp.name) / "feedback"
        env = {"PROJECTWATCH_PHOTO_REVIEW_DB": str(self.db)}
        if self.reviewer is not None:
            env["PROJECTWATCH_REVIEWER"] = self.reviewer
        self.patches = [patch.dict(os.environ, env), patch("yolo_feedback.FEEDBACK", self.feedback)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        if self.reviewer is None:
            os.environ.pop("PROJECTWATCH_REVIEWER", None)
        self.at = AppTest.from_file(str(HERE / "app.py"), default_timeout=60)
        self.at.session_state["primary_navigation"] = "Admin"
        self.render()

    def render(self):
        self.at.run()
        self.assertFalse(self.at.exception, [e.value for e in self.at.exception])
        return self.at

    def open_contract(self, contract, group=GROUP):
        self.at.selectbox(key="saved_review_category").set_value(group)
        self.render()
        self.at.selectbox(key=f"saved_review_contract_{group}").set_value(contract)
        return self.render()

    def click(self, label):
        buttons = [b for b in self.at.button if b.label == label]
        self.assertEqual(len(buttons), 1, f"{label!r}: {[b.label for b in self.at.button]}")
        buttons[0].click()
        return self.render()

    def labels(self):
        return [b.label for b in self.at.button]

    def text(self):
        return all_text(self.at)

    def sql(self, query, *args):
        with closing(sqlite3.connect(self.db)) as db:
            return db.execute(query, args).fetchall()

    def fill(self, contract, category="Photos inconclusive", reason="Scope not clear in photo 1", reviewer=None):
        self.at.selectbox(key=f"manual_category_choice_{contract}").set_value(category)
        self.at.text_area(key=f"manual_category_reason_{contract}").input(reason)
        if reviewer is not None:
            self.at.text_input(key=f"manual_category_reviewer_{contract}").input(reviewer)


class HierarchyTests(WorkspaceCase):
    def test_sections_follow_the_planned_order(self):
        self.open_contract("FIXTURE-MULTI")
        headings = [str(h.value) for h in self.at.subheader]
        order = ["FIXTURE-MULTI", "Assessment and limitations", "Latest dated evidence", "Reviewer decision",
                 "Timeline and budget", "Provenance and history"]
        positions = [headings.index(name) for name in order]
        self.assertEqual(positions, sorted(positions))
        # Reported facts sit between identity and assessment.
        markdown = [str(m.value) for m in self.at.markdown]
        self.assertLess(markdown.index("**Reported facts**"), markdown.index("**Reported project status**"))

    def test_evidence_comes_before_the_decision_form_in_source_order(self):
        # Columns stack in source order on compact screens, so this order is the compact-screen order.
        self.open_contract("FIXTURE-MULTI")
        headings = [str(h.value) for h in self.at.subheader]
        self.assertLess(headings.index("Latest dated evidence"), headings.index("Reviewer decision"))

    def test_reported_facts_are_not_presented_as_findings(self):
        self.open_contract("FIXTURE-MULTI")
        text = self.text()
        self.assertIn("These are reported values, not photo findings.", text)
        self.assertIn(language.LIMITATION_TEXT, text)


class PhotoViewerTests(WorkspaceCase):
    def setUp(self):
        super().setUp()
        self.open_contract("FIXTURE-MULTI")

    def test_newest_dated_group_is_the_default_and_others_are_labelled(self):
        group = self.at.selectbox(key="review_group_FIXTURE-MULTI")
        self.assertEqual(group.value, "2025-01-02")
        labels = [str(option) for option in group.options]
        self.assertEqual(labels[0], "Newest dated group · 02 Jan 2025 · 2 photos")
        self.assertEqual(labels[1], "Earlier · 20 Dec 2024 · 1 photo")
        self.assertEqual(labels[2], "Date unknown · 1 photo")
        self.assertEqual(self.at.selectbox(key="review_photo_FIXTURE-MULTI").value, 1)

    def test_each_photo_shows_date_source_number_observations_and_eligibility_together(self):
        text = self.text()
        self.assertIn("Photo 1 · 02 Jan 2025", text)
        self.assertIn("Source · DPWH gallery via BetterGov (s3.transparency.dpwh.gov.ph)", text)
        self.assertIn("Roads: detected paved road surface (model score 60%)", text)
        self.assertIn("Detector eligibility · Validated detector", text)
        self.assertIn(language.MODEL_SCORE_NOTE, text)
        self.assertIn("Newest dated group", text)

    def test_original_image_and_overlay_controls_are_always_present(self):
        links = [str(b.url) for b in self.at.get("link_button") if "Open original photo" in str(b.label)]
        self.assertEqual(links, ["https://s3.transparency.dpwh.gov.ph/2025-01-02/scope"])
        view = self.at.radio(key="review_view_FIXTURE-MULTI")
        self.assertEqual(list(view.options), ["Original photo", "Detector overlay"])
        self.assertEqual(view.value, "Original photo")
        view.set_value("Detector overlay")
        self.render()
        text = self.text()
        self.assertIn("No overlay is available for this photo. The original is shown instead.", text)
        self.assertIn("Original photo 1", text)  # the original stays on screen

    def test_overlay_is_drawn_only_on_request_and_called_an_observation(self):
        from streamlit.testing.v1 import AppTest

        def harness():
            import streamlit as st
            from PIL import Image
            import review_workspace as workspace
            from test_review_workspace import multi_record
            from contract_assessment import assess_contract
            calls = st.session_state.setdefault("overlay_calls", [])

            def overlay(photo, number):
                calls.append(number)
                return {"Roads": Image.new("RGB", (8, 8), "red")}
            item = multi_record()
            item["assessment"] = assess_contract(item)
            services = workspace.Services(open_db=None, project_url=lambda c: "https://example.test/" + c, overlay=overlay)
            workspace.render_evidence(item, workspace.photo_groups(item["photos"]), services)

        at = AppTest.from_function(harness, default_timeout=60).run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertEqual(at.session_state["overlay_calls"], [])           # not computed for the original view
        at.radio(key="review_view_FIXTURE-MULTI").set_value("Detector overlay").run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertEqual(at.session_state["overlay_calls"], [1])
        self.assertIn("not verification marks", " ".join(str(image.proto) for image in at.get("imgs") or at.get("image")))

    def test_switching_groups_and_photos_keeps_context(self):
        self.at.selectbox(key="review_group_FIXTURE-MULTI").set_value("2024-12-20")
        self.render()
        self.assertEqual(self.at.selectbox(key="review_photo_FIXTURE-MULTI").value, 3)
        text = self.text()
        self.assertIn("Historical photo — excluded from the current latest-date assessment.", text)
        self.assertIn("Photo 3 · 20 Dec 2024", text)
        self.at.selectbox(key="review_group_FIXTURE-MULTI").set_value("unknown")
        self.render()
        self.assertEqual(self.at.selectbox(key="review_photo_FIXTURE-MULTI").value, 4)
        self.assertIn("Capture date unknown.", self.text())
        self.assertIn("Photo 4 · Date unknown", self.text())

    def test_previous_and_next_photo_move_within_the_group(self):
        self.assertTrue([b for b in self.at.button if b.label == "Previous photo"][0].disabled)
        self.click("Next photo")
        self.assertEqual(self.at.selectbox(key="review_photo_FIXTURE-MULTI").value, 2)
        self.assertTrue([b for b in self.at.button if b.label == "Next photo"][0].disabled)
        self.assertIn("Photo 2 · 02 Jan 2025", self.text())
        self.click("Previous photo")
        self.assertEqual(self.at.selectbox(key="review_photo_FIXTURE-MULTI").value, 1)

    def test_selected_photo_survives_leaving_and_coming_back(self):
        self.click("Next photo")
        self.at.selectbox(key="saved_review_contract_" + GROUP).set_value(SCOPE)
        self.render()
        self.assertIn("Photo 1 of 1", " ".join(self.at.selectbox(key=f"review_photo_{SCOPE}").options and
                                              [f"Photo 1 of 1"]))
        self.at.selectbox(key="saved_review_contract_" + GROUP).set_value("FIXTURE-MULTI")
        self.render()
        self.assertEqual(self.at.selectbox(key="review_photo_FIXTURE-MULTI").value, 2)

    def test_latest_date_conflict_shows_counterevidence_and_links_to_photos(self):
        text = self.text()
        self.assertIn("Latest dated photos show both scope evidence and construction cues.", text)
        self.assertIn("Scope detections: photo 1. Construction cues: photo 2.", text)
        # The photo being viewed carries its own counterevidence statement.
        self.assertIn("Counterevidence", text)
        self.assertIn("construction cues also appear in photo 2", text)
        # Both sides are linked: the scope photo and the cue photo (counterevidence) can each be opened.
        keys = [b.key for b in self.at.button if b.key]
        self.assertIn("saved_conflictS_FIXTURE-MULTI_1", keys)
        self.assertIn("saved_conflictC_FIXTURE-MULTI_2", keys)
        self.click_key("saved_conflictC_FIXTURE-MULTI_2")
        self.assertEqual(self.at.selectbox(key="review_photo_FIXTURE-MULTI").value, 2)
        self.assertIn("Photo 2 · 02 Jan 2025", self.text())
        self.click_key("saved_conflictS_FIXTURE-MULTI_1")
        self.assertEqual(self.at.selectbox(key="review_photo_FIXTURE-MULTI").value, 1)

    def click_key(self, key):
        self.at.button(key=key).click()
        return self.render()

    def test_a_flag_links_to_its_supporting_photo_date_and_amounts(self):
        group = "Possible unfinished work shown after reported completion"
        self.open_contract("FIXTURE-FLAGS", group)
        text = self.text()
        self.assertIn("Possible unfinished work in latest photos captured on or after reported completion", text)
        self.assertIn("Award amount · ₱1,200,000.00", text)       # the amounts behind the budget flag
        self.assertIn("Approved budget for the contract · ₱1,000,000.00", text)
        links = [b for b in self.at.button if b.key and b.key.startswith("saved_flagA") and b.key.endswith("_FIXTURE-FLAGS_1")]
        self.assertTrue(links, [b.key for b in self.at.button])
        self.assertEqual(links[0].label, "Photo 1 · 02 Jan 2025")  # the date travels with the link
        self.assertIn("Cited as a possible unfinished-work cue", text)  # and the photo says why it is cited
        self.click_key(links[0].key)
        self.assertEqual(self.at.selectbox(key="review_photo_FIXTURE-FLAGS").value, 1)


class DecisionFormTests(WorkspaceCase):
    def setUp(self):
        super().setUp()
        self.open_contract(SCOPE)

    def test_form_has_current_category_eligible_targets_reason_reviewer_and_one_primary(self):
        text = self.text()
        self.assertIn("Current category", text)
        choice = self.at.selectbox(key=f"manual_category_choice_{SCOPE}")
        self.assertIsNone(choice.value)  # nothing preselected
        self.assertNotIn(GROUP, choice.options)  # the current category is not a target
        self.assertEqual(self.at.text_input(key=f"manual_category_reviewer_{SCOPE}").value, "Test Reviewer")
        self.assertEqual([b.label for b in self.at.button if b.proto.type == "primary"], ["Save decision"])

    def test_missing_reason_blocks_saving_and_keeps_the_draft(self):
        self.at.selectbox(key=f"manual_category_choice_{SCOPE}").set_value("Photos inconclusive")
        self.click("Save decision")
        self.assertIn("Enter a reason for this category change.", self.text())
        self.assertNotIn("Choose a target category.", self.text())
        self.assertEqual(self.at.selectbox(key=f"manual_category_choice_{SCOPE}").value, "Photos inconclusive")
        self.assertEqual(self.sql("SELECT COUNT(*) FROM manual_decision_history"), [(0,)])
        self.assertNotIn("Next contract", self.labels())

    def test_missing_category_and_reviewer_are_flagged_under_their_fields(self):
        self.at.text_input(key=f"manual_category_reviewer_{SCOPE}").input("")
        self.at.text_area(key=f"manual_category_reason_{SCOPE}").input("Scope not clear in photo 1")
        self.click("Save decision")
        text = self.text()
        self.assertIn("Choose a target category.", text)
        self.assertIn("Enter your name so this decision is attributed to you.", text)
        self.assertNotIn("Enter a reason", text)
        self.assertEqual(self.sql("SELECT COUNT(*) FROM manual_decision_history"), [(0,)])

    def test_save_records_a_revision_and_shows_a_confirmation_read_back_from_storage(self):
        self.fill(SCOPE)
        self.click("Save decision")
        text = self.text()
        self.assertIn("Decision saved for FIXTURE-SCOPE", text)
        self.assertIn("Reason · Scope not clear in photo 1", text)
        self.assertIn("Recorded by Test Reviewer", text)
        self.assertIn("revision 1", text)
        self.assertIn("A later correction is saved as a new revision.", text)
        rows = self.sql("SELECT contract_id, category, reason, reviewer, previous_category FROM manual_decision_history")
        self.assertEqual(rows, [(SCOPE, "Photos inconclusive", "Scope not clear in photo 1", "Test Reviewer", GROUP)])
        # The contract stays on screen even though it moved to another group.
        self.assertEqual(self.at.selectbox(key="saved_review_category").value, "Photos inconclusive")
        self.assertEqual(self.at.selectbox(key="saved_review_contract_Photos inconclusive").value, SCOPE)
        # The overview attributes the decision to the reviewer who just saved it (not "older decision").
        self.assertIn("Reviewer · Test Reviewer", text)
        self.assertNotIn("were not recorded for this older decision", text)
        # The form starts empty again: a stale target would be ineligible, and the reason belongs to the old decision.
        self.assertIsNone(self.at.selectbox(key=f"manual_category_choice_{SCOPE}").value)
        self.assertEqual(self.at.text_area(key=f"manual_category_reason_{SCOPE}").value, "")
        self.assertNotIn("Undo", " ".join(self.labels()))  # no undo is promised or offered
        self.assertIn("there is no undo", text)

    def test_next_contract_is_separate_and_only_appears_after_a_successful_save(self):
        self.assertNotIn("Next contract", self.labels())
        self.fill(SCOPE)
        self.click("Save decision")
        self.assertIn("Next contract", self.labels())
        save_index = self.labels().index("Save decision")
        self.assertNotEqual(save_index, self.labels().index("Next contract"))
        # Saving did not move the reviewer: the decided contract is still shown.
        self.assertEqual(self.at.selectbox(key="saved_review_contract_Photos inconclusive").value, SCOPE)
        self.click("Next contract")
        self.assertEqual(self.at.selectbox(key="saved_review_category").value, GROUP)
        self.assertNotEqual(self.at.selectbox(key=f"saved_review_contract_{GROUP}").value, SCOPE)
        self.assertNotIn("Decision saved for FIXTURE-SCOPE", self.text())

    def test_persistence_failure_shows_an_error_keeps_the_draft_and_never_advances(self):
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("CREATE TRIGGER fail_manual BEFORE INSERT ON manual_categories "
                       "BEGIN SELECT RAISE(ABORT, 'simulated failure'); END")
        self.fill(SCOPE)
        self.click("Save decision")
        errors = " ".join(e.value for e in self.at.error).replace("\\", "")
        self.assertIn("Decision could not be saved.", errors)
        self.assertIn("Your draft is still here", errors)
        self.assertEqual(self.at.selectbox(key=f"manual_category_choice_{SCOPE}").value, "Photos inconclusive")
        self.assertEqual(self.at.text_area(key=f"manual_category_reason_{SCOPE}").value, "Scope not clear in photo 1")
        self.assertEqual(len(self.at.success), 0)
        self.assertNotIn("Next contract", self.labels())
        self.assertNotIn("Decision saved for", self.text())
        self.assertEqual(self.at.selectbox(key="saved_review_category").value, GROUP)  # did not move
        self.assertEqual(self.sql("SELECT COUNT(*) FROM manual_decision_history"), [(0,)])
        self.assertEqual(self.sql("SELECT category FROM contract_assessments WHERE contract_id=?", SCOPE), [(GROUP,)])
        # Storage recovers; the same draft saves without retyping.
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("DROP TRIGGER fail_manual")
        self.click("Save decision")
        self.assertIn("Decision saved for FIXTURE-SCOPE", self.text())
        self.assertEqual(self.sql("SELECT COUNT(*) FROM manual_decision_history"), [(1,)])

    def test_a_failed_second_save_does_not_leave_the_old_confirmation_or_next_button(self):
        self.fill(SCOPE)
        self.click("Save decision")
        self.assertIn("Next contract", self.labels())
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("CREATE TRIGGER fail_manual BEFORE INSERT ON manual_decision_history "
                       "BEGIN SELECT RAISE(ABORT, 'simulated failure'); END")
        self.fill(SCOPE, category="Completed · manual review", reason="Second look; photo 1 covers the full length")
        self.click("Save decision")
        self.assertIn("Decision could not be saved.", " ".join(e.value for e in self.at.error).replace("\\", ""))
        self.assertNotIn("Decision saved for", self.text())
        self.assertNotIn("Next contract", self.labels())
        # Browsing away and back does not resurrect the old confirmation either.
        self.at.selectbox(key="saved_review_contract_Photos inconclusive").set_value(
            [o for o in self.at.selectbox(key="saved_review_contract_Photos inconclusive").options if o != SCOPE][0]
        ) if len(self.at.selectbox(key="saved_review_contract_Photos inconclusive").options) > 1 else None
        self.render()
        self.assertNotIn("Decision saved for", self.text())

    def test_reviewer_text_is_shown_literally_not_as_markdown(self):
        self.fill(SCOPE, reviewer="**Ana** :red[x]")
        self.click("Save decision")
        captions = " ".join(str(c.value) for c in self.at.caption)
        self.assertNotIn("Recorded by **Ana**", captions)  # asterisks are escaped, so no bold is produced
        self.assertIn("Recorded by", captions)

    def test_drafts_survive_reruns_photo_switches_and_leaving_the_contract(self):
        self.fill(SCOPE, reason="Draft reason that is not saved yet")
        self.render()
        self.assertEqual(self.at.text_area(key=f"manual_category_reason_{SCOPE}").value, "Draft reason that is not saved yet")
        self.at.selectbox(key=f"saved_review_contract_{GROUP}").set_value("FIXTURE-MULTI")
        self.render()
        self.assertIsNone(self.at.selectbox(key="manual_category_choice_FIXTURE-MULTI").value)  # drafts are per contract
        self.at.selectbox(key=f"saved_review_contract_{GROUP}").set_value(SCOPE)
        self.render()
        self.assertEqual(self.at.selectbox(key=f"manual_category_choice_{SCOPE}").value, "Photos inconclusive")
        self.assertEqual(self.at.text_area(key=f"manual_category_reason_{SCOPE}").value, "Draft reason that is not saved yet")

    def test_a_second_decision_is_a_new_revision_with_history_shown(self):
        self.fill(SCOPE, reason="First look at the photos")
        self.click("Save decision")
        self.fill(SCOPE, category="Completed · manual review", reason="Second look; photo 1 covers the full length",
                  reviewer="Second Reviewer")
        self.click("Save decision")
        rows = self.sql("SELECT revision, category, reviewer, previous_category FROM manual_decision_history ORDER BY revision")
        self.assertEqual(rows, [(1, "Photos inconclusive", "Test Reviewer", GROUP),
                                (2, "Completed · manual review", "Second Reviewer", "Photos inconclusive")])
        history = [d for d in self.at.dataframe if "Decision" in list(getattr(d.value, "columns", []))]
        self.assertTrue(history)
        frame = history[0].value
        self.assertEqual(list(frame["Reviewer"]), ["Second Reviewer", "Test Reviewer"])
        self.assertEqual(list(frame["Reason"])[1], "First look at the photos")

    def test_stale_evidence_blocks_one_save_then_records_against_the_current_evidence(self):
        # A rerun finished in another session: the saved evidence is no longer what this page showed.
        newer = json.loads(self.sql("SELECT summary FROM contract_assessments WHERE contract_id=?", SCOPE)[0][0])
        newer["description"] = "Rerun found different evidence"
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("UPDATE contract_assessments SET summary=? WHERE contract_id=?", (json.dumps(newer), SCOPE))
        self.fill(SCOPE)
        self.click("Save decision")
        errors = " ".join(e.value for e in self.at.error)
        errors = errors.replace("\\", "")
        self.assertIn("The decision was not saved.", errors)
        self.assertIn("evidence for this contract changed after you opened it", errors)
        self.assertEqual(self.sql("SELECT COUNT(*) FROM manual_decision_history"), [(0,)])
        self.assertEqual(self.at.text_area(key=f"manual_category_reason_{SCOPE}").value, "Scope not clear in photo 1")
        self.assertNotIn("Next contract", self.labels())
        # The reviewer has now seen the current evidence; saving again records it against that evidence.
        self.click("Save decision")
        self.assertIn("Decision saved for FIXTURE-SCOPE", self.text())
        stored = self.sql("SELECT evidence_hash FROM manual_decision_history")[0][0]
        from evidence_policy import evidence_fingerprint
        self.assertEqual(stored, evidence_fingerprint(newer))


class AttributionTests(WorkspaceCase):
    reviewer = None

    def test_without_a_configured_reviewer_the_name_is_required(self):
        self.open_contract(SCOPE)
        self.assertEqual(self.at.text_input(key=f"manual_category_reviewer_{SCOPE}").value, "")
        self.fill(SCOPE)
        self.click("Save decision")
        self.assertIn("Enter your name so this decision is attributed to you.", self.text())
        self.assertEqual(self.sql("SELECT COUNT(*) FROM manual_decision_history"), [(0,)])
        self.at.text_input(key=f"manual_category_reviewer_{SCOPE}").input("Ana Reyes")
        self.click("Save decision")
        self.assertEqual(self.sql("SELECT reviewer FROM manual_decision_history"), [("Ana Reyes",)])
        self.assertEqual(self.sql("SELECT reviewer FROM manual_review_metadata"), [("Ana Reyes",)])


class OverridesAndStalenessTests(WorkspaceCase):
    def test_a_saved_decision_survives_a_rerun_of_the_automatic_review(self):
        import app
        self.open_contract(SCOPE)
        self.fill(SCOPE)
        self.click("Save decision")
        saved = json.loads(self.sql("SELECT summary FROM contract_assessments WHERE contract_id=?", SCOPE)[0][0])
        automatic = saved["assessment"]["automatic_evidence_category"]
        fresh = {key: value for key, value in saved.items() if key != "assessment"}
        with patch.dict(os.environ, {"PROJECTWATCH_PHOTO_REVIEW_DB": str(self.db)}), \
                patch.object(app, "PHOTO_REVIEW_DB", self.db):
            rerun = app.assess_with_manual_review(fresh)
        self.assertEqual(rerun["completion_evidence_category"], "Photos inconclusive")
        self.assertEqual(rerun["manual_category_note"], "Scope not clear in photo 1")
        self.assertEqual(rerun["automatic_evidence_category"], automatic)
        self.assertEqual(rerun["manual_review_provenance"]["reviewer"], "Test Reviewer")

    def test_evidence_changed_after_a_decision_is_flagged_and_decision_kept(self):
        self.open_contract(SCOPE)
        self.fill(SCOPE)
        self.click("Save decision")
        saved = json.loads(self.sql("SELECT summary FROM contract_assessments WHERE contract_id=?", SCOPE)[0][0])
        saved["description"] = "Evidence changed after the decision"
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("UPDATE contract_assessments SET summary=? WHERE contract_id=?", (json.dumps(saved), SCOPE))
        self.at.run()
        text = self.text()
        self.assertIn("Check this decision against the current evidence", text)
        self.assertIn("The evidence changed after this decision was recorded. The decision is kept", text)
        self.assertEqual(self.sql("SELECT category FROM manual_categories WHERE contract_id=?", SCOPE), [("Photos inconclusive",)])

    def test_stale_automatic_review_is_stated(self):
        self.open_contract(SCOPE)
        self.assertIn(language.STATES["stale"][2], self.text())  # fixtures predate the current model signature

    def test_legacy_decision_without_provenance_says_history_is_unavailable(self):
        self.open_contract("FIXTURE-MANUAL", "Completed · manual review")
        text = self.text()
        self.assertIn("recorded before revisions were kept", text)
        self.assertIn("recorded before reviewer and evidence details were kept", text)


class IndependentSavesTests(WorkspaceCase):
    """Category saving and training correction are separate forms with separate stores and outcomes."""

    def fake_image(self):
        from PIL import Image
        return Image.new("RGB", (64, 64), "grey")

    def training(self):
        outline = [[.1, .1], [.9, .1], [.9, .9], [.1, .9]]
        return (patch("road_surface.download_photo", return_value=self.fake_image()),
                patch("photo_outline.outline_photo", return_value=outline))

    def open_training(self):
        self.open_contract(SCOPE)
        prefix = f"training_{SCOPE}_1"
        self.at.button(key=f"{prefix}_load").click()
        self.render()
        return prefix

    def feedback_files(self):
        return sorted(p.name for p in self.feedback.rglob("*") if p.is_file())

    def test_there_is_no_combined_move_and_train_action(self):
        self.open_contract(SCOPE)
        joined = " ".join(self.labels()).lower()
        self.assertNotIn("move contract", joined)
        self.assertNotIn("go to next contract", joined)
        self.assertNotIn("also move this contract", self.text().lower())
        source = (HERE / "app.py").read_text(encoding="utf-8")
        for phrase in ("Save training example and move contract", "move_and_train", "Also move this contract"):
            self.assertNotIn(phrase, source)

    def test_saving_a_training_label_does_not_change_the_contract_decision(self):
        download, outline = self.training()
        before = self.sql("SELECT category, reviewed_at, summary FROM contract_assessments ORDER BY contract_id")
        with download, outline:
            prefix = self.open_training()
            self.render()
            self.at.selectbox(key=f"{prefix}_label_Roads").set_value("paved road surface") \
                if f"{prefix}_label_Roads" in [s.key for s in self.at.selectbox] else None
            labels = [s for s in self.at.selectbox if s.key and s.key.startswith(f"{prefix}_label_")]
            self.assertTrue(labels, [s.key for s in self.at.selectbox])
            labels[0].set_value(next(o for o in labels[0].options if o not in (None, "")))
            self.render()
            self.at.button(key=f"{prefix}_save").click()
            self.render()
        text = self.text()
        self.assertIn("Training example saved", text)
        self.assertIn("This contract's decision was not changed.", text)
        self.assertEqual(self.sql("SELECT category, reviewed_at, summary FROM contract_assessments ORDER BY contract_id"), before)
        self.assertEqual(self.sql("SELECT COUNT(*) FROM manual_decision_history"), [(0,)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM manual_categories WHERE contract_id=?", SCOPE), [(0,)])
        saved = [p for p in self.feedback.rglob("*.json")]
        self.assertEqual(len(saved), 1)
        self.assertEqual(json.loads(saved[0].read_text(encoding="utf-8"))["contractId"], SCOPE)

    def test_saving_a_decision_does_not_write_any_training_example(self):
        self.open_contract(SCOPE)
        self.fill(SCOPE)
        self.click("Save decision")
        self.assertEqual(self.feedback_files(), [])
        self.assertNotIn("Training example saved", self.text())

    def test_training_validation_is_independent_of_decision_validation(self):
        download, outline = self.training()
        with download, outline:
            prefix = self.open_training()
            self.at.button(key=f"{prefix}_save").click()
            self.render()
        text = self.text()
        self.assertTrue("Choose a label" in text or "label" in text.lower())
        self.assertNotIn("Enter a reason for this category change.", text)  # the decision form was not touched
        self.assertEqual(self.feedback_files(), [])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM manual_decision_history"), [(0,)])

    def test_unknown_provenance_is_saved_but_excluded_from_training(self):
        download, outline = self.training()
        with download, outline:
            prefix = self.open_training()
            labels = [s for s in self.at.selectbox if s.key and s.key.startswith(f"{prefix}_label_")]
            labels[0].set_value(next(o for o in labels[0].options if o not in (None, "")))
            self.render()
            self.at.text_input(key=f"{prefix}_source").input("")
            self.render()
            self.assertIn("Excluded from training until source is identified.", self.text())
            self.at.button(key=f"{prefix}_save").click()
            self.render()
        saved = [json.loads(p.read_text(encoding="utf-8")) for p in self.feedback.rglob("*.json")]
        self.assertEqual(len(saved), 1)
        self.assertNotIn("contractId", saved[0])
        self.assertIn("Training example saved", self.text())
        self.assertEqual(self.sql("SELECT COUNT(*) FROM manual_decision_history"), [(0,)])

    def test_training_save_failure_does_not_touch_the_decision_form_or_its_draft(self):
        self.open_contract(SCOPE)
        self.fill(SCOPE, reason="A decision reason that stays")
        download, outline = self.training()
        with download, outline, patch("yolo_feedback.save_correction", side_effect=OSError("disk full")):
            prefix = f"training_{SCOPE}_1"
            self.at.button(key=f"{prefix}_load").click()
            self.render()
            labels = [s for s in self.at.selectbox if s.key and s.key.startswith(f"{prefix}_label_")]
            labels[0].set_value(next(o for o in labels[0].options if o not in (None, "")))
            self.render()
            self.at.button(key=f"{prefix}_save").click()
            self.render()
        self.assertIn("could not be saved", " ".join(e.value for e in self.at.error))
        self.assertEqual(self.at.text_area(key=f"manual_category_reason_{SCOPE}").value, "A decision reason that stays")
        self.assertEqual(self.sql("SELECT COUNT(*) FROM manual_decision_history"), [(0,)])


class ExploreSharedRenderingTests(unittest.TestCase):
    """The Explore detail uses the same workspace and the same save path."""

    def setUp(self):
        import streamlit as st
        from streamlit.testing.v1 import AppTest
        from test_explore_ui import FakeApi
        st.cache_data.clear()
        self.tmp = tempfile.TemporaryDirectory(dir=HERE)
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "reviews.db"
        seed_review_db(self.db)
        saved = record([photo("2025-01-02", "paved road surface"), photo("2024-12-20")])
        saved.update(contractId="C-1", coordinates=None, status="On-Going")  # the live source says Completed
        add_review(self.db, saved)
        self.api = FakeApi()
        self.patches = [patch.dict(os.environ, {"PROJECTWATCH_PHOTO_REVIEW_DB": str(self.db),
                                                "PROJECTWATCH_REVIEWER": "Explore Reviewer"}),
                        patch("requests.get", side_effect=self.api)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.at = AppTest.from_file(str(HERE / "app.py"), default_timeout=60)
        self.at.session_state["primary_navigation"] = "Explore projects"
        self.at.session_state["live_contract"] = "C-1"
        self.at.run()
        self.assertFalse(self.at.exception, [e.value for e in self.at.exception])

    def test_explore_detail_uses_the_shared_sections_and_group_default(self):
        headings = [str(h.value) for h in self.at.subheader]
        for name in ("C-1", "Assessment and limitations", "Latest dated evidence", "Reviewer decision",
                     "Timeline and budget", "Provenance and history"):
            self.assertIn(name, headings)
        self.assertEqual(self.at.selectbox(key="review_group_C-1").value, "2025-01-02")
        self.assertIn("Live source gallery with detector observations (not saved)", [e.label for e in self.at.expander])

    def test_a_decision_saved_from_explore_is_the_same_revisioned_record(self):
        self.at.selectbox(key="manual_category_choice_C-1").set_value("Photos inconclusive")
        self.at.text_area(key="manual_category_reason_C-1").input("Checked photo 1 against the record")
        [b for b in self.at.button if b.label == "Save decision"][0].click()
        self.at.run()
        self.assertFalse(self.at.exception, [e.value for e in self.at.exception])
        with closing(sqlite3.connect(self.db)) as db:
            rows = db.execute("SELECT contract_id, reviewer, category FROM manual_decision_history").fetchall()
        self.assertEqual(rows, [("C-1", "Explore Reviewer", "Photos inconclusive")])
        self.assertIn("Decision saved for C-1", all_text(self.at))
        # No queue here, so no Next contract action is offered.
        self.assertNotIn("Next contract", [b.label for b in self.at.button])

    def test_changed_live_record_is_stated_not_hidden(self):
        text = all_text(self.at)
        self.assertIn("The live record differs from the saved review", text)
        self.assertIn("Reported status was “On-Going” when this review was saved and is “Completed” now.", text)


if __name__ == "__main__":
    unittest.main()
