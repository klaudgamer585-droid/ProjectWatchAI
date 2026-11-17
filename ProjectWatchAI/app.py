from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import html
import os
import json
import re
import sqlite3
import logging
from math import sqrt
from evidence_policy import (ASSESSMENT_VERSION, INFERENCE_VERSION,
    checkpoint_digest, evidence_fingerprint, fingerprint, model_quality)
from hashlib import sha256
from importlib import reload
from inspect import signature
from contextlib import closing
from functools import partial
from pathlib import Path
from threading import Event, Lock, Thread
from urllib.parse import quote, urlencode, urlsplit

import pandas as pd
import requests
import streamlit as st
import evidence_language as language
import ui
from ui import (apply_theme, category_badge, escape_markdown, evidence_overview, navigation, page_header,
                assessment_summary, photo_with_fallback, show_flags, show_state)
from ui import empty_state, error_state, field_feedback, freshness, go_to, loading, notice

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
import model_readiness
import queue_view
import queue_worker
import regional_view
import review_store
import review_workspace
from training_correction import render_training_correction


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
    "missingImages": "No photos are available in this source record",
    "imageDateMismatch": "Latest photo date differs from the reported completion date",
    "imageAvailabilityUnknown": "Photo availability unknown",
    "missingImageDate": "Photo capture date unavailable",
    "missingCompletionDate": "Reported completion date unavailable",
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
        error_state("The satellite preview is unavailable.", "The imagery service did not return a picture.",
                    kept="You can still open Copernicus Browser below.")
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
    # Supplied ant-face favicon; the sidebar collapses automatically on small screens.
    st.set_page_config(page_title="BantAIProyekto | Infrastructure review", page_icon=ui.FAVICON,
                       layout="wide", initial_sidebar_state="auto")
    apply_theme()
    # Preserve useful controls when their workspace is not rendered on a rerun.
    for key in ("admin_section", *LIVE_STATE_KEYS, "saved_review_category",
                "saved_review_search", "regional_region", "regional_year", "regional_category",
                "regional_status", "regional_search"):
        if key in st.session_state:
            st.session_state[key] = st.session_state[key]
    def explore():
        page_header("Explore public projects", "Find DPWH contracts and inspect the records behind each project.")
        show_live_projects()

    def regional():
        page_header("Regional review", "Browse saved regional findings, contractor scores, and contract evidence.")
        show_regional_review_page()

    # Native navigation (st.navigation): one URL per destination, aria-current on the active link. Only the
    # chosen workspace renders; inactive admin views do not fetch or run.
    navigation({"Explore projects": explore, "Regional review": regional, "Admin": show_admin}).run()
    with st.expander("About the evidence"):
        st.caption(language.LIMITATION_TEXT)
        st.caption("Reported status comes from the DPWH record. Photo assessments are automatic detector "
                   "observations. Manual decisions are recorded by a reviewer. They are shown separately.")
        st.caption(language.TIMING_NOTE + " Photo dates are capture dates, not verified completion dates.")
        st.caption(language.MODEL_SCORE_NOTE)


MAX_REVIEW_CONTRACTS = 3
CONTRACT_ID_SEPARATORS = re.compile(r"[,;\n\r]+")        # comma, semicolon or new line; a space never separates two IDs
CONTRACT_ID_PATTERN = re.compile(r"[A-Z0-9][A-Z0-9_-]{0,39}")


def parse_contract_input(text):
    """Normalize typed contract IDs. Returns (ids, problem, notes); never drops or splits an entry silently.

    Accepted and normalized: capitals, spaces around an ID, comma / semicolon / new-line separators, repeated
    separators, and the same ID entered more than once (kept once, and said so in ``notes``).
    Rejected with a message naming the entry: a space inside an entry ("24CK 0181" could be one ID or two),
    characters other than letters, numbers, hyphen and underscore, IDs longer than 40 characters, and more
    than three distinct IDs (nothing is truncated).
    """
    entries = [entry.strip() for entry in CONTRACT_ID_SEPARATORS.split(text or "")]
    entries = [entry for entry in entries if entry]
    spaced = [entry for entry in entries if re.search(r"\s", entry)]
    if spaced:
        return (), ("Separate contract IDs with commas or new lines. An ID has no spaces, so this entry is unclear: "
                    + "; ".join(f"“{entry}”" for entry in spaced[:3]) + (" …" if len(spaced) > 3 else "") + "."), ()
    tokens = [entry.upper() for entry in entries]
    invalid = [entry for entry, token in zip(entries, tokens)
               if not (entry.isascii() and CONTRACT_ID_PATTERN.fullmatch(token))]      # ASCII first: upper() must not rewrite "ß"
    if invalid:
        shown = [entry if len(entry) <= 40 else entry[:40] + "…" for entry in invalid[:5]]
        return (), ("Contract IDs use letters, numbers and hyphens only, up to 40 characters. Check: "
                    + ", ".join(shown) + (" …" if len(invalid) > 5 else "") + "."), ()
    repeats = {token: count for token, count in Counter(tokens).items() if count > 1}
    notes = tuple(f"{token} was entered {count} times and is reviewed once." for token, count in repeats.items())
    contracts = tuple(dict.fromkeys(tokens))
    if len(contracts) > MAX_REVIEW_CONTRACTS:
        extra = contracts[MAX_REVIEW_CONTRACTS:]
        return (), (f"Enter up to {MAX_REVIEW_CONTRACTS} contract IDs. Remove {len(extra)} to continue: "
                    + ", ".join(extra) + "."), notes
    return contracts, None, notes


def parse_contract_ids(text):
    """(ids, problem) form of parse_contract_input, kept for callers that do not show notes."""
    ids, problem, _ = parse_contract_input(text)
    return ids, problem


def show_admin():
    page_header("Admin workspace", "Manage reviewed contracts, refresh saved evidence and check detector readiness.", "REVIEW MANAGEMENT")
    with closing(open_assessment_db()) as database:
        groups = dict(database.execute("SELECT category,COUNT(*) FROM contract_assessments GROUP BY category"))
        pending = database.execute("""SELECT COUNT(*) FROM regional_queue q WHERE q.done=0 AND NOT EXISTS (
            SELECT 1 FROM queue_failures f WHERE f.filters=q.filters AND f.contract_id=q.contract_id)""").fetchone()[0]
    metrics = st.columns(3)
    metrics[0].metric("Saved contract reviews", f"{sum(groups.values()):,}")
    attention = sum(count for category, count in groups.items()
                    if category in {NEUTRAL_REVIEW_CATEGORY, "Photos inconclusive", "Photo analysis unavailable · retry needed"})
    metrics[1].metric("Need evidence review", f"{attention:,}", help="Further review needed, inconclusive and failed-analysis categories.")
    metrics[2].metric("Queued checks", f"{pending:,}", help="Contracts waiting to be reviewed, not counting failed ones (see each queue). A contract may appear in more than one queue.")
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
        query = st.text_area("Contract IDs", placeholder="e.g. 24CK0181, 23JE0090", key="admin_contract_ids", height=80,
                             help="Up to three IDs, separated by commas or new lines. Case and spaces around an ID do not matter.")
        contracts, problem, notes = parse_contract_input(query)
        for note in notes:
            field_feedback(note, kind="info")
        if problem:
            field_feedback(problem)
        elif contracts:
            show_ai_reviews(contracts, contracts[0])
        else:
            st.caption("Enter up to three contract IDs to review them side by side.")
    elif section == "Rerun reviews":
        st.subheader("Refresh saved reviews")
        st.caption("Choose a category to recheck with the current models. Manual decisions are preserved.")
        show_category_reruns()
    else:
        show_model_status()


def model_readiness_rows():
    rows = []
    for detector, (baseline, _, target, _) in YOLO_MODELS.items():
        path = active_weights(detector, baseline)
        installed = path.is_file()
        candidate = latest_candidate(detector)
        rows.append(model_readiness.inspect_detector(
            detector, target, path, PROJECT_DIR,
            quality=model_quality(detector, path) if installed else None,
            digest=checkpoint_digest(path) if installed else None,
            candidate=candidate[0] if candidate else None))
    return rows


def show_model_status():
    st.subheader("Model readiness")
    st.caption("See which detectors are installed, how far each has been validated, and what to do next.")
    st.caption("Missing, Experimental, Provisional and Validated are separate states. No detector status certifies a "
               "complete contract.")
    rows = model_readiness_rows()
    totals = model_readiness.summary(rows)
    st.markdown(f"**{totals['text']}**")
    if note := model_readiness.no_checkpoint_note(rows):
        notice(*note, icon=":material/block:")
    st.dataframe([{"Detector": row["detector"], "Detects": row["target"], "Availability": row["availability"],
                   "Eligibility": row["eligibility"], "How observations are used": row["use"],
                   "Next action": row["next_action"]} for row in rows], hide_index=True,
                 column_config={"Next action": st.column_config.TextColumn(width="large"),
                                "How observations are used": st.column_config.TextColumn(width="large")})
    st.caption(f"Assessment policy {ASSESSMENT_VERSION} · inference format {INFERENCE_VERSION}")
    with st.expander("Technical details and release checks"):
        st.caption("Approval is bound to the checkpoint's SHA256 hash, not its file name or a finished training run.")
        st.dataframe([{
            "Detector": row["detector"], "Checkpoint": row["display_path"],
            "Size": f"{row['size_bytes']:,} bytes" if row["size_bytes"] is not None else "Not installed",
            "SHA256": row["sha256"] or "Not installed",
            "Approval file": "Present" if row["approval_report"] else "None",
            "Trained candidate": row["candidate"]["label"] if row["candidate"] else "None",
            "Evaluation report": ("Present" if row["candidate"]["evaluation_report"] else "None") if row["candidate"] else "",
        } for row in rows], hide_index=True)
        present, absent = model_readiness.release_commands(PROJECT_DIR)
        if present:
            st.caption("Release checks available in this checkout. Evaluating and promoting are separate, explicit steps.")
            st.code("\n".join(command for _, command in present), language="bash")
        for script, _ in absent:
            st.caption(f"{script} is not present in this checkout, so that release step cannot be run from here.")


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
            review_store.ensure_schema(database)
            queue_worker.ensure_schema(database)
    except sqlite3.Error:
        database.close()
        raise
    return database


def open_assessment_db_readonly():
    """Read-only handle for showing saved decisions and history. It cannot create tables or change a review."""
    return sqlite3.connect(PHOTO_REVIEW_DB.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)


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
    if manual or override:
        # Keep the automatic observation beside the human decision; the stored category stays the decision.
        assessment["automatic_evidence_category"] = assessment["completion_evidence_category"]
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


def review_is_stale(record, signature=None):
    return (record.get("assessment", {}).get("assessment_version") != ASSESSMENT_VERSION
            or record.get("review_provenance", {}).get("models") != (signature or current_model_signature())
            or record.get("assessment", {}).get("legacy_detection_format", False))


def review_due(record, reviewed_at, signature=None):
    if review_is_stale(record, signature):
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
        error_state("The review finished, but its category could not be saved.",
                    "The saved review database did not accept the update.",
                    kept="The previously saved review is unchanged.")


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
        st.success(escape_markdown(message))  # contains contract IDs and reviewer text
    workspace = st.radio("Review view", ["Review workspace", "Saved groups"], horizontal=True) == "Review workspace"
    refresh = st.button("Refresh saved groups")
    # A saved decision (or a rerun that moved the contract) keeps that contract on screen, even though its
    # group changed. Applied before the group and contract widgets exist, so their keys may be set.
    follow = st.session_state.pop("saved_review_follow", None)
    if follow:
        st.session_state["saved_review_category"] = follow["category"]
        st.session_state[f"saved_review_contract_{follow['category']}"] = follow["contract"]
        if follow.get("queue_next"):
            st.session_state["saved_review_next"] = {"for": follow["contract"], "group": follow["queue_group"],
                                                     "contract": follow["queue_next"]}
        else:
            st.session_state.pop("saved_review_next", None)
        if (st.session_state.get("saved_review_search") or "").strip().upper() not in follow["contract"].upper():
            st.session_state["saved_review_search"] = ""
    try:
        with closing(open_assessment_db()) as database:
            if refresh:
                refresh_inconclusive_reviews(database)
            groups = database.execute(
                "SELECT category, COUNT(*) FROM contract_assessments GROUP BY category ORDER BY category"
            ).fetchall()
            if not groups:
                empty_state("No saved reviews yet.",
                            "Analyze a contract or run a regional review to save its photo evidence assessment here.",
                            action_label="Go to Admin · Regional reviews", key="reviewed_empty_to_regional",
                            on_click=partial(go_to, "Admin", admin_section="Regional reviews"),
                            icon=":material/folder_open:")
                return
            counts = dict(groups)
            completion_groups = review_store.DECISION_CATEGORIES
            options = set(counts).union(completion_groups)
            if selected_group := st.session_state.get("saved_review_category"):
                options.add(selected_group)
            options = sorted(options)
            if st.session_state.get("saved_review_category") not in options:
                st.session_state["saved_review_category"] = groups[0][0]
            # Options stay raw stored values; only their display text is qualified.
            group = st.selectbox("Saved evidence group", options,
                                 key="saved_review_category",
                                 format_func=lambda raw: f"{language.category_label(raw)} ({counts.get(raw, 0):,})")
            search_key = "saved_review_search"
            search = (st.session_state.get(search_key) or "").strip() if workspace else ""
            if workspace:
                search = st.text_input("Find a contract by ID", placeholder="Enter all or part of a contract ID",
                                       key=search_key).strip()
            ids = [row[0] for row in database.execute("""
                SELECT contract_id FROM contract_assessments
                WHERE category = ? AND instr(upper(contract_id), upper(?)) > 0
                ORDER BY reviewed_at DESC, contract_id
            """, (group, search))]
            if not ids:
                if search:
                    empty_state("No contracts match this search.",
                                f"No saved contract ID in {escape_markdown(language.category_label(group))} contains “{escape_markdown(search)}”. "
                                "Check the ID or clear the search.", icon=":material/search_off:")
                else:
                    empty_state(f"No contracts remain in {language.category_label(group)}.",
                                "Choose another saved evidence group.")
                return
            navigation_key = f"saved_review_contract_{group}"
            if st.session_state.get(navigation_key) not in ids:
                st.session_state[navigation_key] = ids[0]
            position = ids.index(st.session_state[navigation_key])
            if workspace:
                previous, counter, following = st.columns([1, 2, 1])
                previous.button("Previous in list", disabled=position == 0, on_click=select_saved_contract,
                                args=(navigation_key, ids[max(0, position - 1)]))
                counter.caption(f"{position + 1:,} of {len(ids):,} matching contracts")
                following.button("Next in list", disabled=position == len(ids) - 1, on_click=select_saved_contract,
                                 args=(navigation_key, ids[min(len(ids) - 1, position + 1)]))
            selected = st.selectbox("Saved contract", ids, key=navigation_key)
            row = database.execute(
                "SELECT reviewed_at, summary FROM contract_assessments WHERE contract_id = ?", (selected,),
            ).fetchone()
    except (OSError, sqlite3.Error):
        error_state("Saved contract groups are unavailable.", "The saved review database could not be read.",
                    kept="No saved review was changed.", retry_label="Retry loading saved groups",
                    key="retry_saved_groups")
        return
    if not row:
        empty_state("This saved review is unavailable.",
                    "It may have moved to another group since this page loaded. Refresh saved groups to update the list.")
        return
    reviewed_at, payload = row
    record = json.loads(payload)
    remaining = ids[position + 1:] or ids[:position]
    queue_state = {"contract": selected, "queue_group": group, "queue_next": remaining[0] if remaining else None}
    services = review_services(
        record, namespace="saved", stale=review_is_stale(record),
        after_save=partial(follow_saved_decision, queue_state),
        next_contract=queued_next(selected),
        actions=partial(show_rerun_review, group, selected))
    show_review_workspace(record, reviewed_at, services)
    if evidence := record["assessment"].get("manual_completion_evidence"):
        photo_path = PHOTO_REVIEW_DB.parent / "manual_evidence" / evidence
        if photo_path.is_file():
            st.image(str(photo_path), caption="Photo supplied with the manual decision")
    if review := record.get("regional_review"):
        st.text(language.relabel_text(review["summary"]))
    with st.expander("Saved contract details"):
        st.json(record)


def select_saved_contract(key, contract):
    st.session_state[key] = contract


def follow_saved_decision(follow, saved):
    """After a decision is saved: keep showing that contract (its group may have changed) and remember
    which contract comes next in the group the reviewer was working through. Navigation only."""
    st.session_state["saved_review_follow"] = {**follow, "category": saved["category"]}


def queued_next(contract):
    """The Next contract action for a just-decided contract, or None when nothing else is queued."""
    queued = st.session_state.get("saved_review_next")
    if not queued or queued.get("for") != contract or not queued.get("contract"):
        return None
    return partial(go_to_queued_contract, queued)


def go_to_queued_contract(queued):
    group, contract = queued["group"], queued["contract"]
    st.session_state["saved_review_category"] = group
    st.session_state[f"saved_review_contract_{group}"] = contract
    st.session_state.get("decision_receipts", {}).pop(queued["for"], None)
    st.session_state.pop("saved_review_next", None)


automatic_category = review_store.automatic_category
eligible_review_categories = review_store.eligible_review_categories


def save_manual_category(record, category, note, reviewer=None):
    """Record one reviewer decision (a new revision). Reviewer defaults to PROJECTWATCH_REVIEWER."""
    reviewer = reviewer if reviewer is not None else os.environ.get("PROJECTWATCH_REVIEWER", "")
    with closing(open_assessment_db()) as database:
        return review_store.save_decision(database, record, category, note, reviewer)


def review_overlay(photo, number):
    """Detector overlays for one saved photo, {detector: image}. Empty when no checkpoint or the photo is unreadable.

    Called only when the reviewer asks for the overlay; each result is cached by analyze_project_photo.
    """
    images = {}
    for finding in photo.get("yolo") or []:
        detector = finding.get("detector")
        if finding.get("status") != "detected" or detector not in YOLO_MODELS or detector in images:
            continue
        weights = review_checkpoints(detector)[0]
        if not weights.is_file():
            continue
        try:
            with loading(f"Drawing the {detector} overlay"):
                _, overlay = analyze_project_photo(photo["url"], detector, str(weights.resolve()),
                                                   (INFERENCE_VERSION, checkpoint_digest(weights)))
        except (ImportError, OSError, RuntimeError, ValueError, requests.RequestException):
            continue
        if overlay is not None:
            images[detector] = overlay
    return images


def review_training_form(record, number):
    """Training correction for the selected photo. A separate form: it never touches the decision."""
    source = record["photos"][number - 1]
    construction = {BUILDING_STAGE_DETECTOR, SCAFFOLDING_DETECTOR, EQUIPMENT_DETECTOR,
                    *(target[0] for target in UNFINISHED_TARGETS.values())}
    detectors = category_detectors(source.get("category"), source.get("scope") or record.get("description")) or tuple(YOLO_MODELS)
    detectors = tuple(dict.fromkeys((*(d for d in detectors if d not in construction), UNDER_CONSTRUCTION_TRAINING)))
    with st.expander("Training correction · saves a photo label, not a decision"):
        render_training_correction(
            record, number, detectors=detectors,
            classes_for=lambda detector: UNDER_CONSTRUCTION_LABELS if detector == UNDER_CONSTRUCTION_TRAINING
            else YOLO_MODELS[detector][1])


def review_services(record, *, namespace, stale, after_save=None, next_contract=None, actions=None, detail=None):
    documents = (lambda: show_cost_documents(detail)) if detail else None
    satellite_source = detail if detail else record
    return review_workspace.Services(
        open_db=open_assessment_db, read_db=open_assessment_db_readonly, project_url=project_url, overlay=review_overlay,
        training_form=review_training_form, review_stale=stale,
        reviewer_default=os.environ.get("PROJECTWATCH_REVIEWER", ""), next_contract=next_contract,
        after_save=after_save, actions=actions, documents=documents, namespace=namespace,
        satellite=(lambda: show_satellite_link(satellite_source, key=f"{namespace}_satellite_{record['contractId']}"))
        if satellite_location(satellite_source) else None)


def show_review_workspace(record, reviewed_at, services, *, dates=None, amounts=None, source_checked=None, live_notes=()):
    automatic, note = automatic_category(record)
    record["assessment"]["flags"] = list(dict.fromkeys([*record["assessment"]["flags"], *status_and_date_flags(record)]))
    review_workspace.render_contract_review(
        record, reviewed_at=reviewed_at, services=services, automatic=automatic, automatic_note=note,
        dates=dates if dates is not None else record.get("dates") or {},
        amounts=amounts if amounts is not None else record.get("budgetPHP") or {},
        source_checked=source_checked, live_notes=live_notes)


def show_rerun_review(group, selected):
    """Rerun the automatic review with the current detectors. A saved reviewer decision is kept."""
    if st.button("Rerun review", key=f"rerun_saved_review_{selected}", icon=":material/refresh:",
                 help="Checks the photos again with the current detectors. A saved reviewer decision is kept."):
        try:
            with loading("Retrieving photos and checking them with the current detectors"):
                updated = summarize_contract(selected, save=False)
                if updated["galleryPhotoCount"] is None:
                    raise ValueError("Gallery unavailable")
                save_contract_assessments([updated], require_saved=True)
        except (requests.RequestException, OSError, sqlite3.Error, KeyError, ValueError):
            error_state("The review couldn’t be refreshed.", "The source or the detectors did not respond.",
                        kept="Your previous review is still saved.")
        else:
            category = updated["assessment"]["completion_evidence_category"]
            st.session_state["saved_review_rerun_result"] = f"Review refreshed for {selected} · {language.category_label(category)}"
            review_workspace.forget_seen_evidence(selected)  # the reviewer asked for, and now sees, the new evidence
            if category != group:
                st.session_state["saved_review_follow"] = {"contract": selected, "category": category,
                                                           "queue_group": None, "queue_next": None}
            st.rerun()


def show_cost_documents(detail):
    links = detail.get("links") or {}
    documents = {"programOfWork": "Program of Work", "advertisement": "Bid documents",
                 "contractAgreement": "Contract agreement"}
    available = []
    for key, label in documents.items():
        url = links.get(key) or ""
        parsed = urlsplit(url)
        if parsed.scheme == "https" and parsed.netloc:
            available.append((label, url))
    if available:
        st.caption("Check the linked documents for an itemized breakdown.")
        for label, url in available:
            st.link_button(label, url)
    else:
        st.caption("The API lists no costing documents for this contract.")


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
    st.session_state.pop("live_list_shown", None)


def reset_project_filters():
    # The fiscal year returns to its explicit default (current year), not to "All years". The result view
    # (table or list) and the detail-column choice are preferences, not filters, so they stay.
    # Widgets get their default values explicitly. Popping their keys is not enough: the browser still holds
    # the old values and sends them back on the next rerun (found in the phase 4 rendered check).
    defaults = {"live_search": "", "live_region": None, "live_category": None, "live_status": None,
                "live_year": fiscal_year_options()[0], "live_photos_only": False, "live_limit": 100,
                "live_open_choice": None}
    for key, value in defaults.items():
        st.session_state[key] = value
    for key in ("live_contract", "live_last_opened"):
        st.session_state.pop(key, None)
    reset_live_page()


# Explore state that must survive reruns, page switches and the contract detail view. live_last_opened and
# live_last_result are plain session values (not widgets) and persist without re-pinning.
LIVE_STATE_KEYS = ("live_search", "live_region", "live_category", "live_status", "live_year", "live_photos_only",
                   "live_limit", "live_page", "live_contract", "project_page_picker", "live_open_choice",
                   "live_view", "live_detail_columns", "live_list_shown")
LIVE_LIST_STEP = 25  # list view renders contract cards in steps so a 500-row page stays light


def shorten(text, limit):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def default_filters_only():
    """True on the initial view: nothing chosen except the default fiscal year."""
    state = st.session_state
    return (not (state.get("live_search") or "").strip()
            and (state.get("live_year") or fiscal_year_options()[0]) == fiscal_year_options()[0]
            and not any(state.get(key) for key in ("live_region", "live_category", "live_status", "live_photos_only")))


def fiscal_year_options():
    current_year = datetime.now(PHILIPPINE_TIME).year
    return [*(str(year) for year in range(current_year, 2015, -1)), "All years"]


def active_filter_summary():
    """The filters behind the current results, in words. The default fiscal year is stated explicitly."""
    state = st.session_state
    default_year = fiscal_year_options()[0]
    year = state.get("live_year") or default_year
    parts = ["All fiscal years" if year == "All years" else
             f"Fiscal year {year}" + (" (default)" if year == default_year else "")]
    parts.append(state.get("live_region") or "All regions")
    if state.get("live_category"):
        parts.append(state["live_category"])
    if state.get("live_status"):
        parts.append(f"Reported status {state['live_status']}")
    if state.get("live_photos_only"):
        parts.append("Only contracts with photos")
    if search := (state.get("live_search") or "").strip():
        parts.append(f"Search “{search}”")
    return parts


def open_live_contract(contract_id):
    # Remember where the user came from: Back to results re-selects this contract in the table, the
    # Contract to open picker and the list view (plan §5A, Law 11).
    if contract_id:
        st.session_state["live_contract"] = contract_id
        st.session_state["live_last_opened"] = contract_id
        st.session_state["live_open_choice"] = contract_id


def open_chosen_contract():
    open_live_contract(st.session_state.get("live_open_choice"))


def close_live_contract():
    st.session_state["live_contract"] = None
    st.session_state["live_returned"] = True  # one-time "You were viewing …" note on the results


def refresh_live_results():
    api_data.clear()


def saved_review_index(contract_ids):
    """Read-only lookup of saved photo assessments for listed contracts. Never runs analysis or writes."""
    if not contract_ids or not PHOTO_REVIEW_DB.is_file():
        return {}
    try:
        uri = PHOTO_REVIEW_DB.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=5)) as database:
            marks = ",".join("?" * len(contract_ids))
            rows = database.execute(
                f"SELECT contract_id, category, reviewed_at FROM contract_assessments WHERE contract_id IN ({marks})",
                tuple(contract_ids),
            ).fetchall()
    except sqlite3.Error:
        return {}
    return {contract: (category, reviewed_at) for contract, category, reviewed_at in rows}


def saved_review_record(contract):
    """Read-only: (reviewed_at, saved record) for one contract, None if not saved. Raises sqlite3.Error/ValueError."""
    if not PHOTO_REVIEW_DB.is_file():
        return None
    uri = PHOTO_REVIEW_DB.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as database:
        row = database.execute("SELECT reviewed_at, summary FROM contract_assessments WHERE contract_id = ?",
                               (contract,)).fetchone()
    if not row:
        return None
    return row[0], json.loads(row[1])


def show_saved_assessment(contract, detail):
    """Explore detail: the saved photo assessment, read-only and separate from the live source record."""
    st.subheader("Photo evidence assessment")
    try:
        saved = saved_review_record(contract)
    except (sqlite3.Error, ValueError):
        error_state("Saved photo assessment could not be read.", "The saved review database did not respond.",
                    kept="The source record and photos below are unaffected. Try again later.")
        return
    if not saved or not isinstance(saved[1].get("assessment"), dict):
        show_state("no_saved_assessment")
        st.caption(f"Reported status · {detail.get('status') or 'Unavailable'} (from the source record; not a photo assessment)")
        return
    reviewed_at, record = saved
    st.caption("Saved review, shown read-only. Opening a contract here never reruns or changes it. "
               "Reported status below is the value saved with the review; the live record above may be newer.")
    if review_is_stale(record):
        show_state("stale")
    automatic, note = automatic_category(record)
    evidence_overview(record, record["assessment"], automatic, reviewed_at=reviewed_at, automatic_note=note)


def review_date(value):
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return "Date unknown"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=PHILIPPINE_TIME)
    return f"{moment.astimezone(PHILIPPINE_TIME):%d %b %Y}"


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
        open_live_contract(contract_ids[selected_rows[0]])


def status_cell_style(status):
    # Reported status is a source fact, not evidence: no colour coding (§4.4), the text carries the meaning.
    # Weight only, so the cell stays readable in both themes without relying on st.context.theme.
    return "font-weight:600" if status in API_STATUSES else ""


# Explore refreshes on demand ("Refresh results"), not on a timer: a timed rerun of an open contract
# repeated photo analysis after cache expiry and could disturb typing or selection (plan §5A, Law 25).
@st.fragment
def show_live_projects():
    # Re-pin Explore state: fragment reruns drop values of widgets that are not rendered (e.g. the
    # filters while a contract is open), and the detail view must return to the same results.
    for key in LIVE_STATE_KEYS:
        if key in st.session_state:
            st.session_state[key] = st.session_state[key]
    if contract := st.session_state.get("live_contract"):
        show_live_contract(contract)
    else:
        show_live_results()


def show_live_search():
    """Search first: ID/description, Region and Fiscal year visible; the rest under More filters."""
    with st.container(border=True):
        st.subheader("Find a contract")
        search = st.text_input(
            "Contract ID or project description", placeholder="e.g. 24CK0181 or riverbank protection",
            key="live_search", on_change=reset_live_page, icon=":material/search:",
        )
        region_column, year_column = st.columns(2)
        region = region_column.selectbox("Region", [None, *API_REGIONS], format_func=lambda value: value or "All regions",
                                         on_change=reset_live_page, key="live_region")
        year = year_column.selectbox("Fiscal year", fiscal_year_options(), on_change=reset_live_page, key="live_year")
        with st.expander("More filters"):
            category = st.selectbox("Infrastructure category", [None, *API_CATEGORIES],
                                    format_func=lambda value: value or "All categories", on_change=reset_live_page,
                                    key="live_category")
            status = st.selectbox("Reported status", [None, *API_STATUSES], format_func=lambda value: value or "All statuses",
                                  on_change=reset_live_page, key="live_status")
            photos_only = st.toggle("Only show contracts with photos", on_change=reset_live_page, key="live_photos_only")
            if photos_only:
                st.session_state["live_limit"] = 50
            elif "live_limit" not in st.session_state:
                st.session_state["live_limit"] = 100
            limit = st.selectbox("Results per page", [50] if photos_only else [50, 100, 200, 500],
                                 on_change=reset_live_page, disabled=photos_only, key="live_limit",
                                 help="Photo checks run on 50 contracts at a time." if photos_only else None)
        show_active_filters()
        st.button("Reset filters", on_click=reset_project_filters, key="reset_live_filters",
                  help=f"Clears every filter and returns to fiscal year {fiscal_year_options()[0]}.")
    return {"page": st.session_state.get("live_page", 1), "limit": limit,
            "year": None if year == "All years" else year, "region": region, "category": category,
            "status": status, "search": search.strip() or None}, photos_only


def show_active_filters():
    """Active filters as a labelled list of chips (text, not colour). The default fiscal year is named."""
    # st.html, not markdown: the search text is user input and is HTML-escaped only (no markdown parsing).
    chips = "".join(f'<span class="bant-chip" role="listitem">{html.escape(part)}</span>'
                    for part in active_filter_summary())
    st.html(f'<div class="bant-filters"><span class="bant-filters-label" id="live-active-filters">Active filters'
            f'</span> <span role="list" aria-labelledby="live-active-filters">{chips}</span></div>')


def fetch_live_results(params):
    """(result, checked_at, stale) or None after reporting an outage.

    A failed request keeps the last successful results for the same filters and page on screen, labelled
    with the time they were checked, instead of discarding them (plan §8 Source/API outage).
    """
    request_key = fingerprint(params)
    try:
        with loading("Loading matching contracts from the DPWH source"):
            result, checked_at = api_data("/projects", params)
        pagination = result["pagination"]
        result = {"data": list(result["data"]),
                  "pagination": {"totalCount": int(pagination["totalCount"]),
                                 "totalPages": int(pagination["totalPages"])}}
    except (requests.RequestException, KeyError, TypeError, ValueError):
        last = st.session_state.get("live_last_result")
        if last and last[0] == request_key:
            error_state("The source is unavailable.", "The BetterGov API did not respond to the latest refresh.",
                        kept=f"Your filters and page are kept. The results below are from the last successful check, "
                             f"{language.format_timestamp(last[2])}. Saved reviews are unaffected.")
            return last[1], last[2], True
        error_state("The source is unavailable.", "The BetterGov API did not respond.",
                    kept="Your filters and page are kept. Saved reviews are unaffected.",
                    retry_label="Refresh results", on_retry=refresh_live_results, key="refresh_live_results_failed")
        return None
    st.session_state["live_last_result"] = (request_key, result, checked_at)
    return result, checked_at, False


def show_live_results():
    params, photos_only = show_live_search()
    page = params["page"]
    fetched = fetch_live_results(params)
    if fetched is None:
        return
    result, checked_at, stale = fetched
    pagination = result["pagination"]
    if page > max(1, pagination["totalPages"]):
        reset_live_page()
        st.rerun(scope="fragment")

    total = pagination["totalCount"]
    # The heading is the source's count of matching records. In photos-only mode it is stated as the count
    # before any photo check, and the checked/shown numbers for this page are reported separately below.
    st.subheader(f"{total:,} matching contract{'' if total == 1 else 's'}"
                 + (" before photo check" if photos_only else ""))
    freshness("Source last checked" if stale else "Source checked", checked_at)
    if default_filters_only():
        st.caption(f"Showing every contract for fiscal year {fiscal_year_options()[0]}. Search by contract ID or "
                   "project description to narrow the list.")
    st.caption("Results update when you change filters or select Refresh results; nothing refreshes on a timer.")
    st.button("Refresh results", key="refresh_live_results", on_click=refresh_live_results,
              icon=":material/refresh:", type="tertiary")
    if not result["data"]:
        empty_state("No contracts match these filters.",
                    "Active filters · " + " · ".join(escape_markdown(part) for part in active_filter_summary())
                    + ". Change a filter or reset them all.",
                    action_label="Reset filters", on_click=reset_project_filters, key="reset_live_filters_empty",
                    icon=":material/search_off:")
        project_pagination(page, pagination["totalPages"])
        return

    projects = result["data"]
    statuses = Counter(project.get("status") for project in projects)
    budgets = pd.to_numeric(pd.Series([project.get("budget") for project in projects]), errors="coerce")
    st.caption(
        f"Page {page:,} lists {len(projects):,} of {total:,} matching records"
        + (" (before photo check)" if photos_only else "")
        + f" · {statuses['Completed']:,} reported completed · {statuses['On-Going']:,} reported on-going · "
        f"reported budget {format_peso(budgets.sum(min_count=1))} across {int(budgets.notna().sum()):,} "
        f"record{'' if int(budgets.notna().sum()) == 1 else 's'} with an amount. Status and budget are as published in the source."
    )
    if photos_only:
        projects = show_photo_check(projects, total)
        if not projects:
            empty_state("No contracts with confirmed photos on this page.",
                        "Try the next results page or change your filters.", icon=":material/hide_image:")
            project_pagination(page, pagination["totalPages"])
            return
    show_live_matches(projects)
    project_pagination(page, pagination["totalPages"])


def show_photo_check(projects, total):
    """Photos-only: check this page's galleries and keep the four counts apart (plan §5A)."""
    with loading("Checking project galleries for photos"):
        availability = check_photo_availability(tuple(project["contractId"] for project in projects))
    shown = [project for project in projects if availability[project["contractId"]] is True]
    unchecked = sum(status is None for status in availability.values())
    counts = [("Matching records", total, "Records that match your filters in the source, before any photo check."),
              ("Checked on this page", len(projects), "Only this results page is checked for photos."),
              ("With photos, shown", len(shown), "Contracts on this page whose gallery has at least one photo.")]
    if unchecked:
        counts.append(("Could not be checked", unchecked, "The gallery source did not answer for these contracts."))
    for column, (label, value, note) in zip(st.columns(len(counts)), counts):
        column.metric(label, f"{value:,}", help=note)
    if unchecked:
        error_state(f"Photo availability could not be verified for {unchecked:,} contracts on this page.",
                    "The gallery source did not answer for them, so they are left out of this photos-only list.",
                    kept="Turn off Only show contracts with photos to list every matching contract.")
    return shown


def result_rows(projects):
    """One row per contract, newest reported completion first. Only source fields and saved reviews:
    nothing here fetches photos or runs analysis."""
    rows = pd.DataFrame(projects).reindex(columns=["contractId", "description", "location", "status",
                                                   "completionDate", "budget", "category", "infraType"])
    rows["completionDate"] = pd.to_datetime(rows["completionDate"], format="ISO8601", errors="coerce")
    rows = rows.sort_values("completionDate", ascending=False, na_position="last", kind="stable")
    contract_ids = tuple(rows["contractId"])
    reviews = saved_review_index(contract_ids)
    return pd.DataFrame({
        "id": contract_ids,
        "project": rows["description"].fillna("Description unavailable").tolist(),
        "region": [(location.get("region") or "—") if isinstance(location, dict) else "—"
                   for location in rows["location"]],
        "status": rows["status"].fillna("Unavailable").tolist(),
        "evidence": [language.category_label(reviews[c][0]) if c in reviews else "Not reviewed" for c in contract_ids],
        "reviewed": [review_date(reviews[c][1]) if c in reviews else "—" for c in contract_ids],
        "completion": [f"{value:%d %b %Y}" if pd.notna(value) else "—" for value in rows["completionDate"]],
        "budget": [format_peso(value) if pd.notna(value) else "—"
                   for value in pd.to_numeric(rows["budget"], errors="coerce")],
        "category": [next((v for v in (category, infra) if isinstance(v, str) and v.strip()), "—")
                     for category, infra in zip(rows["category"], rows["infraType"])],
    })


def show_live_matches(projects):
    rows = result_rows(projects)
    last_opened = st.session_state.get("live_last_opened")
    if st.session_state.pop("live_returned", False) and last_opened in tuple(rows["id"]):
        notice(f"You were viewing {last_opened}.", "It stays selected below, so you can continue from where you were.",
               icon=":material/history:")
    view_column, detail_column = st.columns([1, 1], vertical_alignment="bottom")
    view = view_column.radio("Show results as", ["Table", "List"], horizontal=True, key="live_view",
                             help="List shows each contract as a card with its own Open contract button; "
                                  "it suits narrow screens and screen magnifiers.")
    if view == "List":
        show_live_list(rows)
    else:
        details = detail_column.toggle("Add detail columns", key="live_detail_columns",
                                        help="Adds reported completion date, reported budget and category.")
        show_live_table(rows, details=details)


def show_live_table(rows, *, details=False):
    contract_ids = tuple(rows["id"])
    table = pd.DataFrame({
        # Plan §5A column order; ID and project share one pinned column so identity stays visible
        # when narrower layouts (e.g. 1024 px with the sidebar) need the table to scroll sideways.
        "Contract ID / project": [f"{contract} · {text}" for contract, text in zip(contract_ids, rows["project"])],
        "Region": rows["region"], "Reported status": rows["status"],
        "Photo evidence": rows["evidence"], "Last reviewed": rows["reviewed"],
    })
    column_config = {
        # Widths sum to about 900 px: no sideways scrolling at 1440 px; narrower layouts scroll with the
        # first column pinned.
        "Contract ID / project": st.column_config.TextColumn("Contract ID / project", width=300, pinned=True),
        "Region": st.column_config.TextColumn("Region", width=140),
        "Reported status": st.column_config.TextColumn("Reported status", width=115,
                                                       help="As published in the DPWH record; not a photo finding."),
        "Photo evidence": st.column_config.TextColumn("Photo evidence", width=210,
                                                      help="Saved automatic photo assessment or reviewer decision."),
        "Last reviewed": st.column_config.TextColumn("Last reviewed", width=95,
                                                     help="When the photo review was saved. Not the source check time."),
    }
    if details:
        # Detail columns come after the planned hierarchy; the table then scrolls with the first column pinned.
        table["Reported completion"] = rows["completion"]
        table["Reported budget"] = rows["budget"]
        table["Category"] = rows["category"]
        column_config.update({
            "Reported completion": st.column_config.TextColumn("Reported completion", width=110),
            "Reported budget": st.column_config.TextColumn("Reported budget", width=140),
            "Category": st.column_config.TextColumn("Category", width=180),
        })
    st.caption("Sorted by reported completion date, newest first. Photo evidence and Last reviewed come from saved "
               "reviews. Select a row's checkbox, or choose a contract below and use Open contract. — means the "
               "value is not in the record.")
    table_key = f"contract_results_{fingerprint(contract_ids)}"
    last_opened = st.session_state.get("live_last_opened")
    st.dataframe(
        table.style.map(status_cell_style, subset=["Reported status"]),
        hide_index=True, key=table_key, selection_mode="single-row",
        # Restores the row the user came back from; applied only when the table has no stored selection.
        selection_default={"selection": {"rows": [contract_ids.index(last_opened)]}}
        if last_opened in contract_ids else None,
        on_select=lambda: select_contract_from_table(table_key, contract_ids),
        column_config=column_config,
    )
    descriptions = dict(zip(contract_ids, rows["project"]))
    if st.session_state.get("live_open_choice") not in contract_ids:
        st.session_state["live_open_choice"] = None
    # Control and its action stack (no narrow side column): labels never truncate at any width.
    chosen = st.selectbox(
        "Contract to open", contract_ids, index=None, key="live_open_choice",
        placeholder="Choose a contract from this page",
        format_func=lambda contract: f"{contract} · {shorten(descriptions[contract], 70)}",
    )
    st.button("Open contract", type="primary", key="open_live_contract", on_click=open_chosen_contract,
              disabled=chosen is None)


def show_more_list_items(count):
    st.session_state["live_list_shown"] = count


def show_live_list(rows):
    """Compact, accessible alternative to the wide table: one card per contract, full project text,
    labelled facts and a button named for its contract. Cards render in steps of LIVE_LIST_STEP."""
    ids = list(rows["id"])
    shown = max(LIVE_LIST_STEP, st.session_state.get("live_list_shown") or 0)
    last_opened = st.session_state.get("live_last_opened")
    if last_opened in ids:
        shown = max(shown, ids.index(last_opened) + 1)  # the contract the user came back from is always shown
    shown = min(shown, len(ids))
    st.caption(f"Sorted by reported completion date, newest first · showing {shown:,} of {len(ids):,} on this page.")
    for row in rows.head(shown).itertuples(index=False):
        with st.container(border=True, key=f"live_card_{row.id}"):
            st.markdown(f"**{escape_markdown(row.id)}**"
                        + (" · :material/history: last opened" if row.id == last_opened else ""))
            st.text(row.project)  # external text, never markdown
            st.caption(f"Region: {escape_markdown(row.region)} · Reported status: {escape_markdown(row.status)}  \n"
                       f"Photo evidence: {escape_markdown(row.evidence)} · "
                       f"Last reviewed: {escape_markdown(row.reviewed)}")
            st.button(f"Open contract {row.id}", key=f"open_card_{row.id}", on_click=open_live_contract,
                      args=(row.id,))
    if shown < len(ids):
        more = min(LIVE_LIST_STEP, len(ids) - shown)
        st.button(f"Show {more:,} more on this page", key="live_list_more", on_click=show_more_list_items,
                  args=(shown + more,))


def show_live_contract(contract):
    back, source = st.columns([1, 1], vertical_alignment="center")
    back.button("Back to results", icon=":material/arrow_back:", key="live_back_to_results", on_click=close_live_contract)
    with source:
        st.link_button("Open source record", project_url(contract), icon=":material/open_in_new:")
    st.caption(f"Explore projects / {escape_markdown(contract)} · results kept: "
               + " · ".join(escape_markdown(part) for part in active_filter_summary())
               + f" · page {st.session_state.get('live_page', 1):,}")
    saved = saved_review_index((contract,)).get(contract)
    path = f"/projects/{quote(contract, safe='')}"
    try:
        detail, checked_at = api_data(path)
    except (requests.RequestException, KeyError, ValueError):
        kept = (f"Your previous saved review is retained · {language.category_label(saved[0])} · saved "
                f"{language.format_timestamp(saved[1])}." if saved else "Your results and filters are kept.")
        error_state("The source is unavailable.", "This contract's record could not be loaded.", kept=kept,
                    retry_label="Retry loading contract", on_retry=refresh_live_results,
                    key=f"retry_live_contract_{contract}")
        if saved:
            show_saved_assessment(contract, {})
        return
    summary = detail.get("imageSummary") or {}
    project = pd.DataFrame([{
        "contractId": contract, "description": detail.get("description"), "status": detail.get("status"),
        "contractor": detail.get("contractor"),
        "completionDate": pd.to_datetime(detail.get("completionDate"), errors="coerce"),
    }])
    metadata = pd.DataFrame([{
        "contractId": contract, "hasImages": summary.get("hasImages"),
        "totalImages": summary.get("totalImages"), "latestImageDate": summary.get("latestImageDate"),
    }])
    row = audit_images(project, metadata, pd.DataFrame(columns=PHOTO_COLUMNS)).iloc[0]
    try:
        saved_row = saved_review_record(contract)
    except (sqlite3.Error, ValueError):
        saved_row = None
    if saved_row and isinstance(saved_row[1].get("assessment"), dict) and saved_row[1].get("photos") is not None:
        # One shared workspace for saved and live paths. Dates and amounts come from the live record; any
        # difference from what the review saved is stated, not hidden.
        reviewed_at, record = saved_row
        notes = review_workspace.live_record_differences(record, detail, detail.get("completionDate"))
        services = review_services(record, namespace="live", stale=review_is_stale(record), detail=detail)
        show_review_workspace(record, reviewed_at, services, dates=contract_dates(detail), amounts=contract_amounts(detail),
                              source_checked=checked_at, live_notes=notes)
        with st.expander("Live source gallery with detector observations (not saved)"):
            show_live_photos(contract, path, detail.get("components") or [],
                             detail.get("category") or detail.get("infraType"), description=detail.get("description"))
        return
    show_contract(contract, row)
    source_time, review_time = st.columns(2)
    source_time.caption(f"Source record checked {language.format_timestamp(checked_at)}")
    if saved:
        review_time.caption(f"Saved review {language.format_timestamp(saved[1])} · {language.category_label(saved[0])}")
    else:
        review_time.caption("No saved review for this contract yet. Admin › Contract reviews can analyze it.")
    show_saved_assessment(contract, detail)
    show_live_photos(
        contract, path, detail.get("components") or [], detail.get("category") or detail.get("infraType"),
        description=detail.get("description"),
    )
    with st.expander("Timeline, budget and location"):
        show_satellite_link(detail, key=f"live_satellite_{contract}")
        show_contract_timeline(detail)
        show_budget(detail)


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
        st.caption(":material/arrow_upward: Select a region to start or resume a review.")
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
        with closing(open_assessment_db()) as db:
            queued = db.execute("SELECT COUNT(DISTINCT contract_id) FROM regional_queue WHERE done = 0").fetchone()[0]
        empty_state("No saved regional reviews yet.",
                    "Saved results appear here after a regional review is run in Admin. This page only shows saved "
                    "reviews; it never starts one."
                    + (f" {queued:,} contract{'' if queued == 1 else 's'} {'is' if queued == 1 else 'are'} queued "
                       "and waiting to be reviewed; open Regional reviews in Admin to run or resume them." if queued else ""),
                    action_label="Go to Admin · Regional reviews", key="regional_empty_to_admin",
                    on_click=partial(go_to, "Admin", admin_section="Regional reviews"),
                    icon=":material/map:")
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
    entries = saved_regional_entries(filters)
    if not entries:
        empty_state("No saved reviews match this region and year.",
                    "Choose another year, or run the regional review in Admin to save reviews for this selection.",
                    action_label="Go to Admin · Regional reviews", key="regional_none_to_admin",
                    on_click=partial(go_to, "Admin", admin_section="Regional reviews"), icon=":material/map:")
        return
    with closing(open_assessment_db()) as db:
        saved = db.execute("SELECT report FROM regional_highlights WHERE filters = ?",
                           (regional_filter_key(filters),)).fetchone()
    report = json.loads(saved[0]) if saved else None
    services = regional_view.Services(
        show_details=show_regional_contract_details, open_contract=open_regional_contract,
        contractor_comparison=show_contractor_performance, format_peso=format_peso,
        generate_highlights=partial(generate_regional_highlights, filters),
        go_admin=partial(go_to, "Admin", admin_section="Regional reviews"), model_name=review_model())
    regional_view.render_regional_review(region, "All saved fiscal years" if year == "All saved years" else f"Fiscal year {year}",
                                         filters, entries, saved_regional_queue(filters), report, services)


def open_regional_contract(contract_id):
    open_live_contract(contract_id)
    go_to("Explore projects")


def generate_regional_highlights(filters, records):
    """Run the local model over saved records. Returns (report, error); a failure never touches recorded facts."""
    try:
        report = summarize_regional_evidence(records, review_model())
    except (requests.RequestException, ValueError, OSError, sqlite3.Error, project_review.ReviewError):
        return None, "The local model could not complete the highlights. Check that it is running, then try again."
    report["generated_at"] = datetime.now(PHILIPPINE_TIME).isoformat()
    if report["errors"] and not report["completed_batches"]:
        return report, "The local model did not return any highlights. Check that it is running, then try again."
    if report["completed_batches"] and not report["errors"]:
        try:
            with closing(open_assessment_db()) as db, db:
                db.execute("INSERT OR REPLACE INTO regional_highlights VALUES (?, ?)",
                           (regional_filter_key(filters), json.dumps(report, ensure_ascii=False)))
        except sqlite3.Error:
            return report, "The highlights were generated but could not be saved. They are shown for this visit only."
    return report, None


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
        if st.button("Generate regional overview", type="primary", key="generate_regional_overview"):
            st.session_state.pop("regional_review_v5", None)
            if use_saved:
                try:
                    assessments = saved_regional_assessments(filters)
                except (OSError, sqlite3.Error):
                    error_state("Saved reviews could not be loaded.", "The saved review database did not respond.",
                                kept="No saved review was changed. Select Generate regional overview to retry.")
                    return
                if not assessments:
                    empty_state("No saved queue entries match these filters.",
                                "Run step 1 · Review project evidence first, or turn off Use saved reviews.")
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
        if st.button("Generate evidence highlights", key="generate_regional_highlights", help="Optional. A local model selects recorded facts worth highlighting; it does not write new findings."):
            progress = st.progress(0, text="Generating evidence highlights · starting")
            try:
                report = summarize_regional_evidence(
                    samples, review_model(),
                    lambda done, total: progress.progress(done / total, text=f"Generating evidence highlights · {done:,} of {total:,} batches"),
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
    return [entry["record"] for entry in saved_regional_entries(filters, check_stale=False)]


def saved_regional_queue(filters):
    """Distinct contracts in the saved review queues for this region and year: completed, and still queued."""
    with closing(open_assessment_db()) as db:
        rows = db.execute("""
            SELECT contract_id, done FROM regional_queue
            WHERE CASE WHEN json_valid(filters) THEN
                json_extract(filters, '$.region') = ?
                AND (? IS NULL OR json_extract(filters, '$.year') = ?)
            ELSE 0 END
        """, (filters["region"], filters.get("year"), filters.get("year"))).fetchall()
    completed = {contract for contract, done in rows if done}
    pending = {contract for contract, done in rows if not done} - completed
    return {"completed": len(completed), "pending": len(pending)} if rows else None


def saved_regional_entries(filters, *, check_stale=True):
    """Included saved reviews as {record, reviewed_at, stale}. Stale means saved with earlier rules or detectors."""
    signature = current_model_signature() if check_stale else None
    with closing(open_assessment_db()) as db:
        rows = db.execute("""
            SELECT DISTINCT a.summary, a.reviewed_at
            FROM regional_queue AS q
            JOIN contract_assessments AS a USING (contract_id)
            WHERE q.done = 1 AND CASE WHEN json_valid(q.filters) THEN
                json_extract(q.filters, '$.region') = ?
                AND (? IS NULL OR json_extract(q.filters, '$.year') = ?)
            ELSE 0 END
        """, (filters["region"], filters.get("year"), filters.get("year"))).fetchall()
    search = (filters.get("search") or "").casefold()
    entries = []
    for payload, reviewed_at in rows:
        record = json.loads(payload)
        if any(filters.get(field) and filters[field] != record.get(field) for field in ("category", "status")):
            continue
        if search and search not in record["contractId"].casefold() and search not in (record.get("description") or "").casefold():
            continue
        entries.append({"record": record, "reviewed_at": reviewed_at,
                        "stale": review_is_stale(record, signature) if check_stale else False})
    return entries


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
        with loading("Fetching regional records"):
            totals, projects = regional_summary(filters)
    except (requests.RequestException, KeyError, ValueError):
        error_state("The regional records could not be loaded completely.", "The source did not respond.",
                    kept="Saved reviews are unchanged. Select Generate regional overview to retry.")
        return
    samples = []
    without_photos = set()
    if include_photos and projects:
        progress = st.progress(0, text="Retrieving photos · checking regional galleries")
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
        st.caption("Saved photo evidence assessments by category. Manual decisions appear under their decision category.")
        with st.expander("Regional totals", expanded=not dashboard):
            for finding in regional_findings(totals):
                st.text(finding)
        if counts := totals.get("contract_assessments"):
            # Native progress rows instead of a chart: text follows the theme in both modes, long labels wrap
            # at any width, and each row states kind, category and count in words. Colour (icon) only
            # supplements the kind word, using the same semantic slots as category badges.
            breakdown = pd.DataFrame(
                [(language.category_label(raw), language.KIND_STYLE[language.category_kind(raw)][0], count, raw)
                 for raw, count in counts.items()],
                columns=["Evidence category", "Kind", "Contracts", "Stored value"],
            ).sort_values(["Contracts", "Evidence category"], ascending=[False, True])
            largest = max(counts.values())
            for label, raw, count in zip(breakdown["Evidence category"], breakdown["Stored value"], breakdown["Contracts"]):
                word, icon, color = language.KIND_STYLE[language.category_kind(raw)]
                st.progress(count / largest, text=f":{color}[{icon} {word}] · {escape_markdown(label)} · "
                                                  f"**{count:,} contract{'' if count == 1 else 's'}**")
            st.caption("Bar length is relative to the largest category. Counts are saved contract reviews, not verdicts.")
            with st.expander("Table view of evidence categories"):
                st.dataframe(breakdown[["Evidence category", "Kind", "Contracts", "Stored value"]], hide_index=True)
    if samples:
        with st.container(border=True):
            selected_project = show_contractor_performance(samples)
    flagged = totals.get("flagged_contracts", {})
    st.caption(f"Contracts flagged for review · {len(flagged):,}")
    if flagged:
        with st.expander("Flagged contracts"):
            st.dataframe(
                [{"Contract": contract, "Review flags": " · ".join(
                    language.flag_text(flag)[0] for flag in dict.fromkeys(flags))} for contract, flags in flagged.items()],
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
            st.text(contract)
            if description := selected_project.get("description"):
                st.text(description)
            assessment = selected_project.get("assessment") or {}
            automatic, note = automatic_category(selected_project)
            # Compact form: the full three-part overview is in the contract details above.
            assessment_summary(selected_project, assessment, automatic, automatic_note=note)
            photos = selected_project.get("photos", [])
            latest_day = language.latest_capture_day(photos)
            for start in range(0, len(photos), 3):
                batch = photos[start:start + 3]
                for index, (column, photo) in enumerate(zip(st.columns(3), batch), start=start):
                    caption = language.photo_caption(photos, index, latest_day)
                    with column:
                        photo_with_fallback(photo.get("url"), f"Project photo {index + 1}", caption)
                        if photo.get("url"):
                            st.link_button(f"Open photo {index + 1}", photo["url"])
            if not photos:
                show_state("no_photos" if selected_project.get("galleryPhotoCount") == 0 else "retrieval_failed",
                           "No photos were saved with this review.")
        elif samples:
            st.caption("Select a project row in the contractor table to see its photo evidence.")
    if error:
        error_state("Evidence highlights are unavailable.", escape_markdown(error),
                    kept="Recorded facts are still shown.")
    if isinstance(report, str):
        st.subheader("Evidence highlights")
        st.text(language.relabel_text(report))
    elif report:
        st.subheader("Evidence highlights")
        st.caption("Selected from recorded facts; the text below is the saved fact, not generated wording.")
        st.caption(f"{report['completed_batches']:,} of {report['total_batches']:,} batches summarized.")
        if report["errors"]:
            error_state(f"{len(report['errors']):,} highlight batch{'' if len(report['errors']) == 1 else 'es'} failed.",
                        "  \n".join(escape_markdown(str(failure)) for failure in report["errors"]),
                        kept="Highlights from completed batches and all recorded facts are still shown.")
        if report["summary"]:
            st.text(language.relabel_text(report["summary"]))
        for summary in report["batch_summaries"]:
            st.text(language.relabel_text(summary))


def show_contractor_performance(contracts):
    groups = contractor_performance(contracts)
    if not groups:
        return
    st.subheader("Contractor schedule scores · saved reviews")
    st.caption("A rules-based comparison of reported dates and saved photo assessments for the included contracts. "
               "It is not a comprehensive quality assessment of any contractor.")
    scored_total = sum(row["Points"] is not None for group in groups for row in group["contracts"])
    included_total = sum(len(group["contracts"]) for group in groups)
    by_id = {record["contractId"]: record for record in contracts}
    unreported = [g for g in groups if all(not (by_id[r["Contract"]].get("contractor") or "").strip()
                                          for r in g["contracts"])]
    named = len(groups) - len(unreported)
    unreported_contracts = sum(len(g["contracts"]) for g in unreported)
    st.markdown(f"**{named:,} contractor{'' if named == 1 else 's'} compared · {included_total:,} included "
                f"contract{'' if included_total == 1 else 's'} · {scored_total:,} scored · "
                f"{included_total - scored_total:,} with No score**")
    if unreported_contracts:
        st.caption(f"{unreported_contracts:,} included contract{' has' if unreported_contracts == 1 else 's have'} no reported "
                   f"contractor and {'appears' if unreported_contracts == 1 else 'appear'} in one “Unreported contractor” row. That row is a holding group, not a contractor.")
    st.caption("No score means the contract has no qualifying photo evidence or reported dates to score. It is not zero "
               "and it is left out of every total and average below.")
    st.caption("Formula · Ranking score = total points ÷ √(number of scored contracts). Every contractor is compared on "
               "this same basis; Points per scored contract is the plain average. Scoring rules are unchanged.")
    st.caption(f"Limits · Contractors with fewer than {regional_view.SMALL_SCORED} scored contracts are marked Small sample "
               "because one contract can move their rank. The score uses reported dates and saved photo assessments only; "
               "it says nothing about quality or conduct. " + language.TIMING_NOTE)
    st.html("".join(f'<span class="bant-chip">{language.score_level_label(label)} {points:+d}</span>'
                    for label, points in CONTRACT_SCORE_LEVELS.items() if points is not None))
    with st.expander("How scoring works"):
        st.caption("Schedule points compare the reported completion date with the listed contract expiry. Incomplete "
                   f"contracts before expiry are pending. An assessment flag for possible unfinished work deducts {UNFINISHED_WORK_PENALTY} points.")
        st.caption("Ranking score is total points divided by the square root of the number of scored contracts. "
                   "For example, an average of 2 points across 4 scored contracts gives a ranking score of 4.")
        st.caption("Only contracts with a qualifying photo assessment (visible scope evidence or possible unfinished work) "
                   "receive points. A blank score means No score; it is never counted as zero.")
        st.caption("Scoring convention: if the latest reviewed photo was captured 8–31 days after reported completion, one point "
                   "is deducted; more than 31 days, two points. " + language.TIMING_NOTE)
    totals = []
    for group in groups:
        points = [row["Points"] for row in group["contracts"] if row["Points"] is not None]
        totals.append({
            "Contractor": group["name"], "Included contracts": len(group["contracts"]), "Scored": len(points),
            "Total points": sum(points) if points else None,
            "Ranking score": sum(points) / sqrt(len(points)) if points else None,
            "Points per scored contract": round(sum(points) / len(points), 2) if points else None,
            "No score": len(group["contracts"]) - len(points),
            "Sample": ("No score" if not points else "Small sample" if len(points) < regional_view.SMALL_SCORED else "Scored"),
            "Large ABC–award gaps": sum(row["Difference (%)"] is not None for row in group["contracts"]),
            "Budget gap not checkable": sum(not regional_view.budget_gap_checkable(by_id[row["Contract"]])
                                            for row in group["contracts"]),
        })
    # Sort on the numbers (missing last), then show text: "No score" is never zero and no tint implies a verdict.
    ranking = pd.DataFrame(totals).sort_values(["Ranking score", "Total points", "Contractor"],
                                               ascending=[False, False, True], na_position="last")
    shown = ranking.assign(**{
        "Total points": ranking["Total points"].map(lambda v: language.score_text(v, signed=True)),
        "Ranking score": ranking["Ranking score"].map(lambda v: language.score_text(v, decimals=2)),
        "Points per scored contract": ranking["Points per scored contract"].map(lambda v: language.score_text(v, signed=True, decimals=2)),
    })
    st.dataframe(shown.style.map(lambda value: "font-weight:700" if value > 0 else "", subset=["Large ABC–award gaps"]),
                 hide_index=True, column_config={
        "Contractor": st.column_config.TextColumn("Contractor", width=260),
        "Total points": st.column_config.TextColumn("Total points", help="Sum of points for scored contracts. No score is not zero."),
        "Ranking score": st.column_config.TextColumn("Ranking score", help="Total points ÷ √(scored contracts)."),
        "Points per scored contract": st.column_config.TextColumn("Points per scored contract"),
        "Scored": st.column_config.NumberColumn("Scored", help="Contracts with points. The ranking score uses only these."),
        "No score": st.column_config.NumberColumn("No score", help="Included contracts without a score. Not counted as zero."),
        "Sample": st.column_config.TextColumn("Sample", help=f"Small sample = fewer than {regional_view.SMALL_SCORED} scored contracts."),
        "Large ABC–award gaps": st.column_config.NumberColumn(
            "Large ABC–award gaps", help=f"Contracts whose award differs from the approved budget by at least {BUDGET_FLAG_PERCENT}%."),
        "Budget gap not checkable": st.column_config.NumberColumn(
            "Budget gap not checkable", help="Contracts missing the approved budget or award amount. Unknown amounts are not read as zero."),
    })
    with st.container(border=True):
        selected = st.selectbox("Show contracts for", ranking.index.tolist(), format_func=lambda i: groups[i]["name"])
    rows = groups[selected]["contracts"]
    frame = pd.DataFrame(rows)
    # Display-only relabelling; scoring already ran on the raw values.
    frame["Level"] = frame["Level"].map(language.score_level_label)
    frame["Photo assessment"] = frame["Photo assessment"].map(lambda raw: language.category_label(raw) if raw else "No saved assessment")
    for column in ("Points", "Schedule points", "Photo timing points"):
        frame[column] = frame[column].map(lambda v: language.score_text(v, signed=True))
    for column in ("Reported completion", "Contract expiry", "Latest photo date"):
        frame[column] = frame[column].map(lambda v: language.format_timestamp(None if v is None or v != v else str(v)))
    for column in ("Days from expiry", "Photo days from reported completion"):
        frame[column] = frame[column].map(lambda v: "Date unknown" if v is None or v != v else f"{int(v):+d}")
    frame["Status"] = frame["Status"].fillna("Unavailable")
    frame = frame.rename(columns={"Status": "Reported status",
                                  "Photo days from reported completion": "Photo timing (days from reported completion)"})
    st.caption("Photo timing: days from reported completion to the latest reviewed photo, positive after and negative before. "
               + language.TIMING_NOTE)
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
        st.text(description)
    st.text(f"Contractor · {record.get('contractor') or 'Unavailable'}")
    if assessment.get("completion_evidence_category") is not None:
        automatic, note = automatic_category(record)
        evidence_overview(record, assessment, automatic, automatic_note=note)
    else:
        st.text(f"Reported status · {record.get('status') or 'Unavailable'}")
    show_flags(assessment.get("flags", []))
    dates, budgets = st.columns(2)
    for label, value in (record.get("dates") or {}).items():
        if value:
            dates.text(f"{label} · {language.format_timestamp(value)}")
    for label, value in (record.get("budgetPHP") or {}).items():
        # Unknown is not zero: every source amount is listed, unavailable ones included.
        budgets.text(f"{label} · {format_peso(value)}")
    st.link_button("Open DPWH contract", project_url(contract_id))
    photos = record.get("photos", [])
    latest_day = language.latest_capture_day(photos)
    for index, photo in enumerate(photos):
        if url := photo.get("url"):
            st.link_button(language.photo_caption(photos, index, latest_day), url)


def queue_deps():
    """Everything the background worker needs from this app, resolved at call time."""
    return queue_worker.Deps(
        open_db=open_assessment_db,
        fetch_projects=lambda filters: regional_summary(filters)[1],
        fetch_inputs=lambda contract: fetch_contract_review_inputs(contract),
        summarize=lambda contract, inputs: summarize_contract(contract, cache_counts=Counter(), save=False, inputs=inputs),
        refresh_staged=refresh_staged_record,
        save=lambda record: save_contract_assessments([record], require_saved=True),
        is_due=lambda record, reviewed_at, signature: review_due(record, reviewed_at, signature),
        is_stale=lambda record, signature: review_is_stale(record, signature),
        signature=current_model_signature,
    )


def refresh_staged_record(record):
    record["assessment"] = assess_with_manual_review(record)
    return record


def open_regional_results(filters):
    """Open this scope's saved results in Regional review (only offered when saved results exist)."""
    region = filters["region"]
    year = str(filters["year"]) if filters.get("year") else "All saved years"
    try:
        with closing(open_assessment_db()) as db:
            saved = {(r, str(y) if y is not None else None) for r, y in db.execute("""
                SELECT DISTINCT json_extract(q.filters, '$.region'), json_extract(q.filters, '$.year')
                FROM regional_queue AS q JOIN contract_assessments USING (contract_id)
                WHERE q.done = 1 AND CASE WHEN json_valid(q.filters) THEN 1 ELSE 0 END""")}
    except (OSError, sqlite3.Error):
        saved = set()
    # Preselect only a region and year the reader page actually lists; otherwise just open it.
    if any(r == region for r, _ in saved):
        st.session_state["dashboard_region"] = region
        if year == "All saved years" or (region, year) in saved:
            st.session_state[f"dashboard_year_{region}"] = year
    go_to("Regional review")


def show_regional_queue(filters):
    st.caption("Saves each contract’s rules-based assessment and YOLO findings. Evidence highlights are a separate stage: "
               "generate them in step 2 once reviews are saved; they never start a check.")
    scope = [("Region", filters["region"]), ("Fiscal year", filters.get("year") or "All years"),
             ("Category", filters.get("category") or "All categories"), ("Status", filters.get("status") or "All statuses")]
    if filters.get("search"):
        scope.append(("Search", filters["search"]))
    panel = queue_view.Panel(key=regional_filter_key(filters), noun="regional review", scope=scope, deps=queue_deps,
                             filters=filters, open_results=partial(open_regional_results, filters),
                             widget_prefix="regional_queue")
    show_queue_panel(panel)


@st.fragment(run_every=3)
def show_queue_panel(panel):
    try:
        queue_view.render_queue(panel)
    except (OSError, sqlite3.Error):
        error_state("The saved review queue is unavailable.", "The saved review database did not respond.",
                    kept="Saved reviews and queue progress are unchanged. This section checks again every few seconds.")


def show_category_reruns():
    st.caption("Recheck a saved category with the current YOLO checkpoints. Manual category decisions are preserved.")
    try:
        with closing(open_assessment_db()) as db:
            categories = dict(db.execute("SELECT category, COUNT(*) FROM contract_assessments GROUP BY category ORDER BY category"))
            for (key,) in db.execute("SELECT DISTINCT filters FROM regional_queue WHERE filters LIKE 'category-rerun:%'"):
                categories.setdefault(key.removeprefix("category-rerun:"), 0)
    except (OSError, sqlite3.Error):
        error_state("The saved review queue is unavailable.", "The saved review database did not respond.",
                    kept="Saved reviews and queue progress are unchanged. Reload the page to check again.")
        return
    previous = st.session_state.get("rerun_selected_category")
    options = sorted(categories)
    category = st.selectbox(
        "Saved review category", options, index=options.index(previous) if previous in options else None,
        key="rerun_category", on_change=remember_rerun_category, format_func=language.category_label,
    )
    if category is None:
        return
    key = f"category-rerun:{category}"
    panel = queue_view.Panel(
        key=key, noun="category rerun", scope=[("Saved category", language.category_label(category)),
                                              ("Contracts now in this category", f"{categories[category]:,}")],
        deps=queue_deps, prepare=partial(seed_category_rerun, key, category),
        open_results=partial(go_to, "Admin", admin_section="Reviewed contracts"),
        start_label="Rerun category", resume_label="Resume rerun", check_label="Rerun category", widget_prefix="category_rerun",
        disabled_reason=None if categories[category] else "No saved contracts are in this category, so there is nothing to rerun.")
    show_queue_panel(panel)


def seed_category_rerun(key, category):
    """Start a new rerun: a fresh queue of the contracts now in the category. Earlier reviews stay until replaced."""
    with closing(open_assessment_db()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM regional_queue WHERE filters = ?", (key,))
        db.execute("DELETE FROM queue_failures WHERE filters = ?", (key,))
        db.execute("""
            INSERT INTO regional_queue(filters, position, contract_id)
            SELECT ?, ROW_NUMBER() OVER (ORDER BY contract_id) - 1, contract_id
            FROM contract_assessments WHERE category = ?
        """, (key, category))


def remember_rerun_category():
    st.session_state["rerun_selected_category"] = st.session_state["rerun_category"]


def run_regional_queue(filters, key, total, done, job):
    """Run a queue to the end of its work in this thread (the dashboard starts the same worker in a background thread)."""
    worker = queue_worker.Job(key, stop=job["stop"])
    queue_worker.run(worker, queue_deps(), filters, fetch=filters is not None and (not total or done == total))
    if worker.error:
        job["error"] = worker.error


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
        if st.button("Analyze contract evidence", type="primary", disabled=not targets, key="analyze_contract_evidence"):
            results = {}
            for contract_id in targets:
                with loading(f"Analyzing evidence for {contract_id} · fetching records, retrieving photos, checking detectors"):
                    try:
                        summary = summarize_contract(contract_id)
                    except (requests.RequestException, KeyError, ValueError):
                        results[contract_id] = "BetterGov could not load contract details. Try again."
                        continue
                    try:
                        report = review_project(summary, model)
                        results[contract_id] = (
                            report, summary["photos"], summary["dates"]["Reported completion"], summary["assessment"],
                            summary.get("status"),
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
                error_state("Analysis unavailable for this contract.", escape_markdown(result),
                            kept="Any previously saved review is unchanged. Select Analyze contract evidence to retry.")
                continue
            if result is None:
                error_state("Review unavailable.", "Check the API connections and try again.",
                            kept="Any previously saved review is unchanged.")
                continue
            report, photos, *dates = result
            completion = pd.to_datetime(dates[0] if dates else None, errors="coerce")
            assessment = dates[1] if len(dates) > 1 else None
            if assessment:
                status = dates[2] if len(dates) > 2 else assessment.get("reported_status_summary")
                summary = {"status": status, "dates": {"Reported completion": dates[0] if dates else None},
                           "photos": photos, "budgetPHP": {}, "assessment": assessment}
                automatic, note = automatic_category(summary)
                evidence_overview(summary, assessment, automatic, automatic_note=note)
                if reason := assessment.get("completion_evidence_reason"):
                    st.caption(reason)
                show_flags(assessment["flags"])
            else:
                st.markdown(f"**{escape_markdown(report['conclusion'])}**")
            st.text(language.relabel_text(report["summary"]))
            st.caption(f"Photo sample reviewed · {len(photos)}")
            if assessment and (number := assessment.get("latest_evidence_photo")):
                photo = photos[number - 1]
                st.link_button(f"Latest photo {number} · {language.format_timestamp(photo.get('capturedAt'))}", photo["url"])
                st.caption(language.photo_timing_text(assessment.get("latest_photo_days_from_reported_completion")))
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
                        notice("Review possible unfinished work.",
                               f"Photo dated {captured.date()}; reported completion {completion.date()}. "
                               "Photo timing is not a measured delay.", icon=":material/visibility:")
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
        if st.button("Build contract summaries", type="primary", disabled=not targets, key="build_contract_summaries"):
            summaries, failures = [], []
            for contract_id in targets:
                with loading(f"Fetching records and retrieving photos for {contract_id}"):
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
            error_state(f"Contract details unavailable for {contract_id}.", "The source did not respond.",
                        kept="Other summaries are shown. Select Build contract summaries to retry.")
        for summary in summaries:
            st.subheader(summary["contractId"])
            st.text(summary["description"] or "Description unavailable")
            st.text(f"Contractor · {summary['contractor'] or 'Unavailable'}")
            for label, value in summary["dates"].items():
                st.text(f"{label} · {format_date(pd.to_datetime(value, errors='coerce'))}")
            for label, value in summary["budgetPHP"].items():
                st.text(f"{label} · {format_peso(value)}")
            total = summary["galleryPhotoCount"]
            st.caption(summary["photoCoverage"])
            assessment = summary.get("assessment") or assess_contract(summary)
            if not assessment.get("completion_evidence_category"):
                assessment = {**assessment, "completion_evidence_category": assess_contract(summary)["completion_evidence_category"]}
            automatic, automatic_note = automatic_category({**summary, "assessment": assessment})
            evidence_overview(summary, assessment, automatic, automatic_note=automatic_note)
            st.text(f"Review outcome · {language.outcome_label(assessment['completion_assessment'])}")
            if reason := assessment.get("completion_evidence_reason"):
                st.caption(reason)
            show_flags(assessment["flags"])
            with st.expander(f"{summary['contractId']} assessment details"):
                st.json(assessment)
            st.caption(f"Photos reviewed · {len(summary['photos'])} of {total if total is not None else 'unknown'}")
            latest_day = language.latest_capture_day(summary["photos"])
            for index, photo in enumerate(summary["photos"]):
                st.link_button(language.photo_caption(summary["photos"], index, latest_day), photo["url"])
                if photo["scope"]:
                    st.text(photo["scope"])
                for observation in photo["yolo"]:
                    label, use = language.eligibility(observation.get("quality"))
                    st.text(f"{language.finding_text(observation)} · {label}", help=use)
                if any(o.get("status") == "detected" for o in photo["yolo"]):
                    st.caption(language.MODEL_SCORE_NOTE)
                if not photo["yolo"]:
                    st.caption("No detector configured for this photo category.")
        if st.button("Compare summaries", disabled=len(summaries) < 2 or bool(failures), key="compare_summaries"):
            st.session_state.pop("contract_comparison_v4", None)
            try:
                with loading("Comparing contract evidence"):
                    st.session_state["contract_comparison_v4"] = (targets, compare_contracts(summaries, model))
            except ReviewError as error:
                error_state("Contract comparison unavailable.", escape_markdown(str(error)),
                            kept="The summaries above are unchanged.")
            except (requests.RequestException, ValueError):
                error_state("Contract comparison unavailable.", "Check the source records and retry.",
                            kept="The summaries above are unchanged.")
        comparison = st.session_state.get("contract_comparison_v4")
        if comparison and comparison[0] == targets:
            report = comparison[1]
            st.subheader("Combined conclusion")
            st.text(report["conclusion"])
            for finding in report["findings"]:
                st.text(language.relabel_text(finding))
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
        with column, loading("Analyzing photo"):
            description, overlay = analyze_project_photo(url, *weights)
        if description.startswith("YOLO detected "):
            column.text(plain_photo_finding(description, target))
            label, use = language.eligibility(model_quality(detector, Path(weights[1])))
            column.caption(f"{detector} · {label}. {use}")
        if overlay:
            with column.expander(f"View overlay for {target.removeprefix('visible ')}"):
                st.image(overlay, caption="Detector observation overlay; not a verification mark.")
        return description
    elif detector not in supported:
        column.caption("No detector is configured for this infrastructure category.")
    elif detector not in available:
        with column:
            show_state("detector_missing", f"{detector}: {YOLO_MODELS[detector][2]} could not be assessed.")


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
        error_state("Photos could not be retrieved.", "The BetterGov API did not return this contract's gallery.",
                    kept="The contract record and saved review above are unaffected.",
                    retry_label="Retry loading photos", on_retry=refresh_live_results, key=f"retry_gallery_{contract}")
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
        if pagination["totalCount"] == 0:
            show_state("no_photos")
        else:
            show_state("retrieval_failed", "The gallery reports photos, but none were returned for this page.")
        return
    st.caption("Live detector observations on source photos. They are not saved and do not change the saved "
               "photo evidence assessment above.")
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
                notice("Photo analysis is not installed.",
                       "Install the dashboard's optional YOLO dependency to enable detector observations.")
                break
            except (OSError, RuntimeError, ValueError):
                error_state(f"The YOLO model for {kind} could not be loaded.", "Its checkpoint could not be read.",
                            kept="Photos are still shown without this detector's observations.")
    gallery_latest = reported_day(date_range.get("latest")) or language.latest_capture_day(photos)
    for start in range(0, len(routed_photos), 3):
        for offset, (column, (photo, component, photo_category, detectors)) in enumerate(
                zip(st.columns(3), routed_photos[start:start + 3])):
            index = start + offset
            role = language.photo_role([p for p, *_ in routed_photos], index, gallery_latest)
            role = {"latest date": "latest gallery date", "historical": "earlier date"}.get(role, role)
            caption = [f"Photo {index + 1}", language.format_timestamp(photo.get("capturedAt")), role,
                       photo.get("componentId"), photo.get("purpose")]
            with column:
                photo_with_fallback(photo["url"], f"Project photo {index + 1}, {role}",
                                    " · ".join(str(part) for part in caption if part))
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
                with column:
                    show_state("no_detection" if any(f.startswith("YOLO found no ") for f in findings) else "analysis_failed")
            column.link_button("Open original", photo["url"])


def show_contract(contract, row):
    st.subheader(contract)
    if pd.notna(row["description"]):
        st.text(row["description"])
    contractor = row.get("contractor")
    st.text(f"Contractor · {contractor or 'Unavailable'}")
    details = st.columns(3)
    details[0].metric("Reported status", row["status"] if pd.notna(row["status"]) else "Unavailable",
                      help="As published in the DPWH record. Not a photo assessment.")
    details[1].metric("Reported completion", format_date(row["completionDate"]))
    details[2].metric("Latest photo date", format_date(row["evidenceDate"]))
    source = {"dpwh_metadata": "DPWH image metadata", "supplied_photos": "Supplied photo dates"}
    st.caption(f"Photo date source · {source.get(row['evidenceSource'], 'Unavailable')}")
    notes = [label for key, label in FLAGS.items() if row[key]]
    if notes:
        notice("Record notes for review · not findings", " · ".join(notes), icon=":material/flag:")
    days = row["daysFromCompletion"]
    if pd.notna(days) and days:
        st.caption(f"{language.photo_timing_text(int(days))}. {language.TIMING_NOTE}")


def format_date(value):
    return value.strftime("%d %b %Y") if pd.notna(value) else "Date unknown"


if __name__ == "__main__":
    main()
