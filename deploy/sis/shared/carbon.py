"""IBM Carbon-inspired theming and reusable UI components for Streamlit."""
from __future__ import annotations

from typing import Iterable, Optional

import streamlit as st

# ---------------------------------------------------------------------------
# Carbon Design System tokens
# ---------------------------------------------------------------------------
CARBON = {
    "blue_60": "#0f62fe",
    "blue_70": "#0043ce",
    "blue_80": "#002d9c",
    "gray_05": "#fafafa",
    "gray_10": "#f4f4f4",
    "gray_20": "#e0e0e0",
    "gray_30": "#c6c6c6",
    "gray_50": "#8d8d8d",
    "gray_70": "#525252",
    "gray_80": "#393939",
    "gray_90": "#262626",
    "gray_100": "#161616",
    "white": "#ffffff",
    "green_50": "#24a148",
    "green_10": "#defbe6",
    "yellow_30": "#f1c21b",
    "yellow_10": "#fcf4d6",
    "red_50": "#da1e28",
    "red_10": "#fff1f1",
    "purple_60": "#8a3ffc",
    "teal_60": "#009d9a",
}


# ---------------------------------------------------------------------------
# Global CSS injection
# ---------------------------------------------------------------------------
def inject_carbon_css():
    """Drop Carbon design tokens, IBM Plex font, and component overrides into the page.

    Uses `st.html()` because recent Streamlit versions strip <style> tags from
    `st.markdown(unsafe_allow_html=True)`, leaving the CSS as visible text.
    """
    st.html(
        f"""
        <link rel="preconnect" href="https://fonts.googleapis.com">
        <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
        <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@300;400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
        <style>
        /* ---------- Global type ---------- */
        html, body, [class*="css"], .stApp, .stMarkdown, .stTextInput, .stTextArea,
        .stSelectbox, .stButton, .stRadio, .stCheckbox, button, input, textarea, select {{
            font-family: 'IBM Plex Sans', system-ui, -apple-system, sans-serif !important;
            letter-spacing: 0;
        }}
        code, pre, .stCode {{
            font-family: 'IBM Plex Mono', ui-monospace, monospace !important;
        }}

        /* ---------- App chrome ---------- */
        .stApp {{ background-color: {CARBON["white"]}; color: {CARBON["gray_100"]}; }}
        /* Sidebar is intentionally hidden — every essential control lives
           in the topbar popovers ("Saved drafts", "Status & tools").
           Hide the panel itself and the collapse toggle so users can't
           accidentally reveal an empty drawer. */
        section[data-testid="stSidebar"],
        [data-testid="stSidebarCollapseButton"],
        [data-testid="collapsedControl"],
        button[kind="header"] {{
            display: none !important;
        }}
        .stApp header {{ background-color: transparent; }}
        #MainMenu {{ visibility: hidden; }}
        footer {{ visibility: hidden; }}

        /* ---------- Headings ---------- */
        h1, h2, h3, h4, h5, h6 {{
            font-family: 'IBM Plex Sans', sans-serif !important;
            font-weight: 600 !important;
            color: {CARBON["gray_100"]};
            letter-spacing: -0.01em;
        }}
        h1 {{ font-size: 2.25rem !important; line-height: 1.2 !important; }}
        h2 {{ font-size: 1.75rem !important; line-height: 1.25 !important; }}
        h3 {{ font-size: 1.25rem !important; line-height: 1.3 !important; }}

        /* ---------- Buttons (Carbon style) ---------- */
        .stButton > button {{
            background-color: {CARBON["blue_60"]};
            color: {CARBON["white"]};
            border: 1px solid {CARBON["blue_60"]};
            border-radius: 0;
            padding: 0.55rem 0.9rem;
            font-weight: 400;
            font-size: 0.85rem;
            transition: all 0.12s ease;
            min-height: 38px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        .stButton > button > div {{
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            max-width: 100%;
        }}
        .stButton > button p {{
            white-space: nowrap !important;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        .stButton > button:hover {{
            background-color: {CARBON["blue_70"]};
            border-color: {CARBON["blue_70"]};
            color: {CARBON["white"]};
            transform: translateY(-1px);
        }}
        .stButton > button:active {{ transform: translateY(0); }}
        .stButton > button:focus {{
            outline: 2px solid {CARBON["blue_60"]};
            outline-offset: 2px;
            box-shadow: none;
        }}
        .stButton > button[kind="secondary"] {{
            background-color: transparent;
            color: {CARBON["blue_60"]};
            border: 1px solid {CARBON["blue_60"]};
        }}
        .stButton > button[kind="secondary"]:hover {{
            background-color: {CARBON["gray_10"]};
            color: {CARBON["blue_70"]};
        }}
        .stDownloadButton > button {{
            background-color: {CARBON["gray_100"]};
            color: {CARBON["white"]};
            border-radius: 0;
            border: none;
            transition: all 0.12s ease;
        }}
        .stDownloadButton > button:hover {{
            background-color: {CARBON["gray_90"]};
            transform: translateY(-1px);
        }}

        /* ---------- Inputs ---------- */
        .stTextInput input, .stTextArea textarea, .stSelectbox div[data-baseweb="select"] > div {{
            border-radius: 0 !important;
            border: 1px solid {CARBON["gray_30"]} !important;
            background-color: {CARBON["gray_10"]} !important;
            font-size: 0.875rem !important;
            transition: all 0.12s ease;
        }}
        .stTextInput input:focus, .stTextArea textarea:focus {{
            border-color: {CARBON["blue_60"]} !important;
            outline: 2px solid {CARBON["blue_60"]} !important;
            outline-offset: -2px;
            box-shadow: none !important;
            background-color: {CARBON["white"]} !important;
        }}

        /* ---------- Chat ---------- */
        [data-testid="stChatInput"] {{ border-radius: 0; border: 1px solid {CARBON["gray_30"]}; }}
        [data-testid="stChatMessage"] {{ background-color: transparent; padding: 0.5rem 0; }}
        [data-testid="stChatMessage"][aria-label*="user"] {{
            background-color: {CARBON["gray_10"]};
            padding: 0.85rem 1rem;
            border-left: 3px solid {CARBON["blue_60"]};
        }}

        /* ---------- Tabs ---------- */
        .stTabs [data-baseweb="tab-list"] {{ gap: 0; border-bottom: 1px solid {CARBON["gray_20"]}; }}
        .stTabs [data-baseweb="tab"] {{
            background-color: transparent;
            border-radius: 0;
            padding: 0.75rem 1rem;
            font-weight: 400;
            color: {CARBON["gray_70"]};
            border-bottom: 2px solid transparent;
        }}
        .stTabs [aria-selected="true"] {{
            color: {CARBON["gray_100"]} !important;
            border-bottom: 2px solid {CARBON["blue_60"]} !important;
            font-weight: 600;
        }}

        /* ---------- Expander ---------- */
        .stExpander {{ border: 1px solid {CARBON["gray_20"]}; border-radius: 0; background-color: {CARBON["white"]}; }}
        .stExpander summary {{ font-weight: 500; color: {CARBON["gray_100"]}; }}

        /* ---------- DataFrame ---------- */
        .stDataFrame {{ border-radius: 0; border: 1px solid {CARBON["gray_20"]}; }}

        /* ---------- Toggle ---------- */
        .stToggle [role="switch"] {{ border-radius: 0 !important; }}

        /* ---------- Section card hover lift ---------- */
        [data-testid="stVerticalBlockBorderWrapper"] {{
            transition: border-color 0.15s ease, box-shadow 0.15s ease;
        }}
        [data-testid="stVerticalBlockBorderWrapper"]:hover {{
            border-color: {CARBON["gray_30"]} !important;
        }}

        /* ---------- Confidence badge ---------- */
        .confidence-badge {{
            display: inline-flex;
            align-items: center;
            gap: 0.4rem;
            padding: 0.25rem 0.6rem;
            font-size: 0.75rem;
            font-weight: 600;
            font-family: 'IBM Plex Mono', monospace;
            letter-spacing: 0.04em;
            text-transform: uppercase;
            border-radius: 0;
            transition: all 0.2s ease;
            animation: badgeFade 0.35s ease;
        }}
        @keyframes badgeFade {{
            from {{ opacity: 0; transform: translateY(-2px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}
        .confidence-badge.grade-a, .confidence-badge.grade-b {{
            background-color: {CARBON["green_10"]};
            color: #044317;
            border: 1px solid {CARBON["green_50"]};
        }}
        .confidence-badge.grade-c {{
            background-color: {CARBON["yellow_10"]};
            color: #684e00;
            border: 1px solid {CARBON["yellow_30"]};
        }}
        .confidence-badge.grade-d, .confidence-badge.grade-f {{
            background-color: {CARBON["red_10"]};
            color: #750e13;
            border: 1px solid {CARBON["red_50"]};
        }}
        .confidence-badge.pending {{
            background-color: {CARBON["gray_10"]};
            color: {CARBON["gray_70"]};
            border: 1px solid {CARBON["gray_30"]};
        }}

        /* ---------- Hero / chat-first state ---------- */
        .hero-wrap {{ max-width: 760px; margin: 4rem auto 1.5rem auto; text-align: center; }}
        .hero-eyebrow {{
            font-family: 'IBM Plex Mono', monospace;
            font-size: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            color: {CARBON["blue_60"]};
            margin-bottom: 0.75rem;
        }}
        .hero-title {{
            font-size: 2.5rem;
            font-weight: 300;
            color: {CARBON["gray_100"]};
            line-height: 1.15;
            margin-bottom: 0.75rem;
            letter-spacing: -0.02em;
        }}
        .hero-sub {{ font-size: 1rem; color: {CARBON["gray_70"]}; margin-bottom: 1.5rem; font-weight: 400; }}

        /* ---------- App header bar ---------- */
        .topbar {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0.6rem 0 1rem 0;
            border-bottom: 1px solid {CARBON["gray_20"]};
            margin-bottom: 1.25rem;
        }}
        .topbar-brand {{
            display: flex;
            align-items: center;
            gap: 0.6rem;
            font-weight: 600;
            font-size: 0.95rem;
            color: {CARBON["gray_100"]};
        }}
        .topbar-brand .dot {{
            display: inline-block;
            width: 10px; height: 10px;
            background-color: {CARBON["blue_60"]};
            border-radius: 0;
        }}
        .topbar-meta {{
            display: flex;
            gap: 0.75rem;
            align-items: center;
            font-family: 'IBM Plex Mono', monospace;
            font-size: 0.7rem;
            color: {CARBON["gray_70"]};
            text-transform: uppercase;
            letter-spacing: 0.06em;
        }}
        .topbar-meta .meta-pill {{
            padding: 0.2rem 0.55rem;
            border: 1px solid {CARBON["gray_20"]};
            background-color: {CARBON["gray_05"]};
        }}
        .topbar-meta .meta-pill.live {{ border-color: {CARBON["green_50"]}; color: #044317; background-color: {CARBON["green_10"]}; }}
        .topbar-meta .meta-pill.mock {{ border-color: {CARBON["yellow_30"]}; color: #684e00; background-color: {CARBON["yellow_10"]}; }}

        /* ---------- Source pill ---------- */
        .source-pill {{
            display: inline-block;
            font-family: 'IBM Plex Mono', monospace;
            font-size: 0.7rem;
            background-color: {CARBON["gray_10"]};
            color: {CARBON["gray_70"]};
            padding: 0.15rem 0.55rem;
            border: 1px solid {CARBON["gray_20"]};
            margin-right: 0.4rem;
        }}

        /* ---------- Sidebar status panel ---------- */
        .sb-status {{
            border: 1px solid {CARBON["gray_20"]};
            background-color: {CARBON["white"]};
            padding: 0.85rem 0.95rem;
            margin-bottom: 1rem;
        }}
        .sb-status-row {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 0.25rem 0;
            font-size: 0.78rem;
            color: {CARBON["gray_70"]};
        }}
        .sb-status-row b {{ color: {CARBON["gray_100"]}; font-weight: 500; }}
        .sb-status-row .v {{
            font-family: 'IBM Plex Mono', monospace;
            font-size: 0.75rem;
            color: {CARBON["gray_90"]};
        }}
        .sb-status-row .v.live {{ color: #044317; }}
        .sb-status-row .v.mock {{ color: #684e00; }}

        /* ---------- Skeleton cards (generating state) ---------- */
        .skel {{
            border: 1px solid {CARBON["gray_20"]};
            background-color: {CARBON["white"]};
            padding: 1.1rem 1.25rem;
            margin-bottom: 0.85rem;
        }}
        .skel-bar {{
            height: 12px;
            background: linear-gradient(90deg, {CARBON["gray_10"]} 0%, {CARBON["gray_20"]} 50%, {CARBON["gray_10"]} 100%);
            background-size: 200% 100%;
            margin: 0.5rem 0;
            animation: shimmer 1.4s linear infinite;
        }}
        .skel-bar.w90 {{ width: 90%; }}
        .skel-bar.w75 {{ width: 75%; }}
        .skel-bar.w60 {{ width: 60%; }}
        .skel-bar.w40 {{ width: 40%; }}
        .skel-title {{
            height: 18px;
            width: 35%;
            background: linear-gradient(90deg, {CARBON["gray_20"]} 0%, {CARBON["gray_30"]} 50%, {CARBON["gray_20"]} 100%);
            background-size: 200% 100%;
            animation: shimmer 1.4s linear infinite;
            margin-bottom: 0.85rem;
        }}
        @keyframes shimmer {{
            0% {{ background-position: 200% 0; }}
            100% {{ background-position: -200% 0; }}
        }}

        /* ---------- Quick-action chips row ---------- */
        .qa-strip {{
            display: flex;
            gap: 0.4rem;
            flex-wrap: wrap;
            margin: 0.4rem 0 0.6rem 0;
        }}
        .qa-chip-btn > button {{
            background-color: {CARBON["white"]} !important;
            color: {CARBON["gray_90"]} !important;
            border: 1px solid {CARBON["gray_30"]} !important;
            border-radius: 999px !important;
            padding: 0.25rem 0.85rem !important;
            font-size: 0.78rem !important;
            font-weight: 400 !important;
            min-height: 28px !important;
            line-height: 1 !important;
        }}
        .qa-chip-btn > button:hover {{
            background-color: {CARBON["blue_60"]} !important;
            color: {CARBON["white"]} !important;
            border-color: {CARBON["blue_60"]} !important;
            transform: translateY(-1px);
        }}

        /* ---------- Dimension score bars (confidence detail) ---------- */
        .dim-row {{ margin-bottom: 0.55rem; }}
        .dim-label {{
            display: flex;
            justify-content: space-between;
            font-size: 0.78rem;
            color: {CARBON["gray_90"]};
            margin-bottom: 0.2rem;
        }}
        .dim-label .v {{ font-family: 'IBM Plex Mono', monospace; color: {CARBON["gray_70"]}; }}
        .dim-track {{
            position: relative;
            height: 6px;
            background-color: {CARBON["gray_10"]};
            border: 1px solid {CARBON["gray_20"]};
        }}
        .dim-fill {{
            position: absolute;
            top: 0; left: 0; bottom: 0;
            background-color: {CARBON["blue_60"]};
            transition: width 0.4s ease;
        }}
        .dim-fill.s5 {{ background-color: {CARBON["green_50"]}; }}
        .dim-fill.s4 {{ background-color: {CARBON["green_50"]}; }}
        .dim-fill.s3 {{ background-color: {CARBON["yellow_30"]}; }}
        .dim-fill.s2 {{ background-color: {CARBON["red_50"]}; }}
        .dim-fill.s1 {{ background-color: {CARBON["red_50"]}; }}
        .dim-reason {{
            margin-top: 0.3rem;
            margin-left: 0;
            color: {CARBON["gray_70"]};
            font-size: 0.78rem;
        }}
        .dim-reason li {{ margin-bottom: 0.1rem; }}

        /* ---------- Empty-state for chat ---------- */
        .chat-empty {{
            border: 1px dashed {CARBON["gray_30"]};
            padding: 1rem;
            color: {CARBON["gray_70"]};
            background-color: {CARBON["gray_05"]};
            margin: 0.4rem 0;
        }}
        .chat-empty h5 {{
            margin: 0 0 0.5rem 0 !important;
            font-size: 0.85rem !important;
            color: {CARBON["gray_90"]};
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }}
        .chat-empty li {{ margin-bottom: 0.3rem; font-size: 0.85rem; }}

        /* ---------- Section meta (token count, etc.) ---------- */
        .sec-meta {{
            font-family: 'IBM Plex Mono', monospace;
            font-size: 0.7rem;
            color: {CARBON["gray_50"]};
            text-transform: uppercase;
            letter-spacing: 0.06em;
        }}

        /* ---------- Block container width ---------- */
        .block-container {{
            padding-top: 1.6rem;
            padding-bottom: 2rem;
            max-width: 1480px;
        }}

        /* ---------- Subtle keyframe used elsewhere ---------- */
        @keyframes fadeIn {{
            from {{ opacity: 0; transform: translateY(4px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}
        .fade-in {{ animation: fadeIn 0.4s ease; }}

        /* ---------- Risk playbook grid (idle state) ---------- */
        .pb-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(290px, 1fr));
            gap: 0.85rem;
            margin: 0.5rem 0 1.25rem 0;
        }}
        .pb-card {{
            background-color: {CARBON["white"]};
            border: 1px solid {CARBON["gray_20"]};
            padding: 1rem 1.1rem 0.9rem 1.1rem;
            display: flex;
            flex-direction: column;
            min-height: 180px;
            transition: all 0.15s ease;
            cursor: pointer;
            position: relative;
        }}
        .pb-card:hover {{
            border-color: {CARBON["blue_60"]};
            transform: translateY(-2px);
            box-shadow: 0 6px 16px rgba(15, 98, 254, 0.06);
        }}
        .pb-spec {{
            font-family: 'IBM Plex Mono', monospace;
            font-size: 0.7rem;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            color: {CARBON["blue_60"]};
            margin-bottom: 0.4rem;
        }}
        .pb-title {{
            font-size: 1rem;
            font-weight: 600;
            color: {CARBON["gray_100"]};
            line-height: 1.3;
            margin-bottom: 0.6rem;
            letter-spacing: -0.005em;
        }}
        .pb-stats {{
            display: flex;
            gap: 1.25rem;
            margin-top: auto;
            padding-top: 0.6rem;
            border-top: 1px solid {CARBON["gray_20"]};
        }}
        .pb-stat {{ flex: 1; }}
        .pb-stat-v {{
            font-family: 'IBM Plex Mono', monospace;
            font-size: 1.05rem;
            font-weight: 500;
            color: {CARBON["gray_100"]};
            line-height: 1.1;
        }}
        .pb-stat-l {{
            font-size: 0.7rem;
            color: {CARBON["gray_70"]};
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-top: 0.15rem;
        }}

        /* ---------- Chip-style buttons (small, outlined) ---------- */
        .chips-row .stButton > button {{
            background-color: {CARBON["white"]} !important;
            color: {CARBON["gray_90"]} !important;
            border: 1px solid {CARBON["gray_30"]} !important;
            border-radius: 999px !important;
            padding: 0.25rem 0.85rem !important;
            font-size: 0.78rem !important;
            font-weight: 400 !important;
            min-height: 30px !important;
            line-height: 1.1 !important;
        }}
        .chips-row .stButton > button:hover {{
            background-color: {CARBON["blue_60"]} !important;
            color: {CARBON["white"]} !important;
            border-color: {CARBON["blue_60"]} !important;
            transform: translateY(-1px);
        }}

        /* ---------- Inline confidence bars (compact) ---------- */
        .conf-mini {{
            display: flex;
            gap: 0.35rem;
            align-items: center;
            margin: 0.35rem 0 0.5rem 0;
        }}
        .conf-mini-bar {{
            height: 3px;
            background-color: {CARBON["gray_20"]};
            position: relative;
            overflow: hidden;
            flex: 1;
            min-width: 30px;
        }}
        .conf-mini-bar .f {{
            position: absolute;
            inset: 0 auto 0 0;
            background-color: {CARBON["blue_60"]};
        }}
        .conf-mini-bar .f.s5, .conf-mini-bar .f.s4 {{ background-color: {CARBON["green_50"]}; }}
        .conf-mini-bar .f.s3 {{ background-color: {CARBON["yellow_30"]}; }}
        .conf-mini-bar .f.s2, .conf-mini-bar .f.s1 {{ background-color: {CARBON["red_50"]}; }}
        .conf-mini-name {{
            font-size: 0.7rem;
            color: {CARBON["gray_70"]};
            min-width: 110px;
        }}
        .conf-mini-score {{
            font-family: 'IBM Plex Mono', monospace;
            font-size: 0.7rem;
            color: {CARBON["gray_90"]};
            min-width: 24px;
            text-align: right;
        }}

        /* ---------- Branded chat avatars ---------- */
        [data-testid="stChatMessage"][aria-label*="user"] {{
            background-color: {CARBON["gray_10"]};
            padding: 0.85rem 1rem;
            border-left: 3px solid {CARBON["blue_60"]};
            margin-bottom: 0.4rem;
        }}
        [data-testid="stChatMessage"][aria-label*="assistant"] {{
            background-color: transparent;
            padding: 0.65rem 1rem;
            border-left: 3px solid transparent;
            margin-bottom: 0.4rem;
        }}

        /* ---------- Section content typography (avoid duplicated H1) ---------- */
        .section-content h1:first-child {{
            font-size: 1rem !important;
            font-weight: 500 !important;
            color: {CARBON["gray_70"]} !important;
            margin-top: 0;
            margin-bottom: 0.6rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            border-bottom: 1px solid {CARBON["gray_20"]};
            padding-bottom: 0.4rem;
        }}
        .section-content h2 {{
            font-size: 1.15rem !important;
            margin-top: 1.1rem !important;
        }}
        .section-content h3 {{ font-size: 1rem !important; }}

        /* ---------- Accessible focus rings (Carbon spec) ---------- */
        button:focus-visible, a:focus-visible, [role="combobox"]:focus-visible {{
            outline: 2px solid {CARBON["blue_60"]} !important;
            outline-offset: 2px !important;
            border-radius: 0 !important;
        }}

        /* ---------- Latency caption beside assistant messages ---------- */
        .msg-meta {{
            font-family: 'IBM Plex Mono', monospace;
            font-size: 0.68rem;
            color: {CARBON["gray_50"]};
            margin-top: -0.15rem;
            margin-bottom: 0.5rem;
            padding-left: 1rem;
        }}

        /* ---------- Sticky preview header inside the section card ---------- */
        .preview-anchor {{
            position: sticky;
            top: 0;
            background-color: {CARBON["white"]};
            z-index: 10;
            padding: 0.4rem 0;
            border-bottom: 1px solid {CARBON["gray_20"]};
        }}

        /* ---------- Sticky chat pane in the editing-state split view ---------- *
         * Only one h5 exists in the app (the "Chat" header in the chat pane).
         * We use :has() to find the horizontal block whose first column owns
         * that h5, then sticky just that column. Other column layouts are
         * unaffected. */
        [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"]:first-child h5)
          > [data-testid="stColumn"]:first-child {{
            position: sticky !important;
            top: 0.5rem !important;
            align-self: flex-start !important;
            max-height: calc(100vh - 1rem) !important;
            overflow-y: auto !important;
            padding-right: 0.5rem !important;
        }}
        [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"]:first-child h5)
          > [data-testid="stColumn"]:first-child::-webkit-scrollbar {{ width: 6px; }}
        [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"]:first-child h5)
          > [data-testid="stColumn"]:first-child::-webkit-scrollbar-thumb {{
            background-color: {CARBON["gray_30"]};
        }}
        </style>
        """
    )


# ---------------------------------------------------------------------------
# Components (Streamlit-callable)
# ---------------------------------------------------------------------------
def topbar(app_name: str = "", mode: str = "DRAFT", connection_pill: str | None = None,
           model_pill: str | None = None, max_brand_chars: int = 70):
    """Render the Carbon-style top bar.

    `app_name` empty → just "MyAdvice Builder" (no slash). Otherwise renders as
    "MyAdvice Builder / {app_name}".
    """
    brand_full = f"MyAdvice Builder / {app_name}" if app_name else "MyAdvice Builder"
    if len(brand_full) > max_brand_chars:
        brand_short = brand_full[:max_brand_chars].rsplit(" ", 1)[0] + "…"
        brand_attr = f' title="{_html_escape(brand_full)}"'
        brand_html = f'<span{brand_attr}>{_html_escape(brand_short)}</span>'
    else:
        brand_html = f"<span>{_html_escape(brand_full)}</span>"

    pills = []
    if connection_pill:
        cls = "live" if "live" in connection_pill.lower() else "mock"
        pills.append(f"<span class='meta-pill {cls}'>{connection_pill}</span>")
    if model_pill:
        pills.append(f"<span class='meta-pill'>{model_pill}</span>")
    if mode and mode == "GENERATING":
        pills.append(f"<span class='meta-pill'>{mode}</span>")
    pills_html = "".join(pills)
    st.markdown(
        f"""
        <div class="topbar">
            <div class="topbar-brand">
                <span class="dot"></span>
                {brand_html}
            </div>
            <div class="topbar-meta">{pills_html}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def hero(eyebrow: str, title: str, subtitle: str):
    st.markdown(
        f"""
        <div class="hero-wrap fade-in">
            <div class="hero-eyebrow">{eyebrow}</div>
            <div class="hero-title">{title}</div>
            <div class="hero-sub">{subtitle}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def confidence_badge(grade: str | None) -> str:
    if not grade:
        return '<span class="confidence-badge pending">Confidence: pending</span>'
    g = grade.strip().upper()[:1]
    cls = f"grade-{g.lower()}" if g in "ABCDF" else "pending"
    return f'<span class="confidence-badge {cls}">Confidence: {g}</span>'


def skeleton_card(title: str = "Generating…"):
    """One animated skeleton card."""
    st.markdown(
        f"""
        <div class="skel">
            <div class="skel-title"></div>
            <div class="skel-bar w90"></div>
            <div class="skel-bar w75"></div>
            <div class="skel-bar w60"></div>
            <div class="skel-bar w40"></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_dimension_bars(dimension_scores: dict):
    """Render confidence-detail dimension bars as styled HTML."""
    if not dimension_scores:
        st.caption("No dimension breakdown available.")
        return
    rows = []
    for key in sorted(dimension_scores.keys()):
        d = dimension_scores[key]
        if not isinstance(d, dict):
            continue
        name = d.get("name", key)
        score = int(d.get("score", 0))
        pct = (score / 5.0) * 100.0
        reasoning = d.get("reasoning", []) or []
        reason_html = "".join(f"<li>{_html_escape(r)}</li>" for r in reasoning)
        rows.append(f"""
        <div class="dim-row">
            <div class="dim-label"><span>{_html_escape(name)}</span><span class="v">{score}/5</span></div>
            <div class="dim-track"><div class="dim-fill s{score}" style="width:{pct:.1f}%"></div></div>
            <ul class="dim-reason">{reason_html}</ul>
        </div>
        """)
    st.markdown("".join(rows), unsafe_allow_html=True)


def _html_escape(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def sidebar_status(connected: bool, mode: str, model: str,
                   last_latency_s: float | None, mock_count: int = 0,
                   real_count: int = 0):
    """Render a compact status panel in the sidebar.

    Distinguishes three states:
    - Connected + real calls succeeding → "Live · Cortex" (green)
    - Connected + recent calls falling back to mock → "Live · falling back" (yellow)
    - No connection → "Mock" (yellow)
    """
    if connected and real_count > 0:
        conn_v, conn_class = "Live · Cortex", "live"
    elif connected and mock_count > 0:
        conn_v, conn_class = "Live · fallback", "mock"
    elif connected:
        conn_v, conn_class = "Live · idle", "live"
    else:
        conn_v, conn_class = "Mock", "mock"

    if last_latency_s is None:
        latency_v = "—"
    elif last_latency_s < 1.0:
        latency_v = f"{int(last_latency_s * 1000)}ms"
    else:
        latency_v = f"{last_latency_s:.1f}s"

    rows = [
        ("Connection", conn_v, conn_class),
        ("Mode", mode, ""),
        ("Model", model, ""),
        ("Last call", latency_v, ""),
        ("Real calls", str(real_count), ""),
        ("Mock calls", str(mock_count), ""),
    ]
    rows_html = "".join(
        f"<div class='sb-status-row'><b>{label}</b><span class='v {cls}'>{val}</span></div>"
        for label, val, cls in rows
    )
    st.markdown(
        f"<div class='sb-status'>{rows_html}</div>",
        unsafe_allow_html=True,
    )


def render_style_guide_panel():
    """Sidebar expander that shows the MM Copy Guide. Lets users (and the
    team reviewing prompts) see the style rules every prompt is anchored to.
    """
    try:
        from .style_guide import STYLE_GUIDE
    except ImportError:
        from shared.style_guide import STYLE_GUIDE
    with st.expander("📖 MM Copy Guide", expanded=False):
        st.caption(
            "These rules are baked into every generation prompt via the "
            "`MM_VOICE` component. Update `shared/style_guide.py` to revise."
        )
        st.markdown(STYLE_GUIDE)


def render_cortex_test_button():
    """A small 'Test Cortex' button users can click to validate their
    Snowflake/Cortex connection without generating a full course.
    """
    if st.button("Test Cortex connection", use_container_width=True,
                 type="secondary", help="Run a tiny Cortex.COMPLETE call to verify the connection."):
        try:
            from .cortex import complete, is_connected
        except ImportError:
            from shared.cortex import complete, is_connected
        with st.spinner("Pinging Cortex…"):
            res = complete("Reply with the single word: OK.",
                            model="claude-4-sonnet", temperature=0.0)
        if res.mocked:
            if is_connected():
                st.warning(
                    "Connected to Snowflake but the Cortex call fell back to "
                    "mock. Most common causes: model name not available in your "
                    "Cortex region, role lacks USAGE on CORTEX, or warehouse "
                    "isn't running. See the **Cortex errors** expander."
                )
            else:
                st.info("No Snowflake session available. See the README for "
                        "`.streamlit/secrets.toml` setup.")
        else:
            st.success(f"✅ Cortex live ({res.elapsed_s*1000:.0f}ms): {res.text[:80]}")


def chat_empty_state(starters: Iterable[str]):
    """Render an empty-state hint with starter prompts."""
    items = "".join(f"<li>{_html_escape(s)}</li>" for s in starters)
    st.markdown(
        f"""
        <div class="chat-empty">
            <h5>Try saying</h5>
            <ul style='margin:0; padding-left:1.1rem;'>{items}</ul>
        </div>
        """,
        unsafe_allow_html=True,
    )


def section_meta(token_count: int, word_count: int):
    """Tiny mono caption for a section card."""
    st.markdown(
        f"<span class='sec-meta'>{word_count} words · ~{token_count} tokens</span>",
        unsafe_allow_html=True,
    )


def sticky_chat_script():
    """Kept as a no-op for backwards compatibility.

    Sticky chat is now a pure-CSS rule using :has() — no JS injection
    needed. See the .chat-pane stylesheet block above.
    """
    return


def render_inline_confidence(dimension_scores: dict):
    """Tight inline mini-bars for the section card — always visible."""
    if not dimension_scores:
        return
    rows = []
    for key in sorted(dimension_scores.keys()):
        d = dimension_scores[key]
        if not isinstance(d, dict):
            continue
        name = d.get("name", key)
        score = int(d.get("score", 0))
        pct = (score / 5.0) * 100.0
        rows.append(f"""
        <div class="conf-mini">
            <span class="conf-mini-name">{_html_escape(name)}</span>
            <div class="conf-mini-bar"><div class="f s{score}" style="width:{pct:.1f}%"></div></div>
            <span class="conf-mini-score">{score}/5</span>
        </div>
        """)
    st.markdown("".join(rows), unsafe_allow_html=True)


def playbook_card_html(specialty: str, driver: str,
                        frequency_pct: float | None,
                        severity_usd: float | None) -> str:
    """Compose the HTML for one playbook card. Click is handled outside (button)."""
    freq = f"{frequency_pct:.1f}%" if frequency_pct is not None else "—"
    sev = _format_money(severity_usd) if severity_usd is not None else "—"
    return f"""
    <div class="pb-card">
        <div class="pb-spec">{_html_escape(specialty)}</div>
        <div class="pb-title">{_html_escape(driver)}</div>
        <div class="pb-stats">
            <div class="pb-stat">
                <div class="pb-stat-v">{freq}</div>
                <div class="pb-stat-l">Claim freq.</div>
            </div>
            <div class="pb-stat">
                <div class="pb-stat-v">{sev}</div>
                <div class="pb-stat-l">Avg severity</div>
            </div>
        </div>
    </div>
    """


def _format_money(v: float) -> str:
    if v is None:
        return "—"
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v / 1_000:.0f}K"
    return f"${v:.0f}"
