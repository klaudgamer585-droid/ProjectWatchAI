"""Tester evidence wording. Uses temp folders only; never reads or writes the real data/ tree."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import evidence_policy
import yolo_feedback
import yolo_test_app as tester

HERE = Path(__file__).parent
FORBIDDEN = ("% confidence", "certified", "verified complete", "YOLO detected", "YOLO found no")


class PredictionWordingTests(unittest.TestCase):
    def test_detection_uses_model_score_wording(self):
        text = tester.prediction_text("YOLO detected paved road surface (90% confidence) and "
                                      "unpaved road surface (41% confidence). The predictions may overlap. Review the overlay.")
        self.assertIn("paved road surface · model score 90% for a visible feature", text)
        self.assertIn("unpaved road surface · model score 41% for a visible feature", text)
        self.assertIn("may overlap", text)
        for phrase in FORBIDDEN:
            self.assertNotIn(phrase, text)

    def test_no_detection_is_not_absence(self):
        text = tester.prediction_text("YOLO found no bridge above 50% confidence.")
        self.assertEqual(text, "No target detected. This does not show that the feature is absent.")

    def test_failure_and_unknown_are_distinct(self):
        failed = tester.prediction_text("YOLO could not analyze this photo.")
        self.assertTrue(failed.startswith("Analysis failed"))
        self.assertNotEqual(failed, tester.prediction_text("YOLO found no road above 35% confidence."))
        self.assertTrue(tester.prediction_text(None).startswith("Analysis failed"))

    def test_source_note(self):
        self.assertEqual(tester.source_note(""), "Excluded from training until source is identified.")
        self.assertEqual(tester.source_note("   "), "Excluded from training until source is identified.")
        self.assertIn("held-out", tester.source_note("24CK0181"))


class TrainingStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=HERE)
        self.feedback = patch.object(yolo_feedback, "FEEDBACK", Path(self.tmp.name))
        self.feedback.start()
        self.run = yolo_feedback.feedback_folder("Roads") / "training-1"
        (self.run / "runs/corrected/weights").mkdir(parents=True)
        self.candidate = self.run / "runs/corrected/weights/best.pt"
        self.candidate.write_bytes(b"candidate checkpoint bytes")
        (self.run / "training.log").write_text("epoch 1\n", encoding="utf-8")

    def tearDown(self):
        self.feedback.stop()
        self.tmp.cleanup()

    def status(self, **kwargs):
        return tester.training_status(self.run, "Roads", failed=kwargs.get("failed", False), epochs=kwargs.get("epochs", 10))

    def test_in_progress_and_error(self):
        self.assertEqual(self.status(epochs=3)[0], "Training in progress")
        self.assertEqual(self.status(failed=True)[0], "Training error")

    def report(self, **changes):
        from evidence_policy import checkpoint_digest
        report = {"accepted": True, "detector": "Roads", "checkpoint_sha256": checkpoint_digest(self.candidate),
                  "test_contracts": 12, "positive_images": 25, "negative_images": 25, "precision": .95, "recall": .85,
                  "mask_map50": .6, "annotations_reviewed": True, "split_leakage": False}
        report.update(changes)
        self.candidate.with_suffix(".evaluation.json").write_text(json.dumps(report), encoding="utf-8")

    def test_ready_candidate_is_not_shown_as_promoted(self):
        (self.run / "ready").write_text("", encoding="utf-8")
        status, note = self.status()
        self.assertEqual(status, "Evaluation required")          # trained, saved, not evaluated
        self.assertIn("has not been evaluated", note)
        self.assertIn("The active model is unchanged", note)
        self.assertNotIn("promoted", status.lower())

    def test_evaluation_passed_is_candidate_ready_but_not_active(self):
        (self.run / "ready").write_text("", encoding="utf-8")
        self.report()
        status, note = self.status()
        self.assertEqual(status, "Candidate ready")
        self.assertIn("not the active model until it is promoted", note)
        self.assertNotEqual(status, "Approved active")

    def test_failed_or_unbound_evaluation_is_not_candidate_ready(self):
        (self.run / "ready").write_text("", encoding="utf-8")
        self.report(accepted=False)
        self.assertEqual(self.status()[0], "Evaluation did not pass")
        self.report(checkpoint_sha256="0" * 64)                   # report for different bytes
        self.assertEqual(self.status()[0], "Evaluation did not pass")
        self.report(detector="Bridges")
        self.assertEqual(self.status()[0], "Evaluation did not pass")
        self.report(precision=.1)                                 # accepted flag alone is not trusted
        self.assertEqual(self.status()[0], "Evaluation did not pass")

    def test_each_state_is_distinct_and_has_a_next_action(self):
        states = {name: label for name, (label, note) in tester.model_readiness.CANDIDATE_STATES.items()}
        self.assertEqual(len(set(states.values())), 4)
        for label, note in tester.model_readiness.CANDIDATE_STATES.values():
            self.assertIn("Next:", note)

    def test_approved_active_requires_same_hash_and_validated(self):
        (self.run / "ready").write_text("", encoding="utf-8")
        active = yolo_feedback.feedback_folder("Roads") / "active.pt"
        active.write_bytes(self.candidate.read_bytes())
        with patch.object(tester, "model_quality", return_value="review_only"):
            self.assertEqual(self.status()[0], "Evaluation required")  # copied but not approved
        with patch.object(tester, "model_quality", return_value="validated"):
            self.assertEqual(self.status()[0], "Approved active")
        active.write_bytes(b"a different active checkpoint")
        with patch.object(tester, "model_quality", return_value="validated"):
            self.assertNotEqual(self.status()[0], "Approved active")

    def test_training_completion_never_changes_the_active_model(self):
        (self.run / "ready").write_text("", encoding="utf-8")
        active = yolo_feedback.feedback_folder("Roads") / "active.pt"
        self.status()
        self.assertFalse(active.exists())                          # reading status creates or promotes nothing


class RenderedTesterTests(unittest.TestCase):
    """Runs the real tester script with AppTest; checkpoints and feedback live in a temp folder."""

    def setUp(self):
        from streamlit.testing.v1 import AppTest
        self.tmp = tempfile.TemporaryDirectory(dir=HERE)
        self.root = Path(self.tmp.name)
        self.patches = [patch.object(yolo_feedback, "FEEDBACK", self.root / "feedback")]
        for item in self.patches:
            item.start()
        self.at = AppTest.from_file(str(HERE / "yolo_test_app.py"), default_timeout=90)

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def text(self):
        parts = []
        for kind in ("markdown", "caption", "text", "info", "warning", "success", "error", "title"):
            parts += [str(element.value) for element in getattr(self.at, kind)]
        parts += [element.label for element in self.at.get("file_uploader")] if hasattr(self.at, "get") else []
        return "\n".join(parts)

    def run_with_checkpoint(self, checkpoint, quality=None):
        fake = lambda detector: {"Current model": checkpoint}
        extra = [patch.object(app, "photo_checkpoint_options", fake)]
        if quality:
            extra.append(patch.object(evidence_policy, "model_quality", return_value=quality))
        for item in extra:
            item.start()
        try:
            self.at.run()
        finally:
            for item in reversed(extra):
                item.stop()
        self.assertFalse(self.at.exception, [e.value for e in self.at.exception])
        return self.text()

    def test_missing_checkpoint_wording(self):
        text = self.run_with_checkpoint(self.root / "missing.pt")
        self.assertIn("Not installed — this detector cannot assess photos yet.", text)
        self.assertNotIn("review_only", text)

    def test_available_checkpoint_shows_plain_eligibility(self):
        checkpoint = self.root / "provisional.pt"
        checkpoint.write_bytes(b"fake checkpoint")
        text = self.run_with_checkpoint(checkpoint, quality="provisional")
        self.assertIn("Decision eligibility · Provisional detector", text)
        self.assertIn("No detector status certifies that a contract is complete.", text)
        self.assertNotRegex(text, r"·\s*provisional\b")  # raw stored value never shown as the label

    def test_training_view_marks_ready_candidate_as_needing_evaluation(self):
        run = yolo_feedback.feedback_folder("Roads") / "training-7"
        (run / "runs/corrected/weights").mkdir(parents=True)
        (run / "runs/corrected/weights/best.pt").write_bytes(b"candidate")
        (run / "training.log").write_text("Held-out mask mAP50: 0.41\n", encoding="utf-8")
        (run / "ready").write_text("", encoding="utf-8")
        self.at.query_params["view"] = "training"
        self.at.run()
        self.assertFalse(self.at.exception, [e.value for e in self.at.exception])
        text = self.text()
        self.assertIn("has not been evaluated", text)
        self.assertIn("Training alone never reaches Approved active", text)   # legend; the run itself is not approved
        progress = [element.proto.text for element in self.at.get("progress")]
        self.assertTrue(any(label.startswith("Evaluation required") for label in progress), progress)
        self.assertFalse(any(label.startswith("Approved active") for label in progress), progress)


if __name__ == "__main__":
    unittest.main()
