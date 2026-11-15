from audit_image_evidence import DETAILS_FILE, main as audit
from filter_region import main as filter_projects
from run import URL, download_dataset


DETAILS_URL = URL.replace(".parquet?", "_all_details.parquet?")


def main():
    download_dataset(DETAILS_URL, DETAILS_FILE)
    filter_projects()
    audit([])
    audit(["--nationwide"])


if __name__ == "__main__":
    main()
