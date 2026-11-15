"""Presentation helpers for BantAIProyekto. No review or inference logic."""
import streamlit as st


def apply_theme():
    css = """
    <style>
      .stApp {background:#0A0F1A; color:#E6EDF7;}
      .block-container {max-width:1240px; padding:3.4rem clamp(1rem,3vw,2.5rem) 3rem;}
      [data-testid="stSidebar"] {background:#0D1420; border-right:1px solid #1F2C40;}
      [data-testid="stSidebar"] > div:first-child {padding-top:1.6rem;}
      h1 {font-size:2rem !important; font-weight:720 !important; letter-spacing:-.045em; padding-top:.3rem !important;}
      h2 {font-size:1.4rem !important; font-weight:600 !important; letter-spacing:-.02em;}
      h3 {font-size:1.12rem !important; font-weight:600 !important;}
      p, label {line-height:1.55;}
      [data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p {color:#A1B1C8;}
      [data-testid="stAppDeployButton"] {display:none;}
      [data-testid="stVerticalBlockBorderWrapper"] > div {border-radius:12px;background:#111B2B;box-shadow:0 6px 18px #0002;}
      [data-testid="stMetric"] {background:#111B2B; border:1px solid #1F2C40; border-radius:12px; padding:16px 20px;}
      [data-testid="stMetricValue"] {font-size:1.65rem; font-weight:600;}
      [data-testid="stMetricLabel"] {color:#A1B1C8;}
      [data-testid="stExpander"] {border-color:#1F2C40; border-radius:10px;}
      [data-testid="stExpander"] summary {padding:.85rem 1rem;}
      .stButton button, .stDownloadButton button, .stLinkButton a {border-radius:8px; min-height:40px; font-weight:550;}
      button[kind="primary"] {background:#08766C; border:1px solid #129d8d; color:#fff;}
      button[kind="primary"]:hover {background:#096d64; border-color:#55c9b9;}
      button:focus-visible, a:focus-visible {outline:2px solid #70d4c6 !important; outline-offset:3px;}
      [data-baseweb="tab-list"] {gap:1.3rem; border-bottom:1px solid #1F2C40;}
      [data-baseweb="tab"] {padding:0 4px 12px; height:46px;}
      [data-baseweb="tab-highlight"] {background:#2DD4BF;}
      [data-testid="stSidebar"] [role="radiogroup"] {gap:8px;}
      [data-testid="stSidebar"] [role="radiogroup"] label {padding:10px 12px; border:1px solid transparent; border-radius:9px; margin:0;}
      [data-testid="stSidebar"] [role="radiogroup"] label:hover {background:#111B2B;}
      [data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {background:#143435; border-left:3px solid #2DD4BF;}
      .bant-brand {display:flex; align-items:center; gap:11px; margin:0 0 24px;}
      .bant-mark {display:grid; place-items:center; flex-shrink:0; width:38px; height:38px; background:linear-gradient(135deg,#69eed3,#16ad99); border-radius:10px; color:#062823; font-size:15px; font-weight:750; letter-spacing:-1px;}
      .bant-wordmark {font-size:18px; font-weight:650; letter-spacing:-.5px; color:#E6EDF7;}
      .bant-wordmark span {color:#6dd4c3;}
      .bant-brand-note {font-size:11px; color:#A1B1C8; margin-top:2px;}
      .bant-eyebrow {font-size:11px; color:#6dd4c3; font-weight:650; letter-spacing:.14em; margin:0 0 8px;}
      .bant-guide {border-left:3px solid #08766C; padding:6px 0 6px 15px; margin:12px 0 20px; color:#b5c8d1; font-size:14px;}
      .bant-footer {font-size:12px; color:#A1B1C8; border-top:1px solid #1F2C40; padding-top:16px; margin-top:24px;}
      .bant-live {display:inline-block;width:7px;height:7px;border-radius:50%;background:#2DD4BF;margin-right:7px;animation:bant-pulse 2.5s infinite;}
      @keyframes bant-pulse {0% {box-shadow:0 0 0 0 #2dd4bf66;} 75%,100% {box-shadow:0 0 0 6px #2dd4bf00;}}
      @media(prefers-reduced-motion:reduce) {.bant-live {animation:none;}}
      .bant-chip {display:inline-block;padding:4px 9px;border-radius:999px;background:#143435;margin:0 5px 5px 0;font-size:12px;}
      .bant-breadcrumb {font-size:12px;color:#A1B1C8;margin-bottom:10px;}
      .stApp :is(input,select,[role="combobox"]):focus-visible {outline:2px solid #2DD4BF;outline-offset:2px;}
      @media(max-width:760px) {
        .block-container {padding:3.5rem 1rem 2rem;}
        h1 {font-size:1.65rem !important;}
        [data-testid="stMetric"] {padding:12px;}
        [data-baseweb="tab-list"] {gap:.8rem;}
      }
    </style>
    """
    if getattr(st.context, "theme", {}).get("type") == "light":
        for dark, light in {
            "#0A0F1A": "#F0F5F9", "#0D1420": "#F8FBFD", "#111B2B": "#FFFFFF",
            "#1F2C40": "#C8D5E3", "#E6EDF7": "#17253A", "#A1B1C8": "#465B75",
            "#143435": "#DDF4EE", "#6dd4c3": "#08766C", "#b5c8d1": "#465B75",
        }.items():
            css = css.replace(dark, light)
    st.html(css)


def navigation():
    icons = {"Explore projects": ":material/explore:", "Regional review": ":material/map:", "Admin": ":material/shield:"}
    with st.sidebar:
        st.html("""<div class="bant-brand"><div class="bant-mark" aria-hidden="true">BP</div>
            <div><div class="bant-wordmark">Bant<span>AI</span>Proyekto</div>
            <div class="bant-brand-note">Public infrastructure review</div></div></div>""")
        st.caption("WORKSPACE")
        page = st.radio("Workspace", ["Explore projects", "Regional review", "Admin"],
                        key="primary_navigation", label_visibility="collapsed", format_func=lambda item: f"{icons[item]} {item}")
        st.divider()
        st.caption({"Explore projects": "Find a contract and inspect its dates, budget and photo evidence.",
                    "Regional review": "Browse saved regional findings and contractor scores.",
                    "Admin": "Run reviews, manage saved evidence and inspect model status."}[page])
        st.html('<div class="bant-footer">Philippines · DPWH<br>Public records from BetterGov</div>')
    return page


def page_header(title, description, eyebrow="PROJECT INTELLIGENCE"):
    # All arguments are application-owned text; do not interpolate external records into HTML.
    st.html(f'<div class="bant-breadcrumb">Workspace / {title}</div><div class="bant-eyebrow">{eyebrow}</div>')
    st.title(title)
    st.caption(description)
    st.write("")
