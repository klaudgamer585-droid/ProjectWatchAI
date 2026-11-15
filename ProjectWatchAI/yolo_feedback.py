"""Persist local photo corrections and train a separate YOLO candidate."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from shutil import copyfile
from tempfile import mkdtemp
from uuid import uuid4

from PIL import Image, ImageDraw


PROJECT = Path(__file__).resolve().parent
FEEDBACK = PROJECT / "data/yolo_feedback"
UNDER_CONSTRUCTION_TRAINING = "Under construction"
UNFINISHED_TARGETS = {
    "Roads": ("Road under construction", "unfinished road work", "road_work_stage"),
    "Bridges": ("Bridge under construction", "unfinished bridge work", "bridge_work_stage"),
    "Flood Control and Drainage": ("Flood control under construction", "unfinished flood control or drainage work", "flood_work_stage"),
    "Water Provision and Storage": ("Water system under construction", "unfinished water system work", "water_work_stage"),
}
DATASETS = {
    "Roads": ("yolo_surface", "road_surface"),
    "Slope protection": ("yolo_slope_protection", "slope_protection"),
    "Bridges": ("yolo_bridge", "bridge"),
    "Buildings and Facilities": ("yolo_building", "building_structure"),
    "Building under construction": ("yolo_building_stage", "building_work_stage"),
    UNDER_CONSTRUCTION_TRAINING: ("yolo_building_stage", "building_work_stage"),
    "Temporary scaffolding": ("yolo_scaffolding", "scaffolding"),
    "Construction equipment": ("yolo_equipment", "construction_equipment"),
    "Flood Control and Drainage": ("yolo_flood", "flood_channel"),
    "Riverbank protection": ("yolo_riverbank", "riverbank_protection"),
    "Water Provision and Storage": ("yolo_water", "water"),
    "Stainless steel water tanks": ("yolo_water_stainless", "stainless_water_tank"),
    "Street drain grates": ("yolo_street_drain", "street_drain"),
    **{detector: (f"yolo_{dataset}", dataset) for detector, _, dataset in UNFINISHED_TARGETS.values()},
}


def feedback_folder(detector):
    if detector == UNDER_CONSTRUCTION_TRAINING:
        return FEEDBACK / "yolo_under_construction"
    return FEEDBACK / DATASETS[detector][0]


def active_weights(detector, baseline):
    weights = feedback_folder(detector) / "active.pt"
    return weights if weights.is_file() else baseline


def has_training_dataset(detector):
    directory, name = DATASETS[detector]
    return (PROJECT / f"{name}_annotations.json").is_file() and (
        PROJECT / "data" / directory / "candidates"
    ).is_dir()


def read_correction(detector, digest):
    record = feedback_folder(detector) / f"{digest}.json"
    return json.loads(record.read_text(encoding="utf-8")) if record.is_file() else None


def latest_candidate(detector):
    markers = sorted(feedback_folder(detector).glob("training-*/ready"), key=lambda path: path.stat().st_mtime_ns)
    for marker in reversed(markers):
        checkpoint = marker.parent / "runs/corrected/weights/best.pt"
        if checkpoint.is_file():
            return checkpoint, marker.parent / "training.log"
    return None


def save_correction(detector, digest, photo, label, points, *, contract_id=None, append=False):
    polygon = []
    if label is not None:
        if not isinstance(points, list) or not 3 <= len(points) <= 250:
            raise ValueError("Outline the correct region with at least three points and select Use outline.")
        for point in points:
            if not isinstance(point, list) or len(point) != 2 or not all(
                isinstance(value, (int, float)) and 0 <= value <= 1 for value in point
            ):
                raise ValueError("The outline has an invalid point.")
        mask = Image.new("1", photo.size)
        ImageDraw.Draw(mask).polygon([(x * photo.width, y * photo.height) for x, y in points], fill=1)
        if len(set(map(tuple, points))) < 3 or sum(mask.getdata()) < 25:
            raise ValueError("Outline a larger region of the photo.")
        polygon = [value for point in points for value in point]
    folder = feedback_folder(detector)
    folder.mkdir(parents=True, exist_ok=True)
    filename = f"{digest}.png"
    # The normalized photo is identical on repeated uploads of the same bytes.
    photo_path = folder / filename
    pending_photo = folder / f"{uuid4().hex}.png"
    photo.save(pending_photo)
    digest_on_disk = hashlib.sha256(pending_photo.read_bytes()).hexdigest()
    pending_photo.replace(photo_path)
    record = {
        "file": filename, "sha256": digest_on_disk,
        "sourceSha256": digest, "label": label, "polygons": [polygon] if polygon else [],
    }
    if append and polygon:
        previous = read_correction(detector, digest)
        if previous and previous["label"] == label:
            record["polygons"] = list(previous["polygons"])
            if polygon not in record["polygons"]:
                record["polygons"].append(polygon)
    if contract_id:
        record["contractId"] = contract_id
    pending = folder / f"{uuid4().hex}.tmp"
    pending.write_text(json.dumps(record), encoding="utf-8")
    pending.replace(folder / f"{digest}.json")
    return record


def train_candidate(detector, weights, classes, image_size, device, *, skip_conflicts=False):
    directory, name = DATASETS[detector]
    folder = feedback_folder(detector)
    corrections = [record for path in sorted(folder.glob("*.json"))
                   if "sourceSha256" in (record := json.loads(path.read_text(encoding="utf-8")))]
    if not corrections:
        raise ValueError("Save at least one correction for this detector.")
    base = PROJECT / "data" / directory / "candidates"
    rows = json.loads((PROJECT / f"{name}_annotations.json").read_text(encoding="utf-8"))
    existing = {row["sha256"]: row for row in rows}
    held_out = {row["contractId"] for row in rows if row["split"] != "train"}
    normalized = {}
    for row in rows:
        with Image.open(base / row["file"]) as source:
            image = source.convert("RGB")
        image.thumbnail((960, 960))
        normalized[hashlib.sha256(image.tobytes()).hexdigest()] = row

    job = Path(mkdtemp(prefix="training-", dir=folder))
    candidates = job / "candidates"
    candidates.mkdir()
    by_file = {row["file"]: row for row in rows}
    feedback_pixels = set()
    for correction in corrections:
        path = folder / correction["file"]
        with Image.open(path) as photo:
            pixel_digest = hashlib.sha256(photo.convert("RGB").tobytes()).hexdigest()
        if pixel_digest in feedback_pixels:
            if skip_conflicts:
                continue
            raise ValueError("Two saved corrections use the same photo pixels. Keep one correction per photo.")
        previous = existing.get(correction["sourceSha256"]) or normalized.get(pixel_digest)
        if not correction.get("contractId") and previous is None:
            if skip_conflicts:
                continue
            raise ValueError("Add a source contract/project ID to this correction before training; unknown provenance cannot be split safely.")
        if correction.get("contractId") in held_out:
            if skip_conflicts:
                continue
            raise ValueError("A correction belongs to a held-out contract. Use a training contract.")
        if previous and previous["split"] != "train":
            if skip_conflicts:
                continue
            raise ValueError(f"A correction matches a {previous['split']} photo. Use a new training photo.")
        label = next(iter(classes)) if detector == UNDER_CONSTRUCTION_TRAINING else (
            correction["label"] or next(iter(classes))
        )
        if label not in classes:
            raise ValueError(f"Unknown correction label: {label}")
        if previous and previous["file"] not in by_file and skip_conflicts:
            continue
        feedback_pixels.add(pixel_digest)
        if previous:
            del by_file[previous["file"]]
        row = {**correction, "label": label, "split": "train",
               "contractId": previous["contractId"] if previous else
               correction.get("contractId") or f"feedback-{correction['sourceSha256']}"}
        by_file[row["file"]] = row
        copyfile(path, candidates / row["file"])
    if not feedback_pixels:
        raise ValueError("No eligible corrections remain after checking the training split.")
    for row in by_file.values():
        destination = candidates / row["file"]
        if not destination.exists():
            copyfile(base / row["file"], destination)
    manifest = json.dumps(list(by_file.values()))
    annotations = job / "annotations.json"
    annotations.write_text(manifest, encoding="utf-8")
    (candidates / "manifest.json").write_text(manifest, encoding="utf-8")
    log = job / "training.log"
    command = [
        sys.executable, str(PROJECT / "train_road_yolo.py"), "--root", str(job), "--annotations", str(annotations),
        "--weights", str(weights.resolve()), "--classes", *classes, "--name", "corrected",
        "--epochs", "10", "--imgsz", str(image_size), "--mosaic", "0", "--learning-rate", "0.0003", "--device", device,
    ]
    with log.open("w", encoding="utf-8") as output:
        result = subprocess.run(command, cwd=PROJECT, stdout=output, stderr=subprocess.STDOUT, timeout=900)
    checkpoint = job / "runs/corrected/weights/best.pt"
    if result.returncode or not checkpoint.is_file():
        raise ValueError(f"Training failed. See {log}")
    from evaluate_checkpoint import evaluate
    evaluate(detector, checkpoint, job)
    # Ready means testable, not accepted for automatic decisions.
    (job / "ready").touch()
    return checkpoint, log
