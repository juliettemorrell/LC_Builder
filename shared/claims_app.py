"""App 2: Claims Lesson Generator.

Chat-first idle screen with a ranked claim picker. Pick a claim and the app
pulls the matching Risk Playbook section, generates the full lesson, scores
confidence, and switches to the split view (chat left, preview right) for
iteration with quick-action chips, history/undo, and direct edit.

Run with:
    streamlit run app_claims_lesson.py
"""
from __future__ import annotations

import streamlit as st

from shared.carbon import (
    inject_carbon_css, topbar, hero, confidence_badge, skeleton_card,
    render_dimension_bars, render_inline_confidence, sidebar_status,
    chat_empty_state, section_meta, sticky_chat_script,
    render_cortex_test_button, render_style_guide_panel,
    popover_or_expander,
)
from shared.cortex import complete, is_connected, cortex_status, temp_for
from shared.confidence import confidence_score
from shared.chat_orchestrator import apply_chat_edit, apply_quick_action
from shared.snowflake_client import (
    ranked_claims, get_driver, get_claim_summaries, get_full_claim,
    get_claim_risk_tags, get_risk_driver_stats, get_risk_library,
)
from shared.prompts import build_lesson, build_claim_selection
from shared.export import to_pdf_bytes, to_markdown
from shared.scorm import build_scorm_zip
from shared.quick_actions import QUICK_ACTIONS
from shared.saves import save_item, list_saves, load_save, delete_save
from shared.course_preview import render_claims_lesson_html
import streamlit.components.v1 as components
import time as _time


if not st.session_state.get("_advice_unified_mode"):
    st.set_page_config(
        page_title="Claims Lesson Generator | MyAdvice Builder",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    inject_carbon_css()


ss = st.session_state


def _init_state():
    ss.setdefault("cl_phase", "idle")
    ss.setdefault("cl_messages", [])
    ss.setdefault("cl_claim_id", None)
    ss.setdefault("cl_driver_id", None)
    ss.setdefault("cl_lesson", "")
    ss.setdefault("cl_history", [])
    ss.setdefault("cl_confidence", None)
    ss.setdefault("cl_sources", [])
    ss.setdefault("cl_split_ratio", 35)
    ss.setdefault("cl_edit_mode", False)
    ss.setdefault("cl_settings", {"model": "claude-opus-4-7", "temperature": 0.3})
    ss.setdefault("cl_search_query", "")
    ss.setdefault("cl_save_id", None)
    ss.setdefault("cl_save_toast", None)


_init_state()


def _word_count(text: str) -> int:
    return len([w for w in (text or "").split() if w])


def _est_tokens(text: str) -> int:
    return max(1, int(len(text or "") / 4))


def _save_current() -> str | None:
    if not ss.cl_lesson:
        return None
    driver = get_driver(ss.cl_driver_id) or {}
    title = f"Claims Lesson · {ss.cl_claim_id} · {driver.get('DRIVER','')}"
    payload = {
        "claim_id": ss.cl_claim_id,
        "driver_id": ss.cl_driver_id,
        "lesson": ss.cl_lesson,
        "messages": ss.cl_messages,
        "history": ss.cl_history,
        "sources": ss.cl_sources,
        "settings": ss.cl_settings,
    }
    saved = save_item(
        kind="lesson", title=title, payload=payload,
        save_id=ss.cl_save_id, claim_id=ss.cl_claim_id,
    )
    ss.cl_save_id = saved.save_id
    ss.cl_save_toast = ("Saved", saved.save_id, _time.time())
    return saved.save_id


def _load_save(save_id: str) -> bool:
    item = load_save(save_id)
    if not item or item.kind != "lesson":
        return False
    p = item.payload or {}
    ss.cl_claim_id = p.get("claim_id") or item.claim_id
    ss.cl_driver_id = p.get("driver_id")
    ss.cl_lesson = p.get("lesson", "")
    ss.cl_messages = p.get("messages", [])
    ss.cl_history = p.get("history", [])
    ss.cl_sources = p.get("sources", [])
    ss.cl_settings = p.get("settings", ss.cl_settings)
    ss.cl_save_id = item.save_id
    ss.cl_confidence = confidence_score(
        ss.cl_lesson, ss.cl_sources, output_type="claims_lesson",
    ) if ss.cl_lesson else None
    ss.cl_phase = "editing"
    return True


def _strip_leading_h1(md: str) -> str:
    """Drop the first '# Title' line so it doesn't duplicate the section header."""
    if not md:
        return md
    lines = md.lstrip().splitlines()
    if lines and lines[0].lstrip().startswith("# ") and not lines[0].lstrip().startswith("## "):
        rest = lines[1:]
        while rest and not rest[0].strip():
            rest = rest[1:]
        return "\n".join(rest)
    return md


def _push_history():
    if ss.cl_lesson:
        ss.cl_history.insert(0, ss.cl_lesson)
        del ss.cl_history[8:]


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
def run_ranking():
    candidate_df = ranked_claims(top_n=10)
    drivers = get_risk_library().to_dict("records")
    stats = get_risk_driver_stats().to_dict("records")
    candidates = candidate_df.to_dict("records")
    prompt = build_claim_selection(candidates, stats, drivers)
    res = complete(prompt, kind="claim_selection")
    ss.cl_messages.append({"role": "assistant", "content": res.text})


def kickoff_lesson(claim_id: str):
    ss.cl_phase = "generating"
    ss.cl_claim_id = claim_id

    summaries = get_claim_summaries()
    claim_match = summaries[summaries["DOCUMENT_ID"] == claim_id]
    if len(claim_match) == 0:
        ss.cl_messages.append({
            "role": "assistant",
            "content": f"I couldn't find claim `{claim_id}` in the summaries.",
        })
        ss.cl_phase = "idle"
        return
    claim = claim_match.iloc[0].to_dict()

    tags = get_claim_risk_tags()
    tag_match = tags[tags["DOCUMENT_ID"] == claim_id]
    if len(tag_match) == 0:
        ss.cl_messages.append({
            "role": "assistant",
            "content": f"No risk-driver tag found for `{claim_id}`. Skipping playbook grounding.",
        })
        playbook = {}
    else:
        ss.cl_driver_id = tag_match.iloc[0]["DRIVER_ID"]
        playbook = get_driver(ss.cl_driver_id) or {}

    ss.cl_messages.append({
        "role": "assistant",
        "content": (
            f"Generating a claims lesson for **{claim_id}** "
            f"({claim.get('SPECIALTY','')}), grounded in the "
            f"**{playbook.get('DRIVER','no-playbook-match')}** playbook."
        ),
    })

    progress = st.progress(0.0, text="Pulling full claim text…")
    full_extract = get_full_claim(claim_id)
    progress.progress(0.4, text="Generating the lesson…")
    res = complete(build_lesson(claim, playbook, full_extract), kind="lesson")
    ss.cl_lesson = res.text
    ss.cl_history = []
    ss.cl_sources = [
        f"CLAIM SUMMARY:\n{claim.get('SUMMARY','')}",
        f"PLAYBOOK SECTION:\n{playbook.get('OVERVIEW','')}\n\n{playbook.get('RISK_BRIEF','')}",
    ]
    progress.progress(0.85, text="Scoring confidence…")
    ss.cl_confidence = confidence_score(
        ss.cl_lesson, ss.cl_sources, output_type="claims_lesson",
    )
    progress.progress(1.0)
    progress.empty()

    ss.cl_messages.append({
        "role": "assistant",
        "content": "Lesson is ready. Use the chips above the chat for one-click revisions, or just describe what to change.",
    })
    ss.cl_phase = "editing"


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
def render_sidebar():
    """Deprecated. Use render_tools_popover() in the toolbar instead."""
    return


def render_tools_popover():
    """Render the Tools popover that replaces the old sidebar.

    Status, saved drafts, MM Copy Guide, and recent errors. Settings (model
    + temperature) are no longer user-adjustable — they're hardcoded
    per-prompt-kind in `shared/cortex.py`.
    """
    s = cortex_status()
    with popover_or_expander(":material/build: Tools", use_container_width=True,
                              help="Status, saved drafts, style guide."):
        st.markdown("##### Connection")
        sidebar_status(
            connected=s["connection_live"],
            mode=("Live · Cortex" if s["connection_live"] else "Mock · Local"),
            model="claude-opus-4-7",
            last_latency_s=s["last_latency_s"],
            mock_count=s["calls_mocked"],
            real_count=s["calls_real"],
        )
        render_cortex_test_button()

        st.markdown("##### Saved drafts")
        saves = list_saves("lesson")
        if not saves:
            st.caption("No saved lessons yet. Click **Save draft** in the toolbar.")
        else:
            for it in saves[:8]:
                short = (it.title[:38] + "…") if len(it.title) > 38 else it.title
                ldcol1, ldcol2 = st.columns([4, 1])
                with ldcol1:
                    if st.button(short, key=f"load_{it.save_id}",
                                 use_container_width=True, type="secondary",
                                 help=f"Saved {it.updated_at} · claim {it.claim_id or '—'}"):
                        if _load_save(it.save_id):
                            st.rerun()
                with ldcol2:
                    if st.button("×", key=f"del_{it.save_id}",
                                 help="Delete", use_container_width=True):
                        delete_save(it.save_id)
                        st.rerun()

        render_style_guide_panel()

        # ---- Inspect last Cortex call -----------------------------------
        st.markdown("##### Inspect last call")
        last_kind = s.get("last_kind") or "—"
        last_model = s.get("last_model") or "—"
        last_temp = s.get("last_temperature")
        if s.get("last_prompt_preview"):
            with st.expander(
                f"{last_kind} · {last_model} · "
                f"T={last_temp if last_temp is not None else '—'} · "
                f"{int((s.get('last_latency_s') or 0)*1000)}ms",
                expanded=False,
            ):
                st.caption("Prompt (first 2 KB)")
                st.code(s["last_prompt_preview"], language="text")
                st.caption("Response (first 2 KB)")
                st.code(s.get("last_response_preview") or "", language="text")
        else:
            st.caption("Generate something to see the last prompt + response here.")
        if s.get("retries"):
            st.caption(f"Cortex retries this session: {s['retries']}")

        # ---- Edit history (chat audit log) ------------------------------
        st.markdown("##### Edit history")
        from shared.chat_log import list_recent, to_csv
        recent = list_recent(limit=20, save_id=ss.get("cl_save_id"))
        if not recent:
            st.caption(
                "No edits logged yet. Every chat instruction + quick "
                "action is captured here for review."
            )
        else:
            with st.expander(f"Recent edits ({len(recent)})",
                              expanded=False):
                for e in recent[:10]:
                    st.markdown(
                        f"**{e.section_id}** · _{e.kind}_ · "
                        f"{e.occurred_at} · {e.latency_ms}ms\n\n"
                        f"> {e.instruction[:160]}"
                    )
            csv_bytes = to_csv(recent).encode("utf-8")
            st.download_button(
                "Download edit log (CSV)", data=csv_bytes,
                file_name=f"lesson_edit_log_{ss.get('cl_save_id') or 'session'}.csv",
                mime="text/csv", use_container_width=True,
            )

        if s["errors"]:
            with st.expander("Cortex errors"):
                for e in s["errors"]:
                    st.code(e, language="text")
        sf_errors = st.session_state.get("_snowflake_errors", [])
        if sf_errors:
            with st.expander(f"Snowflake errors ({len(sf_errors)})"):
                for e in sf_errors[-5:]:
                    st.code(e, language="text")
        photo_errors = st.session_state.get("_photo_errors", [])
        if photo_errors:
            with st.expander(f"Photo loading errors ({len(photo_errors)})"):
                for e in photo_errors[-5:]:
                    st.code(e, language="text")
        st.caption(
            "Settings: model + temperature are hardcoded per prompt in "
            "`shared/cortex.py` (MODELS / TEMPS). Not user-adjustable so "
            "clinical accuracy stays consistent across users and runs."
        )


# ---------------------------------------------------------------------------
# Idle / chat-first
# ---------------------------------------------------------------------------
def render_idle():
    s = cortex_status()
    topbar(
        "Claims Lesson Generator",
        mode="DRAFT",
        connection_pill=("Live" if s["connection_live"] else "Mock"),
        model_pill=(s["last_model"] or "claude-opus-4-7"),
    )
    hero(
        eyebrow="Build mode",
        title="Which claim should we teach from?",
        subtitle="We rank claims by teaching value across the risk-driver tags. Pick one and we'll write the lesson grounded in the matching Risk Playbook section.",
    )

    df = ranked_claims(top_n=20)

    ss.setdefault("cl_specialty_filter", "All specialties")
    ss.setdefault("cl_playbook_filter", "All playbooks")

    # Filter row: search + specialty + playbook + claim selector + Generate
    sc1, sc2, sc3 = st.columns([2, 2, 2])
    with sc1:
        ss.cl_search_query = st.text_input(
            "Filter", value=ss.cl_search_query,
            placeholder="Search summaries…",
            label_visibility="collapsed",
        )
    with sc2:
        specialties = sorted({s for s in df["SPECIALTY"].dropna().unique()})
        ss.cl_specialty_filter = st.selectbox(
            "Specialty",
            options=["All specialties"] + specialties,
            index=(["All specialties"] + specialties).index(ss.cl_specialty_filter)
                if ss.cl_specialty_filter in (["All specialties"] + specialties) else 0,
            label_visibility="collapsed",
        )

    # Apply filters
    if ss.cl_search_query:
        q = ss.cl_search_query.lower()
        mask = (df["SPECIALTY"].str.lower().str.contains(q, na=False)
                | df["DRIVER"].str.lower().str.contains(q, na=False)
                | df["SUMMARY"].fillna("").str.lower().str.contains(q, na=False))
        df = df[mask].reset_index(drop=True)
    if ss.cl_specialty_filter != "All specialties":
        df = df[df["SPECIALTY"] == ss.cl_specialty_filter].reset_index(drop=True)

    with sc3:
        playbook_opts = ["All playbooks"] + sorted({d for d in df["DRIVER"].dropna().unique()})
        ss.cl_playbook_filter = st.selectbox(
            "Risk playbook",
            options=playbook_opts,
            index=playbook_opts.index(ss.cl_playbook_filter)
                if ss.cl_playbook_filter in playbook_opts else 0,
            label_visibility="collapsed",
        )

    if ss.cl_playbook_filter != "All playbooks":
        df = df[df["DRIVER"] == ss.cl_playbook_filter].reset_index(drop=True)

    pc1, pc2 = st.columns([3, 1])
    with pc1:
        chosen = st.selectbox(
            "Pick a claim",
            options=["—"] + df["DOCUMENT_ID"].tolist(),
            label_visibility="collapsed",
            placeholder=f"Pick a claim ({len(df)} match)",
        )
    with pc2:
        if st.button("Generate", type="primary", use_container_width=True,
                     disabled=(chosen == "—")):
            kickoff_lesson(chosen)
            st.rerun()

    st.markdown("##### Top candidate claims")
    show_cols = ["DOCUMENT_ID", "SPECIALTY", "DRIVER", "AGE_RANGE",
                 "TAG_CONFIDENCE", "TEACHING_SCORE", "SUMMARY"]
    display_df = df[[c for c in show_cols if c in df.columns]].copy()
    if "SUMMARY" in display_df.columns:
        display_df["SUMMARY"] = display_df["SUMMARY"].apply(
            lambda s: (s[:140] + "…") if isinstance(s, str) and len(s) > 140 else s
        )
    st.dataframe(
        display_df, use_container_width=True, hide_index=True,
        column_config={
            "DOCUMENT_ID": st.column_config.TextColumn("Claim", width="small"),
            "SPECIALTY": st.column_config.TextColumn("Specialty", width="small"),
            "DRIVER": st.column_config.TextColumn("Risk driver", width="medium"),
            "AGE_RANGE": st.column_config.TextColumn("Age", width="small"),
            "TAG_CONFIDENCE": st.column_config.ProgressColumn(
                "Tag conf.", format="%.2f", min_value=0.0, max_value=1.0, width="small",
            ),
            "TEACHING_SCORE": st.column_config.ProgressColumn(
                "Teach score", format="%.3f", min_value=0.0, max_value=0.2, width="small",
            ),
            "SUMMARY": st.column_config.TextColumn("Summary preview", width="large"),
        },
    )

    rcol1, rcol2 = st.columns([1, 4])
    with rcol1:
        if st.button("AI: rank & recommend", use_container_width=True,
                     help="Run the claim-selection prompt and get a recommendation."):
            with st.spinner("Ranking…"):
                run_ranking()
            st.rerun()

    if ss.cl_messages:
        with st.expander("AI ranking output", expanded=True):
            for msg in ss.cl_messages:
                with st.chat_message(msg["role"]):
                    st.markdown(msg["content"])

    user_msg = st.chat_input("Or describe the kind of claim you want to teach from…")
    if user_msg:
        ss.cl_messages.append({"role": "user", "content": user_msg})
        match = _match_claim(user_msg, df)
        if match:
            kickoff_lesson(match)
            st.rerun()
        else:
            ss.cl_messages.append({
                "role": "assistant",
                "content": "I couldn't pin down a single claim. Pick one from the table above and I'll generate it.",
            })
            st.rerun()


def _match_claim(text: str, df) -> str | None:
    t = text.lower()
    for cid in df["DOCUMENT_ID"]:
        if cid.lower() in t:
            return cid
    best, score = None, 0
    for _, row in df.iterrows():
        s = (str(row.get("SUMMARY", "")) + " " + str(row.get("DRIVER", ""))).lower()
        ov = sum(1 for tok in t.split() if len(tok) > 4 and tok in s)
        if ov > score:
            best, score = row["DOCUMENT_ID"], ov
    return best if score >= 2 else None


# ---------------------------------------------------------------------------
# Generating — animated skeleton
# ---------------------------------------------------------------------------
def render_generating():
    s = cortex_status()
    topbar(
        "Claims Lesson Generator",
        mode="GENERATING",
        connection_pill=("Live" if s["connection_live"] else "Mock"),
        model_pill=(s["last_model"] or "claude-opus-4-7"),
    )
    st.caption("Generating your lesson…")
    skeleton_card("Lesson")
    for msg in ss.cl_messages[-2:]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])


# ---------------------------------------------------------------------------
# Editing — split view
# ---------------------------------------------------------------------------
def render_editing():
    s = cortex_status()
    topbar(
        f"Claims Lesson · {ss.cl_claim_id}",
        mode="EDIT",
        connection_pill=("Live" if s["connection_live"] else "Mock"),
        model_pill=(s["last_model"] or "claude-opus-4-7"),
    )

    # Toolbar: Tools popover · spacer · New · Save · PDF · SCORM · MD.
    ss.cl_split_ratio = 35
    ttools, _spacer, tnew, tsave, tpdf, tscorm, tmd = st.columns(
        [1.2, 0.8, 1, 1, 1.1, 1.1, 0.9]
    )
    with ttools:
        render_tools_popover()
    with tnew:
        if st.button("New lesson", type="secondary", use_container_width=True):
            for k in ["cl_messages", "cl_claim_id", "cl_driver_id", "cl_lesson",
                      "cl_history", "cl_confidence", "cl_sources", "cl_edit_mode",
                      "cl_save_id"]:
                if k in ss:
                    del ss[k]
            ss.cl_phase = "idle"
            st.rerun()
    with tsave:
        save_label = "Update save" if ss.cl_save_id else "Save draft"
        if st.button(save_label, use_container_width=True,
                     help="Persist this lesson (updates in place if already saved)."):
            with st.spinner("Saving…"):
                _save_current()
            st.rerun()

    title = f"Claims Lesson · {ss.cl_claim_id}"
    sections_for_export = {"Claims Lesson": ss.cl_lesson}

    # Cache exports keyed on content hash — same pattern as the course gen
    # so we don't rebuild fonts on every Streamlit rerun.
    import hashlib
    sig = hashlib.sha256(
        (title + "|" + repr(sorted(sections_for_export.items())))
        .encode("utf-8")
    ).hexdigest()
    cache = ss.setdefault("cl_export_cache", {})
    if cache.get("sig") != sig:
        cache.clear()
        cache["sig"] = sig
        cache["pdf"] = to_pdf_bytes(title, sections_for_export)
        cache["scorm"] = build_scorm_zip(
            title, ss.cl_claim_id or "lesson", sections_for_export)
        cache["md"] = to_markdown(title, sections_for_export).encode("utf-8")

    with tpdf:
        st.download_button(
            "Export PDF", data=cache["pdf"],
            file_name=f"claims_lesson_{ss.cl_claim_id}.pdf",
            mime="application/pdf", use_container_width=True,
        )
    with tscorm:
        st.download_button(
            "Export SCORM", data=cache["scorm"],
            file_name=f"claims_lesson_{ss.cl_claim_id}_scorm.zip",
            mime="application/zip", use_container_width=True,
            help="SCORM 1.2 package — upload to your LMS.",
        )
    with tmd:
        st.download_button(
            "Markdown", data=cache["md"],
            file_name=f"claims_lesson_{ss.cl_claim_id}.md",
            mime="text/markdown", use_container_width=True,
            help="Plain markdown source for diffing or copying elsewhere.",
        )

    if ss.cl_save_toast and (_time.time() - ss.cl_save_toast[2] < 4):
        st.success(f"Saved as `{ss.cl_save_toast[1]}`. See saved drafts in the sidebar.")

    chat_w, preview_w = ss.cl_split_ratio, 100 - ss.cl_split_ratio
    chat_col, preview_col = st.columns([chat_w, preview_w], gap="large")
    with chat_col:
        _render_chat_pane()
    with preview_col:
        _render_preview_pane()
    sticky_chat_script()


def _render_chat_pane():
    st.markdown("##### Chat")
    msg_container = st.container(height=420, border=False)
    with msg_container:
        if not ss.cl_messages:
            chat_empty_state([
                "Tighten the timeline",
                "Add one more pivotal moment",
                "Make the medical summary more specific",
                "Add the standard-of-care reference",
            ])
        for msg in ss.cl_messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

    for row_start in (0, 3):
        chip_cols = st.columns(3)
        for i, a in enumerate(QUICK_ACTIONS[row_start:row_start + 3]):
            with chip_cols[i]:
                if st.button(a["label"], key=f"qa_{a['id']}",
                             type="secondary",
                             use_container_width=True, help=a["instruction"]):
                    _handle_quick_action(a["id"])
                    st.rerun()

    user_msg = st.chat_input("Tell me what to change…")
    if user_msg:
        _handle_chat_message(user_msg)
        st.rerun()


def _handle_quick_action(action_id: str):
    from shared.quick_actions import by_id
    action = by_id(action_id) or {}
    ss.cl_messages.append({
        "role": "user",
        "content": f"**{action.get('label','?')}** → _Claims lesson_",
    })
    _push_history()
    sources_block = "\n\n---\n\n".join(ss.cl_sources)
    res = apply_quick_action("Claims Lesson", ss.cl_lesson, sources_block,
                              action_id, section_id="claims_lesson",
                              save_id=ss.get("cl_save_id"))
    ss.cl_lesson = res["text"]
    ss.cl_confidence = confidence_score(
        ss.cl_lesson, ss.cl_sources, output_type="claims_lesson",
    )
    ss.cl_messages.append({
        "role": "assistant",
        "content": f"Applied **{action.get('label','?')}**. New confidence: **{ss.cl_confidence.grade}**.",
    })


def _handle_chat_message(user_msg: str):
    ss.cl_messages.append({"role": "user", "content": user_msg})
    _push_history()
    sources_block = "\n\n---\n\n".join(ss.cl_sources)
    res = apply_chat_edit("Claims Lesson", ss.cl_lesson, sources_block,
                           user_msg, section_id="claims_lesson",
                           save_id=ss.get("cl_save_id"))
    ss.cl_lesson = res["text"]
    ss.cl_confidence = confidence_score(
        ss.cl_lesson, ss.cl_sources, output_type="claims_lesson",
    )
    ss.cl_messages.append({
        "role": "assistant",
        "content": f"Lesson updated ({res['latency_s']:.1f}s). New confidence: **{ss.cl_confidence.grade}**.",
    })


def _render_preview_pane():
    driver = get_driver(ss.cl_driver_id) or {}
    from shared.carbon import _html_escape
    st.markdown("### Claims Lesson")
    st.markdown(
        f"<span class='source-pill'>Claim {_html_escape(ss.cl_claim_id or '')}</span>"
        f"<span class='source-pill'>{_html_escape(driver.get('SPECIALTY',''))}</span>"
        f"<span class='source-pill'>{_html_escape((driver.get('DRIVER','') or '')[:60])}</span>",
        unsafe_allow_html=True,
    )
    st.markdown("")

    conf = ss.cl_confidence
    badge_grade = conf.grade if conf else None

    # Flowing layout — no bordered card. Header row + collapsed action menu
    # + content. Same pattern as Course Generator for consistency.
    h1, h2 = st.columns([3, 1])
    with h1:
        st.markdown("#### Lesson")
        section_meta(_est_tokens(ss.cl_lesson), _word_count(ss.cl_lesson))
    with h2:
        st.markdown(
            f"<div style='text-align:right;padding-top:0.4rem'>{confidence_badge(badge_grade)}</div>",
            unsafe_allow_html=True,
        )

    # Section actions are always visible — they're the entire point of
    # being able to iterate on the lesson.
    a1, a2, a3 = st.columns([1, 1, 1])
    with a1:
        if st.button("Re-run", key="regen_lesson", use_container_width=True,
                     help="Regenerate the lesson from scratch"):
            with st.spinner("Regenerating…"):
                kickoff_lesson(ss.cl_claim_id)
            st.rerun()
    with a2:
        edit_on = st.toggle(
            ":material/edit: Edit markdown", value=ss.cl_edit_mode,
            key="editmode_lesson",
            help="Toggle a markdown textarea so you can hand-edit "
                 "the lesson directly.",
        )
        ss.cl_edit_mode = edit_on
    with a3:
        disabled = not ss.cl_history
        undo_label = f"Undo · {len(ss.cl_history)}" if ss.cl_history else "Undo"
        if st.button(undo_label, key="undo_lesson",
                     use_container_width=True, disabled=disabled,
                     help="Restore the previous version"):
            ss.cl_lesson = ss.cl_history[0]
            ss.cl_history = ss.cl_history[1:]
            ss.cl_confidence = confidence_score(
                ss.cl_lesson, ss.cl_sources, output_type="claims_lesson",
            )
            st.rerun()

    if conf:
        decision = conf.publication_decision.title().replace("_", " ")
        st.caption(f"**{decision}** · {conf.summary[:200]}")
    if conf and conf.raw:
        render_inline_confidence(conf.raw.get("dimension_scores", {}))

    if ss.cl_edit_mode:
        new_text = st.text_area(
            "Edit markdown directly",
            value=ss.cl_lesson, height=420,
            key="editor_lesson", label_visibility="collapsed",
        )
        sb1, _sb2 = st.columns([1, 5])
        with sb1:
            if st.button("Save", key="save_lesson", use_container_width=True):
                _push_history()
                ss.cl_lesson = new_text
                ss.cl_confidence = confidence_score(
                    new_text, ss.cl_sources, output_type="claims_lesson",
                )
                ss.cl_edit_mode = False
                st.rerun()
    else:
        # Render the lesson in the SAME MM-styled iframe the Course
        # Generator uses — so colors, corner radii, card borders, dark-
        # gray Pause-and-reflect banner, accent color, list spacing,
        # and Lato typography all match across the two apps.
        title_for_preview = (
            f"Claims Lesson · {(driver.get('SPECIALTY','') or '').strip()}"
            f"{(' · ' + (driver.get('DRIVER','') or '').strip()) if driver.get('DRIVER') else ''}"
        )
        if (ss.cl_lesson or "").strip():
            html_doc = render_claims_lesson_html(
                title=title_for_preview,
                lesson_md=ss.cl_lesson,
                eyebrow="MagMutual · Claims Lesson",
            )
            components.html(html_doc, height=900, scrolling=True)
            st.caption(
                "This preview is what learners will see. The same HTML is "
                "bundled into the SCORM export."
            )
        else:
            st.markdown("_(empty)_")

        with st.expander("View source"):
            for s in ss.cl_sources:
                preview = s[:1500] + ("…" if len(s) > 1500 else "")
                st.code(preview, language="text")
        if conf and conf.raw:
            with st.expander("Full confidence detail"):
                render_dimension_bars(conf.raw.get("dimension_scores", {}))
                if conf.raw.get("blocking_issues"):
                    st.warning("Blocking issues: " + ", ".join(conf.raw["blocking_issues"]))


# ---------------------------------------------------------------------------
# Router (callable from the unified app.py or standalone)
# ---------------------------------------------------------------------------
def render():
    _init_state()  # idempotent; safe to call every rerun
    # Sidebar was retired; tool controls now live in the toolbar popover.
    if ss.cl_phase == "idle":
        render_idle()
    elif ss.cl_phase == "generating":
        render_generating()
    else:
        render_editing()


if not st.session_state.get("_advice_unified_mode"):
    render()
