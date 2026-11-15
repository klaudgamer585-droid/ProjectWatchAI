"""Evaluate held-out presence, split integrity and release eligibility. Never trains."""
import argparse
import json
import re
from pathlib import Path
from PIL import Image
from evidence_policy import checkpoint_digest, release_failures, fingerprint


def evaluate(detector, checkpoint, job, annotations_reviewed=False):
    from app import YOLO_MODELS
    from road_surface import PhotoSegmentationModel
    checkpoint, job = Path(checkpoint), Path(job)
    rows = json.loads((job / "annotations.json").read_text(encoding="utf-8"))
    assignments, images, leakage = {}, {}, False
    for row in rows:
        split = row["split"]
        if assignments.setdefault(row["contractId"], split) != split:
            leakage = True
        with Image.open(job / "candidates" / row["file"]) as photo:
            from hashlib import sha256
            photo = photo.convert("RGB")
            photo.thumbnail((960, 960))
            digest = sha256(photo.tobytes()).hexdigest()
        if images.setdefault(digest, split) != split:
            leakage = True
    tests = [r for r in rows if r["split"] == "test"]
    _, labels, _, size = YOLO_MODELS[detector]
    model = PhotoSegmentationModel(str(checkpoint), labels, size)
    tp = fp = tn = fn = 0
    errors = []
    for row in tests:
        with Image.open(job / "candidates" / row["file"]) as photo:
            result = model.analyze_image(photo.convert("RGB"))
        predicted, positive = bool(result["detections"]), bool(row["polygons"])
        tp += predicted and positive
        fp += predicted and not positive
        tn += not predicted and not positive
        fn += not predicted and positive
        if predicted != positive:
            errors.append({"file": row["file"], "contract": row["contractId"], "kind": "FP" if predicted else "FN"})
    log = (job / "training.log").read_text(encoding="utf-8", errors="replace")
    match = re.search(r"Held-out mask mAP50: ([0-9.]+)", log)
    trained = job / "runs/corrected/weights/best.pt"
    matching = trained.is_file() and checkpoint_digest(trained) == checkpoint_digest(checkpoint)
    report = {"detector": detector, "checkpoint_sha256": checkpoint_digest(checkpoint),
              "annotation_manifest_sha256": fingerprint(rows), "evaluation_job": str(job.resolve()),
              "test_contracts": len({r["contractId"] for r in tests}),
              "positive_images": tp + fn, "negative_images": tn + fp,
              "precision": tp / (tp + fp) if tp + fp else 0, "recall": tp / (tp + fn) if tp + fn else 0,
              "mask_map50": float(match[1]) if match and matching else 0,
              "mask_metric_source": "matching training evaluation log; presence rerun separately",
              "annotations_reviewed": annotations_reviewed, "split_leakage": leakage,
              "tp": tp, "fp": fp, "tn": tn, "fn": fn, "errors": errors}
    report["release_failures"] = release_failures(report)
    report["accepted"] = not report["release_failures"]
    checkpoint.with_suffix(".evaluation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detector", required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--annotations-reviewed", action="store_true",
                        help="Only set after independently reviewing held-out labels for this detector definition")
    args = parser.parse_args()
    print(json.dumps(evaluate(args.detector, args.checkpoint, args.job, args.annotations_reviewed), indent=2))
