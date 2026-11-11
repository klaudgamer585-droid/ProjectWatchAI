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
