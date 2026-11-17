"""Training correction is its own form: independent validation, outcome and storage.

All writes go to a temporary feedback folder; the real data/ tree is never touched.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import photo_outline
import road_surface
import training_correction as tc
import yolo_feedback

HERE = Path(__file__).parent
CLASSES = {"paved road surface": "paved road surface", "unpaved road surface": "unpaved road surface"}
POINTS = [[.1, .1], [.9, .1], [.5, .9]]
PREFIX = "training_TEST-1_1"

HARNESS = f"""
import sys
sys.path.insert(0, {str(HERE)!r})
import streamlit as st
import training_correction as tc

record = {{"contractId": "TEST-1", "photos": [
    {{"url": "https://s3.transparency.dpwh.gov.ph/one", "capturedAt": "2025-01-02"}},
    {{"url": "https://s3.transparency.dpwh.gov.ph/two", "capturedAt": "2025-01-03"}}]}}
tc.render_training_correction(
    record, st.session_state.get("photo_number", 1), detectors=("Roads",),
    classes_for=lambda detector: {{"paved road surface": "paved road surface", "unpaved road surface": "unpaved road surface"}})
"""


class ValidationTests(unittest.TestCase):
    def check(self, label, points, source, **kwargs):
        return tc.validate_correction("Roads", label, points, source, classes=CLASSES, **kwargs)

    def test_no_target_needs_no_outline(self):
        self.assertEqual(self.check(None, [], "24CK0181"), {})

    def test_label_needs_a_real_outline(self):
        self.assertIn("at least three points", self.check("paved road surface", [], "24CK0181")["outline"])
        self.assertIn("invalid point", self.check("paved road surface", [[0, 0], [1, 1], [2, 2]], "A1")["outline"])
        small = [[.1, .1], [.11, .1], [.1, .11]]
        self.assertIn("larger region", self.check("paved road surface", small, "A1", photo=Image.new("RGB", (64, 64)))["outline"])
        self.assertEqual(self.check("paved road surface", POINTS, "A1", photo=Image.new("RGB", (64, 64))), {})

    def test_unknown_label_is_rejected(self):
        self.assertIn("label", self.check("bridge", POINTS, "A1"))

    def test_source_may_be_empty_but_not_malformed(self):
        self.assertEqual(self.check(None, [], ""), {})  # saved, excluded from training
        self.assertIn("source", self.check(None, [], "A1, B2"))
        self.assertIn("source", self.check(None, [], "A*1"))

    def test_outline_authority_is_shared_with_save_correction(self):
        photo = Image.new("RGB", (64, 64))
        with self.assertRaises(ValueError) as caught:
            with tempfile.TemporaryDirectory() as folder, patch.object(yolo_feedback, "FEEDBACK", Path(folder)):
                yolo_feedback.save_correction("Roads", "d", photo, "paved road surface", [[0, 0]])
        self.assertEqual(str(caught.exception), self.check("paved road surface", [[0, 0]], "")["outline"])

    def test_tester_uses_the_shared_helpers(self):
        import yolo_test_app
        self.assertIs(yolo_test_app.source_id_problem, tc.source_id_problem)
        self.assertIs(yolo_test_app.source_note, tc.source_note)
        self.assertEqual(yolo_test_app.EXCLUDED_FROM_TRAINING, tc.EXCLUDED_FROM_TRAINING)


class SourceGuardTests(unittest.TestCase):
    def test_module_cannot_touch_decisions(self):
        source = (HERE / "training_correction.py").read_text(encoding="utf-8").casefold()
        for forbidden in ("manual_categories", "contract_assessments", "save_manual", "review_store",
                          "import app", "from app ", "move contract", "go to next contract", "advance"):
            self.assertNotIn(forbidden, source)

    def test_one_primary_action(self):
        source = (HERE / "training_correction.py").read_text(encoding="utf-8")
        self.assertEqual(source.count('type="primary"'), 1)


class RenderedFormTests(unittest.TestCase):
    def setUp(self):
        from streamlit.testing.v1 import AppTest
        self.tmp = tempfile.TemporaryDirectory(dir=HERE)
        self.root = Path(self.tmp.name)
        self.points = POINTS
        self.patches = [
            patch.object(yolo_feedback, "FEEDBACK", self.root / "feedback"),
            patch.object(road_surface, "download_photo", lambda url: Image.new("RGB", (64, 64), "gray")),
            patch.object(photo_outline, "outline_photo", lambda photo, key, initial: self.points),
        ]
        for item in self.patches:
            item.start()
        self.at = AppTest.from_string(HARNESS, default_timeout=60)

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def render(self):
        self.at.run()
        self.assertFalse(self.at.exception, [e.value for e in self.at.exception])
        return self.at

    def text(self):
        parts = []
        for kind in ("markdown", "caption", "text", "success", "error"):
            parts += [str(element.value) for element in getattr(self.at, kind)]
        return "\n".join(parts).replace("\\", "")

    def load(self):
        self.render()
        self.at.button(key=f"{PREFIX}_load").click()
        return self.render()

    def saved_files(self):
        folder = yolo_feedback.feedback_folder("Roads")
        return sorted(folder.glob("*.json")) if folder.is_dir() else []

    def choose(self, label="paved road surface"):
        self.at.selectbox(key=f"{PREFIX}_label_Roads").set_value(label)
        return self.render()

    def save(self):
        self.at.button(key=f"{PREFIX}_save").click()
        return self.render()

    def test_form_explains_it_does_not_change_the_decision(self):
        self.render()
        text = self.text()
        self.assertIn("Training correction", text)
        self.assertIn("does not change this contract's category or decision", text)
        self.assertEqual([b.label for b in self.at.button], ["Load photo for labeling"])

    def test_save_writes_correction_with_contract_and_confirms(self):
        self.load()
        self.choose()
        self.save()
        files = self.saved_files()
        self.assertEqual(len(files), 1)
        record = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual((record["contractId"], record["label"]), ("TEST-1", "paved road surface"))
        text = self.text()
        self.assertIn("Training example saved for photo 1", text)
        self.assertIn("This contract's decision was not changed.", text)
        self.assertIn("Eligible for training once checks pass", text)
        self.assertEqual(len([b for b in self.at.button if b.proto.type == "primary"]), 1)

    def test_cleared_source_is_saved_but_excluded(self):
        self.load()
        self.choose()
        self.at.text_input(key=f"{PREFIX}_source").input("")
        self.render()
        self.assertIn(tc.EXCLUDED_FROM_TRAINING, self.text())
        self.save()
        files = self.saved_files()
        self.assertEqual(len(files), 1)
        self.assertNotIn("contractId", json.loads(files[0].read_text(encoding="utf-8")))
        self.assertIn(tc.EXCLUDED_FROM_TRAINING, self.text())
        self.assertIn("Training example saved", self.text())

    def test_malformed_source_blocks_only_this_form(self):
        self.load()
        self.choose()
        self.at.text_input(key=f"{PREFIX}_source").input("A1, B2")
        self.render()
        self.assertIn("Enter one contract or project ID", self.text())
        self.assertTrue(self.at.button(key=f"{PREFIX}_save").disabled)
        self.assertEqual(self.saved_files(), [])

    def test_missing_outline_is_explained_and_nothing_is_written(self):
        self.points = []
        self.load()
        self.choose()
        self.save()
        self.assertIn("Outline the correct region with at least three points", self.text())
        self.assertEqual(self.saved_files(), [])
        self.assertNotIn("Training example saved", self.text())
        self.assertEqual(self.at.selectbox(key=f"{PREFIX}_label_Roads").value, "paved road surface")

    def test_label_not_chosen_is_explained(self):
        self.load()
        self.save()
        self.assertIn(tc.LABEL_NOT_CHOSEN, self.text())
        self.assertEqual(self.saved_files(), [])

    def test_no_target_visible_saves_without_outline(self):
        self.points = []
        self.load()
        self.choose("")
        self.save()
        files = self.saved_files()
        self.assertEqual(len(files), 1)
        self.assertIsNone(json.loads(files[0].read_text(encoding="utf-8"))["label"])
        self.assertIn("No target visible", self.text())

    def test_save_failure_keeps_the_draft_and_shows_no_success(self):
        self.load()
        self.choose()
        with patch.object(yolo_feedback, "save_correction", side_effect=OSError("disk full")):
            self.save()
        errors = " ".join(e.value for e in self.at.error).replace("\\", "")
        self.assertIn("The training example could not be saved.", errors)
        self.assertIn("Nothing was saved. Your label and outline are still here.", errors)
        self.assertIn("disk full", errors)
        self.assertEqual(self.at.selectbox(key=f"{PREFIX}_label_Roads").value, "paved road surface")
        self.assertEqual(len(self.at.success), 0)
        self.assertEqual(self.saved_files(), [])
        self.save()  # retry succeeds with the same draft
        self.assertEqual(len(self.saved_files()), 1)

    def test_draft_survives_switching_photos_and_back(self):
        self.load()
        self.choose("unpaved road surface")
        self.at.text_input(key=f"{PREFIX}_source").input("OTHER-9")
        self.render()
        self.at.session_state["photo_number"] = 2
        self.render()
        self.assertNotIn(f"{PREFIX}_label_Roads", [s.key for s in self.at.selectbox])  # photo 2 is a separate draft
        self.at.session_state["photo_number"] = 1
        self.render()
        self.assertEqual(self.at.selectbox(key=f"{PREFIX}_label_Roads").value, "unpaved road surface")
        self.assertEqual(self.at.text_input(key=f"{PREFIX}_source").value, "OTHER-9")
        self.assertEqual(self.saved_files(), [])

    def test_save_does_not_touch_any_review_database(self):
        db = self.root / "review.db"
        with patch.dict("os.environ", {"PROJECTWATCH_PHOTO_REVIEW_DB": str(db)}):
            self.load()
            self.choose()
            self.save()
        self.assertFalse(db.exists())  # the form has no path to the decision store
        self.assertEqual(len(self.saved_files()), 1)


if __name__ == "__main__":
    unittest.main()
