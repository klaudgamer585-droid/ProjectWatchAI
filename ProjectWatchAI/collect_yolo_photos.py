"""Sample dated DPWH photos for manual annotation."""

import argparse
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

import requests
from PIL import Image, ImageOps


API = "https://api.dpwh.bettergov.ph/projects"


def api(path="", **params):
    response = requests.get(API + path, params=params, timeout=20)
    response.raise_for_status()
    return response.json()["data"]


def download_photo(url, output, expected_digest=None):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "s3.transparency.dpwh.gov.ph":
        raise ValueError(f"Unexpected DPWH image URL: {url}")
    response = requests.get(url, timeout=20)
    response.raise_for_status()
    digest = hashlib.sha256(response.content).hexdigest()
    if expected_digest is not None and digest != expected_digest:
        raise ValueError(f"Source image changed: {url}")
    with Image.open(BytesIO(response.content)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    image.thumbnail((960, 960))
    image.save(output(digest), quality=90)
    return digest


def collect_contract(contract_id, output):
    gallery = api(f"/{contract_id}/images", limit=50)["images"]
    dates = {}
    for photo in gallery:
        if photo.get("capturedAt") and photo.get("url"):
            dates.setdefault(photo["capturedAt"][:10], []).append(photo)
    selected = [photos[0] for _, photos in sorted(dates.items())]
    if len(selected) > 4:
        selected = [selected[i * (len(selected) - 1) // 3] for i in range(4)]
    elif len(selected) == 1:
        selected = dates[selected[0]["capturedAt"][:10]][:2]
    records = []
    for photo in selected:
        url = photo["url"]
        try:
            digest = download_photo(url, lambda value: output / f"{contract_id}-{value[:12]}.jpg")
        except (requests.RequestException, OSError, ValueError) as error:
            print(f"{contract_id}: {error}")
            continue
        filename = f"{contract_id}-{digest[:12]}.jpg"
        records.append({
            "file": filename, "sha256": digest, "contractId": contract_id,
            "componentId": photo.get("componentId"), "capturedAt": photo["capturedAt"],
            "url": url,
        })
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/yolo_pilot/candidates"))
    parser.add_argument("--contracts", type=int, default=25)
    parser.add_argument("--search", action="append", help="Contract search term. May be repeated.")
    parser.add_argument("--category", default="Roads", help="BetterGov infrastructure category.")
    parser.add_argument("--from-annotations", type=Path, help="Restore the exact labeled source photos.")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.from_annotations:
        rows = json.loads(args.from_annotations.read_text(encoding="utf-8"))

        def restore(row):
            destination = args.output / Path(row["file"]).name
            if row.get("url"):
                download_photo(row["url"], lambda _: destination, row["sha256"])
            elif not destination.is_file() or hashlib.sha256(destination.read_bytes()).hexdigest() != row["sha256"]:
                raise ValueError(f"Place the original local reference photo at {destination} before restoring.")

        with ThreadPoolExecutor(max_workers=6) as workers:
            list(workers.map(restore, rows))
        (args.output / "manifest.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"Restored {len(rows)} labeled photos.")
        return
    searches = args.search or ["asphalt overlay"]
    ids = list(dict.fromkeys(
        project["contractId"]
        for search in searches
        for project in api(category=args.category, search=search, limit=args.contracts)["data"]
    ))

    def collect(contract_id):
        try:
            return collect_contract(contract_id, args.output)
        except requests.RequestException as error:
            print(f"{contract_id}: {error}")
            return []

    unique = {}
    with ThreadPoolExecutor(max_workers=6) as workers:
        for records in workers.map(collect, ids):
            for record in records:
                unique.setdefault(record["sha256"], record)
    (args.output / "manifest.json").write_text(json.dumps(list(unique.values()), indent=2))
    print(f"Collected {len(unique)} photos from {len({row['contractId'] for row in unique.values()})} contracts.")


if __name__ == "__main__":
    main()
