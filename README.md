# ProjectWatchAI

ProjectWatchAI filters Philippine Department of Public Works and Highways project records to Region III and flags gaps in photo evidence for review.

From the repository root in PowerShell:

```powershell
cd ProjectWatchAI
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python run.py
python filter_region.py
```

For the image evidence audit, download [the detailed DPWH Parquet file](https://huggingface.co/datasets/bettergovph/dpwh-transparency-data/blob/main/dpwh_transparency_data_all_details.parquet) into the project's `data` folder as `dpwh_details.parquet`. Then run:

```powershell
python audit_image_evidence.py --contract-id 20CN0142
```

The audit writes `data/region_iii_image_audit.parquet`. It flags completed contracts whose latest image date differs from the recorded completion date and contracts with no images reported or supplied. A date mismatch is a cue for review.

To audit local photos, pass `--photos photos.json` with a JSON array containing `contractId`, `imagePath` (relative to the manifest or absolute), and `captureDate` (YYYY-MM-DD) for each photo. Supplied dates take precedence for matching contracts.

## Website

From the `ProjectWatchAI` subdirectory, prepare the two DPWH snapshots and launch the dashboard.

```powershell
python prepare_data.py
python -m streamlit run app.py
```

Search for `20CE0010` to see the latest image metadata date 16 days after the reported completion. The dashboard links to the original DPWH gallery. Progress assessment needs comparable photos and a schedule benchmark.

## Huawei Cloud deployment

Use a Linux Elastic Cloud Server (ECS) with Docker installed and an Elastic IP attached. Transfer or clone the private repository onto the server using authorized access. From the `ProjectWatchAI` subdirectory on ECS, build the image and prepare a named volume containing the public DPWH data.

```sh
docker build -t projectwatchai .
docker volume create projectwatch-data
docker run --rm -v projectwatch-data:/app/data projectwatchai python prepare_data.py
docker run -d --name projectwatchai --restart unless-stopped \
  -p 80:8501 -v projectwatch-data:/app/data:ro projectwatchai
curl --fail http://127.0.0.1/_stcore/health
```

Allow inbound TCP port 80 from the intended demo audience in the ECS security group and visit the ECS Elastic IP over HTTP. Restrict SSH access to the administrator's IP. For a domain with HTTPS, use a reverse proxy that supports WebSockets, bind the container to loopback, and expose port 443 through the security group.

ECS runs the dashboard and the batch data preparation. The named volume keeps Parquet files across container restarts. Huawei Cloud OBS can store curated photo evidence when it is collected, and ModelArts can serve a vision model once photo pairs are available. The current dashboard uses DPWH metadata and links to the original gallery.
