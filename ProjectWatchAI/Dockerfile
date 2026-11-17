FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt requirements-yolo.txt ./
RUN pip install --no-cache-dir -r requirements.txt
ARG ENABLE_YOLO=true
RUN if [ "$ENABLE_YOLO" = "true" ]; then \
      apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && \
      rm -rf /var/lib/apt/lists/* && \
      pip install --no-cache-dir torch==2.11.0 torchvision==0.26.0 --index-url https://download.pytorch.org/whl/cpu && \
      pip install --no-cache-dir -r requirements-yolo.txt; \
    fi
COPY app.py ui.py evidence_language.py model_readiness.py queue_view.py queue_worker.py regional_view.py review_evidence.py review_store.py review_workspace.py training_correction.py audit_image_evidence.py contract_assessment.py evidence_policy.py photo_outline.py project_review.py road_surface.py run.py yolo_feedback.py ./
COPY audited_checkpoints.json ./
COPY .streamlit/config.toml .streamlit/config.toml
COPY static ./static

EXPOSE 8501
HEALTHCHECK CMD ["python", "-c", "from urllib.request import urlopen; urlopen('http://127.0.0.1:8501/_stcore/health', timeout=5).close()"]
CMD ["python", "-m", "streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.headless=true", "--browser.gatherUsageStats=false"]
