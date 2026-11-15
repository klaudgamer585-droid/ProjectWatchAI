"""Save category-specific YOLOE checkpoints for local inference."""

import argparse
import json
from pathlib import Path

from road_surface import (
    BRIDGE_NAMES, PHOTO_HOST, RIVERBANK_NAMES, STAINLESS_WATER_NAMES, STREET_DRAIN_NAMES, WATER_NAMES,
    download_photo,
)


# Normalized reference boxes around two DPWH elevated tanks with different paint schemes.
WATER_REFERENCES = (
    ("fy2021/P00551073VS-CW1/1_4d61ff09-9b7e-4bd1-a375-ae25c49375e7.jpg", (0.22, 0.05, 0.58, 0.88)),
    ("fy2021/P00551046VS-CW1/1_339867fc-d748-40a4-9483-5a62b338e846.jpg", (0.07, 0.07, 0.91, 0.99)),
)


def visual_embeddings(weights, image, box):
    import numpy as np
    from ultralytics import YOLOE

    x1, y1, x2, y2 = box
    pixels = np.array([[x1 * image.width, y1 * image.height, x2 * image.width, y2 * image.height]], dtype=np.float32)
    reference = YOLOE(weights)
    reference.predict(
        image, refer_image=image, visual_prompts={"bboxes": pixels, "cls": np.array([0])},
        imgsz=640, device="cpu", verbose=False,
    )
    return reference.model.pe.cpu()


def water_embeddings(weights):
    import torch

    embeddings = []
    for path, box in WATER_REFERENCES:
        image = download_photo(f"https://{PHOTO_HOST}/dpwh-gallery/{path}")
        embeddings.append(visual_embeddings(weights, image, box))
    return torch.nn.functional.normalize(torch.stack(embeddings).mean(0), dim=-1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weights", default="yoloe-11s-seg.pt")
    parser.add_argument("--output", type=Path, default=Path("."))
    parser.add_argument("--riverbank-reference", type=Path, help="Prepare only the riverbank model from the reference photo.")
    parser.add_argument("--stainless-annotations", type=Path, help="Prepare only the stainless tank model from its labeled references.")
    parser.add_argument("--street-drain-annotation", type=Path, help="Prepare only the street drain model from a labeled reference.")
    args = parser.parse_args()

    from ultralytics import YOLOE

    args.output.mkdir(parents=True, exist_ok=True)
    if args.street_drain_annotation:
        from PIL import Image

        row = json.loads(args.street_drain_annotation.read_text(encoding="utf-8"))
        with Image.open(args.output / row["file"]) as source:
            photo = source.convert("RGB")
        photo.thumbnail((960, 960))
        polygon = row["polygons"][0]
        x, y = polygon[::2], polygon[1::2]
        drain = YOLOE(args.weights)
        drain.set_classes(list(STREET_DRAIN_NAMES), visual_embeddings(args.weights, photo, (min(x), min(y), max(x), max(y))))
        drain.save(args.output / "street-drain-yoloe.pt")
        return
    if args.stainless_annotations:
        import torch
        from PIL import Image

        rows = {row["file"]: row for row in json.loads(args.stainless_annotations.read_text(encoding="utf-8"))}
        embeddings = []
        for filename in ("water-stainless-reference.png", "22LE0088-22bcc08bb44a.jpg"):
            polygon = max(rows[filename]["polygons"], key=len)
            x, y = polygon[::2], polygon[1::2]
            with Image.open(args.output / "candidates" / filename) as source:
                photo = source.convert("RGB")
            photo.thumbnail((960, 960))
            embeddings.append(visual_embeddings(args.weights, photo, (min(x), min(y), max(x), max(y))))
        tank = YOLOE(args.weights)
        tank.set_classes(list(STAINLESS_WATER_NAMES), torch.nn.functional.normalize(torch.stack(embeddings).mean(0), dim=-1))
        tank.save(args.output / "stainless-yoloe.pt")
        return
    if args.riverbank_reference:
        from PIL import Image

        with Image.open(args.riverbank_reference) as source:
            photo = source.convert("RGB")
        photo.thumbnail((960, 960))
        riverbank = YOLOE(args.weights)
        # Sample a rectangle inside the labeled concrete face, clear of the water and crest.
        embedding = visual_embeddings(args.weights, photo, (0.23, 0.80, 0.38, 0.98))
        riverbank.set_classes(list(RIVERBANK_NAMES), embedding)
        destination = args.output / "riverbank-yoloe.pt"
        riverbank.save(destination)
        print(f"Saved {destination.resolve()}")
        return
    bridge = YOLOE(args.weights)
    bridge.set_classes(list(BRIDGE_NAMES))
    bridge.save(args.output / "bridge-yoloe.pt")
    water = YOLOE(args.weights)
    water.set_classes(list(WATER_NAMES), water_embeddings(args.weights))
    water.save(args.output / "water-yoloe.pt")
    print(f"Saved both checkpoints under {args.output.resolve()}")


if __name__ == "__main__":
    main()
