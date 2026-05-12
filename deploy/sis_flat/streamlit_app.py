"""Unified LC Builder app — splash + router.

Splash screen at start → pick a content type → enter that builder. The
splash screen reads from a single CONTENT_TYPES registry so new content
types (e.g. "Quick Reference Sheet", "Infographic", "Talking Points") can
be added in a single line without changing anything else in the UI.

Run with:
    streamlit run app.py             # local / Streamlit Community Cloud
    streamlit run streamlit_app.py   # SiS canonical entry (shim that
                                     # invokes this same app code)

The builders live at `shared/course_app.py` and `shared/claims_app.py`
(or `course_app.py` / `claims_app.py` in the flat SiS bundle). They
detect unified mode via `st.session_state["_advice_unified_mode"]` and
skip their own page-config and CSS injection in that case.
"""
from __future__ import annotations

import streamlit as st

# Install Streamlit version-compatibility shims BEFORE any other module
# touches st.X — Streamlit-in-Snowflake bundles an older Streamlit that's
# missing st.html / st.popover / etc. Only the side effect matters.
import _compat  # noqa: F401, E402

st.set_page_config(
    page_title="MyAdvice Builder",
    page_icon="🩺",
    layout="wide",
    initial_sidebar_state="collapsed",
)

from carbon import inject_carbon_css, topbar, hero, _html_escape  # noqa: E402

inject_carbon_css()

# Mark unified mode so imported modules skip their own page-config / CSS.
st.session_state["_advice_unified_mode"] = True

# ---------------------------------------------------------------------------
# Content-type registry — add a new entry here to surface a new builder.
# Each entry needs: id, label, eyebrow, description, icon (emoji),
# render_callable (a no-arg function that draws the builder).
# ---------------------------------------------------------------------------
import course_app as course_app
import claims_app as claims_app

CONTENT_TYPES = [
    {
        "id": "course",
        "label": "Course",
        "eyebrow": "Multi-lesson",
        "description": (
            "A full CME-style course in MagMutual's Reducing Liability format: "
            "five lessons, an embedded case study per topic, and a "
            "10-question assessment."
        ),
        "icon": "📘",
        "render": course_app.render,
    },
    {
        "id": "claims_lesson",
        "label": "Claims Lesson",
        "eyebrow": "Single deep-dive",
        "description": (
            "One focused lesson grounded in a specific claim plus the "
            "matching Risk Playbook section. Ideal for spot training."
        ),
        "icon": "📕",
        "render": claims_app.render,
    },
    # New content types: add a dict here. The splash screen, sidebar selector,
    # and routing will all pick it up automatically.
    # Example skeleton (uncomment + implement render_callable):
    # {
    #     "id": "quick_ref",
    #     "label": "Quick Reference",
    #     "eyebrow": "1-pager",
    #     "description": "A printable pocket reference for a single risk.",
    #     "icon": "📒",
    #     "render": quick_ref_app.render,
    # },
]


def _by_id(content_id: str) -> dict | None:
    for c in CONTENT_TYPES:
        if c["id"] == content_id:
            return c
    return None


# ---------------------------------------------------------------------------
# Mode state
# ---------------------------------------------------------------------------
ss = st.session_state
ss.setdefault("_advice_mode", None)  # None = on splash; otherwise content_id


# ---------------------------------------------------------------------------
# Mode-switcher controls (no longer in the sidebar — sidebar is hidden).
# Each builder calls `render_back_to_picker()` to surface a small back link.
# ---------------------------------------------------------------------------
def render_back_to_picker():
    """Compact 'back to picker' link rendered above each builder's topbar.

    The sidebar has been removed; users return to the splash screen via
    this link. Switching content types means going back and picking a new
    one — with only a couple of types this is simpler than a dropdown.
    """
    bcol, _ = st.columns([1, 7])
    with bcol:
        if st.button("← All builders", type="secondary",
                     use_container_width=True,
                     help="Return to the splash screen to pick another content type."):
            ss["_advice_mode"] = None
            st.rerun()


# Shim so existing callers (`from app import render_mode_switcher`) keep
# working without code changes — the function now just renders the back link.
render_mode_switcher = render_back_to_picker


# ---------------------------------------------------------------------------
# Splash / picker view
# ---------------------------------------------------------------------------
def render_splash():
    topbar()
    hero(
        eyebrow="Choose your build",
        title="What would you like to build today?",
        subtitle=(
            "Each content type has its own builder. They share the same "
            "Risk Playbook and claims data, so you can hop between them."
        ),
    )

    # Card grid — uses the same .pb-card class as the playbook grid
    n = len(CONTENT_TYPES)
    spacer_l, content, spacer_r = st.columns([1, 6, 1])
    with content:
        cols = st.columns(min(n, 3))
        for col, c in zip(cols, CONTENT_TYPES):
            with col:
                st.markdown(
                    f"""
                    <div class="pb-card" style="min-height:200px;">
                        <div class="pb-spec">{c['eyebrow']}</div>
                        <div class="pb-title">{c['icon']} &nbsp; {_html_escape(c['label'])}</div>
                        <div style="color:#525252; font-size:0.88rem; line-height:1.5; margin-top:0.4rem;">
                            {_html_escape(c['description'])}
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                if st.button(f"Start →",
                             key=f"start_{c['id']}",
                             use_container_width=True):
                    ss["_advice_mode"] = c["id"]
                    st.rerun()


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
mode = ss["_advice_mode"]
selected = _by_id(mode) if mode else None

if not selected:
    render_splash()
else:
    render_mode_switcher()
    selected["render"]()
