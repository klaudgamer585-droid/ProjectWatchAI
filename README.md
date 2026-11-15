# BantAIProyekto

BantAIProyekto reads DPWH records from BetterGov, inspects dated project photographs with local YOLO models, and organizes contract evidence for review. Ollama selects highlights from verified text facts for regional overviews. It does not decide whether work was completed.

## Run locally on Windows

From the repository root:

```powershell
cd ProjectWatchAI
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-yolo.txt
python -m streamlit run app.py
```

For a dashboard without local YOLO dependencies, install `requirements.txt` instead. Missing detectors remain explicit; they are not interpreted as negative evidence. The pinned dependencies reflect the tested local environment. Validate the Linux image separately before deployment.

Run the standalone tester with:

```powershell
python -m streamlit run yolo_test_app.py --server.port 8502
```

Restart both Streamlit processes after updating code or dependencies. Open **Admin → Model status** in the dashboard to see available checkpoints and decision eligibility. The tester and dashboard use the same active checkpoint resolver and inference implementation.

## Evidence policy

- Only the newest dated photo group can determine the current evidence category. Older photos remain historical context.
- One qualifying scope detection within that date group supports visible scope. Conflicting construction cues remain explicit review flags. Scores from different models are never compared as probabilities of completion.
- Roads use 35%, buildings 24%, and bridges 50% as scope thresholds. Both paved and unpaved roads are generic road evidence. Slope protection is accepted at 50% for relevant road scope **only when the detector has passed its release gate**.
- Weak or unvalidated detectors are **review_only**. Their observations remain visible but cannot independently assign completion or unfinished categories. Roads, buildings, and stainless tanks currently provide **provisional visible-scope evidence**, not certified completion decisions.
- Date buckets describe photo capture timing after the reported completion date. They are not measured construction delays.
- Missing photos, failed analysis, and successfully analyzed but inconclusive photos are distinguished. Missing checkpoints are displayed in Model status.
- Budget flags require an increase of at least 10% above a positive approved contract budget. Unknown amounts remain unknown. Differences do not establish actual spending or wrongdoing; verify comparable scope.
- Manual decisions are preserved, with reviewer/evidence provenance and warnings when underlying evidence changes. Set `PROJECTWATCH_REVIEWER` to the reviewer name used for new manual decisions. This field is attribution, not authentication.

A detected building, road, riverbank, or tank cannot establish complete quantities, specification compliance, interior work, financial correctness, or the actual completion date. Automatic categories therefore say **Visible scope evidence** rather than verified completion.

## Saved reviews, caching, and rechecks

SQLite defaults to `ProjectWatchAI/data/reviewed_photos.sqlite3`; override with `PROJECTWATCH_PHOTO_REVIEW_DB`. Before overwriting a review, the database preserves its old revision. The review page exposes recent revision metadata and stale status. **Rerun review** is available for every category.

A regional scan's **Check new and due contracts** action requeues stale reviews immediately, ongoing/no-photo/retry-required records after one day, and other completed records after seven days. This happens when a scan is started, not through an unattended scheduler. Pause/resume retains per-contract queue progress. A source outage preserves prior evidence until a successful rerun.

Each review re-fetches photo bytes once per selected photo to detect replacement at an unchanged URL. YOLO inference is reused by content hash, checkpoint digest, and inference configuration. Photos are kept in memory for this check, not added to the training dataset automatically. New observations retain raw confidence, boxes, masks, image/model hashes, and inference settings. Old prose observations are readable but marked stale and require a live rerun.

To migrate old saved rules, after backing up your data:

```powershell
python migrate_review_policy.py
```

The migration also makes an SQLite backup automatically and retains prior review revisions. It recalculates rules over saved evidence; it does not claim to rerun YOLO. Manual overrides are preserved.

## Ollama

Run an Ollama server with the configured model installed. Defaults are `OLLAMA_URL=http://127.0.0.1:11434` and `OLLAMA_MODEL=qwen3:4b`.

Individual contract summaries, dates, budgets, and comparisons are deterministic. Regional overview counts include every supplied contract. Ollama returns only IDs of facts worth highlighting, constrained by a validated schema; the displayed text comes from those exact facts. This prevents generated accusations, invented photo references, and changes to completion categories. Successful batches persist in SQLite and are reused after an interruption. Set `OLLAMA_MODEL_REVISION` when replacing a model under the same name to invalidate saved highlight selections.

## Feedback, evaluation, and checkpoint promotion

In the tester or review workspace, label the actual visible feature and outline it. Supply a source contract/project ID for training eligibility. Unknown-provenance uploads can be saved but are excluded from training until identified. Contract IDs and duplicate image pixels are checked against held-out data. Dates after reported completion are **not** automatic completion labels.

**Train a candidate** leaves the current checkpoint in place and evaluates the candidate. A `ready` marker means the candidate is testable, not approved. Use:

```powershell
python evaluate_checkpoint.py --detector "Roads" --checkpoint <candidate-best.pt> --job <training-folder>
```

Only add `--annotations-reviewed` after independently reviewing the held-out annotations for the current label definition. The conservative release gate requires at least 10 held-out contracts, 20 positive and 20 negative images, precision >= 0.90, recall >= 0.80, mask mAP50 >= 0.50, reviewed annotations, and split isolation. These are engineering screening requirements, not completion accuracy guarantees.

```powershell
python promote_checkpoint.py "Roads" <candidate-best.pt>
```

Promotion checks the evaluation against the candidate hash, retains the previous active checkpoint under `rollback`, and writes hash-bound approval metadata. Existing checkpoints remain available for testing regardless of release status. Category-specific unfinished road/bridge/flood/water checkpoints are currently missing; the dashboard does not pretend they have been trained. Combined construction and auxiliary models provide experimental observations until adequately validated.

## Tests

```powershell
python -B -m unittest test_audit_regressions -v
```

The suite uses disposable databases and mocked network calls. It covers latest-date policy, same-date conflicts, experimental-model restrictions, raw thresholds, timezone normalization, budget boundaries, output validation, persistent Ollama batches, content-based cache reuse, review history, and requeueing existing contracts. Live API, Ollama, UI, and deployment checks are additional integration checks, not covered by mocked tests.

## Huawei ECS / Docker inference deployment

The Docker image includes YOLO by default and runs inference on CPU with two PyTorch threads. Training remains a local workflow; deploy selected checkpoints and approval files, not all candidate datasets or training runs. Preserve their directory layout under `data/` so the registry can find them. Persist reviews, corrections, manual evidence, and Ollama batch caches on writable storage.

The included `compose.yaml` runs the dashboard and Ollama as separate services. Set `OLLAMA_IMAGE` to the tested image tag or digest for your environment, then:

```sh
docker compose build
docker compose up -d
docker compose exec ollama ollama pull qwen3:4b
curl --fail http://127.0.0.1:8501/_stcore/health
```

The dashboard uses `http://ollama:11434` inside the network; container localhost would be incorrect. Set `OLLAMA_MODEL` if using another installed model. The example binds Streamlit to loopback; place it behind your authenticated HTTPS reverse proxy before public access. It is a single-reviewer application, not a multi-tenant service.

Use Huawei ECS compute and persistent disk for the services. OBS may store curated evidence separately, but is not required by this implementation. Cloud deployment and resource sizing still need an end-to-end test on the chosen ECS instance.
