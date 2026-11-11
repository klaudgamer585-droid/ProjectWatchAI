import argparse
from datetime import date
from pathlib import Path

import pandas as pd

from run import DATA_DIR


PROJECTS_FILE = DATA_DIR / "region_iii_projects.parquet"
DETAILS_FILE = DATA_DIR / "dpwh_details.parquet"
REPORT_FILE = DATA_DIR / "region_iii_image_audit.parquet"
PHOTO_COLUMNS = ["contractId", "suppliedPhotoCount", "latestPhotoDate", "latestPhotoPath"]


def read_photos(manifest: Path) -> pd.DataFrame:
    """Read local photo paths and capture dates from a JSON array."""
    photos = pd.read_json(manifest, dtype=False, convert_dates=False)
    if photos.empty:
        return pd.DataFrame(columns=PHOTO_COLUMNS)

    required = {"contractId", "imagePath", "captureDate"}
    if not required.issubset(photos.columns):
        raise ValueError("Every photo needs contractId, imagePath, and captureDate.")
    if photos[list(required)].isna().any().any():
        raise ValueError("Photo manifest fields cannot be null.")

    photos["imagePath"] = photos["imagePath"].map(
        lambda path: str((manifest.parent / path).resolve())
    )
    for path in map(Path, photos["imagePath"]):
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"Photo is missing or empty: {path}")

    photos["captureDate"] = photos["captureDate"].map(date.fromisoformat)
    if photos["imagePath"].duplicated().any():
        raise ValueError("Each photo file must appear only once in the manifest.")

    return photos.sort_values("captureDate", kind="stable").groupby("contractId", as_index=False).agg(
        suppliedPhotoCount=("imagePath", "size"),
        latestPhotoDate=("captureDate", "last"),
        latestPhotoPath=("imagePath", "last"),
    )


def audit_images(
    projects: pd.DataFrame, details: pd.DataFrame, photos: pd.DataFrame
) -> pd.DataFrame:
    unknown_ids = photos.loc[~photos["contractId"].isin(projects["contractId"]), "contractId"]
    if not unknown_ids.empty:
        raise ValueError(f"Photo contract IDs absent from Region III: {unknown_ids.tolist()}")

    audit = projects.merge(details, on="contractId", how="left", validate="one_to_one")
    audit = audit.merge(photos, on="contractId", how="left", validate="one_to_one")
    audit["suppliedPhotoCount"] = audit["suppliedPhotoCount"].fillna(0).astype("Int64")
    supplied = audit["suppliedPhotoCount"].gt(0)
    reported = audit["hasImages"].eq(True).fillna(False) | audit["totalImages"].gt(0).fillna(False)
    audit["evidenceSource"] = "none"
    audit.loc[reported, "evidenceSource"] = "dpwh_metadata"
    audit.loc[supplied, "evidenceSource"] = "supplied_photos"

    # Compare calendar dates as published, keeping the date portion of DPWH timestamps.
    metadata_date = audit["latestImageDate"].astype("string").str.slice(0, 10)
    latest = audit["latestPhotoDate"].where(supplied, metadata_date.where(reported))
    audit["evidenceDate"] = pd.to_datetime(latest, format="%Y-%m-%d", errors="coerce")
    completion = pd.to_datetime(audit["completionDate"], errors="coerce")
    completed = audit["status"].eq("Completed")
    audit["daysFromCompletion"] = (audit["evidenceDate"] - completion).dt.days.where(completed).astype("Int64")

    audit["missingImages"] = ~supplied & audit["hasImages"].eq(False).fillna(False) & ~reported
    audit["imageAvailabilityUnknown"] = ~supplied & ~reported & audit["hasImages"].isna()
    audit["missingImageDate"] = (supplied | reported) & audit["evidenceDate"].isna()
    audit["missingCompletionDate"] = completed & completion.isna()
    audit["imageDateMismatch"] = audit["daysFromCompletion"].ne(0).fillna(False)
    return audit


def main():
    parser = argparse.ArgumentParser(description="Audit Region III project image evidence.")
    parser.add_argument("--photos", type=Path, help="JSON manifest of local photos and capture dates.")
    parser.add_argument("--contract-id", help="Print the audit row for one contract.")
    args = parser.parse_args()

    projects = pd.read_parquet(PROJECTS_FILE, columns=["contractId", "status", "completionDate"])
    details = pd.read_parquet(
        DETAILS_FILE, columns=["contractId", "hasImages", "totalImages", "latestImageDate"]
    )
    photos = (
        read_photos(args.photos)
        if args.photos
        else pd.DataFrame(columns=PHOTO_COLUMNS)
    )
    audit = audit_images(projects, details, photos)
    if args.contract_id:
        selected = audit.loc[audit["contractId"].eq(args.contract_id)]
        if selected.empty:
            parser.error(f"Contract {args.contract_id} was not found in Region III.")
        print(selected.iloc[0].to_json(indent=2, date_format="iso"))

    audit.to_parquet(REPORT_FILE, index=False)
    print(f"Audited {len(audit):,} contracts")
    for flag in (
        "missingImages",
        "imageAvailabilityUnknown",
        "missingImageDate",
        "missingCompletionDate",
        "imageDateMismatch",
    ):
        print(f"{flag}: {audit[flag].sum():,}")
    print(f"Saved to {REPORT_FILE}")


if __name__ == "__main__":
    main()
