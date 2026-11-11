from pathlib import Path

import pandas as pd
import requests


URL = (
    "https://huggingface.co/datasets/bettergovph/"
    "dpwh-transparency-data/resolve/main/"
    "dpwh_transparency_data.parquet?download=true"
)
DATA_DIR = Path(__file__).resolve().parent / "data"
FILE = DATA_DIR / "dpwh_projects.parquet"


def download_dataset(url=URL, destination=FILE):
    if destination.exists():
        print(f"Dataset already downloaded: {destination.name}")
        return

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial_file = destination.with_suffix(".parquet.part")
    print(f"Downloading {destination.name}...")
    with requests.get(url, stream=True, timeout=120) as response:
        response.raise_for_status()
        with partial_file.open("wb") as output:
            for chunk in response.iter_content(1024 * 1024):
                if chunk:
                    output.write(chunk)
    partial_file.replace(destination)
    print("Download complete!")


def inspect_dataset():
    projects = pd.read_parquet(FILE)
    print("\nTotal projects:", len(projects))
    print("\nAvailable columns:")
    print(projects.columns.tolist())
    print("\nFirst 5 projects:")
    print(projects.head().to_string(index=False))


if __name__ == "__main__":
    download_dataset()
    inspect_dataset()
