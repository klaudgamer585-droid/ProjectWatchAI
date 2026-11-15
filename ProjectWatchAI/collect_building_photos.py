"""Collect and sample building photos for YOLO training."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

import requests
from PIL import Image, ImageOps


API = "https://api.dpwh.bettergov.ph/projects"
CONTRACTS = [
    "20IL0123", "23I00148", "23I00146", "23I00135", "23I00137",
    "23I00028", "23I00138", "24IJ0040", "23I00136", "22KC0033",
    "21KL0051", "21LC0093", "23JE0090",
]


def download_photo(url, output_dir, contract_id):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or "transparency.dpwh.gov.ph" not in parsed.netloc:
        raise ValueError(f"Unexpected image URL: {url}")
    response = requests.get(url, timeout=20)
    response.raise_for_status()
    digest = hashlib.sha256(response.content).hexdigest()
    with Image.open(BytesIO(response.content)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    image.thumbnail((960, 960))
    filename = f"{contract_id}-{digest[:12]}.jpg"
    target = output_dir / filename
    image.save(target, quality=90)
    return filename, digest, image.size


def collect_contract(contract_id, output_dir):
    try:
        res = requests.get(f"{API}/{contract_id}/images", params={"limit": 30}, timeout=20).json()
        gallery = res.get("data", {}).get("images", [])
    except Exception as e:
        print(f"Error fetching {contract_id}: {e}", flush=True)
        return []

    valid = [p for p in gallery if p.get("url") and "transparency.dpwh.gov.ph" in p.get("url", "")]
    if not valid:
        return []

    # Pick up to 4 photos spread across time
    dates = {}
    for photo in valid:
        if photo.get("capturedAt"):
            dates.setdefault(photo["capturedAt"][:10], []).append(photo)
        else:
            dates.setdefault("unknown", []).append(photo)

    selected = [photos[0] for _, photos in sorted(dates.items())]
    if len(selected) > 4:
        selected = [selected[i * (len(selected) - 1) // 3] for i in range(4)]
    elif len(selected) < 2 and len(valid) >= 2:
        selected = valid[:3]

    records = []
    for photo in selected:
        url = photo["url"]
        try:
            filename, digest, size = download_photo(url, output_dir, contract_id)
            records.append({
                "file": filename,
                "sha256": digest,
                "contractId": contract_id,
                "componentId": photo.get("componentId"),
                "capturedAt": photo.get("capturedAt"),
                "url": url,
                "width": size[0],
                "height": size[1],
            })
            print(f"Downloaded {filename} ({size[0]}x{size[1]})", flush=True)
        except Exception as e:
            print(f"Failed {url}: {e}", flush=True)
    return records


def main():
    output_dir = Path("ProjectWatchAI/data/yolo_building/candidates")
    output_dir.mkdir(parents=True, exist_ok=True)
    all_records = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        for records in pool.map(lambda c: collect_contract(c, output_dir), CONTRACTS):
            all_records.extend(records)

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(all_records, indent=2), encoding="utf-8")
    print(f"\nDone! Collected {len(all_records)} photos across {len(set(r['contractId'] for r in all_records))} contracts.", flush=True)


if __name__ == "__main__":
    main()
