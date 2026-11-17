"""Regional review (saved reviews): coverage, definitions, drilldown and highlights.

Plan §5C. The page leads with what the figures cover (region, year, how many saved reviews, when they were
saved, how many are out of date or lack usable evidence), then defines each count, then lets every count and
chart category open the saved contracts behind it. Nothing here recomputes an evidence rule or a score:
categories, flags and scores come from the saved records and ``contractor_performance`` unchanged.

Pure helpers (no Streamlit state) come first so they can be tested directly. ``render_*`` functions take a
``Services`` bundle for the few things that live in app.py (details view, opening a contract, scoring table,
highlight generation), so this module never imports app.py.
"""
import csv
import io
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import streamlit as st

import evidence_language as language
from contract_assessment import NEUTRAL_REVIEW_CATEGORY, reported_day
from ui import empty_state, error_state, escape_markdown, notice

PHILIPPINE_TIME = timezone(timedelta(hours=8), "PHT")
SMALL_SAMPLE = 10        # display convention, not a statistical threshold: fewer saved reviews than this is "small"
SMALL_SCORED = 3         # contractors with fewer scored contracts than this are marked as small samples
ANALYSIS_UNAVAILABLE = "Photo analysis unavailable · retry needed"
EVIDENCE_UNAVAILABLE = "Photo evidence unavailable"
NO_PHOTOS = "No photo evidence"
INCONCLUSIVE = {"Photos inconclusive", NEUTRAL_REVIEW_CATEGORY}
FOCUS_KEY = "regional_focus"
FOCUS_SCOPE = "regional_focus_scope"
UNREPORTED = "Unreported contractor"

SCOPE_STATEMENT = (
    "These figures describe only the saved reviews listed below. They are not a census of the region: contracts "
    "that were never reviewed, or whose review is still queued, are not counted, and each saved review reflects the "
    "evidence on the day it was saved."
)
DEFINITIONS = {
    "reviewed": "Reviewed contracts · distinct contracts with a saved photo evidence review in this view. A contract is "
                "counted once, however many times it was reviewed.",
    "contractors": "Contractors · distinct reported contractor names in those saved reviews (spacing and capitals "
                   "ignored). Contracts with no reported contractor are counted separately, not as a contractor.",
    "flagged": "Flagged contracts · reviews with at least one record check or evidence note. A flag asks for a "
               "second look; it is not a finding of wrongdoing.",
}


# ---------------------------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------------------------

def contractor_key(name):
    return " ".join((name or UNREPORTED).split()).casefold()


def category_of(entry):
    return (entry["record"].get("assessment") or {}).get("completion_evidence_category")


def flags_of(entry):
    return list(dict.fromkeys((entry["record"].get("assessment") or {}).get("flags") or ()))


def is_manual(entry):
    return language.is_manual(entry["record"].get("assessment") or {})


def has_budget(entry):
    value = ((entry["record"].get("budgetPHP") or {}).get("Reported project budget"))
    try:
        return value not in (None, "") and float(str(value)) == float(str(value))  # False for NaN
    except ValueError:
        return False


def missing_completion(entry):
    return reported_day(((entry["record"].get("dates") or {}).get("Reported completion"))) is None


def _status(entry):
    return entry["record"].get("status") or "Unknown"


def focus_matches(entry, focus):
    """Whether one included saved review belongs to a drilldown. Every count on the page uses this one function."""
    if focus in (None, "all"):
        return True
    kind, _, value = focus.partition(":")
    category = category_of(entry)
    if kind == "flagged":
        return bool(flags_of(entry))
    if kind == "manual":
        return is_manual(entry)
    if kind == "stale":
        return bool(entry.get("stale"))
    if kind == "unavailable":
        return category in {ANALYSIS_UNAVAILABLE, EVIDENCE_UNAVAILABLE}
    if kind == "no_photos":
        return category == NO_PHOTOS
    if kind == "inconclusive":
        return category in INCONCLUSIVE
    if kind == "no_completion_date":
        return missing_completion(entry)
    if kind == "no_budget":
        return not has_budget(entry)
    if kind == "category":
        return category == value
    if kind == "status":
        return _status(entry) == value
    if kind == "contract":
        return entry["record"]["contractId"] == value
    return False


def focus_label(focus):
    """Reader-facing name of a drilldown, used for the visible filter chip."""
    kind, _, value = (focus or "all").partition(":")
    fixed = {"all": "All included saved reviews", "flagged": "Flagged contracts",
             "manual": "Contracts with a manual reviewer decision", "stale": "Reviews that are out of date",
             "unavailable": "Photo evidence or analysis unavailable", "no_photos": "No photos in the source record",
             "inconclusive": "Photos inconclusive or further review needed",
             "no_completion_date": "No reported completion date", "no_budget": "No reported project budget"}
    if kind in fixed:
        return fixed[kind]
    if kind == "category":
        return f"Evidence category · {language.category_label(value)}"
    if kind == "status":
        return f"Reported status · {value}"
    if kind == "contract":
        return f"Contract · {value}"
    return "All included saved reviews"


def parse_time(value):
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=PHILIPPINE_TIME)


def coverage(entries, queue=None):
    """Counts that frame the page. ``queue`` is {'completed': n, 'pending': n} or None when no queue is recorded."""
    times = [moment for entry in entries if (moment := parse_time(entry.get("reviewed_at")))]
    contractors = {contractor_key(entry["record"].get("contractor")) for entry in entries
                   if (entry["record"].get("contractor") or "").strip()}
    return {
        "included": len(entries),
        "first": min(times) if times else None, "last": max(times) if times else None,
        "stale": sum(bool(entry.get("stale")) for entry in entries),
        "analysis_unavailable": sum(category_of(e) == ANALYSIS_UNAVAILABLE for e in entries),
        "evidence_unavailable": sum(category_of(e) == EVIDENCE_UNAVAILABLE for e in entries),
        "no_photos": sum(category_of(e) == NO_PHOTOS for e in entries),
        "inconclusive": sum(category_of(e) in INCONCLUSIVE for e in entries),
        "manual": sum(is_manual(e) for e in entries),
        "flagged": sum(bool(flags_of(e)) for e in entries),
        "contractors": len(contractors),
        "without_contractor": sum(not (e["record"].get("contractor") or "").strip() for e in entries),
        "no_budget": sum(not has_budget(e) for e in entries),
        "no_completion_date": sum(missing_completion(e) for e in entries),
        "queue": queue, "small_sample": len(entries) < SMALL_SAMPLE,
    }


def unavailable_total(summary):
    return summary["analysis_unavailable"] + summary["evidence_unavailable"]


def date_range_text(summary):
    if not summary["first"]:
        return "Dates unavailable"
    first, last = summary["first"].astimezone(PHILIPPINE_TIME).date(), summary["last"].astimezone(PHILIPPINE_TIME).date()
    show = lambda day: language.format_timestamp(day.isoformat())
    return show(first) if first == last else f"{show(first)} to {show(last)}"


def category_counts(entries):
    return Counter(category_of(entry) for entry in entries)


def status_counts(entries):
    return Counter(_status(entry) for entry in entries)


def focus_choices(entries):
    """(focus id, label with count) for every drilldown that has at least one record, in a stable order."""
    summary = coverage(entries)
    fixed = [("all", summary["included"]), ("flagged", summary["flagged"]), ("manual", summary["manual"]),
             ("stale", summary["stale"]), ("unavailable", unavailable_total(summary)), ("no_photos", summary["no_photos"]),
             ("inconclusive", summary["inconclusive"]), ("no_completion_date", summary["no_completion_date"]),
             ("no_budget", summary["no_budget"])]
    choices = [(focus, count) for focus, count in fixed if count or focus == "all"]
    choices += [(f"category:{raw}", count) for raw, count in sorted(category_counts(entries).items(), key=lambda i: (-i[1], str(i[0])))
                if raw is not None]
    choices += [(f"status:{name}", count) for name, count in sorted(status_counts(entries).items(), key=lambda i: (-i[1], i[0]))]
    return [(focus, f"{focus_label(focus)} · {count:,}") for focus, count in choices]


def table_rows(entries):
    rows = []
    for entry in entries:
        record, assessment = entry["record"], entry["record"].get("assessment") or {}
        rows.append({
            "Contract": record["contractId"], "Project": record.get("description") or "",
            "Contractor": record.get("contractor") or UNREPORTED,
            "Reported status": record.get("status") or "Unavailable",
            "Photo assessment": language.category_label(category_of(entry)) if category_of(entry) else "No saved assessment",
            "Flags": len(flags_of(entry)), "Reviewer decision": "Yes" if is_manual(entry) else "No",
            "Review state": "Out of date" if entry.get("stale") else "Current",
            "Saved": language.format_timestamp(entry.get("reviewed_at")),
        })
    return rows


def _spreadsheet_safe(value):
    text = str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def records_csv(entries):
    """Every included record, one row each, so any aggregate can be traced outside the page. Formula-safe."""
    rows = table_rows(entries)
    for row, entry in zip(rows, entries):
        row["Stored category"] = category_of(entry) or ""
        row["Flag texts"] = " | ".join(language.flag_text(flag)[0] for flag in flags_of(entry))
    buffer = io.StringIO()
    if rows:
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows({key: _spreadsheet_safe(value) for key, value in row.items()} for row in rows)
    return buffer.getvalue()


def budget_summary(entries):
    """Reported project budget total and how many included reviews have no amount (unknown is not zero)."""
    from decimal import Decimal, InvalidOperation
    amounts = []
    for entry in entries:
        try:
            amount = Decimal(str((entry["record"].get("budgetPHP") or {}).get("Reported project budget")))
        except InvalidOperation:
            continue
        if amount.is_finite():
            amounts.append(amount)
    return {"total": sum(amounts) if amounts else None, "with_amount": len(amounts), "without_amount": len(entries) - len(amounts)}


# ---------------------------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------------------------

@dataclass
class Services:
    show_details: Callable[[dict], None]            # one contract's saved details (show_regional_contract_details)
    open_contract: Callable[[str], None]            # on_click callback: open the contract in Explore
    contractor_comparison: Callable[[list], None]   # the scoring table for the included records
    generate_highlights: Callable[[list], tuple]    # records -> (report or None, error text or None)
    format_peso: Callable[[object], str]
    go_admin: Callable[[], None]                    # on_click callback: Admin › Regional reviews
    model_name: str = ""


def set_focus(focus, announce=None):
    st.session_state[FOCUS_KEY] = focus
    if announce and focus != "all":
        # The list is further down the page than the count that was chosen, so say where the result went.
        st.toast(f"Showing {announce}. The contracts are listed under Supporting contracts.", icon=":material/filter_alt:")


def show_button(label, focus, key, *, active, help=None):
    """A tertiary button that opens the supporting contracts. The active one says so in words."""
    st.button(f"{label} · showing" if active else label, key=key, type="tertiary", help=help,
              icon=":material/filter_alt:" if active else ":material/arrow_downward:",
              on_click=set_focus, args=(focus, label), disabled=False)


def render_scope(region, year_text, summary, services):
    st.subheader("What these figures cover")
    with st.container(border=True):
        st.markdown(f"**{escape_markdown(region)} · {escape_markdown(year_text)}**")
        columns = st.columns(4)
        columns[0].metric("Saved reviews included", f"{summary['included']:,}",
                          help="Each included contract is counted once. Open the list below to see every one.")
        columns[1].metric("Saved between", date_range_text(summary),
                          help="Dates of the saved reviews and manual decisions included here (Philippine time).")
        columns[2].metric("Out of date", f"{summary['stale']:,}",
                          help="Saved with earlier assessment rules or detectors than the current ones. Rerun them in Admin.")
        unavailable = unavailable_total(summary)
        columns[3].metric("Evidence unavailable", f"{unavailable:,}",
                          help="Photo evidence or photo analysis could not be retrieved or run. "
                               "This does not show that work is absent.")
        st.caption(SCOPE_STATEMENT)
        queue = summary.get("queue")
        if queue:
            st.caption(f"Saved review queues for this region and year · {queue['completed']:,} "
                       f"contract{'' if queue['completed'] == 1 else 's'} reviewed · "
                       f"{queue['pending']:,} still queued and not included.")
        else:
            st.caption("No review queue is recorded for these reviews, so the share of the region reviewed is not known.")
        if summary["small_sample"]:
            notice(f"Small sample · {summary['included']:,} saved review{'' if summary['included'] == 1 else 's'}",
                   f"Fewer than {SMALL_SAMPLE} reviews are included. Read counts and scores as a short list of "
                   "contracts, not as a pattern for the region. This threshold is a display convention.",
                   icon=":material/info:")
        detail = []
        if summary["no_photos"]:
            detail.append(f"{summary['no_photos']:,} have no photos in the source record")
        if summary["evidence_unavailable"]:
            detail.append(f"{summary['evidence_unavailable']:,} have photo evidence that could not be retrieved")
        if summary["analysis_unavailable"]:
            detail.append(f"{summary['analysis_unavailable']:,} have photo analysis that failed (retry needed)")
        if summary["inconclusive"]:
            detail.append(f"{summary['inconclusive']:,} have photos that were not conclusive")
        if detail:
            st.caption("Evidence available · " + " · ".join(detail) + ". These are separate situations; none is evidence of unfinished work.")


def render_definitions(summary, focus):
    st.subheader("Reviewed contracts, contractors and flagged contracts")
    columns = st.columns(3)
    for column, (name, label, value, target, button) in zip(columns, (
            ("reviewed", "Reviewed contracts", summary["included"], "all", "Show all included reviews"),
            ("contractors", "Contractors", summary["contractors"], None, None),
            ("flagged", "Flagged contracts", summary["flagged"], "flagged", "Show flagged contracts"))):
        with column.container(border=True):
            st.metric(label, f"{value:,}")
            st.caption(DEFINITIONS[name].split(" · ", 1)[1])
            if target:
                show_button(button, target, f"regional_show_{name}", active=focus == target)
            else:
                if summary["without_contractor"]:
                    st.caption(f"{summary['without_contractor']:,} more contract"
                               f"{' has' if summary['without_contractor'] == 1 else 's have'} no reported contractor.")
                st.caption("Compared in the contractor table below.")


def render_categories(entries, focus):
    counts = category_counts(entries)
    if not counts:
        return
    st.subheader("Photo evidence categories")
    st.caption("Each row is one saved category. Select a row to list its contracts under Supporting contracts. "
               "Manual decisions appear under their decision category. Counts are saved reviews, not verdicts.")
    largest = max(counts.values())
    for index, (raw, count) in enumerate(sorted(counts.items(), key=lambda item: (-item[1], str(item[0])))):
        if raw is None:
            continue
        word, icon, color = language.KIND_STYLE[language.category_kind(raw)]
        target = f"category:{raw}"
        label = f"{word} · {language.category_label(raw)} · {count:,} contract{'' if count == 1 else 's'}"
        st.button(f"{label} · showing" if focus == target else label, key=f"regional_cat_{index}", type="tertiary",
                  icon=icon, on_click=set_focus, args=(target, label), use_container_width=True,
                  help="List the saved contracts in this category.")
        st.progress(count / largest)
    with st.expander("Table view of evidence categories"):
        st.dataframe([{"Evidence category": language.category_label(raw), "Kind": language.KIND_STYLE[language.category_kind(raw)][0],
                       "Contracts": count, "Stored value": raw} for raw, count in sorted(counts.items(), key=lambda i: (-i[1], str(i[0])))
                      if raw is not None], hide_index=True)


def render_facts(entries, focus, services):
    """Deterministic recorded facts. They never depend on the highlights model."""
    st.subheader("Recorded facts")
    st.caption("Counted directly from the included saved reviews. Each line opens its contracts.")
    statuses = status_counts(entries)
    for index, (name, count) in enumerate(sorted(statuses.items(), key=lambda i: (-i[1], i[0]))):
        target = f"status:{name}"
        show_button(f"Reported status · {name} · {count:,} contract{'' if count == 1 else 's'}", target,
                    f"regional_status_{index}", active=focus == target)
    budget = budget_summary(entries)
    if budget["total"] is None:
        st.caption("Reported project budget · no included review has a reported amount.")
    else:
        st.caption(f"Reported project budget · {services.format_peso(budget['total'])} across {budget['with_amount']:,} "
                   f"review{'' if budget['with_amount'] == 1 else 's'} with a reported amount. Reviews without an amount are not counted as zero.")
    if budget["without_amount"]:
        show_button(f"No reported project budget · {budget['without_amount']:,} contract"
                    f"{'' if budget['without_amount'] == 1 else 's'}", "no_budget", "regional_no_budget", active=focus == "no_budget")
    missing = sum(missing_completion(entry) for entry in entries)
    if missing:
        show_button(f"No reported completion date · {missing:,} contract{'' if missing == 1 else 's'}", "no_completion_date",
                    "regional_no_completion", active=focus == "no_completion_date")


def render_supporting(all_entries, focus, services):
    """The records behind whichever count was chosen, with the matching filter shown and clearable."""
    st.subheader("Supporting contracts", anchor="supporting-contracts")
    choices = focus_choices(all_entries)
    ids = [name for name, _ in choices]
    if focus not in ids:  # e.g. a highlighted contract: one record
        ids.append(focus)
        choices.append((focus, f"{focus_label(focus)} · {sum(focus_matches(e, focus) for e in all_entries):,}"))
    labels = dict(choices)
    focus = st.selectbox("Show supporting contracts for", ids, key=FOCUS_KEY, format_func=labels.get,
                 help="Every count on this page lists its contracts here. Choose a count from this list or select one above.")
    filtered = [entry for entry in all_entries if focus_matches(entry, focus)]
    chip, clear = st.columns([4, 1], vertical_alignment="center")
    chip.caption(f":material/filter_alt: Filter · {focus_label(focus)} · {len(filtered):,} of {len(all_entries):,} included saved reviews")
    if focus != "all":
        clear.button("Clear filter", key="regional_clear_focus", on_click=set_focus, args=("all",))
    if not filtered:
        empty_state("No included review matches this filter.", "Clear the filter to see every included saved review.",
                    icon=":material/filter_alt_off:")
        return
    with st.expander(f"Contracts ({len(filtered):,})", expanded=focus != "all"):
        key = f"regional_rows_{focus}_{len(filtered)}"
        table = st.dataframe(table_rows(filtered), hide_index=True, key=key, on_select="rerun", selection_mode="single-row")
        st.caption("Select a row to see that contract's saved details.")
        selected = table.selection.rows[0] if table.selection.rows else None
        if selected is not None:
            record = filtered[selected]["record"]
            with st.container(border=True):
                services.show_details(record)
                st.button("Open in Explore projects", key=f"regional_open_{record['contractId']}",
                          on_click=services.open_contract, args=(record["contractId"],), icon=":material/open_in_new:")
        st.download_button("Download these records (CSV)", records_csv(filtered), file_name="saved-regional-reviews.csv",
                           mime="text/csv", key="regional_csv",
                           help="One row per contract with its stored category and flags, so any count can be checked.")


HIGHLIGHTS_KEY = "regional_highlights_result"


def render_highlights(entries, report, scope_key, services):
    error = None
    st.subheader("Evidence highlights")
    st.caption("Optional. A local model picks, from the recorded facts, those that may be worth a manual look. It does not "
               "write new findings, scores or counts. Recorded facts above stay available whether or not it works.")
    if st.button("Generate evidence highlights", key="regional_generate_highlights", type="secondary",
                 disabled=not entries, help="Uses the local model configured by the operator."):
        with st.spinner("Generating evidence highlights…", show_time=True):
            report, error = services.generate_highlights([entry["record"] for entry in entries])
        st.session_state[HIGHLIGHTS_KEY] = (scope_key, report, error)
    result = st.session_state.get(HIGHLIGHTS_KEY)
    if result and result[0] == scope_key:
        _, report, error = result
    if error:
        error_state("Evidence highlights are unavailable.", escape_markdown(error),
                    kept="The recorded facts, counts and scores above are unaffected. Select Generate evidence highlights to retry.")
    if not report:
        if not error:
            st.caption("No highlights have been generated for these saved reviews yet.")
        return
    count = report.get("generated_for")
    made = parse_time(report.get("generated_at")) if report.get("generated_at") else None
    newest = coverage(entries)["last"]
    if (count is not None and count != len(entries)) or (made and newest and newest > made):
        notice("These highlights may be out of date",
               "Saved reviews changed after they were generated"
               + (f" (generated for {count:,} reviews; {len(entries):,} are included now)." if count is not None else "."),
               icon=":material/history:")
    if made and report.get("completed_batches"):
        st.caption(f"Generated {language.format_timestamp(report['generated_at'])}")
    selected = report.get("selected")
    if selected:
        st.caption("Each highlight is a recorded fact about one contract. Select a contract to list it under Supporting contracts.")
        known = {entry["record"]["contractId"] for entry in entries}
        for index, item in enumerate(selected):
            st.text(language.relabel_text(item["text"]))
            if item["contract"] in known:
                show_button(f"Show contract {item['contract']}", f"contract:{item['contract']}", f"regional_hl_{index}", active=False)
            else:
                st.caption(f"Contract {item['contract']} is no longer included in these saved reviews.")
    elif report.get("batch_summaries"):
        notice("Supporting contracts were not recorded for these older highlights",
               "Generate evidence highlights again to link each fact to its contract.", icon=":material/link_off:")
        for summary in report["batch_summaries"]:
            st.text(language.relabel_text(summary))
    elif not error:
        st.caption("The model selected no facts to highlight.")
    if report.get("errors") and not error:
        error_state(f"{len(report['errors']):,} highlight batch{'' if len(report['errors']) == 1 else 'es'} failed.",
                    "Highlights from completed batches are shown.", kept="All recorded facts are still shown.")


def render_operator_details(summary, report, services, filters):
    with st.expander("Operator details"):
        st.caption("Diagnostics for whoever runs the dashboard. Readers do not need these to interpret the page.")
        st.text(f"Highlights model · {services.model_name or 'not configured'}")
        if report:
            st.text(f"Highlight batches · {report.get('completed_batches', 0):,} of {report.get('total_batches', 0):,} completed")
            for failure in report.get("errors", []):
                st.text(f"Batch error · {failure}")
        queue = summary.get("queue") or {}
        st.text(f"Queue entries for this view · {queue.get('completed', 0):,} completed · {queue.get('pending', 0):,} pending")
        st.text(f"Filters · {json.dumps({k: v for k, v in filters.items() if v is not None}, sort_keys=True)}")
        st.button("Go to Admin · Regional reviews", key="regional_operator_admin", on_click=services.go_admin)
        st.json({key: (value.isoformat() if isinstance(value, datetime) else value) for key, value in summary.items()})


def render_regional_review(region, year_text, filters, entries, queue, report, services):
    """The saved Regional review page body, in plan order: coverage, definitions, categories and facts (each opens
    its contracts), contractor comparison, highlights, operator details."""
    scope_key = json.dumps([region, filters.get("year")])
    if st.session_state.get(FOCUS_SCOPE) != scope_key:
        st.session_state[FOCUS_SCOPE] = scope_key
        st.session_state[FOCUS_KEY] = "all"   # a filter from another region or year must not carry over
    focus = st.session_state.get(FOCUS_KEY, "all")
    summary = coverage(entries, queue)
    render_scope(region, year_text, summary, services)
    if focus != "all":
        st.markdown(f":material/filter_alt: **Filter active · {escape_markdown(focus_label(focus))}** · "
                    "[Go to the matching contracts](#supporting-contracts)")
    render_definitions(summary, focus)
    render_categories(entries, focus)
    render_facts(entries, focus, services)
    render_supporting(entries, focus, services)
    st.subheader("Contractor comparison")
    services.contractor_comparison([entry["record"] for entry in entries])
    render_highlights(entries, report, scope_key, services)
    result = st.session_state.get(HIGHLIGHTS_KEY)
    shown = result[1] if result and result[0] == scope_key and result[1] else report
    render_operator_details(summary, shown, services, filters)


def budget_gap_checkable(record):
    """True when the approved budget and award amount are both known, so an ABC–award gap can be judged at all.

    A missing amount is unknown, not zero: it is never read as a zero award or a zero budget.
    """
    from contract_assessment import amount
    budget = record.get("budgetPHP") or {}
    abc, award = amount(budget.get("Approved budget for the contract")), amount(budget.get("Award amount"))
    return abc is not None and abc > 0 and award is not None and award >= 0
