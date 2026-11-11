import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from run import DATA_DIR, FILE, download_dataset


REGION = "Region III"
OUTPUT_FILE = DATA_DIR / "region_iii_projects.parquet"


def filter_region_iii(projects: pd.DataFrame) -> pd.DataFrame:
    regions = projects["location"].map(
        lambda location: location.get("region") if isinstance(location, dict) else None
    )
    regional_projects = projects.loc[regions.eq(REGION)].copy()
    regional_projects["infrastructureCategories"] = (
        regional_projects["componentCategories"]
        .fillna("")
        .map(lambda value: value.strip() or "Uncategorized")
        .str.split(r"\s*,\s*", regex=True)
    )
    return regional_projects


def main():
    download_dataset()
    source = pq.read_table(FILE)
    projects = filter_region_iii(
        source.select(["location", "componentCategories"]).to_pandas()
    )
    regional = source.take(projects.index.to_numpy())
    categories = pa.array(projects["infrastructureCategories"], type=pa.list_(pa.string()))
    regional = regional.append_column("infrastructureCategories", categories)
    pq.write_table(regional, OUTPUT_FILE)

    print(f"\n{REGION} projects: {len(projects):,}")
    print("\nProjects by infrastructure category (a project can have multiple categories):")
    counts = projects["infrastructureCategories"].explode().value_counts()
    print(counts.to_string())
    print(f"\nSaved to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
