from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import os
import json
import sqlite3
import logging
from math import sqrt
from evidence_policy import (ASSESSMENT_VERSION, INFERENCE_VERSION, EVIDENCE_LIMITATION,
    checkpoint_digest, evidence_fingerprint, fingerprint, model_quality)
from hashlib import sha256
from importlib import reload
from inspect import signature
from contextlib import closing
from pathlib import Path
from threading import Event, Lock, Thread
from urllib.parse import quote, urlencode, urlsplit

import altair as alt
import pandas as pd
import requests
import streamlit as st
from ui import apply_theme, navigation, page_header

from audit_image_evidence import PHOTO_COLUMNS, audit_images
import contract_assessment

# Streamlit can rerun this script while keeping a pre-update assessment module in memory.
if getattr(contract_assessment, "ASSESSMENT_VERSION", None) != ASSESSMENT_VERSION:
    reload(contract_assessment)

from contract_assessment import (
    COMPLETION_TIMING_GROUPS, completion_timing_group,
    BRIDGE_CONFIDENCE, BUILDING_CONFIDENCE, BUDGET_FLAG_PERCENT, NEUTRAL_REVIEW_CATEGORY, PHOTOS_BEFORE_COMPLETION, PRE_COMPLETION_EQUIPMENT,
    assess_contract, reported_day, status_and_date_flags,
)
from photo_outline import outline_photo
import project_review
if (getattr(project_review, "REVIEW_VERSION", None) != ASSESSMENT_VERSION
        or getattr(project_review, "CONTRACT_SCORING_VERSION", None) != 3
        or not hasattr(project_review, "contractor_performance")
        or not hasattr(project_review, "plain_photo_finding")
        or not hasattr(project_review, "UNFINISHED_WORK_PENALTY")):
    reload(project_review)

from project_review import (
    DEFAULT_OLLAMA_MODEL, ReviewError, compare_contracts,
    summarize_regional_evidence, review_project, select_review_photos, contractor_performance, plain_photo_finding,
    CONTRACT_SCORE_LEVELS, UNFINISHED_WORK_PENALTY,
)
import road_surface

# Streamlit may retain the detector module across script reruns.
if not hasattr(road_surface.PhotoSegmentationModel, "analyze_image"):
    reload(road_surface)

from road_surface import (
    BRIDGE_NAMES, BUILDING_NAMES, BUILDING_STAGE_NAMES, FLOOD_NAMES, RIVERBANK_NAMES, STAINLESS_WATER_NAMES, STREET_DRAIN_NAMES,
    SURFACE_NAMES, WATER_NAMES, SLOPE_PROTECTION_NAMES,
    SCAFFOLDING_NAMES,
    EQUIPMENT_NAMES, UNDER_CONSTRUCTION_LABELS, UNDER_CONSTRUCTION_NAMES,
    PhotoSegmentationModel,
    download_photo,
)
from yolo_feedback import (UNDER_CONSTRUCTION_TRAINING, UNFINISHED_TARGETS, active_weights,
                           feedback_folder, latest_candidate, read_correction, save_correction)


API_URL = "https://api.dpwh.bettergov.ph"
REFRESH_INTERVAL_SECONDS = 60
PROJECT_DIR = Path(__file__).resolve().parent
PHOTO_REVIEW_DB = Path(os.environ.get(
    "PROJECTWATCH_PHOTO_REVIEW_DB", PROJECT_DIR / "data/reviewed_photos.sqlite3",
))
PHOTO_REVIEW_VERSION = INFERENCE_VERSION
FLOOD_CATEGORY = "Flood Control and Drainage"
STREET_DRAIN_DETECTOR = "Street drain grates"
RIVERBANK_DETECTOR = "Riverbank protection"
WATER_CATEGORY = "Water Provision and Storage"
STAINLESS_WATER_DETECTOR = "Stainless steel water tanks"
BUILDING_STAGE_DETECTOR = "Building under construction"
SCAFFOLDING_DETECTOR = "Temporary scaffolding"
EQUIPMENT_DETECTOR = "Construction equipment"


def checkpoint_path(env_var, baseline, refined):
    configured = os.environ.get(env_var)
    if configured:
        return Path(configured)
    candidate = PROJECT_DIR / refined
    return candidate if candidate.is_file() else PROJECT_DIR / baseline


YOLO_WEIGHTS = checkpoint_path(
    "PROJECTWATCH_YOLO_WEIGHTS",
    "data/yolo_surface/runs/road_seg/weights/best.pt",
    "data/yolo_surface/runs/road_refined/weights/best.pt",
)
FLOOD_YOLO_WEIGHTS = Path(os.environ.get(
    "PROJECTWATCH_FLOOD_YOLO_WEIGHTS",
    Path(__file__).resolve().parent / "data/yolo_flood/runs/flood_channel_seg/weights/best.pt",
))
BUILDING_YOLO_WEIGHTS = Path(os.environ.get(
    "PROJECTWATCH_BUILDING_YOLO_WEIGHTS",
    Path(__file__).resolve().parent / "data/yolo_building/runs/building_seg/weights/best.pt",
))
BUILDING_STAGE_WEIGHTS = Path(os.environ.get(
    "PROJECTWATCH_BUILDING_STAGE_YOLO_WEIGHTS",
    PROJECT_DIR / "data/yolo_building_stage/runs/building_stage/weights/best.pt",
))
SCAFFOLDING_WEIGHTS = Path(os.environ.get(
    "PROJECTWATCH_SCAFFOLDING_YOLO_WEIGHTS",
    PROJECT_DIR / "data/yolo_scaffolding/shoring-2.pt",
))
EQUIPMENT_WEIGHTS = Path(os.environ.get(
    "PROJECTWATCH_EQUIPMENT_YOLO_WEIGHTS",
    PROJECT_DIR / "data/yolo_equipment/construction-equipment.pt",
))
BRIDGE_YOLO_WEIGHTS = checkpoint_path(
    "PROJECTWATCH_BRIDGE_YOLO_WEIGHTS",
    "data/yolo_infrastructure/bridge-yoloe.pt",
    "data/yolo_bridge/runs/bridge_refined/weights/best.pt",
)
WATER_YOLO_WEIGHTS = Path(os.environ.get(
    "PROJECTWATCH_WATER_YOLO_WEIGHTS",
    Path(__file__).resolve().parent / "data/yolo_infrastructure/water-yoloe.pt",
))
RIVERBANK_YOLO_WEIGHTS = Path(os.environ.get(
    "PROJECTWATCH_RIVERBANK_YOLO_WEIGHTS",
    PROJECT_DIR / "data/yolo_riverbank/runs/riverbank_seg/weights/best.pt",
))
STAINLESS_WATER_WEIGHTS = Path(os.environ.get(
    "PROJECTWATCH_STAINLESS_WATER_YOLO_WEIGHTS",
    PROJECT_DIR / "data/yolo_water_stainless/runs/stainless_seg/weights/best.pt",
))
STREET_DRAIN_WEIGHTS = Path(os.environ.get(
    "PROJECTWATCH_STREET_DRAIN_YOLO_WEIGHTS",
    PROJECT_DIR / "data/yolo_street_drain/street-drain-yoloe.pt",
))
SLOPE_PROTECTION_DETECTOR = "Slope protection"
SLOPE_PROTECTION_WEIGHTS = Path(os.environ.get(
    "PROJECTWATCH_SLOPE_PROTECTION_YOLO_WEIGHTS",
    PROJECT_DIR / "data/yolo_infrastructure/slope-protection-yoloe.pt",
))
UNDER_CONSTRUCTION_BASELINE = PROJECT_DIR / "data/yolo_infrastructure/under-construction-yoloe.pt"
UNDER_CONSTRUCTION_WEIGHTS = checkpoint_path(
    "PROJECTWATCH_UNDER_CONSTRUCTION_YOLO_WEIGHTS",
    "data/yolo_infrastructure/under-construction-yoloe.pt",
    "data/yolo_infrastructure/under-construction-trained.pt",
)
YOLO_MODELS = {
    UNDER_CONSTRUCTION_TRAINING: (
        UNDER_CONSTRUCTION_WEIGHTS,
        UNDER_CONSTRUCTION_NAMES, "unfinished work, scaffolding, and construction equipment", 960,
    ),
    **{
        detector: (PROJECT_DIR / f"data/yolo_{dataset}/runs/{dataset}/weights/best.pt",
                   {label: label}, label, 640)
        for detector, label, dataset in UNFINISHED_TARGETS.values()
    },
    SLOPE_PROTECTION_DETECTOR: (SLOPE_PROTECTION_WEIGHTS, SLOPE_PROTECTION_NAMES, "visible slope protection", 640),
    "Roads": (YOLO_WEIGHTS, SURFACE_NAMES, "visible paved and unpaved road surfaces", 512),
    FLOOD_CATEGORY: (FLOOD_YOLO_WEIGHTS, FLOOD_NAMES, "visible lined drainage channels", 640),
    STREET_DRAIN_DETECTOR: (STREET_DRAIN_WEIGHTS, STREET_DRAIN_NAMES, "visible street drain grates", 640),
    RIVERBANK_DETECTOR: (
        RIVERBANK_YOLO_WEIGHTS, RIVERBANK_NAMES, "visible concrete riverbank protection", 640,
    ),
    "Buildings and Facilities": (BUILDING_YOLO_WEIGHTS, BUILDING_NAMES, "visible building structures and facilities", 512),
    BUILDING_STAGE_DETECTOR: (BUILDING_STAGE_WEIGHTS, BUILDING_STAGE_NAMES, "visible signs of unfinished building work", 512),
    SCAFFOLDING_DETECTOR: (SCAFFOLDING_WEIGHTS, SCAFFOLDING_NAMES, "visible temporary scaffolding and timber shoring", 960),
    EQUIPMENT_DETECTOR: (EQUIPMENT_WEIGHTS, EQUIPMENT_NAMES, "visible construction equipment", 640),
    "Bridges": (BRIDGE_YOLO_WEIGHTS, BRIDGE_NAMES, "visible bridges", 640),
    WATER_CATEGORY: (
        WATER_YOLO_WEIGHTS, WATER_NAMES, "visible elevated water tanks", 640,
    ),
    STAINLESS_WATER_DETECTOR: (
        STAINLESS_WATER_WEIGHTS, STAINLESS_WATER_NAMES, "visible stainless steel water tanks", 640,
    ),
}
ADDITIONAL_PHOTO_DETECTORS = {
    "Buildings and Facilities": (SCAFFOLDING_DETECTOR,),
    FLOOD_CATEGORY: (RIVERBANK_DETECTOR, STREET_DRAIN_DETECTOR),
    WATER_CATEGORY: (STAINLESS_WATER_DETECTOR, "Buildings and Facilities", SCAFFOLDING_DETECTOR),
}
PHILIPPINE_TIME = timezone(timedelta(hours=8), "PHT")
API_REGIONS = [
    "National Capital Region", "Cordillera Administrative Region", "Region I", "Region II",
    "Region III", "Region IV-A", "Region IV-B", "Region V", "Region VI", "Region VII",
    "Region VIII", "Region IX", "Region X", "Region XI", "Region XII", "Region XIII",
    "Bangsamoro Autonomous Region in Muslim Mindanao", "Negros Island Region", "Central Office",
]
API_CATEGORIES = [
    "Roads", "Bridges", "Buildings and Facilities", "Flood Control and Drainage",
    "Water Provision and Storage",
]
API_STATUSES = ["Completed", "On-Going", "Not Yet Started", "For Procurement", "Terminated"]


FLAGS = {
    "missingImages": "No images reported",
    "imageDateMismatch": "Photo date differs from completion",
    "imageAvailabilityUnknown": "Image availability unknown",
    "missingImageDate": "Image date unavailable",
    "missingCompletionDate": "Completion date unavailable",
}


def satellite_location(detail):
    location = detail.get("location") or {}
    for source in (location.get("coordinates"), detail.get("coordinates"), detail):
        if not isinstance(source, dict):
            continue
        try:
            lat, lon = float(source["latitude"]), float(source["longitude"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if -90 <= lat <= 90 and -180 <= lon <= 180 and (lat or lon):
            query = urlencode({"zoom": 16, "lat": lat, "lng": lon, "datasetId": "S2_L2A_CDAS",
                               "layerId": "1_TRUE_COLOR", "date": "latest"})
            return lat, lon, f"https://browser.dataspace.copernicus.eu/?{query}"
    return None


@st.cache_data(ttl=3600, max_entries=128, show_spinner=False)
def satellite_capture_date(latitude, longitude, map_level):
    response = requests.get(
        "https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/0/query",
        params={
            "geometry": f"{longitude},{latitude}", "geometryType": "esriGeometryPoint", "inSR": 4326,
            "where": f"MinMapLevel <= {map_level} AND MaxMapLevel >= {map_level}",
            "outFields": "SRC_DATE", "returnGeometry": "false", "f": "json",
        },
        timeout=(3, 5),
    )
    response.raise_for_status()
    dates = {feature["attributes"]["SRC_DATE"] for feature in response.json().get("features", [])}
    if len(dates) == 1:
        return datetime.strptime(str(dates.pop()), "%Y%m%d").date()
    return None


@st.dialog("Satellite imagery", width="large")
def show_satellite_preview(latitude, longitude, copernicus_url):
    bounds = (longitude - 0.0055, latitude - 0.004, longitude + 0.0055, latitude + 0.004)
    query = urlencode({
        "bbox": ",".join(f"{value:.7f}" for value in bounds),
        "bboxSR": 4326, "imageSR": 3857, "size": "800,600", "format": "jpg", "f": "image",
    })
    url = f"https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export?{query}"
    try:
        response = requests.get(url, timeout=(4, 10))
        response.raise_for_status()
        if not response.headers.get("Content-Type", "").startswith("image/jpeg"):
            raise ValueError("Unexpected imagery response")
        st.image(response.content, width="stretch")
    except (requests.RequestException, ValueError):
        st.warning("The satellite preview is unavailable. You can open Copernicus Browser below.")
    captured = None
    try:
        # Estimate the basemap level from the preview's horizontal pixel span.
        from math import log2

        map_level = round(log2(360 * 800 / (256 * (bounds[2] - bounds[0]))))
        captured = satellite_capture_date(latitude, longitude, map_level)
    except (requests.RequestException, KeyError, TypeError, ValueError):
        pass
    st.caption(
        f"Centered on reported coordinates · {latitude:.6f}, {longitude:.6f}. "
        "Imagery: Esri, Maxar, Earthstar Geographics, and the GIS User Community. "
    )
    st.caption(f"Capture date at map center · {captured:%B %d, %Y}" if captured else
               "Capture date at map center unavailable.")
    st.link_button("Browse dated imagery in Copernicus", copernicus_url)


def show_satellite_link(detail, *, key):
    if satellite := satellite_location(detail):
        latitude, longitude, url = satellite
        st.caption(f"Reported coordinates · {latitude:.6f}, {longitude:.6f}")
        if st.button("Open satellite imagery", key=key):
            show_satellite_preview(latitude, longitude, url)


def project_url(contract_id):
    return f"https://transparency.dpwh.gov.ph/?{urlencode({'project': contract_id})}"


def main():
    st.set_page_config(page_title="BantAIProyekto | Infrastructure review", page_icon=":material/account_balance:",
                       layout="wide", initial_sidebar_state="expanded")
    apply_theme()
    # Preserve useful controls when their workspace is not rendered on a rerun.
    for key in ("admin_section", "live_search", "live_region", "live_category", "live_status", "live_year",
                "live_photos_only", "live_limit", "live_page", "live_contract", "saved_review_category",
                "saved_review_search", "regional_region", "regional_year", "regional_category",
                "regional_status", "regional_search"):
        if key in st.session_state:
            st.session_state[key] = st.session_state[key]
    page = navigation()
    # Render the chosen workspace only; inactive admin views no longer fetch or run.
    if page == "Explore projects":
        page_header("Explore public projects", "Find DPWH contracts and inspect the records behind each project.")
        show_live_projects()
    elif page == "Regional review":
        page_header("Regional review", "Browse saved regional findings, contractor scores, and contract evidence.", "FINDINGS")
        show_regional_review_page()
    else:
        show_admin()
    with st.expander("About the evidence"):
        st.caption(EVIDENCE_LIMITATION)
        st.caption("Photo findings support manual review. Photo dates are capture dates, not independently verified completion dates.")


def show_admin():
    page_header("Admin workspace", "Manage reviewed contracts, refresh saved evidence and check detector readiness.", "REVIEW MANAGEMENT")
    with closing(open_assessment_db()) as database:
        groups = dict(database.execute("SELECT category,COUNT(*) FROM contract_assessments GROUP BY category"))
        pending = database.execute("SELECT COUNT(*) FROM regional_queue WHERE done=0").fetchone()[0]
    metrics = st.columns(3)
    metrics[0].metric("Saved contract reviews", f"{sum(groups.values()):,}")
    attention = sum(count for category, count in groups.items()
                    if category in {NEUTRAL_REVIEW_CATEGORY, "Photos inconclusive", "Photo analysis unavailable · retry needed"})
    metrics[1].metric("Need evidence review", f"{attention:,}", help="Neutral, inconclusive and unavailable-analysis categories.")
    metrics[2].metric("Queued checks", f"{pending:,}", help="Pending queue entries; a contract may appear in more than one queue.")
    st.write("")
    section = st.radio("Admin section", ["Reviewed contracts", "Regional reviews", "Contract reviews", "Rerun reviews", "Model status"],
                       horizontal=True, key="admin_section", label_visibility="collapsed")
    st.divider()
    if section == "Reviewed contracts":
        st.subheader("Reviewed contracts")
        st.caption("Inspect saved evidence, update a review category or add a labeled training example.")
        show_reviewed_contracts()
    elif section == "Regional reviews":
        show_regional_review_page(manage=True)
    elif section == "Contract reviews":
        st.subheader("Review individual contracts")
        query = st.text_input("Contract IDs", placeholder="Enter up to three IDs separated by commas")
        contracts = tuple(dict.fromkeys(value.strip().upper() for value in query.split(",") if value.strip()))
        if len(contracts) > 3:
            st.info("Enter up to three contract IDs.")
        elif contracts:
            show_ai_reviews(contracts, contracts[0])
    elif section == "Rerun reviews":
        st.subheader("Refresh saved reviews")
        st.caption("Choose a category to recheck with the current models. Manual decisions are preserved.")
        show_category_reruns()
    else:
        show_model_status()


def show_model_status():
    st.subheader("Model readiness")
    st.caption("See which detectors are available and how their findings can be used.")
    st.caption("Available checkpoints contribute detections under the assessment rules.")
    rows = []
    for detector, (baseline, _, _, _) in YOLO_MODELS.items():
        path = active_weights(detector, baseline)
        status = model_quality(detector, path) if path.is_file() else "missing"
        rows.append({"Detector": detector, "Evaluation status": {"validated": "Validated", "provisional": "Provisional", "review_only": "Experimental", "missing": "Not installed"}[status],
                     "Checkpoint": path.name, "SHA256": checkpoint_digest(path)[:12] if path.is_file() else ""})
    st.dataframe(rows, hide_index=True)
    st.caption(f"Assessment policy {ASSESSMENT_VERSION} · inference format {INFERENCE_VERSION}")
    with st.expander("Technical details and release checks"):
        st.code("python evaluate_checkpoint.py --help\npython promote_checkpoint.py --help", language="bash")


def open_assessment_db():
    PHOTO_REVIEW_DB.parent.mkdir(parents=True, exist_ok=True)
    database = sqlite3.connect(PHOTO_REVIEW_DB, timeout=10)
    try:
        with database:
            database.execute("""
                CREATE TABLE IF NOT EXISTS contract_assessments (
                    contract_id TEXT PRIMARY KEY, category TEXT NOT NULL,
                    reviewed_at TEXT NOT NULL, summary TEXT NOT NULL
                )
            """)
            database.execute("""
                CREATE TABLE IF NOT EXISTS manual_completions (
                    contract_id TEXT PRIMARY KEY, evidence_file TEXT NOT NULL
                )
            """)
            database.execute("""
                CREATE TABLE IF NOT EXISTS manual_categories (
                    contract_id TEXT PRIMARY KEY, category TEXT NOT NULL, note TEXT NOT NULL
                )
            """)
            database.execute("""
                CREATE TABLE IF NOT EXISTS regional_queue (
                    filters TEXT NOT NULL, position INTEGER NOT NULL, contract_id TEXT NOT NULL,
                    staged TEXT, done INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (filters, position)
                )
            """)
            database.execute("""
                CREATE TABLE IF NOT EXISTS regional_highlights (
                    filters TEXT PRIMARY KEY, report TEXT NOT NULL
                )
            """)
            database.execute("""CREATE TABLE IF NOT EXISTS review_history (
                revision INTEGER PRIMARY KEY, contract_id TEXT NOT NULL, category TEXT NOT NULL,
                reviewed_at TEXT NOT NULL, summary TEXT NOT NULL, archived_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")
            database.execute("""CREATE TRIGGER IF NOT EXISTS archive_review_update
                BEFORE UPDATE ON contract_assessments WHEN OLD.summary != NEW.summary BEGIN
                INSERT INTO review_history(contract_id,category,reviewed_at,summary)
                VALUES (OLD.contract_id,OLD.category,OLD.reviewed_at,OLD.summary); END""")
            database.execute("""CREATE TABLE IF NOT EXISTS manual_review_metadata (
                contract_id TEXT PRIMARY KEY, evidence_hash TEXT NOT NULL, reviewed_at TEXT NOT NULL,
                reviewer TEXT NOT NULL
            )""")
    except sqlite3.Error:
        database.close()
        raise
    return database


def assess_with_manual_review(record):
    assessment = assess_contract(record)
    with closing(open_assessment_db()) as database:
        metadata = database.execute("SELECT evidence_hash, reviewed_at, reviewer FROM manual_review_metadata WHERE contract_id = ?",
                                    (record["contractId"],)).fetchone()
        manual = database.execute(
            "SELECT evidence_file FROM manual_completions WHERE contract_id = ?",
            (record["contractId"],),
        ).fetchone()
        override = database.execute(
            "SELECT category, note FROM manual_categories WHERE contract_id = ?", (record["contractId"],),
        ).fetchone()
    if manual:
        assessment["completion_evidence_category"] = "Completed · manual review"
        assessment["completion_assessment"] = "Marked completed by manual review"
        assessment["manual_completion_evidence"] = manual[0]
    if override:
        assessment["completion_evidence_category"] = override[0]
        assessment["completion_assessment"] = f"Manual review · {override[0]}"
        assessment["manual_category_note"] = override[1]
    if manual or override:
        assessment["manual_review_provenance"] = {"evidence_hash": metadata[0], "reviewed_at": metadata[1], "reviewer": metadata[2]} if metadata else {"legacy": True}
        if not metadata or metadata[0] != evidence_fingerprint(record):
            assessment["flags"].append("Manual decision retained; underlying evidence changed or original evidence provenance is unavailable")
    return assessment


def current_model_signature():
    return fingerprint({
        name: [{"sha256": checkpoint_digest(path), "quality": model_quality(name, path)}
               for path in review_checkpoints(name) if path.is_file()]
        for name in YOLO_MODELS
    })


def review_is_stale(record):
    return (record.get("assessment", {}).get("assessment_version") != ASSESSMENT_VERSION
            or record.get("review_provenance", {}).get("models") != current_model_signature()
            or record.get("assessment", {}).get("legacy_detection_format", False))


def review_due(record, reviewed_at):
    if review_is_stale(record):
        return True
    days = 1 if (record.get("status") != "Completed" or not record.get("galleryPhotoCount")
                 or record.get("assessment", {}).get("retry_required")) else 7
    try:
        previous = datetime.fromisoformat(reviewed_at)
        if previous.tzinfo is None:
            previous = previous.replace(tzinfo=PHILIPPINE_TIME)
        return datetime.now(PHILIPPINE_TIME) - previous >= timedelta(days=days)
    except ValueError:
        return True


def save_contract_assessments(summaries, *, require_saved=False):
    reviewed_at = datetime.now(PHILIPPINE_TIME).isoformat()
    try:
        with closing(open_assessment_db()) as database, database:
            database.executemany("""
                INSERT INTO contract_assessments VALUES (?, ?, ?, ?)
                ON CONFLICT(contract_id) DO UPDATE SET
                    category=excluded.category, reviewed_at=excluded.reviewed_at, summary=excluded.summary
                WHERE excluded.reviewed_at >= contract_assessments.reviewed_at
            """, (
                (item["contractId"], item["assessment"]["completion_evidence_category"],
                 reviewed_at, json.dumps(item, ensure_ascii=False))
                for item in summaries
            ))
    except (OSError, sqlite3.Error):
        if require_saved:
            raise
        st.warning("The review finished, but its category could not be saved.")


def refresh_inconclusive_reviews(database):
    source_groups = ("Photos inconclusive", NEUTRAL_REVIEW_CATEGORY, PHOTOS_BEFORE_COMPLETION)
    rows = database.execute("""
        SELECT contract_id, category, summary FROM contract_assessments AS saved
        WHERE category IN (?, ?, ?)
          AND NOT EXISTS (SELECT 1 FROM manual_categories WHERE contract_id = saved.contract_id)
          AND NOT EXISTS (SELECT 1 FROM manual_completions WHERE contract_id = saved.contract_id)
    """, source_groups).fetchall()
    with database:
        for contract, previous, payload in rows:
            record = json.loads(payload)
            assessment = assess_contract(record)
            category = assessment["completion_evidence_category"]
            new_equipment_flag = PRE_COMPLETION_EQUIPMENT in assessment["flags"] and (
                PRE_COMPLETION_EQUIPMENT not in record["assessment"]["flags"]
            )
            if category == previous and not new_equipment_flag:
                continue
            if previous == NEUTRAL_REVIEW_CATEGORY and category != PHOTOS_BEFORE_COMPLETION:
                continue
            record["assessment"] = assessment
            record.pop("regional_review", None)
            database.execute("""
                UPDATE contract_assessments SET category = ?, summary = ?
                WHERE contract_id = ? AND category = ? AND summary = ?
                  AND NOT EXISTS (SELECT 1 FROM manual_categories WHERE contract_id = contract_assessments.contract_id)
                  AND NOT EXISTS (SELECT 1 FROM manual_completions WHERE contract_id = contract_assessments.contract_id)
            """, (category, json.dumps(record, ensure_ascii=False), contract, previous, payload))


@st.fragment
def show_reviewed_contracts():
    if message := st.session_state.pop("saved_review_rerun_result", None):
        st.success(message)
    workspace = st.radio("Review view", ["Review workspace", "Saved groups"], horizontal=True) == "Review workspace"
    refresh = st.button("Refresh saved groups")
    if st.session_state.pop("category_training_saved", False):
        st.success("Moved the contract and saved the photo for YOLO training.")
    if st.session_state.pop("training_example_saved", False):
        st.success("Saved for the next training run in the YOLO Tester.")
    try:
        with closing(open_assessment_db()) as database:
            if refresh:
                refresh_inconclusive_reviews(database)
            groups = database.execute(
                "SELECT category, COUNT(*) FROM contract_assessments GROUP BY category ORDER BY category"
            ).fetchall()
            if not groups:
                st.info("Analyze a contract to save its completion evidence category here.")
                return
            counts = dict(groups)
            completion_groups = (
                PHOTOS_BEFORE_COMPLETION,
                NEUTRAL_REVIEW_CATEGORY,
                "Completed · manual review",
                *COMPLETION_TIMING_GROUPS,
                "Possible unfinished work shown after reported completion",
                "Completion not reported",
                "Reported status unavailable",
                "No photo evidence",
                "Photo evidence unavailable",
                "Photos inconclusive",
            )
            options = set(counts).union(completion_groups)
            if selected_group := st.session_state.get("saved_review_category"):
                options.add(selected_group)
            options = sorted(options)
            moved_group = st.session_state.pop("saved_review_moved_from", None)
            if moved_group in options:
                st.session_state["saved_review_category"] = moved_group
            elif st.session_state.get("saved_review_category") not in options:
                st.session_state["saved_review_category"] = groups[0][0]
            group = st.selectbox("Completion evidence group", options,
                                 key="saved_review_category",
                                 format_func=lambda label: f"{label} ({counts.get(label, 0):,})")
            search_key = "saved_review_search"
            search = (st.session_state.get(search_key) or "").strip() if workspace else ""
            if moved_group == group and search and not database.execute("""
                SELECT 1 FROM contract_assessments
                WHERE category = ? AND instr(upper(contract_id), upper(?)) > 0 LIMIT 1
            """, (group, search)).fetchone():
                st.session_state[search_key] = ""
                search = ""
            if workspace:
                search = st.text_input("Find a contract by ID", placeholder="Enter all or part of a contract ID",
                                       key=search_key).strip()
            ids = [row[0] for row in database.execute("""
                SELECT contract_id FROM contract_assessments
                WHERE category = ? AND instr(upper(contract_id), upper(?)) > 0
                ORDER BY reviewed_at DESC, contract_id
            """, (group, search))]
            if not ids:
                st.info("No contracts match this search." if search else f"No contracts remain in {group}.")
                return
            navigation_key = f"saved_review_contract_{group}"
            if next_contract := st.session_state.pop(f"advance_{navigation_key}", None):
                st.session_state[navigation_key] = next_contract
            if st.session_state.get(navigation_key) not in ids:
                st.session_state[navigation_key] = ids[0]
            position = ids.index(st.session_state[navigation_key])
            if workspace:
                previous, counter, following = st.columns([1, 2, 1])
                previous.button("Previous", disabled=position == 0, on_click=select_saved_contract,
                                args=(navigation_key, ids[max(0, position - 1)]))
                counter.caption(f"{position + 1:,} of {len(ids):,} matching contracts")
                following.button("Next", disabled=position == len(ids) - 1, on_click=select_saved_contract,
                                 args=(navigation_key, ids[min(len(ids) - 1, position + 1)]))
            selected = st.selectbox("Saved contract", ids, key=navigation_key)
            row = database.execute(
                "SELECT reviewed_at, summary FROM contract_assessments WHERE contract_id = ?", (selected,),
            ).fetchone()
    except (OSError, sqlite3.Error):
        st.warning("Saved contract groups are unavailable.")
        return
    if not row:
        st.info("This saved review is unavailable. Refresh saved groups.")
        return
    reviewed_at, payload = row
    record = json.loads(payload)
    if review_is_stale(record):
        st.warning("Saved review is stale: rules, checkpoints, or evidence format changed. Rerun to refresh.")
    st.caption(EVIDENCE_LIMITATION)
    remaining = ids[position + 1:] or ids[:position]
    advance = (navigation_key, remaining[0]) if remaining else None
    if workspace:
        st.subheader(selected)
        if description := record.get("description"):
            st.write(description)
        details = st.columns(3)
        details[0].caption(f"Reported status · {record.get('status') or 'Unavailable'}")
        details[1].caption(f"Reported completion · {(record.get('dates') or {}).get('Reported completion') or 'Unavailable'}")
        details[2].caption(f"Photos in review · {len(record['photos'])}")
    st.caption(f"Last reviewed · {reviewed_at}")
    with st.expander("Review provenance and previous revisions"):
        st.json(record.get("review_provenance", {"status": "Legacy review; rerun required"}))
        if provenance := record.get("assessment", {}).get("manual_review_provenance"):
            st.json(provenance)
        with closing(open_assessment_db()) as db:
            history = db.execute("SELECT revision,category,reviewed_at,archived_at FROM review_history WHERE contract_id=? ORDER BY revision DESC LIMIT 20", (selected,)).fetchall()
        if history:
            st.dataframe([dict(zip(("Revision", "Category", "Reviewed", "Archived"), row)) for row in history], hide_index=True)
    st.link_button("Open DPWH contract", project_url(selected))
    if "coordinates" in record:
        show_satellite_link(record, key="saved_satellite")
    else:
        try:
            detail, _ = api_data(f"/projects/{quote(selected, safe='')}")
        except (requests.RequestException, KeyError, ValueError):
            detail = {}
        show_satellite_link(detail, key="saved_satellite")
    assessment = record["assessment"]
    if timing := assessment.get("completion_photo_timing"):
        st.caption(timing)
    if st.button(
        "Rerun review", key=f"rerun_saved_review_{selected}",
    ):
        try:
            with st.spinner("Checking photos with the current YOLO checkpoints…"):
                updated = summarize_contract(selected, save=False)
                if updated["galleryPhotoCount"] is None:
                    raise ValueError("Gallery unavailable")
                save_contract_assessments([updated], require_saved=True)
        except (requests.RequestException, OSError, sqlite3.Error, KeyError, ValueError):
            st.warning("The review couldn’t be refreshed. Your previous review is still saved.")
        else:
            category = updated["assessment"]["completion_evidence_category"]
            st.session_state["saved_review_rerun_result"] = f"{selected} · {category}"
            if category != group:
                advance_saved_review(advance, moved_from=group)
            st.rerun()
    work_evidence = record["assessment"].get("unfinished_work_evidence", {})
    if work_evidence:
        st.warning(f"Possible under construction · {len(work_evidence)} photo(s) on or after reported completion")
    assessment["flags"] = list(dict.fromkeys([*assessment["flags"], *status_and_date_flags(record)]))
    for flag in assessment["flags"]:
        st.warning(flag)
    if review := record.get("regional_review"):
        st.write(review["summary"])
    if evidence := record["assessment"].get("manual_completion_evidence"):
        photo_path = PHOTO_REVIEW_DB.parent / "manual_evidence" / evidence
        if photo_path.is_file():
            st.image(str(photo_path), caption="Manual completion evidence")
    if workspace:
        gallery, decision = st.columns([3, 2])
        with gallery:
            if record["photos"]:
                show_training_feedback(record, categories=completion_groups, workspace=True, advance=advance,
                                       work_evidence=work_evidence)
            else:
                st.info("No photos are saved with this review.")
        with decision:
            st.markdown("**Move to another review category**")
            show_manual_category(record, completion_groups, advance=advance)
    else:
        with st.expander("Move to another review category"):
            show_manual_category(record, completion_groups, advance=advance)
        if record["photos"]:
            show_training_feedback(record, categories=completion_groups, advance=advance, work_evidence=work_evidence)
    with st.expander("Saved contract details"):
        st.json(record)


def select_saved_contract(key, contract):
    st.session_state[key] = contract


def save_manual_category(record, category, note):
    contract = record["contractId"]
    updated = {
        **record, "assessment": {
            **record["assessment"], "completion_evidence_category": category,
            "completion_assessment": f"Manual review · {category}", "manual_category_note": note,
        },
    }
    updated.pop("regional_review", None)
    with closing(open_assessment_db()) as database, database:
        database.execute("INSERT OR REPLACE INTO manual_categories VALUES (?, ?, ?)", (contract, category, note))
        database.execute("INSERT OR REPLACE INTO manual_review_metadata VALUES (?, ?, ?, ?)",
                         (contract, evidence_fingerprint(record), datetime.now(PHILIPPINE_TIME).isoformat(),
                          os.environ.get("PROJECTWATCH_REVIEWER", "local reviewer")))
        database.execute(
            "UPDATE contract_assessments SET category = ?, reviewed_at = ?, summary = ? WHERE contract_id = ?",
            (category, datetime.now(PHILIPPINE_TIME).isoformat(), json.dumps(updated, ensure_ascii=False), contract),
        )


def eligible_review_categories(record, categories, photo=None):
    photos = record["photos"]
    assessment = record["assessment"]
    completion = reported_day((record.get("dates") or {}).get("Reported completion"))
    capture_days = [day for item in photos if (day := reported_day(item.get("capturedAt")))]
    captured = reported_day(photo.get("capturedAt")) if photo is not None else max(capture_days, default=None)
    allowed = {"Photos inconclusive", NEUTRAL_REVIEW_CATEGORY} if photos else set()
    if completion and capture_days and max(capture_days) < completion:
        allowed.add(PHOTOS_BEFORE_COMPLETION)
    if captured and completion and captured >= completion:
        offset = (captured - completion).days
        allowed.add("Completed · manual review")
        allowed.add(completion_timing_group(offset))
        if offset > 0:
            allowed.add("Possible unfinished work shown after reported completion")
    if not photos:
        allowed.add("No photo evidence" if record.get("galleryPhotoCount") == 0 else "Photo evidence unavailable")
    if not record.get("status"):
        allowed.add("Reported status unavailable")
    elif record["status"].casefold() != "completed":
        allowed.add("Completion not reported")
    return [category for category in categories if category in allowed and category != assessment["completion_evidence_category"]]


def show_manual_category(record, categories, *, advance=None):
    contract = record["contractId"]
    options = eligible_review_categories(record, categories)
    if not options:
        st.caption("No other category matches the saved evidence.")
        return
    with st.form(f"manual_category_{contract}"):
        category = st.selectbox("Move to", options,
                                index=None, placeholder="Choose a category")
        note = st.text_input("Reason for the decision", value=record["assessment"].get("manual_category_note", ""))
        if st.form_submit_button("Move contract"):
            if not category or not note.strip():
                st.warning("Choose a category and enter a reason.")
                return
            try:
                save_manual_category(record, category, note.strip())
            except (OSError, sqlite3.Error):
                st.warning("The manual decision could not be saved.")
                return
            advance_saved_review(advance, moved_from=record["assessment"]["completion_evidence_category"])


def advance_saved_review(advance, *, moved_from=None):
    if moved_from:
        st.session_state["saved_review_moved_from"] = moved_from
    if advance:
        key, contract = advance
        st.session_state[f"advance_{key}"] = contract
    st.rerun()


def show_training_feedback(record, *, categories, workspace=False, advance=None, work_evidence=None):
    contract = record["contractId"]
    with st.expander("Review photo and label for YOLO" if workspace else "Label a photo for YOLO training", expanded=workspace):
        number = st.selectbox("Reviewed photo", range(len(record["photos"])), key=f"review_training_photo_{contract}",
                              format_func=lambda i: f"Photo {i + 1} · {record['photos'][i].get('capturedAt') or 'Date unknown'}")
        source = record["photos"][number]
        if cues := (work_evidence or {}).get(number + 1):
            labels = [label for key, label in (
                ("unfinished_work_detected", "unfinished work"),
                ("scaffolding_detected", "scaffolding"),
                ("equipment_detected", "construction equipment"),
            ) if cues[key]]
            st.warning("Possible under construction · " + ", ".join(labels))
        if workspace:
            st.link_button("Open original photo", source["url"])
            if scope := source.get("scope"):
                st.caption(scope)
            for finding in source.get("yolo", []):
                st.caption(finding["observation"])
        detectors = category_detectors(source.get("category"), source.get("scope") or record.get("description"))
        if not detectors:
            detectors = tuple(YOLO_MODELS)
        construction_detectors = {BUILDING_STAGE_DETECTOR, SCAFFOLDING_DETECTOR, EQUIPMENT_DETECTOR,
                                  *(target[0] for target in UNFINISHED_TARGETS.values())}
        detectors = tuple(detector for detector in detectors if detector not in construction_detectors)
        detector = st.selectbox("Training detector", tuple(dict.fromkeys((*detectors, UNDER_CONSTRUCTION_TRAINING))))
        if st.button("Load photo for labeling", key=f"label_photo_{contract}"):
            try:
                photo = download_photo(source["url"])
            except (requests.RequestException, OSError, ValueError):
                st.warning("The photo could not be loaded.")
                return
            st.session_state["training_review_photo"] = (source["url"], photo)
        loaded = st.session_state.get("training_review_photo")
        if not loaded or loaded[0] != source["url"]:
            return
        photo = loaded[1]
        digest = sha256(f"{photo.size}".encode() + photo.tobytes()).hexdigest()
        classes = UNDER_CONSTRUCTION_LABELS if detector == UNDER_CONSTRUCTION_TRAINING else YOLO_MODELS[detector][1]
        saved = read_correction(detector, digest)
        if saved:
            st.caption("A correction for this photo and detector is saved.")
        labels = ["", *classes]
        if saved and saved["label"] not in labels and saved["label"] is not None:
            saved = {**saved, "label": next(iter(classes))}
        label = st.selectbox("Correct label", labels, index=labels.index(saved["label"] or "") if saved else None,
                             format_func=lambda value: classes[value] if value else "No target visible",
                             key=f"review_label_{detector}_{digest}")
        points = []
        append = False
        if label:
            if detector == UNDER_CONSTRUCTION_TRAINING and saved and saved["label"] == label:
                st.caption(f"Saved outlines · {len(saved['polygons'])}")
                append = st.checkbox("Add another outline", value=True, key=f"append_outline_{digest}")
            polygon = saved["polygons"][-1] if saved and saved["label"] == label and not append else []
            key = f"review_outline_{detector}_{digest}_{label}_{len(saved['polygons']) if append else 0}"
            initial = [polygon[i:i + 2] for i in range(0, len(polygon), 2)]
            state = st.session_state.get(key)
            draft = state.get("points", initial) if isinstance(state, dict) else getattr(state, "points", initial)
            st.caption("Click around the feature and select Use outline.")
            points = outline_photo(photo, key, draft)
        else:
            st.image(photo)
        if st.checkbox("Also move this contract to another category", key=f"move_training_{contract}"):
            options = eligible_review_categories(record, categories, source)
            if not options:
                st.caption("No other category matches the selected photo.")
            destination = st.selectbox("Move to", options, disabled=not options,
                                       index=None, key=f"training_destination_{contract}_{number}")
            note = st.text_input("Reason for moving the contract", key=f"completion_training_note_{contract}")
            if st.button("Move contract and save for YOLO", type="primary",
                         disabled=not destination or label is None or (bool(label) and not points) or not note.strip()):
                try:
                    save_correction(detector, digest, photo, label or None, points, contract_id=contract, append=append)
                except (OSError, ValueError) as error:
                    st.warning(str(error))
                    return
                try:
                    save_manual_category(
                        {**record, "manual_review_photo": number + 1},
                        destination, note.strip(),
                    )
                except (OSError, sqlite3.Error):
                    st.warning("The training photo was saved. The category change could not be saved. Retry to finish.")
                    return
                st.session_state["category_training_saved"] = True
                advance_saved_review(advance, moved_from=record["assessment"]["completion_evidence_category"])
        st.caption("Save each labeled photo, then select another photo from this contract.")
        save_buttons = st.columns(2)
        label_pending = label is None or (bool(label) and not points)
        save_photo = save_buttons[0].button("Save YOLO training example", disabled=label_pending)
        save_and_next = save_buttons[1].button("Save and go to next contract",
                                               disabled=label_pending or not advance)
        if save_photo or save_and_next:
            try:
                save_correction(detector, digest, photo, label or None, points, contract_id=contract, append=append)
                if save_and_next:
                    st.session_state["training_example_saved"] = True
                    advance_saved_review(advance)
                st.success("Saved for the next training run in the YOLO Tester.")
            except (OSError, ValueError) as error:
                st.warning(str(error))


@st.cache_data(ttl=45, max_entries=128, show_spinner=False)
def api_data(path, params=None):
    response = requests.get(f"{API_URL}{path}", params=params, timeout=15)
    response.raise_for_status()
    return response.json()["data"], datetime.now(PHILIPPINE_TIME)


@st.cache_data(ttl=300, max_entries=2048, show_spinner=False)
def project_has_photos(contract_id):
    path = f"/projects/{quote(contract_id, safe='')}/images"
    response = requests.get(f"{API_URL}{path}", params={"page": 1, "limit": 1}, timeout=(4, 8))
    response.raise_for_status()
    return response.json()["data"]["pagination"]["totalCount"] > 0


def check_photo_availability(contract_ids):
    def check(contract_id):
        try:
            return project_has_photos(contract_id)
        except (requests.RequestException, KeyError, TypeError, ValueError):
            return None

    with ThreadPoolExecutor(max_workers=6) as workers:
        return dict(zip(contract_ids, workers.map(check, contract_ids)))


def reset_live_page():
    st.session_state["live_page"] = 1
    st.session_state["project_page_picker"] = 1


def reset_project_filters():
    for key in ("live_search", "live_region", "live_category", "live_status", "live_year",
                "live_photos_only", "live_limit", "live_contract"):
        st.session_state.pop(key, None)
    reset_live_page()


def project_pagination(page, total_pages):
    if total_pages < 2:
        return
    previous, picker, following = st.columns([1, 2, 1], vertical_alignment="bottom")
    previous.button("Previous", icon=":material/chevron_left:", disabled=page <= 1,
                    on_click=set_project_page, args=(page - 1,))
    st.session_state.setdefault("project_page_picker", page)
    picker.selectbox("Results page", range(1, total_pages + 1), index=None,
                     format_func=lambda number: f"Page {number:,} of {total_pages:,}",
                     key="project_page_picker", on_change=select_project_page)
    following.button("Next", icon=":material/chevron_right:", disabled=page >= total_pages,
                     on_click=set_project_page, args=(page + 1,))


def set_project_page(page):
    st.session_state["live_page"] = page
    st.session_state["project_page_picker"] = page


def select_project_page():
    st.session_state["live_page"] = st.session_state["project_page_picker"]


def select_contract_from_table(table_key, contract_ids):
    selected_rows = st.session_state[table_key]["selection"]["rows"]
    if selected_rows:
        st.session_state["live_contract"] = contract_ids[selected_rows[0]]


def status_cell_style(status):
    light = getattr(st.context, "theme", {}).get("type") == "light"
    colors = {
        "Completed": ("#4ADE80", "#166534", "#163328", "#DEF5E7"),
        "On-Going": ("#FBBF24", "#854D0E", "#362C18", "#FFF1D1"),
        "Not Yet Started": ("#A1B1C8", "#40546C", "#243146", "#E8EFF6"),
        "For Procurement": ("#38BDF8", "#075985", "#153347", "#DFF3FC"),
    }
    if status in colors:
        dark_text, light_text, dark_background, light_background = colors[status]
        return f"color:{light_text if light else dark_text};background-color:{light_background if light else dark_background};font-weight:600"
    return ""


@st.fragment(run_every=REFRESH_INTERVAL_SECONDS)
def show_live_projects():
    kpis = st.empty()
    with st.container(border=True):
        title, reset = st.columns([3, 1], vertical_alignment="center")
        title.subheader("Find a project")
        reset.button("Reset filters", on_click=reset_project_filters)
        search = st.text_input(
            "Contract ID or project description", placeholder="e.g. 24CK0181 or riverbank protection",
            key="live_search", on_change=reset_live_page, icon=":material/search:",
        )
        filters = st.columns(5)
        current_year = datetime.now(PHILIPPINE_TIME).year
        region = filters[0].selectbox("Region", [None, *API_REGIONS], format_func=lambda value: value or "All regions", on_change=reset_live_page, key="live_region")
        category = filters[1].selectbox("Infrastructure category", [None, *API_CATEGORIES], format_func=lambda value: value or "All categories", on_change=reset_live_page, key="live_category")
        status = filters[2].selectbox("Project status", [None, *API_STATUSES], format_func=lambda value: value or "All statuses", on_change=reset_live_page, key="live_status")
        year = filters[3].selectbox(
            "Fiscal year", [*(str(year) for year in range(current_year, 2015, -1)), "All years"],
            on_change=reset_live_page, key="live_year",
        )
        photos_only = st.toggle("Only show contracts with photos", on_change=reset_live_page, key="live_photos_only")
        if photos_only:
            st.session_state["live_limit"] = 50
        elif "live_limit" not in st.session_state:
            st.session_state["live_limit"] = 100
        limit = filters[4].selectbox(
            "Per page", [50] if photos_only else [50, 100, 200, 500], index=0,
            on_change=reset_live_page, disabled=photos_only, key="live_limit",
        )
    page = st.session_state.get("live_page", 1)
    params = {
        "page": page, "limit": limit, "year": None if year == "All years" else year, "region": region,
        "category": category, "status": status, "search": search.strip() or None,
    }
    try:
        result, checked_at = api_data("/projects", params)
    except (requests.RequestException, KeyError, ValueError):
        st.error("The BetterGov API is unavailable. Please try again in a moment.")
        return

    pagination = result["pagination"]
    count_label = "matching contracts before checking photos" if photos_only else "matching contracts"
    results_count, checked = st.columns([3, 2], vertical_alignment="center")
    results_count.subheader(f"{pagination['totalCount']:,} {count_label}")
    checked.html(f'<span class="bant-live" aria-hidden="true"></span>Last checked {checked_at:%d %b %Y, %H:%M:%S %Z}')
    if page > max(1, pagination["totalPages"]):
        reset_live_page()
        st.rerun()
    with kpis.container():
        cards = st.columns(4)
        statuses = Counter(project.get("status") for project in result["data"])
        budgets = pd.to_numeric(pd.Series([project.get("budget") for project in result["data"]]), errors="coerce")
        cards[0].metric("Matching contracts", f"{pagination['totalCount']:,}", help="Before filtering galleries for photos.")
        cards[1].metric("Completed · API page", f"{statuses['Completed']:,}")
        cards[2].metric("On-going · API page", f"{statuses['On-Going']:,}")
        cards[3].metric("Reported budget · API page", format_peso(budgets.sum(min_count=1)))
    if not result["data"]:
        st.info("No contracts match these filters.")
        project_pagination(page, pagination["totalPages"])
        return

    projects = result["data"]
    if photos_only:
        st.caption("Photo availability is checked for up to 50 contracts on each results page.")
        with st.spinner("Checking project galleries"):
            availability = check_photo_availability(tuple(project["contractId"] for project in projects))
        projects = [project for project in projects if availability[project["contractId"]] is True]
        st.caption(f"{len(projects):,} contracts with photos on this page.")
        unchecked = sum(status is None for status in availability.values())
        if unchecked:
            st.warning(f"Photo availability could not be verified for {unchecked:,} contracts on this page.")
        if not projects:
            st.info("No contracts with confirmed photos on this page. Try another results page or change your filters.")
            project_pagination(page, pagination["totalPages"])
            return

    rows = pd.DataFrame(projects)
    rows["completionDate"] = pd.to_datetime(rows["completionDate"], format="ISO8601", errors="coerce")
    rows = rows.sort_values("completionDate", ascending=False, na_position="last", kind="stable")
    rows["completionDate"] = rows["completionDate"].dt.date
    st.caption("This page is sorted by reported completion date, newest first.")
    rows["region"] = rows["location"].map(lambda location: (location or {}).get("region"))
    rows["budget"] = pd.to_numeric(rows.get("budget", pd.NA), errors="coerce")
    fiscal_years = rows.reindex(columns=["infraYear"])["infraYear"]
    rows["infraYear"] = pd.to_numeric(fiscal_years, errors="coerce").astype("Int64")
    result_columns = [
        "contractId", "description", "contractor", "infraYear", "region", "status",
        "completionDate", "budget",
    ]
    contract_ids = tuple(rows["contractId"])
    table_key = f"contract_results_{hash(contract_ids)}"
    st.caption("Select a project row to open its details below. The contract link opens the original DPWH record.")
    display_rows = rows.reindex(columns=result_columns).assign(contractId=rows["contractId"].map(project_url))
    display_rows = display_rows.fillna({field: "—" for field in ("description", "contractor", "region", "status")})
    st.dataframe(
        display_rows.style.map(status_cell_style, subset=["status"]),
        hide_index=True,
        key=table_key,
        selection_mode="single-row",
        on_select=lambda: select_contract_from_table(table_key, contract_ids),
        column_config={
            "contractId": st.column_config.LinkColumn("Contract ID", display_text=r"[?]project=([^&]+)"),
            "description": st.column_config.TextColumn("Project", width=340),
            "contractor": st.column_config.TextColumn("Contractor", width=180), "region": "Region", "status": "Status",
            "infraYear": st.column_config.NumberColumn("Fiscal year", format="%d"),
            "completionDate": st.column_config.DateColumn("Reported completion", format="YYYY-MM-DD"),
            "budget": st.column_config.NumberColumn("Reported budget (PHP)", format="₱%.2f"),
        },
    )
    if st.session_state.get("live_contract") not in contract_ids:
        st.session_state["live_contract"] = None
    project_pagination(page, pagination["totalPages"])
    with st.container(border=True):
        contract = st.selectbox("Review a contract", contract_ids, index=None, key="live_contract")
    if contract is None:
        return
    path = f"/projects/{quote(contract, safe='')}"
    try:
        detail, _ = api_data(path)
    except (requests.RequestException, KeyError, ValueError):
        st.warning("The API could not load this contract's details.")
        st.link_button("Open DPWH contract", project_url(contract))
        return

    summary = detail.get("imageSummary") or {}
    project = pd.DataFrame([{
        "contractId": contract, "description": detail["description"], "status": detail["status"],
        "contractor": detail.get("contractor"),
        "completionDate": pd.to_datetime(detail.get("completionDate"), errors="coerce"),
    }])
    metadata = pd.DataFrame([{
        "contractId": contract, "hasImages": summary.get("hasImages"),
        "totalImages": summary.get("totalImages"), "latestImageDate": summary.get("latestImageDate"),
    }])
    row = audit_images(project, metadata, pd.DataFrame(columns=PHOTO_COLUMNS)).iloc[0]
    show_contract(contract, row)
    with st.expander("Project dates, budget, and location"):
        show_satellite_link(detail, key="live_satellite")
        show_contract_timeline(detail)
        show_budget(detail)
    show_live_photos(
        contract, path, detail.get("components") or [], detail.get("category") or detail.get("infraType"),
        description=detail.get("description"),
    )


def review_model():
    return os.environ.get("OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL


def regional_summary(filters):
    params = {**filters, "page": 1, "limit": 500}
    first, _ = api_data("/projects", params)
    expected = first["pagination"]["totalCount"]
    pages = first["pagination"]["totalPages"]
    projects = {}
    for page in range(1, max(1, pages) + 1):
        result = first if page == 1 else api_data("/projects", {**params, "page": page})[0]
        if result["pagination"]["totalCount"] != expected:
            raise ValueError("Regional records changed while loading. Retry the review.")
        for row in result["data"]:
            projects[row["contractId"]] = row
    if len(projects) != expected:
        raise ValueError("The API returned an incomplete set of unique contracts. Retry the review.")

    rows = list(projects.values())
    totals = {
        "filters": filters, "contracts": len(rows), "pages": pages,
        "status_counts": Counter(row.get("status") or "Unknown" for row in rows),
        "category_counts": Counter(row.get("category") or "Unknown" for row in rows),
        "fiscal_year_counts": Counter(str(row.get("infraYear") or "Unknown") for row in rows),
        "top_contractors_by_count": Counter(row["contractor"] for row in rows if row.get("contractor")).most_common(10),
        "contracts_missing_contractor": sum(not row.get("contractor") for row in rows),
        "contracts_missing_completion_date": sum(not row.get("completionDate") for row in rows),
    }
    values = []
    for row in rows:
        try:
            amount = Decimal(str(row.get("budget")))
        except InvalidOperation:
            continue
        if amount.is_finite():
            values.append(amount)
    totals["budget"] = {"total_php": str(sum(values)) if values else None, "records_with_amount": len(values)}
    return totals, projects


def regional_evidence_records(projects, samples, without_photos=()):
    photo_reviews = {sample["contractId"]: sample for sample in samples}
    for contract_id, row in projects.items():
        reviewed = photo_reviews.get(contract_id)
        record = reviewed if reviewed else {
            "contractId": contract_id, "contractor": row.get("contractor"), "description": row.get("description"),
            "status": row.get("status"), "category": row.get("category"), "progress": row.get("progress"),
            "dates": contract_dates(row),
            "budgetPHP": contract_amounts(row),
            "galleryPhotoCount": 0 if contract_id in without_photos else None,
            "photoCoverage": "Gallery reports zero photos" if contract_id in without_photos else
            "No photo observations available for this review", "photos": [],
        }
        yield {**record, "assessment": record.get("assessment") or assess_with_manual_review(record)}


def regional_photo_evidence(ids, progress=None, without_photos=None):
    samples = []
    coverage = Counter()
    detector_results = {}
    photo_timing = Counter()
    for start in range(0, len(ids), 24):
        batch = ids[start:start + 24]
        availability = check_photo_availability(batch)
        for contract in batch:
            coverage["contracts_screened"] += 1
            status = availability[contract]
            if status is None:
                coverage["gallery_checks_unavailable"] += 1
            elif status is False:
                coverage["contracts_without_photos"] += 1
                if without_photos is not None:
                    without_photos.add(contract)
            else:
                coverage["contracts_with_reported_photos"] += 1
                collect_regional_contract(contract, samples, coverage, detector_results, photo_timing)
        if progress:
            progress(min(start + len(batch), len(ids)), len(ids))
    for key in ("contracts_screened", "contracts_with_reported_photos", "gallery_checks_unavailable",
                "sample_summaries_unavailable", "sampled_contracts", "sampled_photos"):
        coverage.setdefault(key, 0)
    return samples, {**coverage, "detector_results": detector_results, "photo_timing": photo_timing}


def collect_regional_contract(contract, samples, coverage, detector_results, photo_timing):
    try:
        summary = summarize_contract(contract, cache_counts=coverage)
    except (requests.RequestException, KeyError, ValueError):
        coverage["sample_summaries_unavailable"] += 1
        return
    if not summary["photos"]:
        coverage["sample_summaries_unavailable"] += 1
        return
    completion = pd.to_datetime(summary["dates"]["Reported completion"], errors="coerce")
    for photo in summary["photos"]:
        captured = pd.to_datetime(photo["capturedAt"], errors="coerce")
        photo["daysFromReportedCompletion"] = (
            (captured.date() - completion.date()).days
            if pd.notna(captured) and pd.notna(completion) else None
        )
    samples.append(summary)
    coverage["sampled_contracts"] += 1
    coverage["sampled_photos"] += len(summary["photos"])
    for photo in summary["photos"]:
        days = photo["daysFromReportedCompletion"]
        timing = "date unavailable" if days is None else "before completion" if days < 0 else "on or after completion"
        photo_timing[timing] += 1
        for finding in photo["yolo"]:
            detector_results.setdefault(finding["detector"], Counter())[finding["status"]] += 1


def regional_findings(totals):
    for status, count in sorted(totals.get("status_counts", {}).items()):
        yield f"{status} · {count:,} contracts"
    values = totals.get("budget") or {}
    yield f"Reported project budget · {format_peso(values.get('total_php'))} across {values.get('records_with_amount', 0):,} records"
    yield "Approved budget and award amounts · Unavailable in the regional totals"
    missing = totals.get("contracts_missing_completion_date")
    if missing is not None:
        yield f"Contracts missing a reported completion date · {missing:,}"
    coverage = totals.get("photo_coverage") or {}
    for label, count in sorted(coverage.get("photo_timing", {}).items()):
        yield f"Sampled photos {label} · {count:,}"
    for detector, counts in sorted(coverage.get("detector_results", {}).items()):
        yield (
            f"{detector} · {counts.get('detected', 0):,} detections · "
            f"{counts.get('no detection', 0):,} no detection · {counts.get('unavailable', 0):,} unavailable"
        )


def show_regional_review_page(*, manage=False):
    if not manage:
        show_saved_regional_dashboard()
        return
    st.subheader("Define your review")
    st.caption("Start with a region, then narrow the year, infrastructure type or project status.")
    columns = st.columns(4)
    region = columns[0].selectbox("Region", API_REGIONS, index=None, key="regional_region")
    current_year = datetime.now(PHILIPPINE_TIME).year
    year = columns[1].selectbox("Fiscal year", ["All years", *(str(year) for year in range(current_year, 2015, -1))],
                                key="regional_year")
    category = columns[2].selectbox("Infrastructure category", [None, *API_CATEGORIES],
                                    format_func=lambda value: value or "All categories", key="regional_category")
    status = columns[3].selectbox("Project status", [None, *API_STATUSES],
                                  format_func=lambda value: value or "All statuses", key="regional_status")
    if not region:
        st.info("Select a region to start or resume a review.")
        return
    search = st.text_input("Contract ID or description", key="regional_search").strip()
    show_regional_review({"region": region, "year": None if year == "All years" else year,
                          "category": category, "status": status, "search": search or None})


def show_saved_regional_dashboard():
    with closing(open_assessment_db()) as db:
        available = db.execute("""
            SELECT DISTINCT json_extract(q.filters, '$.region'), json_extract(q.filters, '$.year')
            FROM regional_queue AS q JOIN contract_assessments USING (contract_id)
            WHERE q.done = 1 AND CASE WHEN json_valid(q.filters)
                THEN json_extract(q.filters, '$.region') IS NOT NULL ELSE 0 END
        """).fetchall()
    if not available:
        st.info("Saved results appear here after a regional review in Admin.")
        return
    filters_card = st.container(border=True)
    region_column, year_column = filters_card.columns(2)
    region = region_column.selectbox("Region", sorted({region for region, _ in available}), key="dashboard_region")
    years = sorted({str(year) for name, year in available if name == region and year is not None}, reverse=True)
    year = year_column.selectbox("Fiscal year", ["All saved years", *years], key=f"dashboard_year_{region}")
    if year == "All saved years":
        if years:
            filters_card.html(f'<span class="bant-chip">Saved fiscal years shown · {", ".join(year for year in years if year.isdecimal())}</span>')
        if (region, None) in available:
            filters_card.caption("Includes saved reviews from runs without a fiscal year filter.")
    filters = {"region": region, "year": None if year == "All saved years" else year}
    records = saved_regional_assessments(filters)
    totals = saved_regional_totals(records)
    metrics = st.columns(3)
    metrics[0].metric(":material/fact_check: Reviewed contracts", f"{len(records):,}")
    metrics[1].metric(":material/groups: Contractors", f"{len(contractor_performance(records)):,}")
    metrics[2].metric(":orange[:material/warning:] Flagged contracts", f"{len(totals['flagged_contracts']):,}")
    with closing(open_assessment_db()) as db:
        saved = db.execute("SELECT report FROM regional_highlights WHERE filters = ?",
                           (regional_filter_key(filters),)).fetchone()
    report = json.loads(saved[0]) if saved else None
    render_regional_overview(totals, records, report, None, dashboard=True)


def regional_filter_key(filters):
    return json.dumps({name: value for name, value in filters.items() if value is not None}, sort_keys=True)


def show_regional_review(params):
    filters = {key: value for key, value in params.items() if key not in {"page", "limit"}}
    with st.expander("1 · Review project evidence", expanded=True):
        show_regional_queue(filters)
    with st.expander("2 · Regional summary and highlights"):
        use_saved = st.checkbox("Use saved reviews", value=True)
        include_photos = False if use_saved else st.checkbox("Include photo evidence", value=True)
        if use_saved:
            st.caption("Uses completed queue entries for the selected region and fiscal year.")
        else:
            st.caption("Fetches current regional records and checks photos if selected.")
        overview_key = (filters, use_saved, include_photos)
        if st.button("Generate regional overview"):
            st.session_state.pop("regional_review_v5", None)
            if use_saved:
                try:
                    assessments = saved_regional_assessments(filters)
                except (OSError, sqlite3.Error):
                    st.warning("Saved reviews could not be loaded.")
                    return
                if not assessments:
                    st.info("No saved queue entries match these filters. Run a regional review first.")
                    return
                st.session_state["regional_review_v5"] = (
                    overview_key, saved_regional_totals(assessments), assessments, None, None,
                )
            else:
                load_live_regional_overview(filters, include_photos, overview_key)
        saved = st.session_state.get("regional_review_v5")
        if not saved or saved[0] != overview_key:
            return
        _, totals, samples, report, error = saved
        if st.button("Generate Ollama highlights"):
            progress = st.progress(0, text="Summarizing contract evidence")
            try:
                report = summarize_regional_evidence(
                    samples, review_model(),
                    lambda done, total: progress.progress(done / total, text=f"Ollama · {done:,} of {total:,} batches summarized"),
                )
                error = None
            except (requests.RequestException, ValueError):
                error = "Ollama could not complete the regional overview. Check the local model and retry."
            finally:
                progress.empty()
            if use_saved and report and not error and report["completed_batches"]:
                with closing(open_assessment_db()) as db, db:
                    db.execute("INSERT OR REPLACE INTO regional_highlights VALUES (?, ?)",
                               (regional_filter_key(filters), json.dumps(report, ensure_ascii=False)))
            st.session_state["regional_review_v5"] = (overview_key, totals, samples, report, error)
        st.write(f"{totals['contracts']:,} {'saved contract reviews' if use_saved else 'contracts reviewed'}")
        render_regional_overview(totals, samples, report, error)


def saved_regional_assessments(filters):
    with closing(open_assessment_db()) as db:
        rows = db.execute("""
            SELECT DISTINCT a.summary
            FROM regional_queue AS q
            JOIN contract_assessments AS a USING (contract_id)
            WHERE q.done = 1 AND CASE WHEN json_valid(q.filters) THEN
                json_extract(q.filters, '$.region') = ?
                AND (? IS NULL OR json_extract(q.filters, '$.year') = ?)
            ELSE 0 END
        """, (filters["region"], filters.get("year"), filters.get("year"))).fetchall()
    search = (filters.get("search") or "").casefold()
    assessments = []
    for (payload,) in rows:
        record = json.loads(payload)
        if any(filters.get(field) and filters[field] != record.get(field) for field in ("category", "status")):
            continue
        if search and search not in record["contractId"].casefold() and search not in (record.get("description") or "").casefold():
            continue
        assessments.append(record)
    return assessments


def saved_regional_totals(assessments):
    amounts = []
    for record in assessments:
        try:
            budget = Decimal(str(record["budgetPHP"].get("Reported project budget")))
        except InvalidOperation:
            continue
        if budget.is_finite():
            amounts.append(budget)
    return {
        "contracts": len(assessments),
        "status_counts": Counter(item.get("status") or "Unknown" for item in assessments),
        "budget": {"total_php": str(sum(amounts)) if amounts else None, "records_with_amount": len(amounts)},
        "contracts_missing_completion_date": sum(
            reported_day(item["dates"].get("Reported completion")) is None for item in assessments
        ),
        "contract_assessments": Counter(item["assessment"]["completion_evidence_category"] for item in assessments),
        "flagged_contracts": {item["contractId"]: item["assessment"]["flags"] for item in assessments if item["assessment"]["flags"]},
    }


def load_live_regional_overview(filters, include_photos, overview_key):
    try:
        with st.spinner("Reading regional records"):
            totals, projects = regional_summary(filters)
    except (requests.RequestException, KeyError, ValueError):
        st.warning("The regional records could not be loaded completely. Try again.")
        return
    samples = []
    without_photos = set()
    if include_photos and projects:
        progress = st.progress(0, text="Checking regional photo galleries")
        samples, totals["photo_coverage"] = regional_photo_evidence(
            list(projects), lambda done, total: progress.progress(done / total, text=f"Photo review · {done:,} of {total:,} contracts checked"),
            without_photos=without_photos,
        )
        progress.empty()
    assessments = list(regional_evidence_records(projects, samples, without_photos))
    reviewed_ids = {item["contractId"] for item in samples}
    save_contract_assessments(
        item for item in assessments if item["contractId"] in reviewed_ids
        or item.get("galleryPhotoCount") == 0
    )
    totals["contract_assessments"] = dict(Counter(
        item["assessment"]["completion_evidence_category"] for item in assessments
    ))
    flagged = {item["contractId"]: item["assessment"]["flags"] for item in assessments if item["assessment"]["flags"]}
    totals["flagged_contracts"] = flagged
    st.session_state["regional_review_v5"] = (overview_key, totals, assessments, None, None)


def render_regional_overview(totals, samples, report, error, *, dashboard=False):
    selected_project = None
    with st.container(border=True):
        st.subheader("Regional findings")
        st.caption("Contract assessments by evidence category.")
        with st.expander("Regional totals", expanded=not dashboard):
            for finding in regional_findings(totals):
                st.text(finding)
        if counts := totals.get("contract_assessments"):
            breakdown = pd.DataFrame(list(counts.items()), columns=["Evidence category", "Contracts"])
            bars = alt.Chart(breakdown).encode(
                x=alt.X("Contracts:Q", axis=alt.Axis(tickMinStep=1), scale=alt.Scale(
                    domain=[0, max(counts.values()) * 1.18])),
                y=alt.Y("Evidence category:N", sort="-x", axis=alt.Axis(title=None, labelLimit=0)),
                tooltip=["Evidence category", "Contracts"],
            )
            fill = alt.Gradient(gradient="linear", x1=0, y1=0, x2=1, y2=0, stops=[
                alt.GradientStop(color="#08766C", offset=0), alt.GradientStop(color="#2DD4BF", offset=1),
            ])
            chart = bars.mark_bar(color=fill, cornerRadiusEnd=5, size=18) + bars.mark_text(
                align="left", dx=6, fontWeight=600,
            ).encode(text="Contracts:Q")
            st.altair_chart(chart.properties(height=alt.Step(30)), width="stretch")
    if samples:
        with st.container(border=True):
            selected_project = show_contractor_performance(samples)
    flagged = totals.get("flagged_contracts", {})
    st.caption(f"Contracts flagged for review · {len(flagged):,}")
    if flagged:
        with st.expander("Flagged contracts"):
            st.dataframe(
                [{"Contract": contract, "Review flags": " · ".join(flags)} for contract, flags in flagged.items()],
                hide_index=True,
            )
    coverage = totals.get("photo_coverage")
    if coverage:
        st.caption(
            f"Photo coverage · {coverage['contracts_screened']} contracts checked · "
            f"{coverage['contracts_with_reported_photos']} contracts with reported photos · "
            f"{coverage['sampled_photos']} photos sampled across {coverage['sampled_contracts']} contracts"
        )
        if coverage["gallery_checks_unavailable"] or coverage["sample_summaries_unavailable"]:
            st.caption("Some galleries or sampled contract details were unavailable.")
        st.caption(
            f"YOLO checks · {coverage.get('reused_detector_checks', 0):,} reused · "
            f"{coverage.get('new_detector_checks', 0):,} new · "
            f"{coverage.get('failed_detector_checks', 0):,} unavailable"
        )
    if not dashboard:
        with st.expander("Overview data"):
            st.json(totals)
    with st.container(border=True):
        st.subheader("Contract photo evidence")
        if selected_project:
            contract = selected_project["contractId"]
            st.write(f"**{contract}**")
            if description := selected_project.get("description"):
                st.write(description)
            st.write(selected_project["assessment"]["completion_evidence_category"])
            if timing := selected_project["assessment"].get("completion_photo_timing"):
                st.caption(timing)
            photos = selected_project.get("photos", [])
            for start in range(0, len(photos), 3):
                batch = photos[start:start + 3]
                for number, (column, photo) in enumerate(zip(st.columns(len(batch)), batch), start=start + 1):
                    column.image(photo["url"], caption=photo.get("capturedAt") or "Date unavailable")
                    column.link_button(f"Open photo {number}", photo["url"])
            if not photos:
                st.caption("This contract has no saved gallery photos.")
        elif samples:
            st.caption("Select a project row in the contractor table to see its photo evidence.")
    if error:
        st.warning(error)
    if isinstance(report, str):
        st.subheader("Ollama evidence summary")
        st.text(report)
    elif report:
        st.subheader("Ollama evidence summary")
        st.caption(f"{report['completed_batches']:,} of {report['total_batches']:,} batches summarized.")
        for failure in report["errors"]:
            st.warning(failure)
        if report["summary"]:
            st.text(report["summary"])
        for summary in report["batch_summaries"]:
            st.text(summary)


def show_contractor_performance(contracts):
    groups = contractor_performance(contracts)
    if not groups:
        return
    st.subheader("Contractor performance")
    st.html("".join(f'<span class="bant-chip">{label} {points:+d}</span>'
                    for label, points in CONTRACT_SCORE_LEVELS.items() if points is not None))
    with st.expander("How scoring works"):
        st.caption("Points use reported completion versus listed contract expiry. Incomplete contracts within "
                   f"their deadline are pending. An assessment flag for possible unfinished work deducts {UNFINISHED_WORK_PENALTY} points.")
        st.caption("Ranking score is total points divided by the square root of the number of scored contracts. "
                   "For example, an average of 2 points across 4 scored contracts gives a ranking score of 4.")
        st.caption("Contracts need qualifying photo evidence of completion or possible unfinished work to receive points.")
        st.caption("Latest reviewed photo timing uses the reported completion date. Captured 8–31 days later "
                   "deducts one point. Captured more than 31 days later deducts two points.")
    totals = []
    for group in groups:
        points = [row["Points"] for row in group["contracts"] if row["Points"] is not None]
        totals.append({
            "Contractor": group["name"], "Contracts": len(group["contracts"]), "Scored": len(points),
            "Total points": sum(points) if points else None,
            "Ranking score": sum(points) / sqrt(len(points)) if points else None,
            "Points per scored contract": round(sum(points) / len(points), 2) if points else None,
            "Large ABC–award gaps": sum(row["Difference (%)"] is not None for row in group["contracts"]),
        })
    ranking = pd.DataFrame(totals).sort_values(["Ranking score", "Total points", "Contractor"], ascending=[False, False, True])
    light = getattr(st.context, "theme", {}).get("type") == "light"

    def point_style(value):
        if pd.isna(value) or value == 0:
            return ""
        if value > 0:
            return "color:#166534;background-color:#DEF5E7" if light else "color:#4ADE80;background-color:#163328"
        return "color:#9F1239;background-color:#FFE4E9" if light else "color:#FDA4AF;background-color:#3B2432"

    styled_ranking = ranking.style.map(point_style, subset=["Ranking score", "Total points", "Points per scored contract"]).map(
        lambda value: status_cell_style("On-Going") if value > 0 else "", subset=["Large ABC–award gaps"],
    )
    st.dataframe(styled_ranking, hide_index=True, column_config={
        "Contractor": st.column_config.TextColumn("Contractor", width=260),
        "Ranking score": st.column_config.NumberColumn(format="%.2f"),
        "Points per scored contract": st.column_config.NumberColumn(format="%.2f"),
    })
    with st.container(border=True):
        selected = st.selectbox("Show contracts for", ranking.index.tolist(), format_func=lambda i: groups[i]["name"])
    rows = groups[selected]["contracts"]
    frame = pd.DataFrame(rows)
    st.caption("Photo days from reported completion shows a positive number for later photos and a negative number for earlier photos.")
    st.caption("Select a project row to see its saved details.")
    budget_columns = ["ABC (PHP)", "Award (PHP)", "Award minus ABC (PHP)", "Difference (%)"]
    contract_ids = tuple(frame["Contract"])
    project_columns = frame.drop(columns=budget_columns).columns.tolist()
    project_columns.remove("Photo assessment")
    project_columns.insert(2, "Photo assessment")
    table = st.dataframe(frame[project_columns], hide_index=True,
                         key=f"contractor_projects_{fingerprint(contract_ids)}",
                         on_select="rerun", selection_mode="single-row",
                         column_config={"Photo assessment": st.column_config.TextColumn(width=380)})
    contract_id = contract_ids[table.selection.rows[0]] if table.selection.rows else contract_ids[0]
    selected_project = next(item for item in contracts if item["contractId"] == contract_id)
    if table.selection.rows:
        with st.container(border=True):
            show_regional_contract_details(selected_project)
    gaps = frame.loc[frame["Difference (%)"].notna(), ["Contract", "Project", *budget_columns]]
    if not gaps.empty:
        st.caption(f"ABC and award differ by at least {BUDGET_FLAG_PERCENT}% of the approved budget on these contracts.")
        gaps = gaps.assign(Contract=gaps["Contract"].map(project_url))
        st.dataframe(gaps.sort_values("Difference (%)", key=lambda values: values.abs(), ascending=False),
                     hide_index=True, column_config={
                         "Contract": st.column_config.LinkColumn("Contract", display_text=r"[?]project=([^&]+)"),
                         **{label: st.column_config.NumberColumn(format="₱%,.2f") for label in budget_columns[:-1]},
                         "Difference (%)": st.column_config.NumberColumn(format="%.2f%%"),
                     })
    return selected_project


def show_regional_contract_details(record):
    contract_id = record["contractId"]
    assessment = record.get("assessment") or {}
    st.subheader(contract_id)
    if description := record.get("description"):
        st.write(description)
    st.write(f"Contractor · {record.get('contractor') or 'Unavailable'}")
    st.write(f"Reported status · {record.get('status') or 'Unavailable'}")
    if category := assessment.get("completion_evidence_category"):
        st.markdown("**Photo assessment**")
        st.write(category)
        if outcome := assessment.get("completion_assessment"):
            st.write(outcome)
    if timing := assessment.get("completion_photo_timing"):
        st.caption(timing)
    for flag in assessment.get("flags", []):
        st.warning(flag)
    dates, budgets = st.columns(2)
    for label, value in (record.get("dates") or {}).items():
        if value:
            dates.write(f"{label} · {value}")
    for label, value in (record.get("budgetPHP") or {}).items():
        if value is not None:
            budgets.write(f"{label} · {format_peso(value)}")
    st.link_button("Open DPWH contract", project_url(contract_id))
    for index, photo in enumerate(record.get("photos", []), start=1):
        if url := photo.get("url"):
            st.link_button(f"Photo {index} · {photo.get('capturedAt') or 'Date unavailable'}", url)


@st.cache_resource
def regional_run_registry():
    return {}, Lock()


@st.fragment(run_every=3)
def show_regional_queue(filters):
    key = regional_filter_key(filters)
    st.caption("Saves each contract’s rules-based assessment and YOLO findings. Ollama is available separately for the regional overview.")
    runs, lock = regional_run_registry()
    with closing(open_assessment_db()) as db:
        total, done = db.execute(
            "SELECT COUNT(*), COALESCE(SUM(done), 0) FROM regional_queue WHERE filters = ?", (key,),
        ).fetchone()
    if total:
        st.progress(done / total, text=f"{done:,} of {total:,} contracts saved")
    with lock:
        job = runs.get(key)
        if job and job["thread"].is_alive():
            stopping = job["stop"].is_set()
            st.caption("Pausing after the current contract…" if stopping else "Regional review is running.")
            if st.button("Pause regional review", disabled=stopping):
                job["stop"].set()
                st.info("Pause requested. The current contract will finish saving.")
            return
        if job and job.get("error"):
            st.warning(f"Regional review paused. {job['error']}")
        elif job and job["stop"].is_set() and done < total:
            st.info("Regional review paused. Saved progress is ready to resume.")
        elif job and not total:
            st.info("No contracts match these filters.")
        action = "Start regional review" if not total else "Resume regional review" if done < total else "Check new and due contracts"
        if st.button(action, key="run_regional_queue", type="primary"):
            job = {"stop": Event()}
            job["thread"] = Thread(target=run_regional_queue, args=(filters, key, total, done, job), daemon=True)
            runs[key] = job
            job["thread"].start()


@st.fragment(run_every=3)
def show_category_reruns():
    st.caption("Recheck a saved category with the current YOLO checkpoints. Manual category decisions are preserved.")
    runs, lock = regional_run_registry()
    try:
        with closing(open_assessment_db()) as db:
            categories = dict(db.execute("SELECT category, COUNT(*) FROM contract_assessments GROUP BY category ORDER BY category"))
            for (key,) in db.execute("SELECT DISTINCT filters FROM regional_queue WHERE filters LIKE 'category-rerun:%'"):
                categories.setdefault(key.removeprefix("category-rerun:"), 0)
            previous = st.session_state.get("rerun_selected_category")
            options = sorted(categories)
            category = st.selectbox(
                "Saved review category", options, index=options.index(previous) if previous in options else None,
                key="rerun_category", on_change=remember_rerun_category,
            )
            if category is None:
                return
            st.caption(f"{categories[category]:,} contracts currently in this category")
            key = f"category-rerun:{category}"
            total, done = db.execute(
                "SELECT COUNT(*), COALESCE(SUM(done), 0) FROM regional_queue WHERE filters = ?", (key,),
            ).fetchone()
            if total:
                st.progress(done / total, text=f"{done:,} of {total:,} contracts rechecked")
                if done == total:
                    st.success("Rerun complete. Results are saved under Reviewed contracts.")
            with lock:
                job = runs.get(key)
                if job and job["thread"].is_alive():
                    stopping = job["stop"].is_set()
                    if st.button("Pause rerun", disabled=stopping):
                        job["stop"].set()
                    if job["stop"].is_set():
                        st.caption("Pausing after the current contract.")
                    return
                if job and job.get("error"):
                    st.warning(f"Rerun paused. {job['error']}")
                resume = done < total
                if not st.button("Resume rerun" if resume else "Rerun category", type="primary",
                                 disabled=not resume and not categories[category]):
                    return
                if not resume:
                    with db:
                        db.execute("BEGIN IMMEDIATE")
                        db.execute("DELETE FROM regional_queue WHERE filters = ?", (key,))
                        db.execute("""
                            INSERT INTO regional_queue(filters, position, contract_id)
                            SELECT ?, ROW_NUMBER() OVER (ORDER BY contract_id) - 1, contract_id
                            FROM contract_assessments WHERE category = ?
                        """, (key, category))
                job = {"stop": Event()}
                job["thread"] = Thread(target=run_regional_queue, args=(None, key, 0, 0, job), daemon=True)
                runs[key] = job
                job["thread"].start()
    except (OSError, sqlite3.Error):
        st.warning("The saved review queue is unavailable.")


def remember_rerun_category():
    st.session_state["rerun_selected_category"] = st.session_state["rerun_category"]


def run_regional_queue(filters, key, total, done, job):
    stop = job["stop"]
    try:
        if filters is not None and (not total or done == total):
            _, projects = regional_summary(filters)
            with closing(open_assessment_db()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                existing = set(db.execute("SELECT contract_id FROM regional_queue WHERE filters = ?", (key,)).fetchall())
                total = len(existing)
                new_ids = [contract for contract in projects if (contract,) not in existing]
                db.executemany(
                    "INSERT INTO regional_queue(filters, position, contract_id) VALUES (?, ?, ?)",
                    ((key, position, contract) for position, contract in enumerate(new_ids, start=total)),
                )
                total += len(new_ids)
                for contract, reviewed, payload in db.execute("SELECT contract_id,reviewed_at,summary FROM contract_assessments").fetchall():
                    if contract in projects and review_due(json.loads(payload), reviewed):
                        db.execute("UPDATE regional_queue SET done = 0, staged = NULL WHERE filters = ? AND contract_id = ?", (key, contract))
        if filters is not None and not total:
            return
        with ThreadPoolExecutor(max_workers=4) as workers:
            while True:
                with closing(open_assessment_db()) as db:
                    batch = db.execute(
                        "SELECT position, contract_id, staged FROM regional_queue "
                        "WHERE filters = ? AND done = 0 ORDER BY position LIMIT 4", (key,),
                    ).fetchall()
                if not batch:
                    return
                batch = [(position, contract, None if staged and review_is_stale(json.loads(staged)) else staged)
                         for position, contract, staged in batch]
                pending = {
                    position: workers.submit(fetch_contract_review_inputs, contract)
                    for position, contract, staged in batch if staged is None
                }
                for position, contract, staged in batch:
                    if stop.is_set():
                        for future in pending.values():
                            future.cancel()
                        return
                    if staged is None:
                        inputs = pending[position].result()
                        if inputs[1]["galleryPhotoCount"] is None:
                            raise ValueError(f"The gallery for {contract} is unavailable.")
                        record = summarize_contract(contract, cache_counts=Counter(), save=False,
                                                    inputs=inputs)
                    else:
                        record = json.loads(staged)
                        record["assessment"] = assess_with_manual_review(record)
                    save_contract_assessments([record], require_saved=True)
                    with closing(open_assessment_db()) as db, db:
                        db.execute("UPDATE regional_queue SET done = 1, staged = NULL WHERE filters = ? AND position = ?",
                                   (key, position))
    except Exception as error:
        job["error"] = str(error)


def show_ai_reviews(contract_ids, selected_contract):
    model = review_model()
    show_contract_comparison(contract_ids, selected_contract, model)
    with st.expander("Contract evidence review"):
        chosen = st.multiselect(
            "Contracts from this results page", contract_ids, max_selections=3,
            placeholder="Choose up to 3 contracts",
        )
        targets = chosen or ([selected_contract] if selected_contract else [])
        st.caption("Contract decisions and budget observations use deterministic evidence rules. Ollama selects regional highlights separately.")
        if st.button("Analyze contract evidence", type="primary", disabled=not targets):
            results = {}
            for contract_id in targets:
                with st.spinner(f"Reviewing {contract_id} evidence…"):
                    try:
                        summary = summarize_contract(contract_id)
                    except (requests.RequestException, KeyError, ValueError):
                        results[contract_id] = "BetterGov could not load contract details. Try again."
                        continue
                    try:
                        report = review_project(summary, model)
                        results[contract_id] = (
                            report, summary["photos"], summary["dates"]["Reported completion"], summary["assessment"],
                        )
                    except ReviewError as error:
                        results[contract_id] = str(error)
                    except requests.RequestException:
                        results[contract_id] = "Could not connect to Ollama or the request timed out."
                    except (KeyError, ValueError):
                        results[contract_id] = "Contract evidence is incomplete or unreadable. Refresh the source records and retry."
            st.session_state["ai_project_reviews_v5"] = results

        results = st.session_state.get("ai_project_reviews_v5", {})
        for contract_id in targets:
            if contract_id not in results:
                continue
            st.subheader(contract_id)
            result = results[contract_id]
            if isinstance(result, str):
                st.warning(result)
                continue
            if result is None:
                st.warning("Review unavailable. Check the API connections and try again.")
                continue
            report, photos, *dates = result
            completion = pd.to_datetime(dates[0] if dates else None, errors="coerce")
            assessment = dates[1] if len(dates) > 1 else None
            st.markdown(f"**{assessment.get('completion_evidence_category', assessment['reported_status_summary']) if assessment else report['conclusion']}**")
            if assessment:
                st.text(assessment["completion_assessment"])
                if timing := assessment.get("completion_photo_timing"):
                    st.caption(timing)
                if reason := assessment.get("completion_evidence_reason"):
                    st.caption(reason)
                for flag in assessment["flags"]:
                    st.warning(flag)
            st.text(report["summary"])
            st.caption(f"Photo sample reviewed · {len(photos)}")
            if assessment and (number := assessment.get("latest_evidence_photo")):
                photo = photos[number - 1]
                st.link_button(f"Latest photo · {photo['capturedAt']}", photo["url"])
                offset = assessment.get("latest_photo_days_from_reported_completion")
                if offset == 0:
                    st.caption("Captured on the reported completion date.")
                elif offset is not None:
                    direction = "after" if offset > 0 else "before"
                    st.caption(f"Captured {abs(offset)} day(s) {direction} reported completion.")
            for finding in report["photo_findings"]:
                number = finding["photo"]
                if 1 <= number <= len(photos):
                    stage = finding.get("road_work_stage")
                    if stage and stage != "Not applicable":
                        st.caption(f"Road work stage · {stage}")
                    building_stage = finding.get("building_work_stage")
                    if building_stage and building_stage != "Not applicable":
                        st.caption(f"Building work stage · {building_stage}")
                    captured = pd.to_datetime(photos[number - 1].get("capturedAt"), errors="coerce")
                    if building_stage == "Visible unfinished work" and photo_on_or_after_completion(captured, completion):
                        st.warning(
                            f"Review possible unfinished work. Photo dated {captured.date()}; "
                            f"reported completion {completion.date()}."
                        )
                    st.text(finding["observation"])
                    st.link_button(f"Open cited photo {number}", photos[number - 1]["url"])
            st.text(f"Budget review · {report['budget_observation']}")
            for gap in report["evidence_gaps"]:
                st.text(f"Evidence gap · {gap}")


def contract_dates(detail):
    procurement = detail.get("procurement") or {}
    dates = {
        "Advertisement": procurement.get("advertisementDate"),
        "Bid submission deadline": procurement.get("bidSubmissionDeadline"),
        "Award": procurement.get("dateOfAward"),
        "Contract effectivity": detail.get("contractEffectivityDate"),
        "Reported start": detail.get("startDate"),
        "Reported completion": detail.get("completionDate"),
        "Contract expiry": detail.get("expiryDate"),
    }
    if detail.get("livestreamDetectedAt"):
        dates["Livestream first detected"] = detail["livestreamDetectedAt"]
    return dates


def contract_amounts(detail):
    procurement = detail.get("procurement") or {}
    return {
        "Reported project budget": detail.get("budget"),
        "Approved budget for the contract": procurement.get("abc"),
        "Award amount": procurement.get("awardAmount"),
    }


def describe_review_image(model, image):
    describe = model.describe_image
    if "render_overlay" in signature(describe).parameters:
        return describe(image, render_overlay=False)[0]
    # A running Streamlit process can still hold a model loaded before this option was added.
    return describe(image)[0]


def saved_photo_observation(contract_id, photo, detector, weights, get_image):
    image = get_image()  # Revalidate bytes once per photo per review; inference can still be reused.
    image_hash = image.info.get("source_sha256") or fingerprint([image.size, sha256(image.tobytes()).hexdigest()])
    _, labels, _, size = YOLO_MODELS[detector]
    key = fingerprint([INFERENCE_VERSION, image_hash, detector, checkpoint_digest(weights), labels, size,
                       BUILDING_CONFIDENCE, BRIDGE_CONFIDENCE, road_surface.SCAFFOLDING_MIN_CONFIDENCE])
    PHOTO_REVIEW_DB.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(PHOTO_REVIEW_DB, timeout=10)) as database:
        with database:
            database.execute("CREATE TABLE IF NOT EXISTS structured_photo_observations (cache_key TEXT PRIMARY KEY, result TEXT NOT NULL)")
        saved = database.execute("SELECT result FROM structured_photo_observations WHERE cache_key = ?", (key,)).fetchone()
        if saved:
            result = json.loads(saved[0])
        else:
            model = load_photo_model(detector, str(weights.resolve()), (INFERENCE_VERSION, checkpoint_digest(weights)))
            result = model.analyze_image(image)
            result["image_sha256"] = image_hash
            with database:
                database.execute("INSERT OR IGNORE INTO structured_photo_observations VALUES (?, ?)", (key, json.dumps(result)))
        result["quality"] = model_quality(detector, weights)
        return result, bool(saved)


def fetch_contract_review_inputs(contract_id):
    path = f"/projects/{quote(contract_id, safe='')}"
    detail, _ = api_data(path)
    satellite = satellite_location(detail)
    summary = {
        "contractId": contract_id, "description": detail.get("description"),
        "category": detail.get("category") or detail.get("infraType"),
        "coordinates": {"latitude": satellite[0], "longitude": satellite[1]} if satellite else None,
        "contractor": detail.get("contractor"), "status": detail.get("status"),
        "progress": detail.get("progress"), "dates": contract_dates(detail),
        "budgetPHP": contract_amounts(detail), "galleryPhotoCount": None, "photos": [],
    }
    try:
        gallery_path = f"{path}/images"
        gallery, _ = api_data(gallery_path, {"page": 1, "limit": 12})
        pagination = gallery["pagination"]
        total = pagination["totalCount"]
        entries = list(gallery["images"])
        for page in range(2, pagination["totalPages"] + 1):
            result, _ = api_data(gallery_path, {"page": page, "limit": 12})
            if result["pagination"]["totalCount"] != total:
                raise ValueError("Gallery changed during review")
            entries.extend(result["images"])
        # BetterGov may return several image entries per counted gallery item.
        photos = select_review_photos(entries)
        summary["galleryPhotoCount"] = max(total, len(entries))
    except (requests.RequestException, KeyError, ValueError):
        summary["photoCoverage"] = "Gallery unavailable"
        return detail, summary, []
    return detail, summary, photos


def summarize_contract(contract_id, *, cache_counts=None, save=True, inputs=None):
    detail, summary, photos = inputs if inputs is not None else fetch_contract_review_inputs(contract_id)
    summary["review_provenance"] = {"models": current_model_signature(), "assessment_version": ASSESSMENT_VERSION,
                                    "inference_version": INFERENCE_VERSION, "reviewed_at": datetime.now(PHILIPPINE_TIME).isoformat()}
    if summary["galleryPhotoCount"] is None:
        summary["assessment"] = assess_with_manual_review(summary)
        # A failed gallery fetch must not erase a previously successful review.
        return summary
    summary["photoCoverage"] = "Includes all available photos from the two latest capture dates"
    components = {component.get("componentId"): component for component in detail.get("components") or []}
    for photo in photos:
        component_id = photo.get("componentId")
        component = components.get(component_id, {}) if component_id else {}
        category = component.get("infraType") or detail.get("category") or detail.get("infraType")
        observations = []
        image = None
        image_error = None

        def get_image():
            nonlocal image, image_error
            if image is None and image_error is None:
                try:
                    image = download_photo(photo["url"])
                except (requests.RequestException, OSError, ValueError) as error:
                    image_error = error
            if image_error is not None:
                raise image_error
            return image

        scope = component.get("typeOfWork") or component.get("description") or (
            detail.get("description") if not components or len(components) == 1 else None
        )
        for detector in category_detectors(category, scope):
            if detector not in YOLO_MODELS:
                continue
            for weights in review_checkpoints(detector):
                reused = False
                try:
                    if weights.is_file():
                        finding, reused = saved_photo_observation(contract_id, photo, detector, weights, get_image)
                    else:
                        finding = {"observation": "Checkpoint unavailable", "status": "unavailable", "detections": [], "quality": "missing"}
                except (ImportError, requests.RequestException, OSError, RuntimeError, ValueError, sqlite3.Error) as error:
                    logging.getLogger(__name__).warning("Detector failed: contract=%s detector=%s error=%s", contract_id, detector, type(error).__name__)
                    finding = {"observation": "Detector unavailable; retry needed", "status": "unavailable", "detections": [], "quality": "unavailable"}
                status = finding["status"]
                if cache_counts is not None:
                    if status == "unavailable":
                        cache_counts["failed_detector_checks"] += 1
                    else:
                        cache_counts["reused_detector_checks" if reused else "new_detector_checks"] += 1
                observations.append({"detector": detector, "checkpoint_sha256": checkpoint_digest(weights) if weights.is_file() else None,
                                     **finding})
        summary["photos"].append({
            "url": photo["url"], "capturedAt": photo.get("capturedAt"),
            "componentId": component_id,
            "scope": scope,
            "category": category, "yolo": observations,
        })
    summary["assessment"] = assess_with_manual_review(summary)
    if save:
        save_contract_assessments([summary])
    return summary


def show_contract_comparison(contract_ids, selected_contract, model):
    with st.expander("Contract summaries and comparison"):
        selected = st.multiselect(
            "Contracts to summarize", contract_ids, max_selections=3, key="summary_contracts",
            placeholder="Choose up to 3 contracts from this results page",
        )
        targets = tuple(selected or ([selected_contract] if selected_contract else []))
        if st.button("Build contract summaries", disabled=not targets):
            summaries, failures = [], []
            for contract_id in targets:
                with st.spinner(f"Summarizing {contract_id}"):
                    try:
                        summaries.append(summarize_contract(contract_id))
                    except (requests.RequestException, KeyError, ValueError):
                        failures.append(contract_id)
            st.session_state["contract_summaries_v3"] = (targets, summaries, failures)
            st.session_state.pop("contract_comparison_v4", None)
        saved = st.session_state.get("contract_summaries_v3")
        if not saved or saved[0] != targets:
            return
        _, summaries, failures = saved
        for contract_id in failures:
            st.warning(f"Contract details unavailable for {contract_id}.")
        for summary in summaries:
            st.subheader(summary["contractId"])
            st.text(summary["description"] or "Description unavailable")
            st.text(f"Contractor · {summary['contractor'] or 'Unavailable'}")
            st.text(f"Reported status · {summary['status'] or 'Unavailable'}")
            for label, value in summary["dates"].items():
                st.text(f"{label} · {format_date(pd.to_datetime(value, errors='coerce'))}")
            for label, value in summary["budgetPHP"].items():
                st.text(f"{label} · {format_peso(value)}")
            total = summary["galleryPhotoCount"]
            st.caption(summary["photoCoverage"])
            assessment = summary.get("assessment") or assess_contract(summary)
            st.text(assessment.get("completion_evidence_category") or assess_contract(summary)["completion_evidence_category"])
            st.text(assessment["completion_assessment"])
            if timing := assessment.get("completion_photo_timing"):
                st.caption(timing)
            if reason := assessment.get("completion_evidence_reason"):
                st.caption(reason)
            for flag in assessment["flags"]:
                st.warning(flag)
            with st.expander(f"{summary['contractId']} assessment details"):
                st.json(assessment)
            st.caption(f"Photos reviewed · {len(summary['photos'])} of {total if total is not None else 'unknown'}")
            for number, photo in enumerate(summary["photos"], start=1):
                label = "Latest photo" if number == assessment.get("latest_evidence_photo") else f"Photo {number}"
                st.link_button(f"{label} · {photo['capturedAt'] or 'Date unavailable'}", photo["url"])
                if photo["scope"]:
                    st.text(photo["scope"])
                for observation in photo["yolo"]:
                    st.text(f"{observation['detector']} · {observation['observation']}")
                if not photo["yolo"]:
                    st.caption("No detector configured for this photo category.")
        if st.button("Compare summaries", disabled=len(summaries) < 2 or bool(failures)):
            st.session_state.pop("contract_comparison_v4", None)
            try:
                with st.spinner("Comparing contract evidence"):
                    st.session_state["contract_comparison_v4"] = (targets, compare_contracts(summaries, model))
            except ReviewError as error:
                st.warning(str(error))
            except (requests.RequestException, ValueError):
                st.warning("Contract comparison unavailable. Check the source records and retry.")
        comparison = st.session_state.get("contract_comparison_v4")
        if comparison and comparison[0] == targets:
            report = comparison[1]
            st.subheader("Combined conclusion")
            st.text(report["conclusion"])
            for finding in report["findings"]:
                st.text(finding)
            for gap in report["evidence_gaps"]:
                st.text(f"Evidence gap · {gap}")


def show_contract_timeline(detail):
    st.subheader("Contract timeline")
    timeline = pd.DataFrame([
        {"Milestone": label, "Reported date": format_date(pd.to_datetime(value, errors="coerce"))}
        for label, value in contract_dates(detail).items()
    ])
    st.table(timeline.set_index("Milestone"))


def show_budget(detail):
    st.subheader("Budget and costing")
    amounts = contract_amounts(detail)
    for column, (label, value) in zip(st.columns(len(amounts)), amounts.items()):
        column.metric(label, format_peso(value))

    links = detail.get("links") or {}
    documents = {
        "programOfWork": "Program of Work",
        "advertisement": "Bid documents",
        "contractAgreement": "Contract agreement",
    }
    available_documents = []
    for key, label in documents.items():
        url = links.get(key) or ""
        parsed = urlsplit(url)
        if parsed.scheme == "https" and parsed.netloc:
            available_documents.append((label, url))

    if available_documents:
        st.caption("Check the linked documents for an itemized breakdown.")
        for label, url in available_documents:
            st.link_button(label, url)
    else:
        st.caption("The API lists no costing documents for this contract.")


def format_peso(value):
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return "Unavailable"
    return f"₱{amount:,.2f}" if amount.is_finite() else "Unavailable"


@st.cache_resource(max_entries=len(YOLO_MODELS), show_spinner=False)
def load_photo_model(category, weights, version):
    _, class_names, _, image_size = YOLO_MODELS[category]
    return PhotoSegmentationModel(weights, class_names, image_size)


def review_checkpoints(detector):
    current = active_weights(detector, YOLO_MODELS[detector][0])
    candidate = latest_candidate(detector)
    if not candidate or not current.is_file():
        return (candidate[0] if candidate else current,)
    latest = candidate[0]
    if checkpoint_digest(current) == checkpoint_digest(latest):
        return (current,)
    if model_quality(detector, current) in {"validated", "provisional"}:
        return current, latest
    return (latest,)


def photo_checkpoint_options(detector):
    baseline = YOLO_MODELS[detector][0]
    current = active_weights(detector, baseline)
    options = {"Current model": current}
    if current != baseline and baseline.is_file():
        options["Previous model"] = baseline
    if detector == UNDER_CONSTRUCTION_TRAINING and UNDER_CONSTRUCTION_BASELINE.is_file():
        options["Previous combined model"] = UNDER_CONSTRUCTION_BASELINE
    candidate = latest_candidate(detector)
    if candidate:
        options["Trained candidate"] = candidate[0]
    for marker in sorted(feedback_folder(detector).glob("training-*/ready"), reverse=True):
        checkpoint = marker.parent / "runs/corrected/weights/best.pt"
        if checkpoint.is_file() and checkpoint not in options.values():
            options[f"Candidate · {marker.parent.name}"] = checkpoint
    if detector == RIVERBANK_DETECTOR:
        run = PROJECT_DIR / "data/yolo_riverbank/runs/riverbank_post_completion"
        checkpoint = run / "weights/best.pt"
        if (run / "ready").is_file() and checkpoint.is_file():
            options["Post-completion candidate"] = checkpoint
    return options


@st.cache_data(ttl=900, max_entries=96, show_spinner=False)
def analyze_project_photo(url, category, weights, version):
    model = load_photo_model(category, weights, version)
    try:
        return model.describe(url)
    except ValueError as error:
        return f"YOLO could not analyze this photo. {error}", None
    except (requests.RequestException, OSError):
        return "YOLO could not read this photo.", None


def photo_on_or_after_completion(captured, completion):
    capture_day, completion_day = reported_day(captured), reported_day(completion)
    return bool(capture_day and completion_day and capture_day >= completion_day)


def category_detectors(category, scope=None):
    detectors = (category, *ADDITIONAL_PHOTO_DETECTORS.get(category, ()))
    if category == "Roads" and "slope protection" in (scope or "").casefold():
        detectors += (SLOPE_PROTECTION_DETECTOR,)
    if category in API_CATEGORIES:
        detectors = tuple(detector for detector in detectors if detector != SCAFFOLDING_DETECTOR)
        detectors += (UNDER_CONSTRUCTION_TRAINING, SCAFFOLDING_DETECTOR, EQUIPMENT_DETECTOR)
        if category == "Buildings and Facilities":
            detectors += (BUILDING_STAGE_DETECTOR,)
        if category in UNFINISHED_TARGETS:
            detectors += (UNFINISHED_TARGETS[category][0],)
    return tuple(dict.fromkeys(detectors))


def show_photo_analysis(column, url, detector, supported, available, models):
    weights = models.get(detector)
    if weights:
        target = YOLO_MODELS[detector][2]
        with column, st.spinner("Checking photo"):
            description, overlay = analyze_project_photo(url, *weights)
        if description.startswith("YOLO detected "):
            column.write(plain_photo_finding(description, target))
        if overlay:
            with column.expander(f"View overlay for {target.removeprefix('visible ')}"):
                st.image(overlay)
        return description
    elif detector not in supported:
        column.caption("YOLO analysis is unavailable for this infrastructure category.")
    elif detector not in available:
        column.caption(f"The server needs a trained checkpoint for {YOLO_MODELS[detector][2]}.")


def show_live_photos(contract, path, components, category, *, description=None):
    st.subheader("Project photos")
    components_by_id = {
        component["componentId"]: component
        for component in components if component.get("componentId")
    }
    st.link_button("Open full DPWH gallery", project_url(contract))
    page_key = f"photos_page_{contract}"
    page = st.session_state.get(page_key, 1)
    try:
        gallery, _ = api_data(f"{path}/images", {"page": page, "limit": 12})
    except (requests.RequestException, KeyError, ValueError):
        st.warning("The API could not load the photo gallery.")
        return
    pagination = gallery["pagination"]
    if page > max(1, pagination["totalPages"]):
        st.session_state[page_key] = 1
        st.rerun()
    st.caption(f"Photo count reported by the API · {pagination['totalCount']:,}")
    date_range = (gallery.get("summary") or {}).get("dateRange") or {}
    if date_range.get("earliest") or date_range.get("latest"):
        earliest = format_date(pd.to_datetime(date_range.get("earliest"), errors="coerce"))
        latest = format_date(pd.to_datetime(date_range.get("latest"), errors="coerce"))
        st.caption(f"Earliest reported photo · {earliest} | Latest reported photo · {latest}")
    if pagination["totalPages"] > 1:
        st.number_input("Photo page", min_value=1, max_value=pagination["totalPages"], step=1, key=page_key)
    photos = [photo for photo in gallery["images"] if urlsplit(photo.get("url") or "").scheme == "https"]
    if not photos:
        st.caption("No photos returned for this page.")
        return
    if len(photos) != pagination["totalCount"]:
        st.caption(f"Photo entries on this page · {len(photos):,}")
    override = st.selectbox(
        "Gallery YOLO detector", [None, *YOLO_MODELS],
        format_func=lambda detector: detector or "Automatic by infrastructure category",
        key=f"gallery_detector_{contract}",
    )
    override_weights = None
    if override:
        options = photo_checkpoint_options(override)
        selected = st.selectbox("Gallery checkpoint", list(options), key=f"gallery_checkpoint_{contract}_{override}")
        override_weights = options[selected]
    routed_photos = []
    for photo in photos:
        component = components_by_id.get(photo.get("componentId"), {})
        photo_category = component.get("infraType") or category
        scope = component.get("typeOfWork") or component.get("description") or (
            description if len(components_by_id) <= 1 else None
        )
        detectors = (override,) if override else category_detectors(photo_category, scope)
        routed_photos.append((photo, component, photo_category, detectors))

    supported = {
        detector: override_weights if detector == override else review_checkpoints(detector)[0]
        for _, _, _, detectors in routed_photos for detector in detectors if detector in YOLO_MODELS
    }
    available = {kind: checkpoint for kind, checkpoint in supported.items() if checkpoint.is_file()}
    models = {}
    if available and st.toggle("Show AI photo findings", value=True):
        for kind, checkpoint in available.items():
            try:
                weights = (kind, str(checkpoint.resolve()), (INFERENCE_VERSION, checkpoint_digest(checkpoint)))
                load_photo_model(*weights)
                models[kind] = weights
            except ModuleNotFoundError:
                st.info("Install the dashboard's optional YOLO dependency to enable photo analysis.")
                break
            except (OSError, RuntimeError, ValueError):
                st.warning(f"The YOLO model for {kind} could not be loaded.")
    for start in range(0, len(routed_photos), 3):
        for column, (photo, component, photo_category, detectors) in zip(st.columns(3), routed_photos[start:start + 3]):
            captured = pd.to_datetime(photo.get("capturedAt"), errors="coerce")
            caption = [format_date(captured), photo.get("componentId"), photo.get("purpose")]
            column.image(photo["url"], caption=" · ".join(part for part in caption if part))
            work = component.get("typeOfWork") or component.get("description")
            if work:
                column.caption(f"Reported work · {work}")
            if photo_category:
                column.caption(f"Infrastructure · {photo_category}")
            findings = []
            for detector in detectors:
                if finding := show_photo_analysis(column, photo["url"], detector, supported, available, models):
                    findings.append(finding)
            if findings and not any(f.startswith("YOLO detected ") for f in findings):
                column.caption("No features detected." if any(f.startswith("YOLO found no ") for f in findings)
                               else "AI photo analysis was unavailable.")
            column.link_button("Open original", photo["url"])


def show_contract(contract, row):
    st.subheader(contract)
    st.write(row["description"])
    contractor = row.get("contractor")
    st.text(f"Contractor · {contractor or 'Unavailable'}")
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


def format_date(value):
    return value.strftime("%d %b %Y") if pd.notna(value) else "Unavailable"


if __name__ == "__main__":
    main()
