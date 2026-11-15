"""Test a ProjectWatch YOLO checkpoint on an uploaded image."""

from hashlib import sha256
from evidence_policy import INFERENCE_VERSION, checkpoint_digest, model_quality
from csv import DictReader
from importlib import reload
from io import BytesIO
from subprocess import TimeoutExpired

from PIL import Image, ImageOps
import streamlit as st

import app

# Streamlit can rerun the tester while retaining an older dashboard module.
if not hasattr(app, "UNDER_CONSTRUCTION_LABELS"):
    reload(app)

from app import BUILDING_STAGE_WEIGHTS, UNDER_CONSTRUCTION_LABELS, YOLO_MODELS, load_photo_model, photo_checkpoint_options
from photo_outline import outline_photo
from road_surface import MAX_PHOTO_BYTES
from yolo_feedback import UNDER_CONSTRUCTION_TRAINING, feedback_folder, has_training_dataset, latest_candidate, read_correction, save_correction, train_candidate


@st.fragment(run_every=3)
def training_progress():
    runs = {}
    for detector in YOLO_MODELS:
        logs = list(feedback_folder(detector).glob("training-*/training.log"))
        if logs:
            runs[detector] = max(logs, key=lambda path: path.stat().st_mtime)
    if not runs:
        st.info("Training progress appears once a candidate run starts.")
        return
    selected = st.selectbox("Training run", sorted(runs, key=lambda name: runs[name].stat().st_mtime, reverse=True))
    log = runs[selected]
    run = log.parent
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    finished = (run / "ready").is_file()
    results = run / "runs/corrected/results.csv"
    epochs = 0
    if results.is_file():
        with results.open(encoding="utf-8", newline="") as file:
            epochs = sum(row.get("epoch", "").strip().isdigit() for row in DictReader(file, skipinitialspace=True))
    failed = any("Traceback (most recent call last)" in line for line in lines)
    if finished:
        status = "Candidate ready"
    elif failed:
        status = "Training error"
    elif epochs >= 10:
        status = "Training epochs recorded · validation pending"
    else:
        status = "Candidate not ready"
    st.progress(min(epochs / 10, 1.0), text=f"{status} · {epochs} of 10 epochs recorded")
    for line in lines:
        if line.startswith("Held-out mask mAP50:"):
            st.caption(line)
    with st.expander("Latest log output"):
        st.code("\n".join(lines[-12:]) or "Waiting for training output.", language=None)


def main():
    st.set_page_config(page_title="BantAIProyekto | Model tester", layout="wide")
    from ui import apply_theme
    apply_theme()
    st.title("BantAIProyekto")
    st.caption("Model tester · inspect detections and save corrections")
    default_view = 1 if st.query_params.get("view") == "training" else 0
    if st.radio("Tester view", ["Test and train", "Training progress"], index=default_view, horizontal=True) == "Training progress":
        training_progress()
        return
    detector = st.selectbox("Detector", list(YOLO_MODELS))
    weights, classes, target, image_size = YOLO_MODELS[detector]
    seed_weights = weights
    if detector == UNDER_CONSTRUCTION_TRAINING:
        classes = UNDER_CONSTRUCTION_LABELS
        seed_weights = BUILDING_STAGE_WEIGHTS
    st.caption(f"Detection target · {target}")
    with st.expander("Train with saved corrections"):
        st.caption("Fine-tune a separate checkpoint with your corrections and the existing training photos.")
        can_train = has_training_dataset(detector)
        if not can_train:
            st.caption("Fine-tuning requires a labeled training and evaluation dataset for this detector.")
        device = st.selectbox("Training device", ["GPU", "CPU"])
        if st.button("Train a candidate", disabled=not seed_weights.is_file() or not can_train):
            try:
                with st.spinner("Fine-tuning YOLO. This can take several minutes."):
                    train_candidate(detector, seed_weights, classes, image_size, "0" if device == "GPU" else "cpu")
                st.success("Candidate trained. Select it below to test it on a different photo.")
            except (OSError, ValueError, TimeoutExpired) as error:
                st.error(str(error))
    candidate = latest_candidate(detector)
    checkpoints = photo_checkpoint_options(detector)
    weights = checkpoints["Current model"]
    if candidate:
        st.caption(f"Training log · {candidate[1]}")
    if len(checkpoints) > 1:
        selected = st.radio("Checkpoint", list(checkpoints), horizontal=True, key=f"checkpoint_{detector}")
        weights = checkpoints[selected]
    uploaded = st.file_uploader("Upload a road or infrastructure photo", type=["jpg", "jpeg", "png"])
    checkpoint_available = weights.is_file()
    if checkpoint_available:
        st.caption(f"Decision eligibility · {model_quality(detector, weights)}")
    if not checkpoint_available:
        st.info("You can save labels now and run detection once a checkpoint is available.")
    if uploaded is None:
        return
    if uploaded.size > MAX_PHOTO_BYTES:
        st.error("Choose a photo smaller than 12 MB.")
        return
    try:
        raw = uploaded.getvalue()
        digest = sha256(raw).hexdigest()
        with Image.open(BytesIO(raw)) as source:
            if source.width * source.height > 25_000_000:
                st.error("Choose a photo with at most 25 megapixels.")
                return
            photo = ImageOps.exif_transpose(source).convert("RGB")
        photo.thumbnail((960, 960))
        key = (detector, digest, str(weights), (INFERENCE_VERSION, checkpoint_digest(weights)) if checkpoint_available else None)
        if st.button("Run YOLO", type="primary", disabled=not checkpoint_available):
            with st.spinner("Running YOLO"):
                model = load_photo_model(detector, str(weights.resolve()), (INFERENCE_VERSION, checkpoint_digest(weights)))
                st.session_state["prediction"] = (key, model.describe_image(photo))
    except (Image.DecompressionBombError, ImportError, OSError, RuntimeError, ValueError) as error:
        st.error(f"YOLO could not analyze this photo. {error}")
        return
    saved = read_correction(detector, digest)
    if saved and saved["label"] and detector == UNDER_CONSTRUCTION_TRAINING:
        saved = {**saved, "label": next(iter(classes))}
    if saved:
        st.success(f"Your correction · {classes[saved['label']] if saved['label'] else 'No target visible'}")
    original, result = st.columns(2)
    original.image(photo, caption="Original photo")
    prediction = st.session_state.get("prediction")
    if prediction and prediction[0] == key:
        description, overlay = prediction[1]
        result.write(description)
        if overlay:
            result.image(overlay, caption="YOLO prediction")
    with st.expander("Correct YOLO"):
        labels = [None, *classes]
        current = saved["label"] if saved else None
        label = st.selectbox("Correct label", labels, index=labels.index(current),
                             format_func=lambda value: classes[value] if value else "No target visible",
                             key=f"label_{detector}_{digest}")
        points = []
        append = False
        if label:
            st.caption("Click around the feature in order, then select Use outline.")
            polygons = saved["polygons"] if saved and saved["label"] == label else []
            if detector == UNDER_CONSTRUCTION_TRAINING and polygons:
                append = st.checkbox("Add another outline", value=True, key=f"append_outline_{digest}")
            polygon = polygons[-1] if polygons and not append else []
            initial = [polygon[index:index + 2] for index in range(0, len(polygon), 2)]
            outline_key = f"outline_{detector}_{digest}_{len(polygons) if append else 0}"
            state = st.session_state.get(outline_key)
            draft = state.get("points", initial) if isinstance(state, dict) else getattr(state, "points", initial)
            points = outline_photo(photo, outline_key, draft)
        source_contract = st.text_input("Source contract/project ID (required for training)", value=(saved or {}).get("contractId", ""), key=f"source_contract_{digest}")
        if st.button("Save correction"):
            try:
                save_correction(detector, digest, photo, label, points, append=append, contract_id=source_contract.strip() or None)
                st.rerun()
            except (OSError, ValueError) as error:
                st.error(str(error))


if __name__ == "__main__":
    main()
