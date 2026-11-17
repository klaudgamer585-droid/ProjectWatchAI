"""Admin queue panel (regional reviews and category reruns): scope, real stage, honest outcome, working actions.

Everything shown comes from ``queue_worker`` (stored queue rows, stored failures, the stored/live run). Nothing
is estimated: a total is shown only when the queue exists, a stage only while a worker is in it, and Pause,
Paused, Resume and Interrupted follow the worker, not the last button clicked. Every button here does something.
"""
import time
from contextlib import closing
from dataclasses import dataclass
from typing import Callable, Optional

import pandas as pd
import streamlit as st
from streamlit.errors import StreamlitAPIException

import evidence_language as language
import queue_worker as qw
from ui import empty_state, error_state, escape_markdown, freshness, notice

STAGE_ORDER = (qw.FETCHING, qw.PHOTOS, qw.ANALYSING, qw.SAVING)
CHECK_EXPLANATION = (
    "Checking fetches the current list of matching contracts, adds any that are not in the saved queue, and reviews saved "
    "contracts again when they are due: a week after their last review (a day for incomplete or photo-less contracts), or "
    "sooner if they were made with earlier rules or detectors. Rechecks happen only when you start a check. There is no "
    "unattended scheduler, so nothing is rechecked while no one has started a check."
)
_DUE_CACHE: dict = {}


@dataclass
class Panel:
    key: str                                   # regional_queue.filters value
    noun: str                                  # "regional review" | "category rerun"
    scope: list                                # [(label, value)] shown before anything can start
    deps: Callable[[], qw.Deps]
    filters: Optional[dict] = None             # set for regional reviews: the check fetches records for these
    prepare: Optional[Callable] = None         # called before a fresh (not resumed) start, e.g. to seed a rerun queue
    open_results: Optional[Callable] = None    # on_click callback; shown only when saved results exist
    start_label: str = "Start regional review"
    resume_label: str = "Resume regional review"
    check_label: str = "Check new and due contracts"
    disabled_reason: Optional[str] = None      # why starting is not possible (e.g. an empty category)
    widget_prefix: str = "queue"


def due_cached(panel, deps, db, totals):
    """Due count is cheap but not free (it re-reads every saved review), so the 3-second refresh reuses it for 30 s."""
    signature = (totals["total"], totals["saved"], totals["pending"])
    cached = _DUE_CACHE.get(panel.key)
    if cached and cached[1] == signature and time.monotonic() - cached[0] < 30:
        return cached[2]
    value = qw.due_count(db, panel.key, deps)
    _DUE_CACHE[panel.key] = (time.monotonic(), signature, value)
    return value


def render_scope(panel, totals, due, running):
    with st.container(border=True):
        st.markdown("**Scope for this check**")
        for label, value in panel.scope:
            st.markdown(f"**{escape_markdown(label)}** · {escape_markdown(str(value))}")
        if not totals["total"]:
            st.caption("No saved queue for this scope yet. The first check fetches the matching contracts from the source; "
                       "how many there are is not known until that finishes.")
        else:
            st.caption(f"Saved queue · {totals['total']:,} contracts · {totals['saved']:,} saved · "
                       f"{totals['failed']:,} failed · {totals['retry_required']:,} saved but need a retry · "
                       f"{totals['pending']:,} pending" + (f" · {due:,} due for a recheck now" if due is not None else ""))
        st.caption(CHECK_EXPLANATION)


def stage_line(stage):
    parts = []
    for name in STAGE_ORDER:
        parts.append(f"**{name} · now**" if name == stage else name)
    return " → ".join(parts)


def render_running(panel, totals, status):
    stage, current = status.get("stage"), status.get("current")
    stopping = status["state"] == qw.PAUSE_REQUESTED
    done = totals["saved"] + totals["retry_required"]
    if stopping:
        freshness("Pause requested · finishing" + (f" {current}" if current else " the current contract") + " ·",
                  "Saved work is kept.", running=True)
    else:
        freshness(f"{panel.noun.capitalize()} running ·", stage or "starting", running=True)
    st.markdown(stage_line(stage))
    if totals["total"] and stage != qw.FETCHING:
        st.progress(done / totals["total"], text=reviewed_text(totals) + (f" · now on {current}" if current else ""))
    else:
        st.caption("Fetching regional records · the number of contracts is not known until the fetch finishes."
                   if stage == qw.FETCHING else "Starting.")
    if totals["failed"]:
        st.caption(f"{totals['failed']:,} contract{'' if totals['failed'] == 1 else 's'} failed so far; the run continues with the rest.")


def reviewed_text(totals):
    """'8 of 10 contracts reviewed' counts saved and need-a-retry together; the retry share is named so the metrics add up."""
    done = totals["saved"] + totals["retry_required"]
    text = f"{done:,} of {totals['total']:,} contracts reviewed"
    return text + (f" ({totals['retry_required']:,} need a retry)" if totals["retry_required"] else "")


def render_state_notice(panel, totals, status):
    state = status["state"]
    if state == qw.PAUSED:
        notice(f"{panel.noun.capitalize()} paused.",
               f"{reviewed_text(totals)}. Saved reviews and manual decisions are kept. Resume continues with the "
               f"{totals['pending']:,} pending contract{'' if totals['pending'] == 1 else 's'}.",
               icon=":material/pause_circle:")
    elif state == qw.INTERRUPTED:
        notice(f"{panel.noun.capitalize()} was interrupted.",
               "It stopped without being paused (the dashboard may have restarted). Saved reviews are kept"
               + (f"; it was on {status['current']}" if status.get("current") else "")
               + f". Resume continues with the {totals['pending']:,} pending contract{'' if totals['pending'] == 1 else 's'}.",
               icon=":material/error_outline:")
    elif state == qw.ELSEWHERE:
        notice(f"{panel.noun.capitalize()} is running in another dashboard process.",
               "It can be paused only from that process. Progress updates as it saves.", icon=":material/sync:")
        if totals["total"]:
            done = totals["saved"] + totals["retry_required"]
            st.progress(done / totals["total"], text=reviewed_text(totals) + (f" · now on {status['current']}" if status.get("current") else ""))
    elif state == qw.STOPPED:
        error_state(f"{panel.noun.capitalize()} stopped.", escape_markdown(status.get("error") or "The run stopped."),
                    kept=f"{totals['saved'] + totals['retry_required']:,} contract{'' if totals['saved'] + totals['retry_required'] == 1 else 's'} "
                         f"reviewed and {totals['pending']:,} still pending. Nothing saved was changed.")


def render_outcome(panel, totals, status):
    """Final counts. The headline is plain only when nothing failed, needs a retry or is still pending."""
    if not totals["total"] or status["state"] in (qw.RUNNING, qw.PAUSE_REQUESTED):
        return
    clean = not (totals["failed"] or totals["retry_required"] or totals["pending"])
    if status["state"] == qw.FINISHED:
        if clean:
            notice(f"Finished · all {totals['total']:,} contracts saved.", "No contract failed or needs a retry.",
                   icon=":material/task_alt:")
        else:
            notice("Finished with problems.",
                   f"{totals['saved']:,} saved · {totals['failed']:,} failed · {totals['retry_required']:,} saved but need a retry"
                   f" · {totals['pending']:,} still pending. Use Retry to run only the contracts that need it.",
                   icon=":material/warning:")
    columns = st.columns(4)
    columns[0].metric("Saved", f"{totals['saved']:,}", help="Reviews saved with usable photo analysis.")
    columns[1].metric("Failed", f"{totals['failed']:,}", help="Contracts whose review failed. Their earlier review, if any, is kept.")
    columns[2].metric("Need a retry", f"{totals['retry_required']:,}",
                      help="Saved, but photo analysis was unavailable, so the result is provisional until retried.")
    columns[3].metric("Pending", f"{totals['pending']:,}", help="Not yet reviewed in this queue.")
    st.caption(f"Preserved · {totals['earlier_reviews_kept']:,} earlier review{'' if totals['earlier_reviews_kept'] == 1 else 's'} "
               f"kept for failed contracts · {totals['manual_decisions_kept']:,} manual decision"
               f"{'' if totals['manual_decisions_kept'] == 1 else 's'} in this scope kept as recorded.")


def render_failures(panel, db):
    failed = qw.failures(db, panel.key)
    retry = db.execute(f"""SELECT a.contract_id, a.category FROM regional_queue q JOIN contract_assessments a
                           ON a.contract_id = q.contract_id WHERE q.filters = ? AND q.done = 1 AND {qw._RETRY_SQL}
                           ORDER BY a.contract_id""", (panel.key,)).fetchall()
    if not (failed or retry):
        return
    with st.expander(f"Contracts that need attention ({len(failed) + len(retry):,})"):
        if failed:
            st.caption("Failed · the review could not be completed. Any earlier saved review is unchanged.")
            st.dataframe(pd.DataFrame(failed, columns=["Contract", "What failed", "When", "Attempts"]).assign(
                When=lambda frame: frame["When"].map(language.format_timestamp)), hide_index=True)
        if retry:
            st.caption("Saved but need a retry · photo analysis was unavailable, so these results are provisional.")
            st.dataframe(pd.DataFrame([(c, language.category_label(category)) for c, category in retry],
                                      columns=["Contract", "Saved result"]), hide_index=True)


def refresh():
    """Show the new state at once. Inside the Admin fragment only the panel reruns; elsewhere the page does."""
    try:
        st.rerun(scope="fragment")
    except StreamlitAPIException:
        st.rerun()


def begin(panel, *, fetch, fresh, only=None):
    deps = panel.deps()
    job = qw.Job(panel.key)
    job.only = only
    started = qw.start(panel.key, deps, filters=panel.filters, fetch=fetch, job=job,
                       prepare=panel.prepare if fresh and panel.prepare else None)
    if started is None:
        st.toast("Already running for this scope.", icon=":material/sync:")


def retry_failed(panel):
    deps = panel.deps()
    with closing(deps.open_db()) as db:
        ids = qw.scoped_retry(db, panel.key)
    if ids:
        begin(panel, fetch=False, fresh=False, only=set(ids))


def pause(panel):
    qw.request_pause(panel.key, panel.deps())


def render_queue(panel):
    deps = panel.deps()
    with closing(deps.open_db()) as db:
        totals = qw.counts(db, panel.key)
        status = qw.run_status(db, panel.key)
        due = due_cached(panel, deps, db, totals) if totals["total"] else None
        render_scope(panel, totals, due, status["state"] in (qw.RUNNING, qw.PAUSE_REQUESTED))
        state = status["state"]
        running = state in (qw.RUNNING, qw.PAUSE_REQUESTED)
        if running:
            render_running(panel, totals, status)
        else:
            render_state_notice(panel, totals, status)
        render_outcome(panel, totals, status)
        render_failures(panel, db)
        results_exist = bool(totals["saved"] + totals["retry_required"])

    prefix = panel.widget_prefix
    resumable = totals["pending"] > 0 and state in (qw.PAUSED, qw.INTERRUPTED, qw.STOPPED, qw.IDLE, qw.FINISHED)
    label = (panel.start_label if not totals["total"] else panel.resume_label if resumable else panel.check_label)
    needs_attention = totals["failed"] + totals["retry_required"]
    first = st.container()
    second, third, fourth = st.columns(3)
    if first.button(label, key=f"{prefix}_run", type="primary", disabled=running or state == qw.ELSEWHERE or bool(panel.disabled_reason)):
        begin(panel, fetch=not resumable, fresh=not resumable)
        refresh()
    if panel.disabled_reason:
        st.caption(panel.disabled_reason)
    if running:
        if second.button("Pause", key=f"{prefix}_pause", disabled=state == qw.PAUSE_REQUESTED):
            pause(panel)
            refresh()
        if state == qw.PAUSE_REQUESTED:
            second.caption("Pause requested")
    if not running and state != qw.ELSEWHERE and needs_attention:
        if third.button(f"Retry {needs_attention:,} contract{'' if needs_attention == 1 else 's'}", key=f"{prefix}_retry",
                        help="Runs only the failed and need-a-retry contracts in this scope. Everything else is left as saved."):
            retry_failed(panel)
            refresh()
    if results_exist and panel.open_results:
        if fourth.button("Open results", key=f"{prefix}_open",
                         help="Opens the saved results for this scope in Regional review."):
            panel.open_results()
            st.rerun()      # a full rerun: the page switch happens on the next run, not inside this fragment
