import pandas as pd
import streamlit as st

from audit_image_evidence import PROJECTS_FILE, REPORT_FILE


FLAGS = {
    "missingImages": "No images reported",
    "imageDateMismatch": "Photo date differs from completion",
    "imageAvailabilityUnknown": "Image availability unknown",
    "missingImageDate": "Image date unavailable",
    "missingCompletionDate": "Completion date unavailable",
}


@st.cache_data
def load_data(projects_version, audit_version):
    projects = pd.read_parquet(
        PROJECTS_FILE, columns=["contractId", "description", "infrastructureCategories", "progress"]
    )
    audit = pd.read_parquet(REPORT_FILE)
    return projects.merge(audit, on="contractId", validate="one_to_one")


def main():
    st.set_page_config(page_title="ProjectWatchAI | Region III", page_icon="📍", layout="wide")
    st.title("ProjectWatchAI")
    st.caption("Region III · DPWH construction photo evidence")
    if not PROJECTS_FILE.is_file() or not REPORT_FILE.is_file():
        st.info("The dashboard data is missing. Run `python prepare_data.py` and reload.")
        st.stop()

    projects = load_data(PROJECTS_FILE.stat().st_mtime_ns, REPORT_FILE.stat().st_mtime_ns)
    metrics = st.columns(3)
    metrics[0].metric("Contracts", f"{len(projects):,}")
    metrics[1].metric("No images reported", f"{projects['missingImages'].sum():,}")
    metrics[2].metric("Photo date differs", f"{projects['imageDateMismatch'].sum():,}")

    query = st.text_input("Find a contract by ID or description").strip()
    filters = st.columns(2)
    categories = sorted(projects["infrastructureCategories"].explode().dropna().unique())
    category = filters[0].selectbox("Infrastructure category", ["All categories", *categories])
    flag = filters[1].selectbox("Evidence flag", [None, *FLAGS], format_func=lambda value: FLAGS.get(value, "All records"))
    matches = projects
    if query:
        matches = matches[
            matches["contractId"].str.contains(query, case=False, regex=False, na=False)
            | matches["description"].str.contains(query, case=False, regex=False, na=False)
        ]
    if category != "All categories":
        matches = matches[matches["infrastructureCategories"].map(lambda values: category in values)]
    if flag:
        matches = matches[matches[flag].fillna(False)]
    if matches.empty:
        st.info("No contracts match these filters.")
        return

    st.caption(f"{len(matches):,} matching contracts")
    visible = matches.head(200)
    st.dataframe(
        visible[["contractId", "description", "status", "completionDate", "totalImages"]],
        hide_index=True,
        column_config={
            "contractId": "Contract ID",
            "description": "Project",
            "status": "Status",
            "completionDate": "Reported completion",
            "totalImages": "Images reported",
        },
    )
    if len(matches) > len(visible):
        st.caption("Showing the first 200 matches. Narrow the search to see another contract.")

    ids = visible["contractId"].tolist()
    default = ids.index("20CE0010") if "20CE0010" in ids else 0
    contract = st.selectbox("Review a contract", ids, index=default)
    row = visible.loc[visible["contractId"].eq(contract)].iloc[0]
    st.subheader(contract)
    st.write(row["description"])
    details = st.columns(3)
    details[0].metric("Reported status", row["status"] if pd.notna(row["status"]) else "Unavailable")
    details[1].metric("Reported completion", format_date(row["completionDate"]))
    details[2].metric("Latest evidence date", format_date(row["evidenceDate"]))
    source = {"dpwh_metadata": "DPWH image metadata", "supplied_photos": "Supplied photo dates"}
    st.caption(f"Date source · {source.get(row['evidenceSource'], 'Unavailable')}")
    for key, label in FLAGS.items():
        if row[key]:
            st.warning(label)
    days = row["daysFromCompletion"]
    if pd.notna(days) and days:
        direction = "after" if days > 0 else "before"
        st.write(f"Latest evidence is {abs(days)} days {direction} reported completion.")
    st.info("Progress rate needs comparable dated photos of the same component and a schedule benchmark.")
    st.link_button("Open DPWH contract and gallery", f"https://transparency.dpwh.gov.ph/?project={contract}")


def format_date(value):
    return value.strftime("%d %b %Y") if pd.notna(value) else "Unavailable"


if __name__ == "__main__":
    main()
