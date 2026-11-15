"""Run local segmentation models on public DPWH gallery photos."""

from io import BytesIO
from hashlib import sha256
from evidence_policy import INFERENCE_VERSION, checkpoint_digest
from threading import Lock
from urllib.parse import urlsplit

import requests
from PIL import Image, ImageOps
from contract_assessment import BRIDGE_CONFIDENCE, BUILDING_CONFIDENCE, CONSTRUCTION_EQUIPMENT


PHOTO_HOST = "s3.transparency.dpwh.gov.ph"
MAX_PHOTO_BYTES = 12 * 1024 * 1024
SURFACE_NAMES = {
    "paved_road_surface": "paved road surface",
    "unpaved_road_surface": "unpaved road surface",
}
SLOPE_PROTECTION_NAMES = {"slope protection": "slope protection"}
FLOOD_NAMES = {"lined_drainage_channel": "lined drainage channel"}
STREET_DRAIN_NAMES = {"street drain grate": "street drain grate"}
RIVERBANK_NAMES = {"concrete riverbank protection": "concrete riverbank protection"}
BUILDING_NAMES = {"building_structure": "building structure"}
BUILDING_STAGE_NAMES = {"visible unfinished building work": "possible unfinished building work"}
EQUIPMENT_NAMES = {name: name for name in CONSTRUCTION_EQUIPMENT}
SCAFFOLDING_NAMES = {
    "temporary scaffolding": "temporary scaffolding",
    "temporary metal scaffolding": "temporary metal scaffolding",
    "temporary timber shoring": "temporary timber shoring",
}
SCAFFOLDING_MIN_CONFIDENCE = {"temporary metal scaffolding": 0.40, "temporary timber shoring": 0.50}
UNDER_CONSTRUCTION_LABELS = {"visible unfinished building work": "Under construction"}
UNDER_CONSTRUCTION_NAMES = {
    label: "Under construction" for label in (*BUILDING_STAGE_NAMES, *SCAFFOLDING_NAMES, *EQUIPMENT_NAMES)
}
BRIDGE_NAMES = {"bridge": "bridge"}
WATER_NAMES = {"elevated water tank": "elevated water tank"}
STAINLESS_WATER_NAMES = {"stainless steel water tank": "stainless steel water tank"}


def download_photo(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != PHOTO_HOST:
        raise ValueError("The photo is outside the supported DPWH image host.")
    with requests.get(url, stream=True, timeout=(5, 15), allow_redirects=False) as response:
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError("The DPWH image server did not return a photo.")
        data = BytesIO()
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if data.tell() + len(chunk) > MAX_PHOTO_BYTES:
                raise ValueError("The photo exceeds the analysis size limit.")
            data.write(chunk)
    content_digest = sha256(data.getvalue()).hexdigest()
    data.seek(0)
    try:
        with Image.open(data) as source:
            if source.width * source.height > 25_000_000:
                raise ValueError("The photo exceeds the analysis resolution limit.")
            photo = ImageOps.exif_transpose(source).convert("RGB")
    except Image.DecompressionBombError as error:
        raise ValueError("The photo exceeds the analysis resolution limit.") from error
    photo.thumbnail((960, 960))
    photo.info["source_sha256"] = content_digest
    return photo


class PhotoSegmentationModel:
    def __init__(self, weights, class_names, image_size):
        from ultralytics import YOLO
        import torch

        torch.set_num_threads(min(torch.get_num_threads(), 2))
        self.checkpoint_sha256 = checkpoint_digest(weights)
        self.model = YOLO(weights)
        self.lock = Lock()
        self.class_names = class_names
        self.image_size = image_size
        model_labels = set(self.model.names.values())
        allowed_labels = [set(class_names)]
        if class_names == UNDER_CONSTRUCTION_NAMES:
            allowed_labels.append(set(UNDER_CONSTRUCTION_LABELS))
        if self.model.task != "segment" or model_labels not in allowed_labels:
            raise ValueError("The checkpoint classes do not match the selected project category.")
        # The fine-tuned bridge head was validated with square inputs and a higher cutoff.
        layers = getattr(self.model.model, "model", ())
        refined_bridge = class_names == BRIDGE_NAMES and bool(layers) and getattr(layers[-1], "is_fused", False)
        self.rect = not refined_bridge
        self.confidence = BRIDGE_CONFIDENCE / 100 if refined_bridge else 0.25
        if class_names == BUILDING_NAMES:
            self.confidence = BUILDING_CONFIDENCE / 100

    def describe(self, url):
        return self.describe_image(download_photo(url))

    def analyze_image(self, photo):
        return self.describe_image(photo, render_overlay=False, structured=True)

    def describe_image(self, photo, *, render_overlay=True, structured=False):
        def result(description, overlay=None, detections=None):
            if not structured:
                return description, overlay
            return {"observation": description, "detections": detections or [],
                    "checkpoint_sha256": self.checkpoint_sha256, "inference_version": INFERENCE_VERSION,
                    "settings": {"confidence": self.confidence, "image_size": self.image_size, "rect": self.rect},
                    "status": "detected" if detections else "no detection"}

        with self.lock:
            prediction = self.model.predict(
                photo, imgsz=self.image_size, conf=self.confidence, rect=self.rect, device="cpu", verbose=False,
            )[0]
            if self.class_names in (SCAFFOLDING_NAMES, UNDER_CONSTRUCTION_NAMES) and prediction.boxes is not None:
                # The metal prompt produced a false positive on a finished covered court at 34%.
                keep = [
                    index for index, (class_id, score) in enumerate(
                        zip(prediction.boxes.cls.tolist(), prediction.boxes.conf.tolist())
                    )
                    if score >= SCAFFOLDING_MIN_CONFIDENCE.get(self.model.names[int(class_id)], self.confidence)
                ]
                if not keep:
                    target = "Under construction" if self.class_names == UNDER_CONSTRUCTION_NAMES else "scaffolding or shoring"
                    return result(f"YOLO found no {target} above the detection thresholds.")
                prediction = prediction[keep]
            if prediction.masks is None:
                targets = " or ".join(dict.fromkeys(self.class_names.values()))
                return result(f"YOLO found no {targets} above {self.confidence:.0%} confidence.")

            detections = []
            for index, (class_id, score) in enumerate(zip(prediction.boxes.cls.tolist(), prediction.boxes.conf.tolist())):
                detections.append({"class_id": int(class_id), "label": self.class_names[self.model.names[int(class_id)]],
                                   "confidence": float(score), "box_xyxyn": prediction.boxes.xyxyn[index].tolist(),
                                   "polygon_xyn": prediction.masks.xyn[index].tolist()})
            scores = {}
            for class_id, score in zip(prediction.boxes.cls.tolist(), prediction.boxes.conf.tolist()):
                label = self.class_names[self.model.names[int(class_id)]]
                scores[label] = max(score, scores.get(label, 0.0))
            if render_overlay:
                if self.class_names == UNDER_CONSTRUCTION_NAMES:
                    prediction.names = {class_id: self.class_names[label]
                                        for class_id, label in prediction.names.items()}
                overlay = Image.fromarray(prediction.plot(conf=True, boxes=True, color_mode="class")[..., ::-1])

        findings = [
            f"{label} ({score:.0%} confidence)"
            for label, score in sorted(scores.items(), key=lambda item: -item[1])
        ]
        description = f"YOLO detected {' and '.join(findings)}."
        if len(findings) > 1:
            description += " The predictions may overlap. Review the overlay."
        if not render_overlay:
            return result(description, detections=detections)
        output = BytesIO()
        overlay.save(output, format="JPEG", quality=85)
        return result(description, output.getvalue(), detections)
