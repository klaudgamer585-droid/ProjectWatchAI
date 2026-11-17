"""Phase 7: detector readiness (pure helpers) and the Admin Model status view.

Checkpoints are disposable fake files in a temp folder; nothing is trained, evaluated or promoted, and the
real checkpoint folders are never read (the rendered tests patch the detector table and the quality helpers).
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import model_readiness as mr
from evidence_policy import checkpoint_digest

HERE = Path(__file__).parent


def good_report(name, checkpoint, **changes):
    report = {"accepted": True, "detector": name, "checkpoint_sha256": checkpoint_digest(checkpoint),
              "test_contracts": 12, "positive_images": 25, "negative_images": 25, "precision": .95, "recall": .85,
              "mask_map50": .6, "annotations_reviewed": True, "split_leakage": False}
    report.update(changes)
    return report


class Disposable(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=HERE)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def checkpoint(self, name="best.pt", content=b"fake checkpoint"):
        path = self.root / name
        path.write_bytes(content)
        return path


class InspectTests(Disposable):
    def test_missing_checkpoint_is_missing_with_the_path_in_the_next_action(self):
        row = mr.inspect_detector("Roads", "road surfaces", self.root / "data/roads.pt", self.root, quality="validated")
        self.assertFalse(row["installed"])
        self.assertEqual((row["state"], row["readiness"], row["availability"]), ("missing", "Missing", "Not installed"))
        self.assertEqual(row["eligibility"], "Not installed")
        self.assertIn("data/roads.pt", row["next_action"])
        self.assertIn("reload", row["next_action"])
        self.assertIsNone(row["sha256"])                 # a quality value is ignored when there is no file
        self.assertIsNone(row["size_bytes"])

    def test_four_states_stay_distinct(self):
        path = self.checkpoint()
        rows = {q: mr.inspect_detector("Roads", "x", path, self.root, quality=q, digest="a" * 64)
                for q in ("review_only", "provisional", "validated")}
        rows["missing"] = mr.inspect_detector("Roads", "x", self.root / "nope.pt", self.root)
        self.assertEqual({r["readiness"] for r in rows.values()}, {"Missing", "Experimental", "Provisional", "Validated"})
        self.assertEqual(len({r["eligibility"] for r in rows.values()}), 4)
        self.assertEqual(len({r["next_action"] for r in rows.values()}), 4)
        self.assertEqual(rows["review_only"]["eligibility"], "Experimental · not validated")

    def test_experimental_wording_does_not_promise_gating_the_policy_does_not_have(self):
        text = mr.next_action("review_only", "x.pt")
        self.assertIn("unconfirmed", text)
        self.assertIn("can still set a photo assessment", text)
        self.assertNotIn("never support", text)

    def test_unknown_quality_on_an_installed_checkpoint_is_experimental_not_validated(self):
        row = mr.inspect_detector("Roads", "x", self.checkpoint(), self.root, quality=None, digest="b" * 64)
        self.assertEqual(row["state"], "review_only")
        row = mr.inspect_detector("Roads", "x", self.checkpoint(), self.root, quality="surprise", digest="b" * 64)
        self.assertEqual(row["state"], "review_only")

    def test_hash_size_and_approval_are_inspectable(self):
        path = self.checkpoint(content=b"123456789")
        row = mr.inspect_detector("Roads", "x", path, self.root, quality="validated", digest="c" * 64)
        self.assertEqual((row["sha256"], row["size_bytes"], row["approval_report"]), ("c" * 64, 9, False))
        path.with_suffix(".approval.json").write_text("{}", encoding="utf-8")
        self.assertTrue(mr.inspect_detector("Roads", "x", path, self.root, quality="validated")["approval_report"])

    def test_display_path_is_relative_inside_the_project_and_absolute_outside(self):
        self.assertEqual(mr.display_path(self.root / "a/b.pt", self.root), "a/b.pt")
        self.assertEqual(mr.display_path(Path("elsewhere.pt"), self.root), "elsewhere.pt")

    def test_summary_counts_and_text(self):
        path = self.checkpoint()
        rows = [mr.inspect_detector(n, "x", path, self.root, quality=q) for n, q in
                (("A", "validated"), ("B", "provisional"), ("C", "review_only"))]
        rows.append(mr.inspect_detector("D", "x", self.root / "none.pt", self.root))
        totals = mr.summary(rows)
        self.assertEqual(totals["text"], "3 of 4 detectors available · 1 validated")
        self.assertEqual((totals["provisional"], totals["experimental"], totals["missing"]), (1, 1, 1))

    def test_no_checkpoint_note_only_when_nothing_is_installed(self):
        missing = [mr.inspect_detector("A", "x", self.root / "a.pt", self.root)]
        title, body = mr.no_checkpoint_note(missing)
        self.assertEqual(title, "No detector checkpoints are installed.")
        self.assertIn("Photo analysis failed · retry needed", body)         # the label the review page actually shows
        self.assertIn("Rerun reviews", body)
        self.assertIn("not evidence either way", body)
        installed = missing + [mr.inspect_detector("B", "x", self.checkpoint(), self.root, quality="provisional")]
        self.assertIsNone(mr.no_checkpoint_note(installed))
        self.assertIsNone(mr.no_checkpoint_note([]))

    def test_the_failed_category_named_in_the_note_is_the_real_stored_category(self):
        from contract_assessment import assess_contract
        record = {"contractId": "X", "status": "Completed", "category": "Roads", "description": "Road",
                  "dates": {"Reported completion": "2025-01-01"}, "budgetPHP": {}, "galleryPhotoCount": 1,
                  "photos": [{"capturedAt": "2025-01-02", "url": "u", "category": "Roads", "scope": "",
                              "yolo": [{"detector": "Roads", "status": "unavailable", "quality": "missing",
                                        "observation": "Checkpoint unavailable", "detections": []}]}]}
        self.assertEqual(assess_contract(record)["completion_evidence_category"], mr.FAILED_CATEGORY)

    def test_release_commands_only_when_the_scripts_exist(self):
        present, absent = mr.release_commands(self.root)
        self.assertEqual((present, [s for s, _ in absent]), ([], ["evaluate_checkpoint.py", "promote_checkpoint.py"]))
        (self.root / "evaluate_checkpoint.py").write_text("", encoding="utf-8")
        present, absent = mr.release_commands(self.root)
        self.assertEqual([s for s, _ in present], ["evaluate_checkpoint.py"])
        self.assertEqual([s for s, _ in absent], ["promote_checkpoint.py"])
        present, _ = mr.release_commands(HERE)                              # this checkout ships both
        self.assertEqual(len(present), 2)


class CandidateStateTests(Disposable):
    def setUp(self):
        super().setUp()
        self.candidate = self.checkpoint("candidate.pt", b"candidate bytes")
        self.report_file = self.candidate.with_suffix(".evaluation.json")

    def state(self, **kw):
        return mr.candidate_state("Roads", self.candidate, **kw)

    def test_no_report_means_evaluation_required(self):
        self.assertEqual(self.state(), "evaluation_required")

    def test_unreadable_report_is_not_a_pass(self):
        self.report_file.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.state(), "evaluation_required")
        self.report_file.write_text("[]", encoding="utf-8")
        self.assertEqual(self.state(), "evaluation_failed")

    def test_a_hash_bound_passing_report_is_candidate_ready_and_not_active(self):
        self.report_file.write_text(json.dumps(good_report("Roads", self.candidate)), encoding="utf-8")
        self.assertEqual(self.state(), "candidate_ready")
        self.assertNotEqual(self.state(active=self.checkpoint("active.pt", b"other"), active_quality="validated"), "approved_active")

    def test_accepted_flag_alone_or_wrong_bytes_or_detector_fail(self):
        for changes in ({"accepted": False}, {"checkpoint_sha256": "0" * 64}, {"detector": "Bridges"},
                        {"recall": .1}, {"split_leakage": True}, {"annotations_reviewed": False}):
            self.report_file.write_text(json.dumps(good_report("Roads", self.candidate, **changes)), encoding="utf-8")
            self.assertEqual(self.state(), "evaluation_failed", changes)

    def test_approved_active_needs_identical_bytes_and_validated_quality(self):
        active = self.checkpoint("active.pt", b"candidate bytes")
        self.assertEqual(self.state(active=active, active_quality="validated"), "approved_active")
        self.assertEqual(self.state(active=active, active_quality="provisional"), "evaluation_required")
        active.write_bytes(b"different")
        self.assertEqual(self.state(active=active, active_quality="validated"), "evaluation_required")
        self.assertEqual(self.state(active=self.root / "gone.pt", active_quality="validated"), "evaluation_required")

    def test_states_have_distinct_labels_and_a_next_step_and_none_says_promoted_automatically(self):
        labels = [label for label, _ in mr.CANDIDATE_STATES.values()]
        self.assertEqual(labels, ["Evaluation required", "Candidate ready", "Evaluation did not pass", "Approved active"])
        for key, (label, note) in mr.CANDIDATE_STATES.items():
            self.assertIn("Next:", note)
            if key != "approved_active":
                self.assertIn("unchanged" if key != "candidate_ready" else "not the active model", note)
        self.assertNotIn("automatically", " ".join(n for _, n in mr.CANDIDATE_STATES.values()))

    def test_row_carries_the_candidate_state_but_reading_it_changes_nothing(self):
        active = self.checkpoint("active.pt", b"active bytes")
        before = sorted(p.name for p in self.root.iterdir())
        row = mr.inspect_detector("Roads", "x", active, self.root, quality="provisional", candidate=self.candidate)
        self.assertEqual(row["candidate"]["state"], "evaluation_required")
        self.assertFalse(row["candidate"]["evaluation_report"])
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), before)
        self.assertEqual(active.read_bytes(), b"active bytes")


def model_status_page():
    import app
    app.show_model_status()


class RenderedModelStatusTests(Disposable):
    def render(self, models, quality=None, candidate=None):
        import app
        import yolo_feedback
        from streamlit.testing.v1 import AppTest
        patches = [patch.object(app, "YOLO_MODELS", models),
                   patch.object(yolo_feedback, "FEEDBACK", self.root / "feedback"),
                   patch.object(app, "PROJECT_DIR", self.root),
                   patch.object(app, "model_quality", side_effect=lambda detector, path: (quality or {}).get(detector, "review_only")),
                   patch.object(app, "checkpoint_digest", side_effect=lambda path: ("%064x" % abs(hash(Path(path).name)))[:64])]
        if candidate is not None:
            patches.append(patch.object(app, "latest_candidate", side_effect=lambda detector: (candidate.get(detector), None) if detector in candidate else None))
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        at = AppTest.from_function(model_status_page, default_timeout=60)
        at.run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        return at

    def text(self, at):
        text = "\n".join(str(e.value) for kind in ("markdown", "caption", "text") for e in getattr(at, kind))
        return text.replace("\\", "")   # markdown escapes such as "installed\."

    def table(self, at, column):
        return next(d.value for d in at.dataframe if column in d.value.columns)

    def models(self, **paths):
        return {name: (path, {}, f"{name.lower()} features", 512) for name, path in paths.items()}

    def test_no_checkpoints_is_explained_without_alarm_and_names_the_next_action(self):
        at = self.render(self.models(Roads=self.root / "data/roads.pt", Bridges=self.root / "data/bridges.pt"))
        text = self.text(at)
        self.assertIn("0 of 2 detectors available · 0 validated", text)
        self.assertIn("No detector checkpoints are installed.", text)
        self.assertIn("Photo analysis failed · retry needed", text)
        self.assertEqual(len(at.error) + len(at.warning) + len(at.exception), 0)        # neutral, not an alert
        table = self.table(at, "Next action")
        self.assertEqual(set(table["Availability"]), {"Not installed"})
        self.assertEqual(set(table["Eligibility"]), {"Not installed"})
        self.assertTrue(all("Add the checkpoint file at data/" in text for text in table["Next action"]))

    def test_mixed_states_show_distinct_availability_eligibility_and_actions(self):
        files = {n: self.checkpoint(f"{n}.pt", n.encode()) for n in ("Roads", "Bridges", "Slope protection")}
        models = self.models(**files, **{"Water Provision and Storage": self.root / "absent.pt"})
        at = self.render(models, quality={"Roads": "validated", "Bridges": "provisional", "Slope protection": "review_only"})
        text = self.text(at)
        self.assertIn("3 of 4 detectors available · 1 validated", text)
        self.assertNotIn("No detector checkpoints are installed.", text)
        table = self.table(at, "Next action").set_index("Detector")
        self.assertEqual(table.loc["Roads", "Eligibility"], "Validated detector")
        self.assertEqual(table.loc["Bridges", "Eligibility"], "Provisional detector")
        self.assertEqual(table.loc["Slope protection", "Eligibility"], "Experimental · not validated")
        self.assertEqual(table.loc["Water Provision and Storage", "Eligibility"], "Not installed")
        self.assertEqual(len(set(table["Next action"])), 4)
        self.assertEqual(table.loc["Roads", "Availability"], "Installed")
        self.assertIn("No action needed", table.loc["Roads", "Next action"])
        self.assertNotIn("review_only", " ".join(table.astype(str).values.ravel()))     # raw stored value never shown

    def test_technical_details_expose_full_hash_path_size_and_release_scripts(self):
        files = {"Roads": self.checkpoint("Roads.pt", b"123456789")}
        at = self.render(self.models(**files), quality={"Roads": "validated"})
        details = next(e for e in at.expander if e.label == "Technical details and release checks")
        table = self.table(at, "SHA256")
        row = table.iloc[0]
        self.assertEqual(len(row["SHA256"]), 64)
        self.assertEqual(row["Checkpoint"], "Roads.pt")
        self.assertEqual(row["Size"], "9 bytes")
        self.assertEqual(row["Approval file"], "None")
        self.assertEqual(row["Trained candidate"], "None")
        self.assertIsNotNone(details)

    def test_release_commands_are_hidden_with_an_honest_note_when_scripts_are_absent(self):
        at = self.render(self.models(Roads=self.root / "none.pt"))        # PROJECT_DIR patched to an empty folder
        text = self.text(at)
        self.assertIn("evaluate_checkpoint.py is not present in this checkout", text)
        self.assertIn("promote_checkpoint.py is not present in this checkout", text)
        self.assertEqual(len(at.get("code")), 0)

    def test_release_commands_appear_when_the_scripts_exist(self):
        (self.root / "evaluate_checkpoint.py").write_text("", encoding="utf-8")
        (self.root / "promote_checkpoint.py").write_text("", encoding="utf-8")
        at = self.render(self.models(Roads=self.root / "none.pt"))
        code = " ".join(str(c.value) for c in at.get("code"))
        self.assertIn("python evaluate_checkpoint.py", code)
        self.assertIn("python promote_checkpoint.py", code)

    def test_a_trained_candidate_reads_evaluation_required_not_promoted(self):
        active = self.checkpoint("Roads.pt", b"active")
        candidate = self.checkpoint("cand.pt", b"candidate")
        at = self.render(self.models(Roads=active), quality={"Roads": "provisional"}, candidate={"Roads": candidate})
        table = self.table(at, "Trained candidate").iloc[0]
        self.assertEqual(table["Trained candidate"], "Evaluation required")
        self.assertEqual(table["Evaluation report"], "None")
        self.assertEqual(self.table(at, "Next action").iloc[0]["Eligibility"], "Provisional detector")   # active model unchanged
        candidate.with_suffix(".evaluation.json").write_text(json.dumps(good_report("Roads", candidate)), encoding="utf-8")
        at = self.render(self.models(Roads=active), quality={"Roads": "provisional"}, candidate={"Roads": candidate})
        self.assertEqual(self.table(at, "Trained candidate").iloc[0]["Trained candidate"], "Candidate ready")
        self.assertEqual(self.table(at, "Next action").iloc[0]["Eligibility"], "Provisional detector")


if __name__ == "__main__":
    unittest.main()
