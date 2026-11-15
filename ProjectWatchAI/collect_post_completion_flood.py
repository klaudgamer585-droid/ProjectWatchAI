"""Collect dated DPWH photos taken after reported contract completion."""

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from functools import partial
from pathlib import Path

import requests

from collect_yolo_photos import api, download_photo


def captured_after(photo, completed):
    captured = photo.get("capturedAt")
    if not isinstance(captured, str) or not photo.get("url"):
        return False
    try:
        return date.fromisoformat(captured[:10]) > completed
    except ValueError:
        return False


def collect(contract_id, output):
    records = []
    try:
        detail = api(f"/{contract_id}")
        if detail["status"] != "Completed" or not detail.get("completionDate"):
            return records
        completed = date.fromisoformat(detail["completionDate"][:10])
        gallery = api(f"/{contract_id}/images", limit=50)["images"]
        eligible = [photo for photo in gallery if captured_after(photo, completed)]
        # Prefer completion evidence and spread examples across components and dates.
        eligible.sort(key=lambda photo: photo.get("purpose") != "completion")
        groups = {}
        for photo in eligible:
            group = (photo.get("componentId"), photo["capturedAt"][:10])
            groups.setdefault(group, []).append(photo)
        first = [photos[0] for photos in groups.values()]
        extra = [photo for photos in groups.values() for photo in photos[1:]]
        def filename(digest):
            return f"{contract_id}-{digest[:12]}.jpg"

        for photo in (first + extra)[:3]:
            try:
                digest = download_photo(photo["url"], lambda digest: output / filename(digest))
            except (requests.RequestException, OSError, ValueError) as error:
                print(f"{contract_id} photo: {error}", flush=True)
                continue
            records.append({
                "file": filename(digest), "sha256": digest, "contractId": contract_id,
                "completionDate": completed.isoformat(), "capturedAt": photo["capturedAt"],
                "componentId": photo.get("componentId"), "purpose": photo.get("purpose"),
                "url": photo["url"],
            })
    except (requests.RequestException, OSError, ValueError, KeyError) as error:
        print(f"{contract_id}: {error}", flush=True)
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/yolo_riverbank/post_completion"))
    parser.add_argument("--limit", type=int, default=20, help="Contracts to check per search term.")
    parser.add_argument("--category", default="Flood Control and Drainage")
    parser.add_argument("--search", action="append", help="Contract search term. May be repeated.")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    ids = list(dict.fromkeys(
        item["contractId"]
        for term in (args.search or ("riverbank protection", "revetment", "river dike", "drainage canal"))
        for item in api(category=args.category, status="Completed", search=term, limit=args.limit)["data"]
    ))
    unique = {}
    with ThreadPoolExecutor(max_workers=6) as workers:
        for records in workers.map(partial(collect, output=args.output), ids):
            for record in records:
                unique.setdefault(record["sha256"], record)
    (args.output / "manifest.json").write_text(json.dumps(list(unique.values()), indent=2), encoding="utf-8")
    print(f"Collected {len(unique)} post-completion photos from {len({r['contractId'] for r in unique.values()})} contracts.")


if __name__ == "__main__":
    main()
