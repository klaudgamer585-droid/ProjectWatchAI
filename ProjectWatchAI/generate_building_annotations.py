"""Generate building segmentation annotations from candidate photos."""

import json
from pathlib import Path
import cv2
import numpy as np


def generate_building_annotations():
    root = Path("ProjectWatchAI/data/yolo_building")
    candidates_dir = root / "candidates"
    manifest = json.loads((candidates_dir / "manifest.json").read_text(encoding="utf-8"))

    # Deduplicate by SHA256
    unique_items = {}
    for item in manifest:
        if item["sha256"] not in unique_items:
            unique_items[item["sha256"]] = item

    # Update manifest.json with deduplicated list
    (candidates_dir / "manifest.json").write_text(
        json.dumps(list(unique_items.values()), indent=2), encoding="utf-8"
    )

    splits = {
        "train": {"20IL0123", "23I00148", "23I00146", "23I00135", "23I00137", "23I00028", "23I00138", "24IJ0040", "23I00136"},
        "val": {"22KC0033", "21KL0051"},
        "test": {"21LC0093", "23JE0090"},
    }

    annotations = []
    for item in unique_items.values():
        cid = item["contractId"]
        filename = item["file"]
        split = "train"
        for s, contracts in splits.items():
            if cid in contracts:
                split = s
                break

        img_path = candidates_dir / filename
        img = cv2.imread(str(img_path))
        if img is None:
            continue
        h, w = img.shape[:2]

        # Convert to grayscale and detect structure boundaries
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # Analyze structure: building roofs, trusses, and facades typically have strong horizontal/vertical edges in the middle 70% of the image
        # Exclude pure sky (top 10%) and immediate ground (bottom 15-20%) if present
        # Compute gradient magnitude
        grad_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        grad_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        mag = cv2.magnitude(grad_x, grad_y)

        # Blur and threshold
        blurred = cv2.GaussianBlur(mag, (21, 21), 0)
        norm_mag = cv2.normalize(blurred, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
        _, thresh = cv2.threshold(norm_mag, 45, 255, cv2.THRESH_BINARY)

        # Focus mask on building region (central 80% width, 10%-85% height)
        roi_mask = np.zeros_like(thresh)
        y1, y2 = int(h * 0.10), int(h * 0.85)
        x1, x2 = int(w * 0.05), int(w * 0.95)
        roi_mask[y1:y2, x1:x2] = 255
        thresh = cv2.bitwise_and(thresh, roi_mask)

        # Morphological closing to connect columns, trusses, and walls
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25))
        closed = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        polygons = []

        # Find the major building contour
        min_area = (w * h) * 0.08  # At least 8% of image
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area > min_area:
                # Simplify polygon using Douglas-Peucker
                epsilon = 0.015 * cv2.arcLength(cnt, True)
                approx = cv2.approxPolyDP(cnt, epsilon, True)
                if len(approx) >= 4:
                    pts = approx.reshape(-1, 2)
                    norm_pts = []
                    for pt in pts:
                        norm_pts.extend([round(float(pt[0]) / w, 5), round(float(pt[1]) / h, 5)])
                    polygons.append(norm_pts)

        # If no distinct building contour (e.g. background or billboard photo), leave empty as negative sample
        # Limit to top 2 largest polygons
        if len(polygons) > 2:
            polygons = sorted(polygons, key=len, reverse=True)[:2]

        record = {
            "file": filename,
            "sha256": item["sha256"],
            "contractId": cid,
            "componentId": item.get("componentId"),
            "capturedAt": item.get("capturedAt"),
            "url": item["url"],
            "split": split,
            "label": "building_structure",
            "polygons": polygons,
        }
        annotations.append(record)

    out_file = Path("ProjectWatchAI/building_structure_annotations.json")
    out_file.write_text(json.dumps(annotations, indent=2), encoding="utf-8")
    print(f"Generated {len(annotations)} annotations ({len([a for a in annotations if a['polygons']])} with building polygons, {len([a for a in annotations if not a['polygons']])} background).")


if __name__ == "__main__":
    generate_building_annotations()
