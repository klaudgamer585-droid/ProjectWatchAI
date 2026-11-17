"""Phase 7: queue worker, Admin queue panel and contract-ID boundaries.

Everything runs on disposable SQLite files with fake dependencies: no network, no detectors, no training, no
promotion. Pause, interruption, partial failure, retry, outages and ID limits are exercised against real
worker threads and the real stored queue/run state.
"""
import json
import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from threading import Event
from unittest.mock import patch

import queue_worker as qw
from test_states_ui import all_text

HERE = Path(__file__).parent
KEY = '{"region": "Region III", "year": "2024"}'
RETRY = qw.ANALYSIS_UNAVAILABLE
OK = "Visible scope evidence · fixture"


def open_db_factory(path):
    def open_db():
        db = sqlite3.connect(path, timeout=10)
        db.execute("CREATE TABLE IF NOT EXISTS contract_assessments (contract_id TEXT PRIMARY KEY, category TEXT NOT NULL, "
                   "reviewed_at TEXT NOT NULL, summary TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS regional_queue (filters TEXT NOT NULL, position INTEGER NOT NULL, "
                   "contract_id TEXT NOT NULL, staged TEXT, done INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (filters, position))")
        db.execute("CREATE TABLE IF NOT EXISTS manual_categories (contract_id TEXT PRIMARY KEY, category TEXT NOT NULL, note TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS manual_completions (contract_id TEXT PRIMARY KEY, evidence_file TEXT NOT NULL)")
        qw.ensure_schema(db)
        return db
    return open_db


def record(contract, category=OK, retry=False):
    return {"contractId": contract, "galleryPhotoCount": 1, "status": "Completed", "photos": [],
            "assessment": {"completion_evidence_category": category, "retry_required": retry, "flags": []}}


class Fixture:
    """Fake dependencies with switches for the failure modes under test."""

    def __init__(self, path, contracts=("A", "B", "C"), key=KEY):
        self.path, self.key = path, key
        self.open_db = open_db_factory(path)
        self.fail = {}                      # contract -> exception raised while analysing
        self.gallery_unknown = set()
        self.categories = {}                # contract -> saved category
        self.gate = {}                      # contract -> Event the worker waits on while analysing
        self.reached = {}                   # contract -> Event set when analysis of it begins
        self.saves, self.stages, self.fetches = [], [], 0
        self.job = None
        self.projects_error = None
        self.projects = {}
        with closing(self.open_db()) as db, db:
            for position, contract in enumerate(contracts):
                db.execute("INSERT INTO regional_queue VALUES (?,?,?,NULL,0)", (key, position, contract))

    def deps(self):
        def fetch_inputs(contract):
            return ({}, {"galleryPhotoCount": None if contract in self.gallery_unknown else 1}, [])

        def summarize(contract, inputs):
            self.stages.append((contract, self.job.stage if self.job else None))
            self.reached.setdefault(contract, Event()).set()
            if contract in self.gate:
                assert self.gate[contract].wait(10), "test gate never opened"
            if contract in self.fail:
                raise self.fail[contract]
            return record(contract, self.categories.get(contract, OK), retry=self.categories.get(contract) == RETRY)

        def save(record_):
            self.stages.append((record_["contractId"], "save:" + str(self.job.stage if self.job else None)))
            self.saves.append(record_["contractId"])
            with closing(self.open_db()) as db, db:
                db.execute("INSERT OR REPLACE INTO contract_assessments VALUES (?,?,?,?)",
                           (record_["contractId"], record_["assessment"]["completion_evidence_category"],
                            qw.now(), json.dumps(record_)))

        def fetch_projects(filters):
            if self.projects_error:
                raise self.projects_error
            self.fetches += 1
            return self.projects

        return qw.Deps(open_db=self.open_db, fetch_projects=fetch_projects, fetch_inputs=fetch_inputs, summarize=summarize,
                       refresh_staged=lambda r: r, save=save, is_due=lambda r, at, sig: False,
                       is_stale=lambda r, sig: False, signature=lambda: "sig")

    def run(self, **kw):
        self.job = qw.Job(self.key)
        self.job.only = kw.pop("only", None)
        qw.run(self.job, self.deps(), **kw)
        return self.job

    def start(self, **kw):
        self.job = qw.Job(self.key)
        self.job.only = kw.pop("only", None)
        started = qw.start(self.key, self.deps(), job=self.job, **kw)
        return started

    def wait(self, timeout=10):
        deadline = time.time() + timeout
        while self.job.alive and time.time() < deadline:
            time.sleep(.02)
        assert not self.job.alive, "worker did not finish"

    def counts(self):
        with closing(self.open_db()) as db:
            return qw.counts(db, self.key)

    def status(self):
        with closing(self.open_db()) as db:
            return qw.run_status(db, self.key)


class WorkerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=HERE)
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "queue.db"
        qw.REGISTRY.clear()
        self.addCleanup(qw.REGISTRY.clear)


class RunTests(WorkerCase):
    def test_full_run_saves_everything_and_finishes(self):
        fx = Fixture(self.path)
        fx.run()
        self.assertEqual(fx.counts(), {"total": 3, "saved": 3, "failed": 0, "retry_required": 0, "pending": 0,
                                       "earlier_reviews_kept": 0, "manual_decisions_kept": 0})
        self.assertEqual(fx.status()["state"], qw.FINISHED)
        self.assertEqual(fx.saves, ["A", "B", "C"])

    def test_stages_are_the_real_ones_in_order(self):
        fx = Fixture(self.path, contracts=("A",))
        fx.run()
        self.assertEqual(fx.stages, [("A", qw.ANALYSING), ("A", "save:" + qw.SAVING)])
        with closing(fx.open_db()) as db:
            row = qw.stored_run(db, KEY)
        self.assertEqual(row["state"], qw.FINISHED)
        self.assertIsNone(row["stage"])               # nothing is "in progress" after the run

    def test_fetch_stage_enqueues_new_contracts_only_once(self):
        fx = Fixture(self.path, contracts=("A",))
        fx.projects = {"A": {}, "B": {}, "C": {}}
        fx.run(filters={"region": "Region III"}, fetch=True)
        self.assertEqual(fx.counts()["total"], 3)
        fx.run(filters={"region": "Region III"}, fetch=True)       # a second check adds nothing and re-reviews nothing
        self.assertEqual(fx.counts()["total"], 3)
        self.assertEqual(fx.saves, ["A", "B", "C"])

    def test_unknown_gallery_is_a_failure_not_a_stop(self):
        fx = Fixture(self.path)
        fx.gallery_unknown = {"B"}
        fx.run()
        counts = fx.counts()
        self.assertEqual((counts["saved"], counts["failed"], counts["pending"]), (2, 1, 0))
        self.assertEqual(fx.status()["state"], qw.FINISHED)
        with closing(fx.open_db()) as db:
            [(contract, message, *_)] = qw.failures(db, KEY)
        self.assertEqual(contract, "B")
        self.assertIn("gallery could not be read", message)


class PlainErrorTests(WorkerCase):
    def test_connection_errors_are_summarised_not_dumped(self):
        class HTTPSConnectionPool(Exception):
            pass

        class ConnectionError(Exception):          # name-based, like requests' hierarchy
            pass
        raw = ConnectionError("HTTPSConnectionPool(host='api.example', port=443): Max retries exceeded ... WinError 10061")
        message = qw.plain_error(raw)
        self.assertNotIn("HTTPSConnectionPool", message)
        self.assertNotIn("WinError", message)
        self.assertIn("could not be reached", message)

    def test_a_fetch_failure_shows_no_raw_exception_text(self):
        fx = Fixture(self.path, contracts=("A",))
        fx.projects_error = ConnectionError("HTTPSConnectionPool(host='x'): ProxyError WinError 10061")
        fx.run(filters={"region": "Region III"}, fetch=True)
        error = fx.status()["error"]
        self.assertIn("Fetching regional records failed", error)
        self.assertNotIn("HTTPSConnectionPool", error)
        self.assertNotIn("WinError", error)

    def test_our_own_plain_messages_pass_through(self):
        self.assertEqual(qw.plain_error(ValueError("The photo gallery could not be read from the source.")),
                         "The photo gallery could not be read from the source.")
        self.assertEqual(qw.plain_error(RuntimeError("")), "RuntimeError.")
        self.assertLessEqual(len(qw.plain_error(RuntimeError("x" * 5000))), 202)


class PartialFailureTests(WorkerCase):
    def test_one_failed_contract_does_not_block_the_rest_and_keeps_its_earlier_review(self):
        fx = Fixture(self.path)
        fx.fail = {"B": RuntimeError("detector crashed")}
        with closing(fx.open_db()) as db, db:
            db.execute("INSERT INTO contract_assessments VALUES ('B','Earlier category','2026-01-01T00:00:00+08:00','{}')")
        fx.run()
        counts = fx.counts()
        self.assertEqual((counts["saved"], counts["failed"], counts["pending"], counts["earlier_reviews_kept"]), (2, 1, 0, 1))
        with closing(fx.open_db()) as db:
            self.assertEqual(db.execute("SELECT category FROM contract_assessments WHERE contract_id='B'").fetchone()[0],
                             "Earlier category")                                    # the failure did not overwrite it
        self.assertEqual(fx.saves, ["A", "C"])

    def test_retry_required_is_counted_apart_from_saved(self):
        fx = Fixture(self.path)
        fx.categories = {"C": RETRY}
        fx.run()
        counts = fx.counts()
        self.assertEqual((counts["saved"], counts["retry_required"], counts["failed"]), (2, 1, 0))

    def test_manual_decisions_are_untouched_and_counted(self):
        fx = Fixture(self.path)
        with closing(fx.open_db()) as db, db:
            db.execute("INSERT INTO manual_categories VALUES ('A','Completed · manual review','Reviewer note')")
            db.execute("INSERT INTO manual_completions VALUES ('B','evidence.pdf')")
        fx.run()
        with closing(fx.open_db()) as db:
            self.assertEqual(db.execute("SELECT category, note FROM manual_categories").fetchall(),
                             [("Completed · manual review", "Reviewer note")])
            self.assertEqual(db.execute("SELECT evidence_file FROM manual_completions").fetchall(), [("evidence.pdf",)])
        self.assertEqual(fx.counts()["manual_decisions_kept"], 2)

    def test_scoped_retry_runs_only_the_failed_contract(self):
        fx = Fixture(self.path, contracts=("A", "B", "C", "D"))
        fx.fail = {"B": RuntimeError("temporary")}
        fx.run(only={"A", "B"})                                  # C and D never ran: pending
        self.assertEqual({k: v for k, v in fx.counts().items() if k in ("saved", "failed", "pending")},
                         {"saved": 1, "failed": 1, "pending": 2})
        fx.fail = {}
        with closing(fx.open_db()) as db:
            ids = qw.scoped_retry(db, KEY)
        self.assertEqual(ids, ["B"])
        fx.saves.clear()
        fx.run(only=set(ids))
        self.assertEqual(fx.saves, ["B"])                        # A was not redone, C and D were not started
        counts = fx.counts()
        self.assertEqual((counts["saved"], counts["failed"], counts["pending"]), (2, 0, 2))
        self.assertEqual(fx.status()["state"], qw.FINISHED)

    def test_retry_also_requeues_saved_but_provisional_results(self):
        fx = Fixture(self.path)
        fx.categories = {"C": RETRY}
        fx.run()
        fx.categories = {}
        with closing(fx.open_db()) as db:
            ids = qw.scoped_retry(db, KEY)
        self.assertEqual(ids, ["C"])
        fx.saves.clear()
        fx.run(only=set(ids))
        self.assertEqual(fx.saves, ["C"])
        self.assertEqual(fx.counts()["retry_required"], 0)

    def test_retry_never_touches_another_queue(self):
        other = '{"region": "Region I"}'
        fx = Fixture(self.path)
        with closing(fx.open_db()) as db, db:
            db.execute("INSERT INTO regional_queue VALUES (?,0,'Z',NULL,0)", (other,))
            db.execute("INSERT INTO queue_failures(filters, contract_id, message, failed_at, attempts) VALUES (?,?,?,?,1)", (other, "Z", "elsewhere", qw.now()))
        fx.fail = {"B": RuntimeError("x")}
        fx.run()
        with closing(fx.open_db()) as db:
            qw.scoped_retry(db, KEY)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM queue_failures WHERE filters=? AND retry=0", (other,)).fetchone()[0], 1)


class OutageTests(WorkerCase):
    def test_repeated_failures_stop_the_run_and_leave_the_rest_pending(self):
        fx = Fixture(self.path, contracts=tuple(f"C{i}" for i in range(9)))
        fx.fail = {f"C{i}": ConnectionError("source down") for i in range(9)}
        job = fx.run()
        counts = fx.counts()
        self.assertEqual((counts["failed"], counts["saved"], counts["pending"]), (qw.FAILURE_LIMIT, 0, 9 - qw.FAILURE_LIMIT))
        status = fx.status()
        self.assertEqual(status["state"], qw.STOPPED)
        self.assertIn(f"Stopped after {qw.FAILURE_LIMIT} contracts in a row failed or returned no usable analysis", status["error"])
        self.assertIn("could not be reached", status["error"])
        self.assertNotIn("source down", status["error"])
        self.assertEqual(job.error, status["error"])

    def test_a_success_resets_the_failure_streak(self):
        fx = Fixture(self.path, contracts=tuple(f"C{i}" for i in range(9)))
        fx.fail = {f"C{i}": ConnectionError("blip") for i in (0, 1, 2, 3, 5, 6, 7, 8)}     # C4 succeeds between streaks of 4
        fx.run()
        self.assertEqual(fx.status()["state"], qw.FINISHED)
        self.assertEqual(fx.counts()["failed"], 8)

    def test_fetch_failure_stops_cleanly_and_changes_nothing(self):
        fx = Fixture(self.path, contracts=("A",))
        fx.projects_error = ConnectionError("no route")
        fx.run(filters={"region": "Region III"}, fetch=True)
        status = fx.status()
        self.assertEqual(status["state"], qw.STOPPED)
        self.assertIn("Fetching regional records failed", status["error"])
        self.assertEqual(fx.counts()["total"], 1)
        self.assertEqual(fx.saves, [])


class PauseResumeTests(WorkerCase):
    def test_pause_requested_then_paused_then_resume_keeps_completed_work(self):
        fx = Fixture(self.path)
        fx.gate = {"B": Event()}
        self.assertIsNotNone(fx.start())
        self.assertTrue(fx.reached.setdefault("B", Event()).wait(10))
        self.assertEqual(fx.status()["state"], qw.RUNNING)
        self.assertEqual(fx.status()["stage"], qw.ANALYSING)
        self.assertTrue(qw.request_pause(KEY, fx.deps()))
        self.assertEqual(fx.status()["state"], qw.PAUSE_REQUESTED)      # worker still alive, finishing B
        fx.gate["B"].set()
        fx.wait()
        status = fx.status()
        self.assertEqual(status["state"], qw.PAUSED)                    # now it has actually stopped
        counts = fx.counts()
        self.assertEqual((counts["saved"], counts["pending"]), (2, 1))   # A and B finished; C waits
        self.assertEqual(fx.saves, ["A", "B"])
        fx.gate.clear()
        fx.saves.clear()
        fx.start()
        fx.wait()
        self.assertEqual(fx.saves, ["C"])                               # resume did not redo A or B
        self.assertEqual(fx.status()["state"], qw.FINISHED)
        self.assertEqual(fx.counts()["saved"], 3)

    def test_pause_without_a_worker_is_refused(self):
        fx = Fixture(self.path)
        self.assertFalse(qw.request_pause(KEY, fx.deps()))
        self.assertEqual(fx.status()["state"], qw.IDLE)

    def test_a_second_start_while_running_is_refused(self):
        fx = Fixture(self.path)
        fx.gate = {"A": Event()}
        fx.start()
        self.assertTrue(fx.reached.setdefault("A", Event()).wait(10))
        self.assertIsNone(qw.start(KEY, fx.deps()))
        fx.gate["A"].set()
        fx.wait()
        self.assertEqual(fx.saves, ["A", "B", "C"])                     # each contract was saved exactly once

    def test_pause_finishing_the_last_contract_is_finished_not_paused(self):
        fx = Fixture(self.path, contracts=("A",))
        fx.gate = {"A": Event()}
        fx.start()
        self.assertTrue(fx.reached.setdefault("A", Event()).wait(10))
        qw.request_pause(KEY, fx.deps())
        fx.gate["A"].set()
        fx.wait()
        self.assertEqual(fx.status()["state"], qw.FINISHED)


class InterruptionTests(WorkerCase):
    def stored(self, fx, state, owner, age_seconds=0):
        when = (datetime.now(qw.PHILIPPINE_TIME) - timedelta(seconds=age_seconds)).isoformat(timespec="seconds")
        with closing(fx.open_db()) as db, db:
            db.execute("INSERT OR REPLACE INTO queue_runs VALUES (?,?,?,?,?,?,?,?)",
                       (KEY, state, qw.PHOTOS, "B", None, owner, when, when))

    def test_stored_running_without_a_worker_is_interrupted_not_paused(self):
        fx = Fixture(self.path)
        self.stored(fx, qw.RUNNING, os.getpid())
        self.assertEqual(fx.status()["state"], qw.INTERRUPTED)
        self.stored(fx, qw.PAUSE_REQUESTED, os.getpid())
        self.assertEqual(fx.status()["state"], qw.INTERRUPTED)

    def test_fresh_run_owned_by_another_process_is_running_elsewhere(self):
        fx = Fixture(self.path)
        self.stored(fx, qw.RUNNING, os.getpid() + 1, age_seconds=5)
        self.assertEqual(fx.status()["state"], qw.ELSEWHERE)
        self.stored(fx, qw.RUNNING, os.getpid() + 1, age_seconds=qw.HEARTBEAT_SECONDS + 60)
        self.assertEqual(fx.status()["state"], qw.INTERRUPTED)

    def test_resume_after_interruption_continues_without_redoing_saved_work(self):
        fx = Fixture(self.path)
        fx.run(only={"A"})
        self.stored(fx, qw.RUNNING, os.getpid())
        self.assertEqual(fx.status()["state"], qw.INTERRUPTED)
        fx.saves.clear()
        fx.start()
        fx.wait()
        self.assertEqual(fx.saves, ["B", "C"])
        self.assertEqual(fx.status()["state"], qw.FINISHED)

    def test_a_dead_registry_job_is_not_reported_as_running(self):
        fx = Fixture(self.path)
        fx.run()
        qw.REGISTRY[KEY] = fx.job                                      # finished thread object left behind
        self.assertEqual(fx.status()["state"], qw.FINISHED)


class AdversarialFixTests(WorkerCase):
    """Defects found by the adversarial review, each reproduced first."""

    def test_an_outage_does_not_replace_a_good_review_with_analysis_unavailable(self):
        fx = Fixture(self.path, contracts=("A", "B"))
        good = json.dumps(record("A"))
        with closing(fx.open_db()) as db, db:
            db.execute("INSERT INTO contract_assessments VALUES ('A',?,?,?)", (OK, "2026-01-01T00:00:00+08:00", good))
        fx.categories = {"A": RETRY, "B": RETRY}
        fx.run()
        with closing(fx.open_db()) as db:
            self.assertEqual(db.execute("SELECT category, summary FROM contract_assessments WHERE contract_id='A'").fetchone(), (OK, good))
            [(contract, message, *_)] = qw.failures(db, KEY)
        self.assertEqual(contract, "A")
        self.assertIn("earlier saved review was kept", message)
        counts = fx.counts()
        self.assertEqual((counts["failed"], counts["retry_required"], counts["earlier_reviews_kept"]), (1, 1, 1))   # B had no earlier review

    def test_unavailable_analysis_in_a_row_trips_the_outage_breaker(self):
        fx = Fixture(self.path, contracts=tuple(f"C{i}" for i in range(9)))
        fx.categories = {f"C{i}": RETRY for i in range(9)}
        fx.run()
        status = fx.status()
        self.assertEqual(status["state"], qw.STOPPED)
        self.assertIn("returned no usable analysis", status["error"])
        self.assertEqual(fx.counts()["pending"], 9 - qw.FAILURE_LIMIT)

    def test_malformed_staged_value_does_not_block_the_queue(self):
        fx = Fixture(self.path)
        with closing(fx.open_db()) as db, db:
            db.execute("UPDATE regional_queue SET staged='garbage{' WHERE contract_id='A'")
        fx.run()
        self.assertEqual(fx.status()["state"], qw.FINISHED)
        self.assertEqual(fx.saves, ["A", "B", "C"])

    def test_rerun_seeding_cannot_rewrite_a_queue_a_worker_is_walking(self):
        fx = Fixture(self.path)
        fx.gate = {"A": Event()}
        fx.start()
        self.assertTrue(fx.reached.setdefault("A", Event()).wait(10))
        seeded = []
        self.assertIsNone(qw.start(KEY, fx.deps(), prepare=lambda: seeded.append(True)))
        self.assertEqual(seeded, [])                                   # prepare never ran
        fx.gate["A"].set()
        fx.wait()
        self.assertEqual(fx.saves, ["A", "B", "C"])

    def test_queue_owned_by_another_process_cannot_be_started_or_seeded(self):
        fx = Fixture(self.path)
        with closing(fx.open_db()) as db, db:
            now = qw.now()
            db.execute("INSERT INTO queue_runs VALUES (?,?,?,?,?,?,?,?)", (KEY, qw.RUNNING, qw.PHOTOS, "A", None, os.getpid() + 1, now, now))
        seeded = []
        self.assertIsNone(qw.start(KEY, fx.deps(), prepare=lambda: seeded.append(True)))
        self.assertEqual(seeded, [])

    def test_a_late_pause_does_not_overwrite_a_finished_run(self):
        fx = Fixture(self.path, contracts=("A",))
        fx.run()
        self.assertEqual(fx.status()["state"], qw.FINISHED)
        qw._write_run(fx.deps(), fx.job, qw.PAUSE_REQUESTED, only_if=qw.RUNNING)
        self.assertEqual(fx.status()["state"], qw.FINISHED)             # not "interrupted"

    def test_attempts_accumulate_across_retries(self):
        fx = Fixture(self.path, contracts=("A",))
        fx.fail = {"A": RuntimeError("one")}
        fx.run()
        with closing(fx.open_db()) as db:
            qw.scoped_retry(db, KEY)
            self.assertEqual(qw.counts(db, KEY)["pending"], 1)           # retrying counts as pending, not failed
            self.assertEqual(qw.counts(db, KEY)["failed"], 0)
        fx.fail = {"A": RuntimeError("two")}
        fx.run(only={"A"})
        with closing(fx.open_db()) as db:
            [(contract, message, failed_at, attempts)] = qw.failures(db, KEY)
        self.assertEqual((contract, message, attempts), ("A", "two.", 2))

    def test_a_crash_between_retry_and_run_loses_nothing(self):
        fx = Fixture(self.path, contracts=("A", "B"))
        fx.fail = {"A": RuntimeError("x")}
        fx.run()
        with closing(fx.open_db()) as db:
            qw.scoped_retry(db, KEY)                                     # ... process dies here
        fx.fail = {}
        fx.saves.clear()
        fx.run()                                                         # a plain resume still reviews A
        self.assertEqual(fx.saves, ["A"])
        self.assertEqual(fx.counts()["saved"], 2)

    def test_unreadable_saved_summary_is_due_not_a_crash(self):
        fx = Fixture(self.path, contracts=("A",))
        with closing(fx.open_db()) as db, db:
            db.execute("UPDATE regional_queue SET done=1")
            db.execute("INSERT INTO contract_assessments VALUES ('A','x','2026-01-01T00:00:00+08:00','[]')")
        deps = fx.deps()
        deps.is_due = lambda record_, at, sig: record_["contractId"]       # a list has no such key: TypeError
        with closing(fx.open_db()) as db:
            self.assertEqual(qw.due_count(db, KEY, deps), 1)

    def test_failure_table_made_before_retries_is_migrated_in_place(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE queue_failures (filters TEXT NOT NULL, contract_id TEXT NOT NULL, message TEXT NOT NULL, "
                       "failed_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 1, PRIMARY KEY (filters, contract_id))")
            db.execute("INSERT INTO queue_failures VALUES ('k','A','m','t',3)")
            qw.ensure_schema(db)
            qw.ensure_schema(db)                                             # idempotent
            self.assertEqual(db.execute("SELECT contract_id, attempts, retry FROM queue_failures").fetchall(), [("A", 3, 0)])

    def test_a_running_worker_keeps_its_heartbeat_fresh(self):
        fx = Fixture(self.path, contracts=("A",))
        fx.gate = {"A": Event()}
        with patch.object(qw, "HEARTBEAT_SECONDS", .3):
            fx.start()
            self.assertTrue(fx.reached.setdefault("A", Event()).wait(10))
            with closing(fx.open_db()) as db:
                first = qw.stored_run(db, KEY)["updated_at"]
            time.sleep(1.2)
            with closing(fx.open_db()) as db:
                later = qw.stored_run(db, KEY)["updated_at"]
            fx.gate["A"].set()
            fx.wait()
        self.assertNotEqual(first, later)


class DueTests(WorkerCase):
    def test_due_count_uses_only_saved_contracts_in_this_queue(self):
        fx = Fixture(self.path)
        fx.run()
        deps = fx.deps()
        deps.is_due = lambda record_, at, sig: record_["contractId"] != "A"
        with closing(fx.open_db()) as db:
            self.assertEqual(qw.due_count(db, KEY, deps), 2)
            self.assertEqual(qw.due_count(db, '{"other": 1}', deps), 0)

    def test_unreadable_saved_record_counts_as_due(self):
        fx = Fixture(self.path, contracts=("A",))
        with closing(fx.open_db()) as db, db:
            db.execute("UPDATE regional_queue SET done=1")
            db.execute("INSERT INTO contract_assessments VALUES ('A','x','2026-01-01T00:00:00+08:00','not json')")
        with closing(fx.open_db()) as db:
            self.assertEqual(qw.due_count(db, KEY, fx.deps()), 1)


class ContractIdTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app
        cls.parse = staticmethod(app.parse_contract_input)

    def test_boundaries(self):
        ids, problem, notes = self.parse("a1, b2, c3")
        self.assertEqual((ids, problem, notes), (("A1", "B2", "C3"), None, ()))
        ids, problem, _ = self.parse("a1, b2, c3, d4")
        self.assertEqual(ids, ())
        self.assertIn("Remove 1 to continue: D4", problem)               # named, never truncated
        ids, problem, _ = self.parse("a1,b2,c3,d4,e5,f6")
        self.assertIn("Remove 3 to continue: D4, E5, F6", problem)
        self.assertEqual(self.parse("")[:2], ((), None))
        self.assertEqual(self.parse(None)[:2], ((), None))
        self.assertEqual(self.parse(" , ;\n\r\n ,")[:2], ((), None))

    def test_casing_whitespace_and_planned_separators(self):
        for text in ("24ck0181,23je0090", "  24CK0181 ,\n23je0090\n", "24ck0181;23je0090", "24CK0181\r\n23JE0090", "24ck0181,,\n,23je0090,"):
            self.assertEqual(self.parse(text)[:2], (("24CK0181", "23JE0090"), None), text)

    def test_duplicates_are_explicit_and_counted_once_against_the_limit(self):
        ids, problem, notes = self.parse("a1, A1, a1, b2, c3")
        self.assertEqual((ids, problem), (("A1", "B2", "C3"), None))
        self.assertEqual(notes, ("A1 was entered 3 times and is reviewed once.",))
        ids, problem, notes = self.parse("a1, A1, b2, c3, d4")
        self.assertEqual(ids, ())                                         # four distinct IDs: still over the limit
        self.assertIn("D4", problem)
        self.assertIn("A1 was entered 2 times", notes[0])

    def test_ambiguous_entries_are_rejected_not_split(self):
        for text in ("24CK 0181", "24CK0181 23JE0090", "a1, b 2"):
            ids, problem, notes = self.parse(text)
            self.assertEqual(ids, (), text)
            self.assertIn("Separate contract IDs with commas or new lines", problem)
        self.assertIn("“24CK 0181”", self.parse("24CK 0181")[1])

    def test_uppercasing_never_rewrites_characters_into_valid_ids(self):
        for text in ("stra\u00dfe", "\ufb011", "\u0131i", "a1, \u00df"):
            self.assertEqual(self.parse(text)[0], (), text)
            self.assertIn("letters, numbers and hyphens only", self.parse(text)[1])

    def test_a_huge_invalid_entry_is_truncated_in_the_message(self):
        problem = self.parse("x!" * 50000)[1]
        self.assertLess(len(problem), 400)

    def test_invalid_characters_and_length_reject_the_whole_input(self):
        for text in ("a1, b*2", "a1, b2!", "a1, ../x", "a1, １２"):
            ids, problem, _ = self.parse(text)
            self.assertEqual(ids, (), text)
            self.assertIn("letters, numbers and hyphens only", problem)
        self.assertEqual(self.parse("A" * 40)[:2], ((("A" * 40),), None))
        self.assertEqual(self.parse("A" * 41)[0], ())
        self.assertEqual(self.parse("a_b-1")[:2], (("A_B-1",), None))
        self.assertEqual(self.parse("-a1")[0], ())                         # must start with a letter or number

    def test_nothing_is_ever_partially_accepted(self):
        for text in ("a1, b2, c3, d4", "a1, b*2", "a1 b2", "a1, b2, c 3"):
            self.assertEqual(self.parse(text)[0], (), text)


# --- the Admin panel --------------------------------------------------------------------------

def panel_app(path, key, filters, state):
    """AppTest.from_function body: renders the queue panel against a fixture DB."""
    import test_queue as t
    import queue_view as qv
    import queue_worker as qw_
    import streamlit as st_
    fx = t.FIXTURES[path]
    panel = qv.Panel(key=key, noun="regional review", scope=[("Region", "Region III"), ("Fiscal year", "2024")],
                     deps=fx.deps, filters=filters,
                     open_results=lambda: st_.session_state.__setitem__("opened_results", True), widget_prefix="q")
    qv.render_queue(panel)


FIXTURES: dict = {}


class PanelCase(WorkerCase):
    def setUp(self):
        super().setUp()
        import streamlit as st
        st.cache_data.clear()
        qw_view = __import__("queue_view")
        qw_view._DUE_CACHE.clear()

    def render(self, fx, contracts=None):
        from streamlit.testing.v1 import AppTest
        FIXTURES[str(self.path)] = fx
        self.addCleanup(FIXTURES.clear)
        at = AppTest.from_function(panel_app, args=(str(self.path), KEY, {"region": "Region III", "year": "2024"}, None),
                                   default_timeout=30)
        at.run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        return at

    def labels(self, at):
        return [b.label for b in at.button]

    def stage_text(self, at):
        return all_text(at)


class PanelTests(PanelCase):
    def test_scope_and_explanation_come_before_any_action(self):
        at = self.render(Fixture(self.path, contracts=()))
        text = all_text(at)
        self.assertIn("Scope for this check", text)
        self.assertIn("**Region** · Region III", text)
        self.assertIn("**Fiscal year** · 2024", text)
        self.assertIn("No saved queue for this scope yet", text)
        self.assertIn("how many there are is not known until that finishes", text)
        self.assertIn("There is no unattended scheduler", text)
        self.assertIn("Start regional review", self.labels(at))
        for hidden in ("Pause", "Open results"):
            self.assertNotIn(hidden, self.labels(at))
        self.assertFalse([l for l in self.labels(at) if l.startswith("Retry")])

    def test_buttons_are_never_inert(self):
        fx = Fixture(self.path)
        fx.fail = {"B": RuntimeError("boom")}
        at = self.render(fx)
        at.button(key="q_run").click()
        at.run()
        fx.wait() if fx.job else None
        deadline = time.time() + 10
        while qw.REGISTRY.get(KEY) and qw.REGISTRY[KEY].alive and time.time() < deadline:
            time.sleep(.05)
        at.run()
        self.assertEqual(fx.saves, ["A", "C"])
        self.assertIn("Finished with problems.", all_text(at))
        retry = [b for b in at.button if b.label.startswith("Retry")]
        self.assertEqual([b.label for b in retry], ["Retry 1 contract"])
        fx.fail = {}
        retry[0].click()
        at.run()
        deadline = time.time() + 10
        while qw.REGISTRY[KEY].alive and time.time() < deadline:
            time.sleep(.05)
        at.run()
        self.assertEqual(fx.saves, ["A", "C", "B"])                        # only the failed one was rerun
        self.assertIn("Finished · all 3 contracts saved.", all_text(at))
        self.assertFalse([b for b in at.button if b.label.startswith("Retry")])

    def test_partial_failure_is_not_labelled_success(self):
        fx = Fixture(self.path)
        fx.fail = {"B": RuntimeError("boom")}
        fx.run()
        at = self.render(fx)
        text = all_text(at)
        self.assertIn("Finished with problems.", text)
        self.assertNotIn("Finished · all", text)
        self.assertIn("2 saved · 1 failed · 0 saved but need a retry · 0 still pending", text)
        self.assertEqual({m.label: m.value for m in at.metric}, {"Saved": "2", "Failed": "1", "Need a retry": "0", "Pending": "0"})
        self.assertIn("Preserved · 0 earlier reviews kept for failed contracts · 0 manual decisions", text)
        self.assertIn("Contracts that need attention (1)", [e.label for e in at.expander])

    def test_retry_required_results_are_not_counted_as_clean_success(self):
        fx = Fixture(self.path)
        fx.categories = {"C": RETRY}
        fx.run()
        at = self.render(fx)
        self.assertIn("Finished with problems.", all_text(at))
        self.assertEqual(at.metric[2].value, "1")
        self.assertIn("Retry 1 contract", self.labels(at))

    def test_paused_interrupted_and_stopped_states_follow_the_worker(self):
        fx = Fixture(self.path)
        fx.gate = {"B": Event()}
        fx.start()
        fx.reached.setdefault("B", Event()).wait(10)
        at = self.render(fx)
        self.assertIn("Regional review running", all_text(at))
        self.assertIn("**Analysing photos · now**", all_text(at))
        self.assertIn("1 of 3 contracts reviewed · now on B", str(at.get("progress")[0].proto.text))
        self.assertIn("Pause", self.labels(at))
        self.assertTrue(at.button(key="q_run").disabled)
        at.button(key="q_pause").click()
        at.run()
        self.assertIn("Pause requested · finishing B", all_text(at))
        self.assertTrue(at.button(key="q_pause").disabled)
        fx.gate["B"].set()
        fx.wait()
        at.run()
        text = all_text(at)
        self.assertIn("Regional review paused.", text)
        self.assertIn("2 of 3 contracts reviewed", text)
        self.assertIn("manual decisions are kept", text)
        self.assertIn("Resume regional review", self.labels(at))
        self.assertNotIn("Pause", self.labels(at))
        with closing(fx.open_db()) as db, db:
            db.execute("UPDATE queue_runs SET state='running', owner=?", (os.getpid(),))
        qw.REGISTRY.clear()
        at.run()
        text = all_text(at)
        self.assertIn("Regional review was interrupted.", text)
        self.assertNotIn("Regional review paused.", text)
        self.assertIn("Resume regional review", self.labels(at))
        with closing(fx.open_db()) as db, db:
            db.execute("UPDATE queue_runs SET state='stopped', error='Stopped after 5 contracts in a row failed or returned no usable analysis.'")
        at.run()
        self.assertEqual(len(at.error), 1)
        self.assertIn("Stopped after 5 contracts in a row failed or returned no usable analysis.", all_text(at))

    def test_running_elsewhere_cannot_be_started_or_paused_here(self):
        fx = Fixture(self.path)
        with closing(fx.open_db()) as db, db:
            now = qw.now()
            db.execute("INSERT INTO queue_runs VALUES (?,?,?,?,?,?,?,?)", (KEY, qw.RUNNING, qw.PHOTOS, "A", None, os.getpid() + 1, now, now))
        at = self.render(fx)
        self.assertIn("running in another dashboard process", all_text(at))
        self.assertTrue(at.button(key="q_run").disabled)
        self.assertNotIn("Pause", self.labels(at))

    def test_retry_is_not_offered_while_another_process_owns_the_run(self):
        fx = Fixture(self.path)
        fx.fail = {"B": RuntimeError("boom")}
        fx.run()
        with closing(fx.open_db()) as db, db:
            now = qw.now()
            db.execute("INSERT OR REPLACE INTO queue_runs VALUES (?,?,?,?,?,?,?,?)", (KEY, qw.RUNNING, qw.PHOTOS, "A", None, os.getpid() + 1, now, now))
        at = self.render(fx)
        self.assertFalse([l for l in self.labels(at) if l.startswith("Retry")])

    def test_open_results_only_with_saved_results_and_it_works(self):
        fx = Fixture(self.path)
        at = self.render(fx)
        self.assertNotIn("Open results", self.labels(at))
        fx.run()
        at = self.render(fx)
        self.assertIn("Open results", self.labels(at))
        at.button(key="q_open").click()
        at.run()
        self.assertTrue(at.session_state["opened_results"])

    def test_due_count_is_shown_when_a_queue_exists(self):
        fx = Fixture(self.path)
        fx.run()
        at = self.render(fx)
        self.assertIn("Saved queue · 3 contracts · 3 saved · 0 failed", all_text(at))
        self.assertIn("due for a recheck now", all_text(at))
        self.assertIn("Check new and due contracts", self.labels(at))

    def test_fetching_stage_says_the_count_is_unknown(self):
        fx = Fixture(self.path, contracts=())
        gate = Event()
        fx.projects = {}
        original = fx.deps

        def slow():
            deps = original()
            real = deps.fetch_projects
            deps.fetch_projects = lambda f: (gate.wait(10), real(f))[1]
            return deps
        fx.deps = slow
        fx.start(filters={"region": "Region III"}, fetch=True)
        deadline = time.time() + 5
        while qw.REGISTRY[KEY].stage != qw.FETCHING and time.time() < deadline:
            time.sleep(.02)
        at = self.render(fx)
        text = all_text(at)
        self.assertIn("**Fetching regional records · now**", text)
        self.assertIn("the number of contracts is not known until the fetch finishes", text)
        self.assertEqual(len(at.get("progress")), 0)                       # no fabricated denominator
        gate.set()
        fx.wait()


class OpenResultsWiringTests(unittest.TestCase):
    """Open results must actually navigate: a click inside the 3-second fragment needs a full rerun to switch pages."""

    def test_open_results_switches_to_regional_review_with_the_scope_preselected(self):
        import streamlit as st
        from streamlit.testing.v1 import AppTest
        import test_regional_view as trv
        st.cache_data.clear()
        with tempfile.TemporaryDirectory(dir=HERE) as tmp:
            db = Path(tmp) / "open.db"
            import app
            items = trv.fixtures()
            signature = app.current_model_signature()
            for item in items:
                item["review_provenance"] = {"models": signature}
            trv.seed(db, [i for i in items if i["contractId"] != "FX-ELSEWHERE"])
            with patch.dict(os.environ, {"PROJECTWATCH_PHOTO_REVIEW_DB": str(db)}):
                at = AppTest.from_file(str(HERE / "app.py"), default_timeout=60)
                at.session_state["primary_navigation"] = "Admin"
                at.session_state["admin_section"] = "Regional reviews"
                at.run()
                at.selectbox(key="regional_region").set_value("Region III")
                at.run()
                at.selectbox(key="regional_year").set_value("2024")
                at.run()
                self.assertIn("Open results", [b.label for b in at.button])
                at.button(key="regional_queue_open").click()
                at.run()
                self.assertFalse(at.exception, [e.value for e in at.exception])
                self.assertEqual(at.session_state["primary_navigation"], "Regional review")
                at.run()
                self.assertEqual(at.session_state["dashboard_region"], "Region III")
                self.assertIn("What these figures cover", [s.value for s in at.subheader])


class AppWiringTests(unittest.TestCase):
    """The real Admin page shows the scope before the action and keeps the old combined actions gone."""

    def test_admin_regional_reviews_shows_scope_then_start(self):
        import streamlit as st
        from streamlit.testing.v1 import AppTest
        st.cache_data.clear()
        with tempfile.TemporaryDirectory(dir=HERE) as tmp:
            with patch.dict(os.environ, {"PROJECTWATCH_PHOTO_REVIEW_DB": str(Path(tmp) / "a.db")}):
                at = AppTest.from_file(str(HERE / "app.py"), default_timeout=60)
                at.session_state["primary_navigation"] = "Admin"
                at.session_state["admin_section"] = "Regional reviews"
                at.run()
                at.selectbox(key="regional_region").set_value("Region III")
                at.run()
                self.assertFalse(at.exception, [e.value for e in at.exception])
                text = all_text(at)
                self.assertIn("Scope for this check", text)
                self.assertIn("Region III", text)
                self.assertLess(text.index("Scope for this check"), text.index("There is no unattended scheduler") + 1)
                self.assertIn("Start regional review", [b.label for b in at.button])

    def test_admin_contract_ids_show_duplicate_note_and_reject_spaces(self):
        import streamlit as st
        from streamlit.testing.v1 import AppTest
        st.cache_data.clear()
        with tempfile.TemporaryDirectory(dir=HERE) as tmp:
            with patch.dict(os.environ, {"PROJECTWATCH_PHOTO_REVIEW_DB": str(Path(tmp) / "a.db")}):
                at = AppTest.from_file(str(HERE / "app.py"), default_timeout=60)
                at.session_state["primary_navigation"] = "Admin"
                at.session_state["admin_section"] = "Contract reviews"
                at.run()
                at.text_area(key="admin_contract_ids").input("24CK 0181")
                at.run()
                self.assertIn("Separate contract IDs with commas or new lines", all_text(at))
                at.text_area(key="admin_contract_ids").input("a1, A1, b2, c3, d4")
                at.run()
                text = all_text(at)
                self.assertIn("A1 was entered 2 times and is reviewed once.", text)
                self.assertIn("Remove 1 to continue: D4.", text)


if __name__ == "__main__":
    unittest.main()
