"""Presentation helpers for BantAIProyekto. No review or inference logic."""
import html
import re
from pathlib import Path

import streamlit as st

import evidence_language


BRAND_DIR = Path(__file__).resolve().parent / "static" / "brand"
# Supplied favicon (Elements/favicon_ant_face_64.png, copied by scripts/derive_brand_assets.py): the simplified
# ant face stays legible at 16-32 px where the full tile mark does not.
FAVICON = str(BRAND_DIR / "favicon-64.png")

# The three destinations. Titles are also the values of st.session_state["primary_navigation"].
DESTINATIONS = (
    ("Explore projects", "explore", ":material/travel_explore:",
     "Find a contract and inspect its dates, budget and photo evidence."),
    ("Regional review", "regional-review", ":material/map:",
     "Browse saved regional findings and contractor scores."),
    ("Admin", "admin", ":material/admin_panel_settings:",
     "Run reviews, manage saved evidence and inspect model status."),
)
_NAV_KEY = "primary_navigation"       # current destination title (read by callers and tests)
_NAV_SYNCED = "_primary_navigation_synced"  # last value written by navigation(); a difference is a request

# Colours, fonts, radii and semantic slots live in .streamlit/config.toml. This CSS holds only what the
# theme cannot express. Rules:
#  * no data-testid selectors; only Streamlit's documented .st* element classes, ARIA attributes
#    (aria-current, role) and native elements;
#  * identical in both themes. Where a custom colour must differ, light-dark() is used: Streamlit sets
#    `color-scheme` on the app root to match the active theme, so the browser picks the value without
#    st.context.theme (which can be wrong on first load);
#  * spacing uses the 4/8/12/16/24/32/48 px scale below.
_CSS = """
<style>
  .stApp {
    --bant-1:.25rem; --bant-2:.5rem; --bant-3:.75rem; --bant-4:1rem; --bant-6:1.5rem; --bant-8:2rem; --bant-12:3rem;
    --bant-focus: light-dark(#1F6F99, #6CC0E6);      /* 5.0:1 on cream / 9.3:1 on dark page */
    --bant-rule: light-dark(#8C857B, #7D766C);       /* = theme borderColor; >= 3:1 on each page */
    --bant-active-bg: light-dark(#E2DBD0, #34302B);  /* active nav row; text stays >= 4.5:1 on it */
    --bant-brand-red: #D93829;                       /* as supplied in Elements/*.svg; identity only */
    font-variant-numeric: tabular-nums;
  }
  .block-container {max-width:1240px; padding-top:4.5rem; padding-bottom:var(--bant-12);}
  h1 {letter-spacing:-.02em; line-height:1.15;}
  .stMarkdown p, .stCaption p {max-width:72ch;}

  /* Focus: one 2 px ring on every focusable thing, in a colour that passes 3:1 on all surfaces. */
  .stApp :focus-visible, .stApp details > summary:focus-visible {
    outline:2px solid var(--bant-focus) !important; outline-offset:2px; box-shadow:none;
  }
  .stApp [role="radiogroup"] label:has(input:focus-visible),
  .stApp label:has(input[type="checkbox"]:focus-visible) {
    outline:2px solid var(--bant-focus); outline-offset:2px; border-radius:.25rem;
  }

  /* Target size: 44 px for buttons, fields, choices, expanders and navigation rows. */
  .stButton button, .stFormSubmitButton button, .stDownloadButton button, .stLinkButton a,
  .stPopover button {min-height:44px;}
  .stApp [data-baseweb="select"] > div, .stApp [data-baseweb="input"] {min-height:44px;}
  .stApp [role="radiogroup"] {row-gap:0;}
  .stApp [role="radiogroup"] label, .stApp label:has(input[type="checkbox"]) {min-height:44px; align-items:center;}
  .stApp details > summary {min-height:44px; display:flex; align-items:center;}
  /* Header icon buttons (Expand/Collapse sidebar, Main menu) are 28 px natively; they keep their
     Streamlit aria-labels and get a 44 px hit area. */
  .stApp header button {min-width:44px; min-height:44px;}

  /* Tertiary buttons: Streamlit colours their hover/active text with primaryColor, which is a fill colour
     (dark stone) and fell to ~2.6:1 on the dark page. Keep the body text colour and add an underline. */
  .stApp button[kind="tertiary"], .stApp button[kind="tertiary"]:hover,
  .stApp button[kind="tertiary"]:active, .stApp button[kind="tertiary"]:focus-visible {color:inherit;}
  .stApp button[kind="tertiary"]:hover, .stApp button[kind="tertiary"]:active {text-decoration:underline; text-underline-offset:3px;}
  /* Disabled: still readable (Streamlit's faded text measured 2.45:1), marked by a dashed edge and cursor. */
  .stApp button:disabled, .stApp button:disabled:hover, .stApp button:disabled:active {
    cursor:not-allowed; color:light-dark(#736D66, #8F887E); border-style:dashed;
  }
  .stApp button:disabled p {color:inherit;}

  /* Navigation (native st.navigation): the current destination gets a filled row, a 4 px bar and bold
     text, not colour alone. aria-current="page" is set by Streamlit. Never brand red. */
  .stApp a[href][aria-current="page"] {
    background:var(--bant-active-bg); box-shadow:inset 4px 0 0 currentColor; font-weight:700;
  }
  .stApp a[href][aria-current="page"] span {font-weight:700;}
  .stApp li a[href] {min-height:44px; align-items:center;}  /* flex nav rows; inline text links unaffected */

  /* Brand */
  /* 7887 x 1100 viewBox -> 1.5rem tall wordmark, ~172 px wide. */
  .bant-wordmark {position:relative; height:1.5rem; aspect-ratio:7887/1100; max-width:100%; margin:0 0 var(--bant-1);}
  .bant-wordmark-ink, .bant-wordmark-ai {position:absolute; inset:0; width:100%; height:100%;}
  .bant-wordmark-ink {background:currentColor;
      -webkit-mask:url(app/static/brand/wordmark-ink.svg) center/contain no-repeat;
              mask:url(app/static/brand/wordmark-ink.svg) center/contain no-repeat;}
  .bant-brand {margin:var(--bant-4) 0 var(--bant-2);}
  .bant-brand-note {font-size:.875rem; margin-top:var(--bant-1);}
  .bant-breadcrumb {font-size:.875rem; margin:0 0 var(--bant-2);}
  .bant-footer {font-size:.875rem; border-top:1px solid var(--bant-rule); padding-top:var(--bant-4); margin-top:var(--bant-6);}
  .bant-chip {display:inline-block; padding:var(--bant-1) .625rem; border-radius:999px; margin:0 .375rem .375rem 0;
              font-size:.875rem; border:1px solid var(--bant-rule);}
  /* Explore active filters: a label, then one chip per filter. Chips wrap; long search text breaks. */
  .bant-filters {display:flex; flex-wrap:wrap; align-items:baseline; gap:var(--bant-2); font-size:.875rem;}
  .bant-filters-label {font-weight:600;}
  .bant-filters .bant-chip {margin:0 var(--bant-1) var(--bant-1) 0; overflow-wrap:anywhere; max-width:100%;}

  /* Regional category rows are buttons: read them left-aligned like a list, not centred. */
  .stApp [class*="st-key-regional_cat_"] button {justify-content:flex-start; text-align:left;}

  /* Review workspace: evidence | decision. Streamlit stacks columns only below 640 px, which leaves the decision
     column about 190 px wide at 1024 px, so this pair stacks below 1100 px (evidence stays first). */
  @media (max-width:1100px) {
    .stApp [class*="st-key-"][class*="_review_split"] .stHorizontalBlock {flex-direction:column;}
    .stApp [class*="st-key-"][class*="_review_split"] .stColumn {width:100% !important; flex:1 1 100% !important; min-width:0 !important;}
  }

  @media (max-width:760px) { .block-container {padding-left:var(--bant-4); padding-right:var(--bant-4); padding-top:3.5rem;} }

  /* Reduced motion: stop transitions and looping animation (spinners become static; their text and
     elapsed time still say that work is running). */
  @media (prefers-reduced-motion: reduce) {
    .stApp *, .stApp *::before, .stApp *::after {
      animation-duration:.001ms !important; animation-iteration-count:1 !important;
      transition-duration:.001ms !important; transition-delay:0s !important; scroll-behavior:auto !important;
    }
  }
</style>
"""


def apply_theme():
    st.html(_CSS)


def brand_logo():
    """Supplied marks only. Expanded: tile mark derived from Elements/Logo.png. Collapsed sidebar /
    small header: the supplied simplified ant face (Elements/favicon_ant_face_64.png).

    Framework limitation: st.logo renders alt="Logo"; the wordmark below carries the accessible name.
    """
    st.logo(str(BRAND_DIR / "mark-128.png"), icon_image=FAVICON, size="large")


def navigation(pages=None):
    """Native multipage navigation for the three destinations.

    `pages` maps each destination title to a callable that renders it. Returns the st.Page to run
    (call .run()). Without `pages`, returns the current title only (for callers that render themselves).

    Requests: set st.session_state["primary_navigation"] (tests, fixtures) or call go_to(); if it differs
    from what navigation() last wrote, the app switches to that destination before rendering.
    """
    brand_logo()
    renderers = pages or {}
    entries = {title: st.Page(renderers.get(title, lambda: None), title=title, icon=icon, url_path=path,
                              default=(path == "explore"), help=purpose)
               for title, path, icon, purpose in DESTINATIONS}
    current = st.navigation(list(entries.values()), position="sidebar")
    requested = st.session_state.get(_NAV_KEY)
    if requested in entries and requested != current.title and requested != st.session_state.get(_NAV_SYNCED):
        st.session_state[_NAV_SYNCED] = requested
        st.switch_page(entries[requested])
    st.session_state[_NAV_KEY] = st.session_state[_NAV_SYNCED] = current.title
    with st.sidebar:
        # Not a link: a full-page link would start a new session and drop drafts and filters.
        # Supplied wordmark (Elements/wordmark_dark.svg, split by scripts/derive_brand_assets.py): ink layer
        # painted in the theme text colour through a CSS mask, red "AI" layer as supplied.
        st.html('<div class="bant-brand"><div class="bant-wordmark" role="img" aria-label="BantAIProyekto">'
                '<span class="bant-wordmark-ink"></span>'
                '<img class="bant-wordmark-ai" src="app/static/brand/wordmark-ai.svg" alt=""></div>'
                '<div class="bant-brand-note">Public infrastructure review</div></div>')
        st.caption(dict((title, purpose) for title, _, _, purpose in DESTINATIONS)[current.title])
        st.html('<div class="bant-footer">Philippines · DPWH<br>Public records from BetterGov</div>')
    return current if pages else current.title


def category_badge(raw):
    """One shared badge for a stored evidence category: text + icon, colour only supplementary."""
    # st.badge pills cannot wrap and truncated in narrow columns (seen at 1024 px), so the kind is
    # coloured icon text using the same semantic theme slot; the full label wraps below it.
    label, icon, color = evidence_language.category_badge(raw)
    kind = evidence_language.KIND_STYLE[evidence_language.category_kind(raw)][0]
    st.markdown(f":{color}[{icon} {kind}]  \n**{escape_markdown(label)}**")


def show_state(name, detail=None):
    """One standardized treatment per evidence state: coloured icon + title (meaning in words), then why."""
    _, icon, color, title, explanation = evidence_language.state(name)
    st.markdown(f":{color}[{icon}] **{escape_markdown(title)}**")
    st.caption(" ".join(part for part in (detail, explanation) if part))


def photo_with_fallback(url, alt, caption):
    """Remote photo with alt text, so a broken or blocked image still says what it was."""
    if not str(url or "").startswith("https://"):
        st.caption(f"{caption} · photo link unavailable")
        return
    st.html(f'<img src="{html.escape(url, quote=True)}" alt="{html.escape(alt, quote=True)}" '
            f'style="width:100%;height:auto;border-radius:.5rem" loading="lazy">')
    st.caption(caption)


def evidence_overview(record, assessment, automatic, *, reviewed_at=None, automatic_note=None):
    """Show reported status, automatic observation and human decision as three separate facts.

    External record text is rendered with st.text (no Markdown/HTML parsing). Date-only values stay
    date-only; timestamps are shown in Philippine time.
    """
    status, observation, decision = st.columns(3)
    with status.container(border=True):
        st.markdown("**Reported project status**")
        st.caption("From the DPWH record via BetterGov")
        st.text(record.get("status") or "Unavailable")
        completion = (record.get("dates") or {}).get("Reported completion")
        st.caption(f"Reported completion · {evidence_language.format_timestamp(completion) if completion else 'Unavailable'}")
    with observation.container(border=True):
        st.markdown("**Photo evidence assessment**")
        st.caption("Automatic, from detector observations")
        category_badge(automatic)
        st.caption(evidence_language.category_explanation(automatic))
        if automatic_note:
            st.caption(automatic_note)
        days = assessment.get("completion_support_days_from_reported_completion")
        if days is None and automatic in evidence_language.CATEGORIES and evidence_language.category_kind(automatic) != "scope":
            days = assessment.get("latest_photo_days_from_reported_completion")
        if days is not None or assessment.get("latest_evidence_photo"):
            st.caption(evidence_language.photo_timing_text(days))
        if evidence_language.experimental_basis(assessment):
            st.markdown(f":orange[:material/science:] {evidence_language.EXPERIMENTAL_BASIS_NOTE}")
        st.caption(evidence_language.LIMITATION_TEXT)  # limitation sits with the assessment it qualifies
    with decision.container(border=True):
        st.markdown("**Manual reviewer decision**")
        if evidence_language.is_manual(assessment):
            st.caption("Recorded by a reviewer; preserved during reruns")
            category_badge(assessment.get("completion_evidence_category"))
            if note := assessment.get("manual_category_note"):
                st.text(f"Reason · {note}")
            provenance = assessment.get("manual_review_provenance") or {}
            if provenance.get("legacy") or not provenance:
                st.caption("Reviewer and time were not recorded for this older decision.")
            else:
                when = evidence_language.format_timestamp(provenance.get("reviewed_at"))
                st.caption(f"Reviewer · {provenance.get('reviewer') or 'Unrecorded'} · {when}")
        else:
            st.caption("No manual decision recorded")
    st.caption(evidence_language.TIMING_NOTE)
    if state_name := evidence_language.category_state(automatic):
        show_state(state_name)
    if conflict := evidence_language.same_date_conflict(record):
        show_state("same_date_conflict", evidence_language.conflict_text(conflict))
    if reviewed_at:
        st.caption(f"Saved review time · {evidence_language.format_timestamp(reviewed_at)}")


def assessment_summary(record, assessment, automatic, *, automatic_note=None):
    """Compact, still-separated form of evidence_overview for side panels: three labelled lines."""
    st.text(f"Reported project status · {record.get('status') or 'Unavailable'}")
    st.markdown("**Photo evidence assessment** · automatic")
    category_badge(automatic)
    if automatic_note:
        st.caption(automatic_note)
    days = assessment.get("completion_support_days_from_reported_completion")
    if days is None and evidence_language.category_kind(automatic) != "scope":
        days = assessment.get("latest_photo_days_from_reported_completion")
    if days is not None:
        st.caption(evidence_language.photo_timing_text(days))
    if evidence_language.experimental_basis(assessment):
        st.markdown(f":orange[:material/science:] {evidence_language.EXPERIMENTAL_BASIS_NOTE}")
    if evidence_language.is_manual(assessment):
        label = evidence_language.category_label(assessment.get("completion_evidence_category"))
        st.markdown(f":violet[:material/person:] **Manual reviewer decision** · {escape_markdown(label)}")
    else:
        st.caption("Manual reviewer decision · none recorded")
    st.caption(f"{evidence_language.LIMITATION_TEXT} {evidence_language.TIMING_NOTE}")
    if conflict := evidence_language.same_date_conflict(record):
        show_state("same_date_conflict", evidence_language.conflict_text(conflict))


def show_flags(flags):
    """Grouped, deduplicated flags with a short reason each, instead of a wall of identical banners."""
    inconsistencies, notes = evidence_language.grouped_flags(flags)
    if inconsistencies:
        with st.container(border=True):
            st.markdown(f":orange[:material/warning:] **Record checks needing attention · {len(inconsistencies)}**")
            for _, text, why in inconsistencies:
                st.markdown(f"- {escape_markdown(text)}")
                if why:
                    st.caption(why)
    if notes:
        with st.container(border=True):
            st.markdown(f":material/info: **Evidence gaps and review notes · {len(notes)}**")
            for _, text, why in notes:
                st.markdown(f"- {escape_markdown(text)}")
                if why:
                    st.caption(why)


_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]<>()#+\-.!|~$:])")


def escape_markdown(text):
    """Escape text before placing it inside st.markdown; external text should prefer st.text."""
    return _MARKDOWN_SPECIAL.sub(r"\\\1", str(text))


# ---------------------------------------------------------------------------------------------
# Shared interface states (phase 3). One treatment per state across all destinations.
# Emphasis budget: st.error only for actual failures; empty states are neutral; field feedback sits
# directly under its field; loading text names the real stage. Colour is never the only cue.
# ---------------------------------------------------------------------------------------------

def empty_state(title, body, *, action_label=None, on_click=None, args=(), key=None,
                icon=":material/inbox:"):
    """Neutral empty state: what is missing, why, and (optionally) one quiet next action."""
    with st.container(border=True):
        st.markdown(f"{icon} **{escape_markdown(title)}**")
        st.caption(body)
        if action_label:
            st.button(action_label, on_click=on_click, args=args, key=key)


def error_state(title, body, *, kept=None, retry_label=None, on_retry=None, args=(), key=None):
    """Actual failure: says what failed and what is preserved; optional retry. Error colour + icon + words."""
    message = f"**{escape_markdown(title)}** {body}"
    if kept:
        message += f"  \n{kept}"
    st.error(message, icon=":material/error:")
    if retry_label:
        st.button(retry_label, on_click=on_retry, args=args, key=key, icon=":material/refresh:")


def notice(title, body=None, *, icon=":material/info:"):
    """Non-failure information that matters (e.g. stale review). Not an alert box: quieter than errors."""
    st.markdown(f"{icon} **{escape_markdown(title)}**")
    if body:
        st.caption(body)


def field_feedback(message, *, kind="error"):
    """Feedback rendered directly under the field it concerns (Law of Proximity).

    Framework limitation: Streamlit cannot attach aria-describedby to a widget, so the message is
    adjacent text with an icon and the word-level meaning, not a programmatic association.
    """
    icon, color = {"error": (":material/error:", "red"), "info": (":material/info:", "gray")}[kind]
    st.markdown(f":{color}[{icon} {escape_markdown(message)}]")


def loading(stage):
    """Context manager for work in progress; `stage` names the real step (e.g. 'Retrieving photos')."""
    return st.spinner(f"{stage}…", show_time=True)


def freshness(label, when, *, running=False):
    """Static freshness/activity line replacing decorative pulses. `when` is a datetime or text."""
    text = evidence_language.format_timestamp(when) if not isinstance(when, str) else when
    prefix = ":material/sync: Working ·" if running else ":material/schedule:"
    st.caption(f"{prefix} {label} {text}")


def go_to(destination, **state):
    """Navigate to one of the three destinations, optionally pre-setting session keys
    (e.g. admin_section='Regional reviews'). Use as a button on_click callback.

    Records a request only; navigation() performs st.switch_page on the following run (switching pages
    inside a widget callback is not supported). Session context and drafts are kept.
    """
    if destination not in [title for title, *_ in DESTINATIONS]:
        raise ValueError(f"Unknown destination: {destination}")
    for key, value in state.items():
        st.session_state[key] = value
    st.session_state[_NAV_KEY] = destination


def page_header(title, description, eyebrow=None):
    """Breadcrumb, title and one-line purpose. `eyebrow` is accepted for old callers and ignored."""
    # All arguments are application-owned text; do not interpolate external records into HTML.
    st.html(f'<nav class="bant-breadcrumb" aria-label="Breadcrumb">Workspace / <span aria-current="page">{title}</span></nav>')
    st.title(title)
    st.caption(description)
