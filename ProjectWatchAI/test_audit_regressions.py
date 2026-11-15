import json
import os
import tempfile
import unittest
from copy import deepcopy
from contextlib import closing
from pathlib import Path
from threading import Event
from unittest.mock import patch, Mock

from contract_assessment import assess_contract, reported_day, NEUTRAL_REVIEW_CATEGORY
from evidence_policy import ASSESSMENT_VERSION, release_failures
from project_review import (review_project, request_review, ReviewError, REVIEW_SCHEMA,
                            summarize_regional_evidence, select_review_photos)


def photo(day, label=None, score=.9, detector="Roads", category="Roads", quality="validated"):
    return {"capturedAt": day, "url": "https://s3.transparency.dpwh.gov.ph/" + day,
            "category": category, "scope": "", "yolo": [{"detector": detector,
            "status": "detected" if label else "no detection", "quality": quality,
            "observation": f"YOLO detected {label} ({score:.0%} confidence).",
            "detections": [{"label": label, "confidence": score}] if label else []}]}


def record(photos=()):
    return {"contractId": "TEST", "status": "Completed", "category": "Roads",
            "description": "Road construction", "dates": {"Reported completion": "2025-01-01"},
            "budgetPHP": {}, "galleryPhotoCount": len(photos), "photos": list(photos)}


class RulesTests(unittest.TestCase):
    def test_stronger_same_day_scope_evidence_takes_precedence(self):
        r = record([photo("2025-01-02", "paved road surface"), photo("2025-01-02", "Under construction", .6, "Under construction")])
        a = assess_contract(r)
        self.assertEqual(a["completion_evidence_category"], "Completion evidence found · 1–7 days later")
        self.assertEqual(a["possible_unfinished_work_photos"], [])

    def test_stronger_scope_evidence_takes_precedence_in_same_image(self):
        p = photo("2025-01-02", "paved road surface", .99)
        p["yolo"] += photo("2025-01-02", "Under construction", .51, "Under construction")["yolo"]
        a = assess_contract(record([p]))
        self.assertEqual(a["possible_unfinished_work_photos"], [])
        self.assertEqual(a["completion_supporting_photo"], 1)

    def test_latest_blank_does_not_use_old_support_or_work(self):
        for label, detector in [("paved road surface", "Roads"), ("Under construction", "Under construction")]:
            a = assess_contract(record([photo("2025-01-02", label, detector=detector), photo("2025-01-03")]))
            self.assertIsNone(a["completion_supporting_photo"])
            self.assertEqual(a["possible_unfinished_work_photos"], [])
            self.assertEqual(a["completion_evidence_category"], NEUTRAL_REVIEW_CATEGORY)

    def test_slope_support(self):
        r = record([photo("2025-01-02", "slope protection", .95, "Slope protection")])
        r["description"] = "Road slope protection"
        self.assertEqual(assess_contract(r)["completion_supporting_photo"], 1)

    def test_raw_confidence_boundary(self):
        for value, expected in [(.346, None), (.35, 1)]:
            self.assertEqual(assess_contract(record([photo("2025-01-02", "paved road surface", value)]))["completion_supporting_photo"], expected)

    def test_unavailable_is_retry_not_neutral(self):
        p = photo("2025-01-02")
        p["yolo"][0]["status"] = "unavailable"
        a = assess_contract(record([p, p]))
        self.assertEqual(a["completion_evidence_category"], "Photo analysis unavailable · retry needed")
        self.assertTrue(a["retry_required"])

    def test_experimental_detection_counts_as_unfinished_evidence(self):
        a = assess_contract(record([photo("2025-01-02", "Under construction", .99, "Under construction", quality="review_only")]))
        self.assertEqual(a["possible_unfinished_work_photos"], [1])
        self.assertEqual(a["experimental_evidence_photos"], [1])
        self.assertIn("Possible unfinished work in photos on or after reported completion", a["flags"])

    def test_timezone_equivalence(self):
        self.assertEqual(reported_day("2025-01-01T23:30:00Z"), reported_day("2025-01-02T07:30:00+08:00"))

    def test_bad_release_metrics_are_rejected(self):
        self.assertTrue(release_failures({"precision": float("nan"), "recall": "high"}))

    def test_pre_completion_is_not_late_work(self):
        a = assess_contract(record([photo("2024-12-31", "Under construction", .99, "Under construction")]))
        self.assertEqual(a["completion_evidence_category"], "Photos precede reported completion")
        self.assertEqual(a["possible_unfinished_work_photos"], [])

    def test_budget_threshold_and_unknowns(self):
        for award, flags in [(1099999, 0), (1100000, 1), (None, 0)]:
            r = record([photo("2025-01-02", "paved road surface")])
            r["budgetPHP"] = {"Award amount": award, "Approved budget for the contract": 1000000}
            self.assertEqual(len(assess_contract(r)["flags"]), flags)

    def test_photo_selection_keeps_all_latest_day_views(self):
        photos = [photo(f"2025-01-{d:02d}") for d in range(1, 8)]
        photos += [{**photo("2025-01-07"), "url": f"https://example.org/{n}"} for n in range(6)]
        selected = select_review_photos(photos)
        self.assertEqual(sum(p["capturedAt"] == "2025-01-07" for p in selected), 7)


class NarrativeTests(unittest.TestCase):
    def test_manual_category_is_not_overruled_by_missing_detections(self):
        r = record()
        r["assessment"] = assess_contract(r)
        r["assessment"].update(manual_category_note="Reviewer inspected the scope",
                               completion_evidence_category="Completed · manual review")
        self.assertEqual(review_project(r)["conclusion"], "Manual review decision")

    def test_experimental_finding_can_support_a_discrepancy(self):
        r = record([photo("2025-01-02", "Under construction", .99, "Under construction", quality="review_only")])
        r["assessment"] = assess_contract(r)
        self.assertEqual(review_project(r)["conclusion"], "Possible discrepancy")

    def test_malformed_ollama_rejected(self):
        invalid = {"conclusion": "INVALID", "summary": 3, "photo_findings": "bad", "budget_observation": None, "evidence_gaps": False}
        response = Mock(status_code=200)
        response.json.return_value = {"message": {"content": json.dumps(invalid)}}
        with patch("project_review.requests.post", return_value=response), self.assertRaises(ReviewError):
            request_review([], "test", "test", REVIEW_SCHEMA)

    def test_contract_narrative_cannot_invent_photo_or_budget(self):
        r = record([photo("2025-01-02", "paved road surface")])
        r["budgetPHP"] = {"Award amount": 1050000, "Approved budget for the contract": 1000000}
        r["assessment"] = assess_contract(r)
        with patch("project_review.request_review", side_effect=AssertionError("Must not generate contract facts")):
            result = review_project(r, "test")
        self.assertEqual([p["photo"] for p in result["photo_findings"]], [1])
        self.assertIn("10%", result["budget_observation"])
        self.assertTrue(result["evidence_gaps"])

    def test_regional_empty_photo_payload_and_persistent_resume(self):
        r = record([photo("2025-01-02", "paved road surface")])
        r["assessment"] = assess_contract(r)
        r["photos"] = []  # Regional caller sends organized assessments, not all photos.
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            with patch.dict(os.environ, {"PROJECTWATCH_PHOTO_REVIEW_DB": str(Path(tmp) / "test.db")}):
                with patch("project_review.request_review", return_value={"ids": [0]}) as call:
                    first = summarize_regional_evidence([r], "test")
                    second = summarize_regional_evidence([r], "test")
                    self.assertEqual(call.call_count, 1)
                    self.assertEqual(first, second)
                    self.assertEqual(first["completed_batches"], 1)

    def test_invalid_highlight_id_is_not_rendered(self):
        r = record()
        r["assessment"] = assess_contract(r)
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            with patch.dict(os.environ, {"PROJECTWATCH_PHOTO_REVIEW_DB": str(Path(tmp) / "test.db")}):
                with patch("project_review.request_review", return_value={"ids": [999]}):
                    result = summarize_regional_evidence([r], "test")
                    self.assertEqual(result["completed_batches"], 0)
                    self.assertTrue(result["errors"])
                    self.assertIn("1 contracts", result["summary"])


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        import app
        self.app = app
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.path_patch = patch.object(app, "PHOTO_REVIEW_DB", Path(self.tmp.name) / "test.db")
        self.path_patch.start()
        self.signature_patch = patch.object(app, "current_model_signature", return_value="test-models")
        self.signature_patch.start()

    def tearDown(self):
        self.signature_patch.stop()
        self.path_patch.stop()
        self.tmp.cleanup()

    def test_history_and_manual_provenance(self):
        r = record([photo("2025-01-02", "paved road surface")])
        r["assessment"] = assess_contract(r)
        self.app.save_contract_assessments([r], require_saved=True)
        self.app.save_manual_category(r, "Completed · manual review", "Reviewed visible scope")
        a = self.app.assess_with_manual_review(r)
        self.assertIn("manual_review_provenance", a)
        changed = deepcopy(r)
        changed["description"] = "Changed scope"
        self.assertTrue(any("underlying evidence changed" in f for f in self.app.assess_with_manual_review(changed)["flags"]))
        with closing(self.app.open_assessment_db()) as db, db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM review_history").fetchone()[0], 1)

    def test_due_existing_contract_is_requeued(self):
        r = record()
        r["assessment"] = assess_contract(r)
        self.app.save_contract_assessments([r], require_saved=True)
        with closing(self.app.open_assessment_db()) as db, db:
            db.execute("INSERT INTO regional_queue VALUES ('key',0,'TEST',NULL,1)")
        job = {"stop": Event()}
        with patch.object(self.app, "regional_summary", return_value=({}, {"TEST": {}})), \
             patch.object(self.app, "fetch_contract_review_inputs", return_value=({}, r, [])), \
             patch.object(self.app, "summarize_contract", return_value=r) as run:
            self.app.run_regional_queue({}, "key", 1, 1, job)
            self.assertNotIn("error", job)
            run.assert_called_once()

    def test_cache_uses_changed_pixels_and_raw_detections(self):
        from PIL import Image
        weights = Path(self.tmp.name) / "test.pt"
        weights.write_bytes(b"fake checkpoint for mocked model")
        model = Mock()
        model.analyze_image.return_value = {"status": "detected", "observation": "Road", "detections": [{"label": "paved road surface", "confidence": .346}]}
        p = photo("2025-01-02")
        with patch.object(self.app, "load_photo_model", return_value=model):
            first, reused = self.app.saved_photo_observation("TEST", p, "Roads", weights, lambda: Image.new("RGB", (8, 8), "red"))
            self.assertFalse(reused)
            _, reused = self.app.saved_photo_observation("TEST", p, "Roads", weights, lambda: Image.new("RGB", (8, 8), "red"))
            self.assertTrue(reused)
            _, reused = self.app.saved_photo_observation("TEST", p, "Roads", weights, lambda: Image.new("RGB", (8, 8), "blue"))
            self.assertFalse(reused)
            self.assertEqual(model.analyze_image.call_count, 2)
            self.assertEqual(first["detections"][0]["confidence"], .346)

    def test_weak_small_evaluation_cannot_be_promoted(self):
        self.assertTrue(release_failures({"test_contracts": 3, "precision": .2, "recall": 1}))

    def test_failed_gallery_cannot_overwrite_saved_review(self):
        r = record([photo("2025-01-02", "paved road surface")])
        r["assessment"] = assess_contract(r)
        self.app.save_contract_assessments([r], require_saved=True)
        unavailable = record()
        unavailable["galleryPhotoCount"] = None
        self.app.summarize_contract("TEST", inputs=({}, unavailable, []))
        with closing(self.app.open_assessment_db()) as db:
            saved = json.loads(db.execute("SELECT summary FROM contract_assessments WHERE contract_id='TEST'").fetchone()[0])
        self.assertEqual(len(saved["photos"]), 1)


if __name__ == "__main__":
    unittest.main()
