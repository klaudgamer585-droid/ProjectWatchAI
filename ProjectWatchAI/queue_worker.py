"""Background review queues: regional reviews and category reruns. No Streamlit.

One queue = one `regional_queue.filters` key. A run walks the queue's pending contracts, contract by contract,
through four real stages (retrieving photos, analysing, saving) after an optional fetch of the region's records.
What the page shows is what is stored here:

* ``regional_queue``  — one row per contract; ``done`` = 1 once its review is saved. Completed rows are never
  reset by pausing, interruption or a failure elsewhere in the queue.
* ``queue_failures``  — contracts whose review failed in this queue (retry required). They are skipped by the run
  so one bad contract cannot block the rest, and they keep any earlier saved review.
* ``queue_runs``      — the last run's state (running, pause_requested, paused, finished, stopped), stage and
  heartbeat, so a page opened later (or after a restart) can tell *Paused* from *Interrupted*.

App-specific work (HTTP, detectors, saving) is injected through ``Deps`` so the worker is testable with fixtures.
"""
import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Event, Lock, Thread
from typing import Callable, Optional

PHILIPPINE_TIME = timezone(timedelta(hours=8), "PHT")
ANALYSIS_UNAVAILABLE = "Photo analysis unavailable · retry needed"
BATCH = 4
FAILURE_LIMIT = 5            # consecutive contract failures before a run stops: the source or a model is likely down
HEARTBEAT_SECONDS = 30       # a run owned by another process is "running elsewhere" only while its heartbeat is this fresh

RUNNING, PAUSE_REQUESTED, PAUSED, FINISHED, STOPPED, INTERRUPTED, ELSEWHERE, IDLE = (
    "running", "pause_requested", "paused", "finished", "stopped", "interrupted", "running_elsewhere", "idle")
FETCHING, PHOTOS, ANALYSING, SAVING = ("Fetching regional records", "Retrieving photos", "Analysing photos", "Saving review")

REGISTRY: dict = {}          # key -> Job, shared by every session of this process (module state, not per-session)
LOCK = Lock()


@dataclass
class Deps:
    open_db: Callable[[], sqlite3.Connection]
    fetch_projects: Callable[[dict], dict]            # filters -> {contract_id: row}
    fetch_inputs: Callable[[str], tuple]              # contract -> inputs (inputs[1]["galleryPhotoCount"] must be known)
    summarize: Callable[[str, tuple], dict]           # (contract, inputs) -> record, not yet saved
    refresh_staged: Callable[[dict], dict]            # a staged record -> record re-assessed with manual decisions kept
    save: Callable[[dict], None]                      # persists one record; raises if it could not be saved
    is_due: Callable[[dict, str, object], bool]       # (record, reviewed_at, signature)
    is_stale: Callable[[dict, object], bool]          # (record, signature)
    signature: Callable[[], object]


class StopRun(Exception):
    """The run cannot continue (fetch failed, or repeated failures). Remaining work stays pending."""


class Job:
    def __init__(self, key, stop=None):
        self.key = key
        self.stop = stop or Event()
        self.thread: Optional[Thread] = None
        self.error: Optional[str] = None
        self.stage: Optional[str] = None
        self.current: Optional[str] = None
        self.consecutive = 0
        self.only: Optional[set] = None      # a scoped run (Retry) touches only these contracts
        self.owner = os.getpid()

    @property
    def alive(self):
        return bool(self.thread and self.thread.is_alive())


def now():
    return datetime.now(PHILIPPINE_TIME).isoformat(timespec="seconds")


def ensure_schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS queue_failures (
        filters TEXT NOT NULL, contract_id TEXT NOT NULL, message TEXT NOT NULL, failed_at TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 1, retry INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (filters, contract_id))""")
    if "retry" not in {row[1] for row in db.execute("PRAGMA table_info(queue_failures)")}:
        db.execute("ALTER TABLE queue_failures ADD COLUMN retry INTEGER NOT NULL DEFAULT 0")   # tables made before retries kept attempts
    db.execute("""CREATE TABLE IF NOT EXISTS queue_runs (
        filters TEXT PRIMARY KEY, state TEXT NOT NULL, stage TEXT, current_contract TEXT, error TEXT,
        owner INTEGER, started_at TEXT, updated_at TEXT NOT NULL)""")


# --- stored run state --------------------------------------------------------------------------

def _write_run(deps, job, state, *, error=None, new=False, only_if=None):
    with closing(deps.open_db()) as db, db:
        if only_if:
            db.execute("UPDATE queue_runs SET state=?, updated_at=? WHERE filters=? AND state=?", (state, now(), job.key, only_if))
        elif new:
            db.execute("INSERT OR REPLACE INTO queue_runs VALUES (?,?,?,?,?,?,?,?)",
                       (job.key, state, job.stage, job.current, None, job.owner, now(), now()))
        else:
            db.execute("UPDATE queue_runs SET state=?, stage=?, current_contract=?, error=?, updated_at=? WHERE filters=?",
                       (state, job.stage, job.current, error, now(), job.key))


def _stage(deps, job, stage, current=None):
    job.stage, job.current = stage, current
    state = PAUSE_REQUESTED if job.stop.is_set() else RUNNING
    _write_run(deps, job, state)


def stored_run(db, key):
    row = db.execute("SELECT state, stage, current_contract, error, owner, started_at, updated_at FROM queue_runs WHERE filters=?",
                     (key,)).fetchone()
    if not row:
        return None
    return dict(zip(("state", "stage", "current", "error", "owner", "started_at", "updated_at"), row))


def run_status(db, key, *, job=None):
    """What is actually happening to this queue, from the live worker and the stored run, never from a button click.

    * Pause requested: a live worker has been told to stop and is finishing its current contract.
    * Paused: the worker has stopped on request; saved work is intact and Resume continues.
    * Interrupted: the stored run says it was working but no worker exists (restart or crash).
    """
    job = job if job is not None else REGISTRY.get(key)
    stored = stored_run(db, key) or {}
    if job is not None and job.alive:
        state = PAUSE_REQUESTED if job.stop.is_set() else RUNNING
        return {**stored, "state": state, "stage": job.stage or stored.get("stage"), "current": job.current}
    state = stored.get("state")
    if state in (RUNNING, PAUSE_REQUESTED):
        try:
            age = (datetime.now(PHILIPPINE_TIME) - datetime.fromisoformat(stored["updated_at"])).total_seconds()
        except (KeyError, ValueError, TypeError):
            age = HEARTBEAT_SECONDS + 1
        if stored.get("owner") not in (None, os.getpid()) and age <= HEARTBEAT_SECONDS:
            return {**stored, "state": ELSEWHERE}
        return {**stored, "state": INTERRUPTED}
    return {**stored, "state": state or IDLE}


# --- counts ------------------------------------------------------------------------------------

def counts(db, key):
    """Saved / failed / retry-required / pending, plus what was preserved. Each contract is in exactly one of the four."""
    total, done = db.execute("SELECT COUNT(*), COALESCE(SUM(done), 0) FROM regional_queue WHERE filters=?", (key,)).fetchone()
    failed = db.execute("""SELECT COUNT(*) FROM regional_queue q JOIN queue_failures f
                           ON f.filters = q.filters AND f.contract_id = q.contract_id AND f.retry = 0
                           WHERE q.filters = ? AND q.done = 0""", (key,)).fetchone()[0]
    retry = db.execute(f"""SELECT COUNT(*) FROM regional_queue q JOIN contract_assessments a ON a.contract_id = q.contract_id
                           WHERE q.filters = ? AND q.done = 1 AND {_RETRY_SQL}""", (key,)).fetchone()[0]
    kept = db.execute("""SELECT COUNT(*) FROM regional_queue q JOIN queue_failures f
                         ON f.filters = q.filters AND f.contract_id = q.contract_id AND f.retry = 0
                         JOIN contract_assessments a ON a.contract_id = q.contract_id
                         WHERE q.filters = ? AND q.done = 0""", (key,)).fetchone()[0]
    manual = db.execute("""SELECT COUNT(*) FROM regional_queue q WHERE q.filters = ?
                           AND (q.contract_id IN (SELECT contract_id FROM manual_categories)
                                OR q.contract_id IN (SELECT contract_id FROM manual_completions))""", (key,)).fetchone()[0]
    return {"total": total, "saved": done - retry, "failed": failed, "retry_required": retry,
            "pending": total - done - failed, "earlier_reviews_kept": kept, "manual_decisions_kept": manual}


_RETRY_SQL = """(a.category = 'Photo analysis unavailable · retry needed'
                 OR json_extract(CASE WHEN json_valid(a.summary) THEN a.summary ELSE '{}' END,
                                 '$.assessment.retry_required') = 1)"""


def failures(db, key):
    return db.execute("SELECT contract_id, message, failed_at, attempts FROM queue_failures WHERE filters=? AND retry=0 ORDER BY failed_at, contract_id",
                      (key,)).fetchall()


def due_count(db, key, deps):
    """Saved contracts in this queue that the next check would review again (older than their recheck window,
    or made with earlier rules). Known offline: it needs no source access."""
    signature = deps.signature()
    due = 0
    for contract, reviewed_at, payload in db.execute(
            """SELECT a.contract_id, a.reviewed_at, a.summary FROM regional_queue q JOIN contract_assessments a
               ON a.contract_id = q.contract_id WHERE q.filters = ? AND q.done = 1""", (key,)):
        try:
            due += bool(deps.is_due(json.loads(payload), reviewed_at, signature))
        except Exception:        # an unreadable saved review is treated as due, never as a crash
            due += 1
    return due


def scoped_retry(db, key):
    """Put only this queue's failed and retry-required contracts back in line; return their ids.
    Completed, current reviews and every other pending contract are left exactly as they are."""
    with db:
        db.execute("BEGIN IMMEDIATE")
        ids = {row[0] for row in db.execute("SELECT contract_id FROM queue_failures WHERE filters=? AND retry=0", (key,))}
        retry = {row[0] for row in db.execute(f"""SELECT q.contract_id FROM regional_queue q JOIN contract_assessments a
                                               ON a.contract_id = q.contract_id WHERE q.filters = ? AND q.done = 1
                                               AND {_RETRY_SQL}""", (key,))}
        # Marked for retry, not deleted: attempts keep counting, and a crash before the run loses nothing.
        db.execute("UPDATE queue_failures SET retry = 1 WHERE filters=? AND retry = 0", (key,))
        db.executemany("UPDATE regional_queue SET done = 0, staged = NULL WHERE filters = ? AND contract_id = ?",
                       ((key, contract) for contract in retry))
        return sorted(ids | retry)


# --- the run -----------------------------------------------------------------------------------

def start(key, deps, *, filters=None, fetch=False, job=None, prepare=None):
    """Start a worker for this queue unless one is running here or in another process. Returns the job, or None.

    ``prepare`` (e.g. seeding a rerun queue) runs under the same lock and only once starting is allowed, so a second
    click can never rewrite a queue that a worker is walking."""
    with LOCK:
        existing = REGISTRY.get(key)
        if existing is not None and existing.alive:
            return None
        with closing(deps.open_db()) as db:
            if run_status(db, key, job=Job(key))["state"] == ELSEWHERE:
                return None
        if prepare:
            prepare()
        job = job or Job(key)
        REGISTRY[key] = job
        job.thread = Thread(target=run, args=(job, deps, filters, fetch), daemon=True)
        job.thread.start()
        return job


def request_pause(key, deps):
    job = REGISTRY.get(key)
    if job is None or not job.alive:
        return False
    job.stop.set()
    # Only while the stored state is still "running": the worker may have written its final state meanwhile.
    _write_run(deps, job, PAUSE_REQUESTED, only_if=RUNNING)
    return True


def run(job, deps, filters=None, fetch=False):
    """Run to completion, a pause, or a stop. Never raises: the outcome is the stored state."""
    done = Event()
    Thread(target=_heartbeat, args=(job, deps, done), daemon=True).start()
    try:
        _write_run(deps, job, RUNNING, new=True)
        if fetch and filters is not None:
            _stage(deps, job, FETCHING)
            _enqueue(job, deps, filters)
        _process(job, deps)
        remaining = len(_pending(deps, job.key, job.only, limit=-1))
        job.stage = job.current = None
        _write_run(deps, job, PAUSED if job.stop.is_set() and remaining else FINISHED)
    except StopRun as stop:
        job.error = str(stop)
        _write_run(deps, job, STOPPED, error=job.error)
    except Exception as error:   # the worker must report, not die silently
        job.error = f"{type(error).__name__}: {error}"[:400]
        try:
            _write_run(deps, job, STOPPED, error=job.error)
        except Exception:
            pass
    finally:
        done.set()


def _heartbeat(job, deps, done):
    """Touch the stored run while the worker lives, so another dashboard process can tell 'running' from 'died'."""
    while not done.wait(HEARTBEAT_SECONDS / 3):
        try:
            with closing(deps.open_db()) as db, db:
                db.execute("UPDATE queue_runs SET updated_at=? WHERE filters=? AND state IN (?,?)", (now(), job.key, RUNNING, PAUSE_REQUESTED))
        except Exception:
            pass


def _enqueue(job, deps, filters):
    try:
        projects = deps.fetch_projects(filters)
    except Exception as error:
        raise StopRun(f"Fetching regional records failed: {plain_error(error)} "
                      "Nothing was changed; select the button again to retry.") from error
    key = job.key
    signature = deps.signature()
    with closing(deps.open_db()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        existing = {row[0] for row in db.execute("SELECT contract_id FROM regional_queue WHERE filters=?", (key,))}
        start_at = db.execute("SELECT COALESCE(MAX(position) + 1, 0) FROM regional_queue WHERE filters=?", (key,)).fetchone()[0]
        db.executemany("INSERT INTO regional_queue(filters, position, contract_id) VALUES (?,?,?)",
                       ((key, position, contract) for position, contract in
                        enumerate((c for c in projects if c not in existing), start=start_at)))
        for contract, reviewed, payload in db.execute("SELECT contract_id, reviewed_at, summary FROM contract_assessments").fetchall():
            if contract in projects and contract in existing:
                try:
                    due = deps.is_due(json.loads(payload), reviewed, signature)
                except Exception:
                    due = True
                if due:
                    db.execute("UPDATE regional_queue SET done=0, staged=NULL WHERE filters=? AND contract_id=?", (key, contract))


def _pending(deps, key, only=None, *, limit=BATCH):
    with closing(deps.open_db()) as db:
        rows = db.execute("""SELECT q.position, q.contract_id, q.staged FROM regional_queue q
                             WHERE q.filters = ? AND q.done = 0 AND NOT EXISTS (
                                 SELECT 1 FROM queue_failures f WHERE f.filters = q.filters AND f.contract_id = q.contract_id
                                 AND f.retry = 0)
                             ORDER BY q.position LIMIT ?""", (key, limit)).fetchall()
    return [row for row in rows if only is None or row[1] in only]


def _retry_required(record):
    assessment = record.get("assessment") or {}
    return bool(assessment.get("retry_required")) or assessment.get("completion_evidence_category") == ANALYSIS_UNAVAILABLE


def _earlier_usable_review(deps, contract):
    """True when a saved review exists that is not itself a provisional 'retry needed' result."""
    with closing(deps.open_db()) as db:
        row = db.execute("SELECT category FROM contract_assessments WHERE contract_id=?", (contract,)).fetchone()
    return bool(row) and row[0] != ANALYSIS_UNAVAILABLE


def _process(job, deps):
    key = job.key
    pool = ThreadPoolExecutor(max_workers=BATCH)
    futures = {}
    try:
        while not job.stop.is_set():
            batch = _pending(deps, key, job.only, limit=-1 if job.only is not None else BATCH)[:BATCH]
            if not batch:
                return
            signature = deps.signature()
            prepared = []
            for position, contract, staged in batch:
                try:
                    stale = bool(staged) and deps.is_stale(json.loads(staged), signature)
                except Exception:
                    stale = True                       # an unreadable staged record is discarded, never fatal
                prepared.append((position, contract, None if (stale or not staged) else staged))
            futures = {position: pool.submit(deps.fetch_inputs, contract) for position, contract, staged in prepared if staged is None}
            for position, contract, staged in prepared:
                if job.stop.is_set():
                    return
                try:
                    if staged is None:
                        _stage(deps, job, PHOTOS, contract)
                        inputs = futures[position].result()
                        if inputs[1]["galleryPhotoCount"] is None:
                            raise ValueError("The photo gallery could not be read from the source.")
                        _stage(deps, job, ANALYSING, contract)
                        record = deps.summarize(contract, inputs)
                    else:
                        record = deps.refresh_staged(json.loads(staged))
                    provisional = _retry_required(record)
                    if provisional and _earlier_usable_review(deps, contract):
                        # An outage must not replace a good saved review with "analysis unavailable".
                        raise ValueError("Photo analysis was unavailable, so the earlier saved review was kept.")
                    _stage(deps, job, SAVING, contract)
                    deps.save(record)
                    with closing(deps.open_db()) as db, db:
                        db.execute("UPDATE regional_queue SET done=1, staged=NULL WHERE filters=? AND position=? AND contract_id=?",
                                   (key, position, contract))
                        db.execute("DELETE FROM queue_failures WHERE filters=? AND contract_id=?", (key, contract))
                    # A saved-but-unavailable result counts toward the outage breaker like a failure does.
                    job.consecutive = job.consecutive + 1 if provisional else 0
                    if provisional and job.consecutive >= FAILURE_LIMIT:
                        raise StopRun(_stop_message("Photo analysis was unavailable"))
                except StopRun:
                    raise
                except Exception as error:
                    message = plain_error(error)
                    with closing(deps.open_db()) as db, db:
                        db.execute("""INSERT INTO queue_failures(filters, contract_id, message, failed_at, attempts, retry)
                                      VALUES (?,?,?,?,1,0)
                                      ON CONFLICT(filters, contract_id) DO UPDATE SET message=excluded.message,
                                      failed_at=excluded.failed_at, attempts=attempts+1, retry=0""", (key, contract, message, now()))
                    job.consecutive += 1
                    if job.consecutive >= FAILURE_LIMIT:
                        raise StopRun(_stop_message(message)) from error
    finally:
        for future in futures.values():
            future.cancel()
        pool.shutdown(wait=False, cancel_futures=True)     # unused prefetches are dropped; the final state is written promptly


def plain_error(error):
    """A reader-facing reason. Connection and HTTP errors carry long technical text (hosts, proxies, error codes), so
    they are summarised; our own messages (which are already plain) pass through, trimmed."""
    names = {cls.__name__ for cls in type(error).__mro__}
    if names & {"RequestException", "ConnectionError", "Timeout", "HTTPError", "URLError", "TimeoutError"}:
        return f"The source could not be reached or did not respond ({type(error).__name__})."
    if "JSONDecodeError" in names:
        return "The source returned data that could not be read."
    return ((str(error) or type(error).__name__)[:200]).rstrip(".") + "."


def _stop_message(last):
    return (f"Stopped after {FAILURE_LIMIT} contracts in a row failed or returned no usable analysis. The source or a model may "
            f"be unavailable. Completed reviews are unchanged. Last problem: {last}")
