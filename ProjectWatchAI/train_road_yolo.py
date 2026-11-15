"""Train and evaluate YOLO segmentation on contract-separated DPWH photos."""

import argparse
import json
import os
from pathlib import Path
from shutil import copyfile
from time import sleep


CLASSES = ["paved_road_surface"]
UNPAVED = "unpaved_road_surface"
SPLITS = ("train", "val", "test")


def prepare_dataset(root, annotations, classes=None):
    candidates = root / "candidates"
    manifest = {
        row["file"]: row
        for row in json.loads((candidates / "manifest.json").read_text(encoding="utf-8"))
    }
    rows = json.loads(annotations.read_text(encoding="utf-8"))
    if classes is None:
        classes = [*CLASSES, UNPAVED] if any(row.get("surface") == UNPAVED for row in rows) else CLASSES
    assignments = {}
    seen = set()
    digests = set()
    for row in rows:
        filename, split = row["file"], row["split"]
        if row.get("label", row.get("surface", classes[0])) not in classes:
            raise ValueError(f"Unknown segmentation class for {filename}")
        source = manifest[filename]
        contract = source["contractId"]
        digest = source["sha256"]
        if digest != row["sha256"]:
            raise ValueError(f"Source photo differs from the annotation manifest: {filename}")
        if filename in seen or digest in digests or split not in SPLITS:
            raise ValueError(f"Duplicate image or invalid split for {filename}")
        if assignments.setdefault(contract, split) != split:
            raise ValueError(f"Duplicate image or conflicting split for {filename}")
        seen.add(filename)
        digests.add(digest)
        for polygon in row["polygons"]:
            if len(polygon) < 6 or len(polygon) % 2 or not all(0 <= value <= 1 for value in polygon):
                raise ValueError(f"Invalid normalized polygon for {filename}")
    if set(assignments.values()) != set(SPLITS):
        raise ValueError("Training, validation, and test each require at least one contract.")

    dataset = root / "dataset"
    for split in SPLITS:
        for kind in ("images", "labels"):
            (dataset / kind / split).mkdir(parents=True, exist_ok=True)
    config = {"path": str(dataset.resolve()), **{split: [] for split in SPLITS}, "names": classes}
    for row in rows:
        filename, split = row["file"], row["split"]
        image = dataset / "images" / split / filename
        copyfile(candidates / filename, image)
        class_id = classes.index(row.get("label", row.get("surface", classes[0])))
        lines = [f"{class_id} " + " ".join(map(str, polygon)) for polygon in row["polygons"]]
        (dataset / "labels" / split / Path(filename).with_suffix(".txt")).write_text("\n".join(lines))
        config[split].append(str(image.resolve()))
    for split in SPLITS:
        image_list = dataset / f"{split}.txt"
        image_list.write_text("\n".join(config[split]), encoding="utf-8")
        config[split] = str(image_list.resolve())
    yaml = dataset / "road.yaml"
    yaml.write_text(json.dumps(config), encoding="utf-8")
    return yaml


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/yolo_pilot"))
    parser.add_argument("--annotations", type=Path, default=Path("road_pilot_annotations.json"))
    parser.add_argument("--weights", default="yolo11n-seg.pt")
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--imgsz", type=int, default=512)
    parser.add_argument("--mosaic", type=float, default=1.0)
    parser.add_argument("--learning-rate", type=float, help="Use AdamW with this initial learning rate.")
    parser.add_argument("--classes", nargs="+", help="Segmentation class names in checkpoint order.")
    parser.add_argument("--name", default="road_seg")
    parser.add_argument("--device", default="0")
    args = parser.parse_args()
    os.environ.setdefault("YOLO_CONFIG_DIR", str((args.root / "settings").resolve()))
    from ultralytics import YOLO, YOLOE

    yaml = prepare_dataset(args.root, args.annotations, args.classes)
    model = YOLO(args.weights)
    # Leave idle time between batches to reduce sustained CPU/GPU load.
    model.add_callback("on_train_batch_end", lambda trainer: sleep(0.35))
    training_options = {"optimizer": "AdamW", "lr0": args.learning_rate} if args.learning_rate else {}
    if isinstance(model, YOLOE):
        from ultralytics.models.yolo.yoloe import YOLOEPESegTrainer

        # Preserve the configured text or visual prompts when initializing the trainable head.
        model.model.eval()
        head = model.model.model[-1]
        if not head.is_fused:
            head.fuse(model.model.pe)
        training_options["trainer"] = YOLOEPESegTrainer
    model.train(
        data=str(yaml.resolve()), epochs=args.epochs, imgsz=args.imgsz, mosaic=args.mosaic, batch=4, workers=0,
        device=args.device, amp=False, seed=0, project=str((args.root / "runs").resolve()),
        name=args.name, **training_options,
    )
    from ultralytics.models.yolo.segment import SegmentationValidator

    best = YOLO(str(model.trainer.best))
    validator = SegmentationValidator(args={
        "model": str(model.trainer.best), "data": str(yaml.resolve()), "split": "test",
        "imgsz": args.imgsz, "device": args.device, "workers": 0,
        "project": str((args.root / "runs").resolve()),
        "name": "road_test" if args.name == "road_seg" else f"{args.name}_test",
    })
    validator()
    metrics = validator.metrics
    print(f"Held-out mask mAP50: {metrics.seg.map50:.3f}; mAP50–95: {metrics.seg.map:.3f}")
    for class_id, ap50, ap in zip(metrics.seg.ap_class_index, metrics.seg.ap50, metrics.seg.ap):
        print(f"{best.names[int(class_id)]}: mask AP50={ap50:.3f}, AP50–95={ap:.3f}")
    print(f"Checkpoint: {model.trainer.best}")


if __name__ == "__main__":
    main()
