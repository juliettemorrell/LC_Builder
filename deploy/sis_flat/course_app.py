"""App 1: Course Generator.

Chat-first idle screen. After a course is generated, switches to a split view:
chat on the left, live preview on the right, with an adjustable width slider.

Sections in the preview each have:
  - Confidence badge with dimension-bar detail
  - Regenerate, Undo, AI-edit (chat with quick-action chips), Direct edit
  - Source freshness pill, view-source expander
  - Token / word count

Run with:
    streamlit run app_course_generator.py
"""
from __future__ import annotations

import streamlit as st

from carbon import (
    inject_carbon_css, topbar, hero, confidence_badge, skeleton_card,
    render_dimension_bars, render_inline_confidence, sidebar_status,
    chat_empty_state, section_meta, playbook_card_html, sticky_chat_script,
    render_cortex_test_button, render_style_guide_panel, _html_escape,
    popover_or_expander,
)
from snowflake_client import get_risk_driver_stats
from cortex import complete, is_connected, cortex_status, temp_for
from cortex import confidence_score
from chat import apply_chat_edit, apply_quick_action
from snowflake_client import (
    list_risk_drivers, get_driver, claims_for_driver, get_risk_library,
    top_contributing_factors,
    stats_key_for_playbook_title,
    chart_factors_from_playbook,
)
from prompts import (
    build_course_body, build_assessment, build_closing,
    build_embedded_lesson_for_topic, playbook_strategies_text, playbook_factors,
)
import re
import time
from export import to_pdf_bytes, to_markdown
from export import build_scorm_zip
from course_preview import render_course_html
from photos import (list_photos, get_photo, add_uploaded_photo,
                            auto_pick_for_topic, search_photos)
from chat import QUICK_ACTIONS
from saves import save_item, list_saves, load_save, delete_save
import streamlit.components.v1 as components


# ---------------------------------------------------------------------------
# Page setup — only runs when this file is the entry point. The unified
# `app.py` sets _advice_unified_mode in session_state before importing this
# module, so the page-config + CSS are deferred to app.py in that case.
# ---------------------------------------------------------------------------
if not st.session_state.get("_advice_unified_mode"):
    st.set_page_config(
        page_title="Course Generator | MyAdvice Builder",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    inject_carbon_css()


# ---------------------------------------------------------------------------
# Session state defaults
# ---------------------------------------------------------------------------
# IMPORTANT: must run on EVERY rerun (per session), not just at module import,
# because Python caches the module at the process level — the first session
# would set defaults but new sessions in the same process would miss them.
# `_init_state()` is called at the top of `render()` to handle this.
ss = st.session_state

# Static "anchor" sections always present. Labels include the MagMutual
# "Lesson N of 5" prefix so the preview's lesson sequence is unbroken.
ANCHOR_LABELS = {
    "course_body": "Lessons 1-3 · Body",
    "assessment": "Lesson 4 of 5 · Assessment",
    "closing": "Lesson 5 of 5 · Closing",
}


def _init_state():
    ss.setdefault("cg_phase", "idle")            # idle | generating | editing
    ss.setdefault("cg_messages", [])
    ss.setdefault("cg_driver_id", None)
    ss.setdefault("cg_sections", {})             # sid -> markdown
    ss.setdefault("cg_confidence", {})           # sid -> ConfidenceResult
    ss.setdefault("cg_sources", {})              # sid -> list[str]
    ss.setdefault("cg_history", {})              # sid -> list[str]
    ss.setdefault("cg_split_ratio", 35)
    ss.setdefault("cg_edit_mode", {})
    ss.setdefault("cg_target_section", "All sections")
    ss.setdefault("cg_settings", {"model": "claude-opus-4-7", "temperature": 0.3})
    ss.setdefault("cg_search_query", "")
    ss.setdefault("cg_save_id", None)
    ss.setdefault("cg_save_toast", None)
    # Live HTML is the default — it's the styled MagMutual-format preview
    # the team is actually evaluating against. Editable shows per-section
    # cards with a working markdown textarea (toggle Edit on each card).
    ss.setdefault("cg_preview_mode", "Live HTML")
    # Default ordering matches MagMutual's lesson sequence. kickoff_generation
    # inserts per-topic lesson cards between course_body and assessment.
    ss.setdefault("cg_section_order", ["course_body", "assessment", "closing"])
    ss.setdefault("cg_section_labels", dict(ANCHOR_LABELS))
    ss.setdefault("cg_section_meta", {})


# Run state init now (covers the standalone-app case and the first import in
# unified mode). render() will call it again on each rerun for safety.
_init_state()


def _section_order() -> list[str]:
    return ss.cg_section_order


def _section_label(sid: str) -> str:
    return ss.cg_section_labels.get(sid, sid)


# Maximum number of abridged lessons embedded in a course
MAX_EMBEDDED_LESSONS = 5
MIN_EMBEDDED_LESSONS = 3


def _extract_topics(course_body: str) -> list[str]:
    """Extract topic titles for embedded case-study lessons.

    Strategy:
      1. Look for "## Lesson 3 ..." section header. Within that section grab
         the H3 sub-headings (those are the topics). Skip "Introduction" etc.
      2. If no Lesson-3 marker exists, fall back to all H2 module-style
         headings minus structural ones.
    """
    skip = {"learning objectives", "summary", "key takeaways", "overview",
            "introduction", "conclusion", "references", "definitions",
            "definition of key terms", "average annual frequency",
            "average annual indemnity", "top allegations", "degree of injury",
            "pause and reflect", "what's next", "what you'll learn",
            "objectives", "course overview", "loss trends", "closing"}
    lines = course_body.splitlines()

    # Pass 1: try to scope to the "Lesson 3" section (H2 starting with "Lesson 3")
    in_lesson3 = False
    h3_topics: list[str] = []
    for line in lines:
        s = line.strip()
        m_h2 = re.match(r"^##\s+(.+?)\s*$", s)
        if m_h2:
            head = m_h2.group(1).lower()
            in_lesson3 = head.startswith("lesson 3") or "key loss driver" in head
            continue
        if in_lesson3:
            m_h3 = re.match(r"^###\s+(.+?)\s*$", s)
            if m_h3:
                clean = m_h3.group(1).strip()
                clean = re.sub(r"^Topic\s+\d+\s*[:\-—]\s*", "", clean, flags=re.I).strip()
                if clean.lower() in skip:
                    continue
                h3_topics.append(clean)
    if h3_topics:
        return h3_topics

    # Pass 2: fall back to H2 module-style headings
    topics = []
    for line in lines:
        m = re.match(r"^##\s+(.+?)\s*$", line.strip())
        if not m:
            continue
        raw = m.group(1).strip()
        clean = re.sub(r"^(Module|Lesson)\s+\d+(?:\s+of\s+\d+)?\s*[:\-—]\s*",
                       "", raw, flags=re.I).strip()
        if clean.lower() in skip:
            continue
        topics.append(clean)
    return topics


def _push_history(sid: str):
    """Snapshot the current section text into history before mutating."""
    cur = ss.cg_sections.get(sid)
    if cur:
        stack = ss.cg_history.setdefault(sid, [])
        stack.insert(0, cur)
        del stack[8:]  # keep last 8


def _word_count(text: str) -> int:
    return len([w for w in (text or "").split() if w])


def _est_tokens(text: str) -> int:
    return max(1, int(len(text or "") / 4))


def _save_current(prompt_for_title: bool = False) -> str | None:
    """Persist the current course to Snowflake (or JSON fallback). Returns save_id."""
    if not ss.cg_sections:
        return None
    driver = get_driver(ss.cg_driver_id) or {}
    title = driver.get("TITLE") or driver.get("DRIVER", "Untitled course")
    payload = {
        "phase": "editing",
        "driver_id": ss.cg_driver_id,
        "messages": ss.cg_messages,
        "sections": ss.cg_sections,
        "sources": ss.cg_sources,
        "history": ss.cg_history,
        "section_order": ss.cg_section_order,
        "section_labels": ss.cg_section_labels,
        "section_meta": ss.cg_section_meta,
        "confidence_grades": {sid: (c.grade if c else None)
                              for sid, c in ss.cg_confidence.items()},
        "settings": ss.cg_settings,
    }
    saved = save_item(
        kind="course",
        title=title,
        payload=payload,
        save_id=ss.cg_save_id,
        driver_id=ss.cg_driver_id,
    )
    ss.cg_save_id = saved.save_id
    ss.cg_save_toast = ("Saved", saved.save_id, time.time())
    return saved.save_id


def _load_save(save_id: str) -> bool:
    item = load_save(save_id)
    if not item or item.kind != "course":
        return False
    p = item.payload or {}
    ss.cg_driver_id = p.get("driver_id") or item.driver_id
    ss.cg_messages = p.get("messages", [])
    ss.cg_sections = p.get("sections", {})
    ss.cg_sources = p.get("sources", {})
    ss.cg_history = p.get("history", {})
    ss.cg_settings = p.get("settings", ss.cg_settings)
    ss.cg_section_order = p.get("section_order", list(ss.cg_sections.keys()))
    ss.cg_section_labels = p.get("section_labels", dict(ANCHOR_LABELS))
    ss.cg_section_meta = p.get("section_meta", {})
    ss.cg_save_id = item.save_id
    ss.cg_confidence = {}
    for sid in ss.cg_section_order:
        if sid in ss.cg_sections:
            ss.cg_confidence[sid] = confidence_score(
                ss.cg_sections[sid], ss.cg_sources.get(sid, []),
                output_type="course_generator",
            )
    ss.cg_phase = "editing"
    return True


def _strip_leading_h1(md: str) -> str:
    """Drop the first '# Title' line so it doesn't duplicate the section header."""
    if not md:
        return md
    lines = md.lstrip().splitlines()
    if lines and lines[0].lstrip().startswith("# ") and not lines[0].lstrip().startswith("## "):
        rest = lines[1:]
        # also drop one trailing blank line after the title for tighter top
        while rest and not rest[0].strip():
            rest = rest[1:]
        return "\n".join(rest)
    return md


def _refresh_downstream_sources(driver: dict):
    """When course_body changes, refresh downstream sources to reference it."""
    los = _learning_objectives(driver)
    cb = ss.cg_sections.get("course_body", "")
    risk_brief_src = (f"RISK_BRIEF: {driver.get('RISK_BRIEF','')}\n\n"
                      f"PLAYBOOK:\n{playbook_strategies_text(driver)}")
    if "assessment" in ss.cg_sections:
        ss.cg_sources["assessment"] = [cb, "Learning objectives:\n" + "\n".join(los)]
    if "closing" in ss.cg_sections:
        ss.cg_sources["closing"] = [cb, risk_brief_src]
    for sid in _section_order():
        if sid.startswith("lesson_"):
            meta = ss.cg_section_meta.get(sid, {})
            ss.cg_sources[sid] = [
                cb,
                f"Topic anchor: {meta.get('topic','')}",
                f"Claim summary:\n{meta.get('claim_summary','')}",
            ]


def _learning_objectives(driver: dict) -> list[str]:
    los = driver.get("LEARNING_OBJECTIVES", []) or []
    if isinstance(los, str):
        los = [s for s in los.splitlines() if s.strip()]
    return los


# ---------------------------------------------------------------------------
# Post-processing helpers
# ---------------------------------------------------------------------------
def _ensure_five_takeaways(closing_md: str, driver: dict) -> str:
    """Guarantee that Lesson 5's "Key takeaways" section has exactly 5
    distinct items, regardless of what the LLM produced.

    Despite explicit "EXACTLY 5" instructions, the model occasionally
    collapses takeaways into a single comprehensive bullet/paragraph.
    This post-processor detects that and fills in the missing items
    using the contributing factors from the driver's RISK_BRIEF.

    The LLM's takeaway content is preserved when it exists — we only
    APPEND synthetic ones when the count is short.
    """
    if not closing_md:
        return closing_md

    # Find the Key takeaways section. Sentence-case heading per MM style,
    # but tolerate Title-Case too.
    pattern = re.compile(
        r"(###\s+Key\s+takeaways\s*\n)(.*?)(?=\n###\s|\Z)",
        re.IGNORECASE | re.DOTALL,
    )
    m = pattern.search(closing_md)
    if not m:
        return closing_md
    body = m.group(2)

    # Count existing takeaway items. Accept three formats Claude might emit:
    #   1. Numbered list:        "1. ..." / "2. ..."
    #   2. Bold-prefixed prose:  "**Takeaway 1:** ..."
    #   3. H4 sections:          "#### Takeaway 1: ..."
    numbered_items = re.findall(r"^\s*\d+\.\s+\S", body, re.MULTILINE)
    bold_items = re.findall(r"\*\*Takeaway\s+\d+\b", body, re.IGNORECASE)
    h4_items = re.findall(r"^####\s+Takeaway\s+\d+", body, re.MULTILINE | re.IGNORECASE)
    existing_count = max(len(numbered_items), len(bold_items), len(h4_items))

    if existing_count >= 5:
        return closing_md

    # Need to add 5 - existing_count synthetic takeaways. Pull contributing
    # factors from the driver's playbook prose.
    brief = driver.get("RISK_BRIEF", "") or ""
    factors = playbook_factors(brief)

    factor_takeaways: list[str] = []
    for f in factors:
        title = (f.get("title") or "").strip()
        if not title:
            continue
        # Build a one-line takeaway phrasing for this factor.
        # Mirror the structural language MM uses: name the factor + the
        # concrete behavior change tied to it.
        first_word = title.split(" ", 1)[0].lower()
        if first_word in ("failure", "insufficient", "documentation", "communication"):
            phrasing = (
                f"{title}: build a workflow check that closes this gap "
                f"before disposition — an order-set forcing function, "
                f"a structured handoff template, or a chart-audit trigger."
            )
        elif first_word in ("error",):
            phrasing = (
                f"{title}: institute a pre-procedure timeout or peer "
                f"verification step targeting the specific decision point "
                f"where this error pattern lands in your team's claims."
            )
        else:
            phrasing = (
                f"{title}: review one workflow artefact tied to this "
                f"factor (order set, documentation template, peer-review "
                f"queue) and tighten it this week."
            )
        factor_takeaways.append(phrasing)

    # Bottom-line action takeaway (always include as #5 if room)
    action_takeaway = (
        "Pick ONE of the takeaways above and put it in front of your team "
        "this week. Audit your last 10 cases against it and bring the gaps "
        "to the next quality-review meeting — small concrete changes "
        "embedded in the workflow are what move the needle."
    )

    needed = 5 - existing_count
    # Pick the most-relevant factor-takeaways first; backfill with the
    # generic action takeaway if there aren't enough factors.
    synth: list[str] = []
    for ph in factor_takeaways:
        if len(synth) >= needed - 1:  # leave one slot for the action takeaway
            break
        synth.append(ph)
    while len(synth) < needed - 1 and factor_takeaways:
        # Cycle factors if we still need more (very rare — most drivers have 4+).
        synth.append(factor_takeaways[len(synth) % len(factor_takeaways)])
    if len(synth) < needed:
        synth.append(action_takeaway)

    # Append the synthetic items as a numbered list continuation.
    # Start numbering at existing_count + 1 so the list reads naturally.
    start = max(existing_count, 0) + 1
    appendix_lines = []
    if existing_count == 0:
        # No items at all — replace the body wholesale with a fresh list.
        # Keep whatever prose the LLM wrote before the list (intro
        # paragraph) so the section doesn't lose context.
        prose_before = body.strip()
        if prose_before:
            appendix_lines.append(prose_before)
            appendix_lines.append("")
        for i, ph in enumerate(synth, start=1):
            appendix_lines.append(f"{i}. {ph}")
            appendix_lines.append("")
        new_body = "\n".join(appendix_lines).rstrip() + "\n\n"
    else:
        appendix_lines.append(body.rstrip())
        appendix_lines.append("")
        for offset, ph in enumerate(synth):
            appendix_lines.append(f"{start + offset}. {ph}")
            appendix_lines.append("")
        new_body = "\n".join(appendix_lines).rstrip() + "\n\n"

    return closing_md[:m.start(2)] + new_body + closing_md[m.end(2):]


# ---------------------------------------------------------------------------
# Generation pipeline
# ---------------------------------------------------------------------------
def kickoff_generation(driver_id: str):
    ss.cg_phase = "generating"
    ss.cg_driver_id = driver_id
    ss.cg_save_id = None
    # Final section order matches MagMutual's Lesson sequence:
    # course body (Lessons 1-3 outline) → per-topic case studies (Lesson 3
    # expansion) → assessment (Lesson 4) → closing (Lesson 5).
    # Per-topic lessons are inserted between course_body and assessment
    # in `kickoff_generation` below.
    ss.cg_section_order = ["course_body"]
    ss.cg_section_labels = dict(ANCHOR_LABELS)
    ss.cg_section_meta = {}

    driver = get_driver(driver_id)
    if not driver:
        ss.cg_messages.append({
            "role": "assistant",
            "content": f"I couldn't find a driver with id `{driver_id}`.",
        })
        ss.cg_phase = "idle"
        return

    los = _learning_objectives(driver)
    playbook = playbook_strategies_text(driver)
    risk_brief_src = f"RISK_BRIEF: {driver.get('RISK_BRIEF','')}\n\nPLAYBOOK:\n{playbook}"

    ss.cg_messages.append({
        "role": "assistant",
        "content": (
            f"Building a course on **{driver['DRIVER']}** ({driver['SPECIALTY']}). "
            f"I'll generate the body, the assessment, and a short claims lesson "
            f"for each main topic, then score every section."
        ),
    })

    progress = st.progress(0.0, text="Generating course body…")
    # Chart = exactly the playbook factors that get case studies. Each
    # bar's % is the stats CSV value for that factor (0% when stats has
    # no entry). 1:1 with Lesson 3 in count, order, and titles.
    pb_factors_for_chart = playbook_factors(driver.get("RISK_BRIEF", "") or "")
    factors = chart_factors_from_playbook(
        ss.cg_driver_id,
        [f["title"] for f in pb_factors_for_chart],
    )
    ss.cg_top_factors = factors
    cb = complete(
        build_course_body(driver, playbook, los, top_factors=factors),
        kind="course_body",
    )
    ss.cg_sections["course_body"] = cb.text
    ss.cg_sources["course_body"] = [risk_brief_src]
    ss.cg_history["course_body"] = []
    progress.progress(0.18, text="Extracting topics from the body…")

    # Case-study topics use the SAME order as the chart: highest-impact
    # contributing factor first (sorted by claim-frequency %, ties
    # broken by original playbook order). `factors` is already sorted
    # desc by chart_factors_from_playbook above, so Lesson 3 simply
    # walks `factors` in order — guaranteeing the chart and Lesson 3
    # are 1:1 in count, titles, AND order.
    topics = [f["label"] for f in factors] if factors else []
    # Fallback if the playbook had no factors with advice (shouldn't
    # happen — verified 73/73 drivers carry at least 4).
    if not topics:
        topics = [driver.get("DRIVER", "Risk reduction")]

    # Pull all available tagged claims for this driver — we'll pick one
    # per case study based on which claim's tagged ACTION_OR_OMISSION
    # fields best match the contributing factor for that case.
    claims_df = claims_for_driver(driver_id, top_n=20)
    have_claims = len(claims_df) > 0
    used_doc_ids: set[str] = set()

    def _pick_claim_for_topic(topic: str) -> dict:
        """Pick the claim whose tagged contributing factors most overlap
        with `topic`. Falls back to round-robin if no overlap. Avoids
        reusing the same claim across cases when possible."""
        if not have_claims:
            return {"DOCUMENT_ID": "—",
                    "SUMMARY": "(no claim available)",
                    "ALLEGATIONS": [],
                    "SPECIALTY": driver.get("SPECIALTY", "")}
        topic_lc = (topic or "").lower()
        topic_tokens = set(re.findall(r"[a-z]+", topic_lc))
        topic_tokens -= {"a","an","the","of","for","and","or","to","in",
                          "on","with","by","at","from","is","as","be"}
        best_idx = None
        best_score = -1.0
        for i, row in claims_df.iterrows():
            doc = str(row.get("DOCUMENT_ID", ""))
            actions = " ".join(str(row.get(f"ACTION_OR_OMISSION_{k}", ""))
                                for k in (1, 2, 3)).lower()
            if not actions.strip():
                continue
            action_tokens = set(re.findall(r"[a-z]+", actions))
            score = len(topic_tokens & action_tokens)
            # Strong bonus for unused claims so each case gets its own
            if doc not in used_doc_ids:
                score += 0.5
            if score > best_score:
                best_score = score
                best_idx = i
        if best_idx is None:
            # Pure round-robin fallback when no tokens overlap
            best_idx = claims_df.index[len(used_doc_ids) % len(claims_df)]
        claim = claims_df.loc[best_idx].to_dict()
        used_doc_ids.add(str(claim.get("DOCUMENT_ID", "")))
        return claim

    for i, topic in enumerate(topics):
        sid = f"lesson_{i+1}"
        # MagMutual numbers Lesson 3's case studies as "Lesson 3 · 1", etc.
        label = f"Lesson 3 · {i+1} of {len(topics)} · {topic[:36]}{'…' if len(topic) > 36 else ''}"
        ss.cg_section_order.append(sid)
        ss.cg_section_labels[sid] = label

        claim = _pick_claim_for_topic(topic)
        # i is 0-based; prompt uses 1-based "Case study N" numbering.
        # Pass driver + total_cases so each case knows (a) its playbook
        # slice and (b) its share of the 1-CME-credit Lesson 3 word
        # budget — keeping the course at ~60 minutes total regardless
        # of how many factors a driver has.
        prompt = build_embedded_lesson_for_topic(
            cb.text, topic, claim, index=i + 1,
            total_cases=len(topics), risk_driver=driver)
        res = complete(prompt, kind="embedded_lesson")
        ss.cg_sections[sid] = res.text
        ss.cg_sources[sid] = [
            cb.text,
            f"Topic anchor: {topic}",
            f"Claim summary:\n{claim.get('SUMMARY','')}",
        ]
        ss.cg_history[sid] = []
        ss.cg_section_meta[sid] = {
            "topic": topic,
            "claim_id": claim.get("DOCUMENT_ID", ""),
            "claim_summary": claim.get("SUMMARY", ""),
        }
        progress.progress(0.18 + 0.55 * (i + 1) / len(topics),
                          text=f"Lesson 3 case study {i+1} of {len(topics)}…")

    # Append Lesson 4 (Assessment) AFTER all Lesson-3 case studies so the
    # preview reads in MagMutual's intended order.
    ss.cg_section_order.append("assessment")
    progress.progress(0.78, text="Generating assessment (Lesson 4)…")
    asm = complete(build_assessment(cb.text, los), kind="assessment")
    ss.cg_sections["assessment"] = asm.text
    ss.cg_sources["assessment"] = [cb.text, "Learning objectives:\n" + "\n".join(los)]
    ss.cg_history["assessment"] = []

    # Append Lesson 5 (Closing) last.
    ss.cg_section_order.append("closing")
    progress.progress(0.88, text="Generating closing (Lesson 5)…")
    cl = complete(build_closing(cb.text, driver), kind="closing")
    ss.cg_sections["closing"] = _ensure_five_takeaways(cl.text, driver)
    ss.cg_sources["closing"] = [cb.text, risk_brief_src]
    ss.cg_history["closing"] = []

    progress.progress(0.95, text="Scoring confidence…")
    for sid in _section_order():
        ss.cg_confidence[sid] = confidence_score(
            ss.cg_sections.get(sid, ""), ss.cg_sources.get(sid, []),
            output_type="course_generator",
        )
    progress.progress(1.0, text="Done.")
    progress.empty()

    ss.cg_messages.append({
        "role": "assistant",
        "content": (
            f"Course is ready with **{len(topics)} embedded lessons** "
            f"(one per topic). Use the chips above the chat for one-click "
            f"revisions, or tell me what to change."
        ),
    })
    ss.cg_phase = "editing"


def regenerate_section(section_id: str):
    driver = get_driver(ss.cg_driver_id)
    if not driver:
        return
    los = _learning_objectives(driver)
    playbook = playbook_strategies_text(driver)

    _push_history(section_id)

    if section_id == "course_body":
        factors = top_contributing_factors(ss.cg_driver_id)
        prompt = build_course_body(driver, playbook, los, top_factors=factors)
        kind = "course_body"
    elif section_id == "assessment":
        prompt = build_assessment(ss.cg_sections.get("course_body", ""), los)
        kind = "assessment"
    elif section_id == "closing":
        prompt = build_closing(ss.cg_sections.get("course_body", ""), driver)
        kind = "closing"
    elif section_id.startswith("lesson_"):
        meta = ss.cg_section_meta.get(section_id, {})
        topic = meta.get("topic", "General")
        # Try to re-fetch the same claim; otherwise pick the top one
        claim_id = meta.get("claim_id")
        claim_summary = meta.get("claim_summary", "")
        claim = {"DOCUMENT_ID": claim_id or "—",
                 "SUMMARY": claim_summary,
                 "SPECIALTY": driver.get("SPECIALTY", ""),
                 "ALLEGATIONS": []}
        # Recover the 1-based index from the section id ('lesson_3' → 3)
        try:
            cs_index = int(section_id.split("_")[-1])
        except ValueError:
            cs_index = 1
        prompt = build_embedded_lesson_for_topic(
            ss.cg_sections.get("course_body", ""), topic, claim,
            index=cs_index,
        )
        kind = "embedded_lesson"
    else:
        return

    res = complete(prompt, kind=kind)
    text = res.text
    if section_id == "closing":
        text = _ensure_five_takeaways(text, driver)
    ss.cg_sections[section_id] = text

    if section_id == "course_body":
        _refresh_downstream_sources(driver)

    ss.cg_confidence[section_id] = confidence_score(
        res.text, ss.cg_sources.get(section_id, []), output_type="course_generator",
    )


# ---------------------------------------------------------------------------
# Sidebar — removed. All previous controls now live in the toolbar tools
# popover (`render_tools_popover()`). This function is kept as a no-op so
# external callers (app.py, tests) don't break.
# ---------------------------------------------------------------------------
def render_sidebar():
    """Deprecated. The sidebar was removed in favor of a toolbar popover."""
    return


def render_tools_popover():
    """Render the Tools popover that replaces the old sidebar.

    Contains the connection status, a Cortex ping button, the saved-drafts
    loader, the MM Copy Guide, and the most recent Cortex / Snowflake
    errors. Settings (model + temperature) are no longer user-adjustable —
    they're hardcoded per-prompt-kind in `shared/cortex.py`.
    """
    with popover_or_expander("Tools", use_container_width=True,
                              help="Saved drafts."):
        st.markdown("##### Saved drafts")
        saves = list_saves("course")
        if not saves:
            st.caption("No saved courses yet. Click **Save draft** in the toolbar.")
        else:
            for it in saves[:8]:
                short_title = (it.title[:42] + "…") if len(it.title) > 42 else it.title
                ldcol1, ldcol2 = st.columns([4, 1])
                with ldcol1:
                    if st.button(
                        f"{short_title}",
                        key=f"load_{it.save_id}",
                        use_container_width=True,
                        type="secondary",
                        help=f"Saved {it.updated_at} · driver {it.driver_id or '—'}",
                    ):
                        if _load_save(it.save_id):
                            st.rerun()
                with ldcol2:
                    if st.button("×", key=f"del_{it.save_id}",
                                 help="Delete this draft", use_container_width=True):
                        delete_save(it.save_id)
                        st.rerun()


# ---------------------------------------------------------------------------
# Idle / chat-first state
# ---------------------------------------------------------------------------
def render_idle():
    """Two-step picker: pick a specialty, THEN pick a driver within it.

    Showing all 73 driver cards at once was overwhelming — there are 19
    specialties with 1-8 drivers each, so a specialty grid first cuts
    the cognitive load way down. `ss.cg_pick_specialty` carries the
    chosen specialty between steps; clearing it goes back.
    """
    s = cortex_status()
    topbar(
        "Course Generator",
        mode="DRAFT",
        connection_pill=("Live" if s["connection_live"] else "Mock"),
        model_pill=(s["last_model"] or "claude-opus-4-7"),
    )

    drivers = list_risk_drivers()
    library = get_risk_library()
    stats_df = get_risk_driver_stats()
    library_by_id = {r["DRIVER_ID"]: r for _, r in library.iterrows()}
    stats_by_id = {r["DRIVER_ID"]: r for _, r in stats_df.iterrows()}

    # Group drivers by specialty
    by_specialty: dict[str, list[dict]] = {}
    for d in drivers:
        spec = d["label"].split("·")[0].strip()
        by_specialty.setdefault(spec, []).append(d)

    ss.setdefault("cg_pick_specialty", None)

    # ------------------------------------------------------------------
    # Step 1: Specialty dropdown (default state)
    # ------------------------------------------------------------------
    if not ss.cg_pick_specialty:
        hero(
            eyebrow="Step 1 of 2",
            title="Which specialty are you building for?",
            subtitle="Pick a specialty to see its risk drivers — or describe "
                     "the course in your own words below.",
        )
        specialties = sorted(by_specialty.keys())
        # Centered selector — narrower than the page so it feels like the
        # primary call-to-action rather than a wide form field.
        _l, mid, _r = st.columns([1, 3, 1])
        with mid:
            options = ["— pick a specialty —"] + [
                f"{s} · {len(by_specialty[s])} driver"
                f"{'s' if len(by_specialty[s]) != 1 else ''}"
                for s in specialties
            ]
            choice = st.selectbox(
                "Specialty",
                options=options,
                index=0,
                label_visibility="collapsed",
                key="cg_specialty_select",
            )
            if choice != options[0]:
                # Decode "Emergency Medicine · 8 drivers" back to "Emergency Medicine"
                ss.cg_pick_specialty = choice.split(" · ")[0]
                st.rerun()
        # Free-text fallback below the dropdown — power users can skip
        # both steps by describing what they want.
        prompt = st.chat_input(
            "Or describe the course you want to build (e.g. "
            "'Missed ACS in the ED')…"
        )
        if prompt:
            ss.cg_messages.append({"role": "user", "content": prompt})
            match = _match_driver(prompt, drivers)
            if match:
                kickoff_generation(match)
                st.rerun()
            else:
                ss.cg_messages.append({
                    "role": "assistant",
                    "content": "I couldn't pin down which driver. Pick a "
                               "specialty above and I'll show you the drivers in it.",
                })
                st.rerun()
        return

    # ------------------------------------------------------------------
    # Step 2: Driver grid for the chosen specialty
    # ------------------------------------------------------------------
    spec = ss.cg_pick_specialty
    bcol, _ = st.columns([1, 6])
    with bcol:
        if st.button("← All specialties", type="secondary",
                      use_container_width=True):
            ss.cg_pick_specialty = None
            st.rerun()
    spec_drivers = sorted(by_specialty.get(spec, []),
                           key=lambda d: d["label"])
    hero(
        eyebrow=f"Step 2 of 2 · {spec}",
        title=f"Which {spec.lower()} risk driver?",
        subtitle=f"{len(spec_drivers)} risk driver"
                 f"{'s' if len(spec_drivers) != 1 else ''} available. Pick one to "
                 "kick off course generation, or describe the focus below.",
    )

    # Driver card grid
    rows = [spec_drivers[i:i + 3] for i in range(0, len(spec_drivers), 3)]
    for row in rows:
        cols = st.columns(len(row))
        for col, d in zip(cols, row):
            with col:
                lib = library_by_id.get(d["id"], {})
                # No frequency_pct / severity_usd in this env — those aggregates
                # don't exist on RISK_DRIVER_STATS. The card just shows
                # specialty + driver name.
                st.markdown(
                    playbook_card_html(
                        specialty=lib.get("SPECIALTY", ""),
                        driver=lib.get("DRIVER", ""),
                    ),
                    unsafe_allow_html=True,
                )
                if st.button("Build course →", key=f"build_{d['id']}",
                              use_container_width=True):
                    ss.cg_messages.append(
                        {"role": "user", "content": f"Build a course on {d['label']}"}
                    )
                    kickoff_generation(d["id"])
                    st.rerun()

    # Free-text fallback inside step 2 (filtered to this specialty's drivers)
    st.markdown("")
    prompt = st.chat_input(
        f"Or describe the {spec} course you want to build…"
    )
    if prompt:
        ss.cg_messages.append({"role": "user", "content": prompt})
        match = _match_driver(prompt, spec_drivers) or _match_driver(prompt, drivers)
        if match:
            kickoff_generation(match)
            st.rerun()
        else:
            ss.cg_messages.append({
                "role": "assistant",
                "content": "I couldn't pin down which driver. Click a card above.",
            })
            st.rerun()


def _match_driver(text: str, drivers: list[dict]) -> str | None:
    t = text.lower()
    best, best_score = None, 0
    for d in drivers:
        label = d["label"].lower()
        score = sum(1 for token in label.split() if token in t and len(token) > 3)
        if score > best_score:
            best, best_score = d["id"], score
    return best if best_score >= 2 else None


# ---------------------------------------------------------------------------
# Generating state — animated skeleton
# ---------------------------------------------------------------------------
def render_generating():
    s = cortex_status()
    topbar(
        "Course Generator",
        mode="GENERATING",
        connection_pill=("Live" if s["connection_live"] else "Mock"),
        model_pill=(s["last_model"] or "claude-opus-4-7"),
    )
    st.caption("Generating your course… typically 20–40 seconds in production.")
    skeleton_card("Course body")
    skeleton_card("Assessment")
    skeleton_card("Embedded claims lesson")
    for msg in ss.cg_messages[-2:]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])


# ---------------------------------------------------------------------------
# Editing state — split view
# ---------------------------------------------------------------------------
def render_editing():
    s = cortex_status()
    driver = get_driver(ss.cg_driver_id) or {}
    # Defensive: if the driver_id in session no longer resolves (e.g. the
    # risk_library was rebuilt with new IDs after a migration), drop the
    # stale state and bounce back to the picker. Otherwise we'd render a
    # blank "Reducing Liability in :" title.
    if not driver and ss.cg_driver_id:
        st.warning(
            f"Saved session pointed at `{ss.cg_driver_id}` which is no "
            "longer in the risk library. Returning to the playbook picker."
        )
        for k in ("cg_driver_id", "cg_sections", "cg_section_order",
                   "cg_section_labels", "cg_top_factors"):
            ss.pop(k, None)
        ss.cg_phase = "idle"
        st.rerun()
    topbar(
        f"Course Generator · {driver.get('DRIVER', 'untitled')[:48]}",
        mode="EDIT",
        connection_pill=("Live" if s["connection_live"] else "Mock"),
        model_pill=(s["last_model"] or "claude-opus-4-7"),
    )

    # Toolbar: Tools · spacer · New · Save · PDF · SCORM · MD.
    # Tools popover replaces the old sidebar; chat is sticky at 35% so the
    # split is fixed.
    ss.cg_split_ratio = 35
    ttools, _spacer, tnew, tsave, tpdf, tscorm, tmd = st.columns(
        [1.2, 0.8, 1, 1, 1.1, 1.1, 0.9]
    )
    with ttools:
        render_tools_popover()
    with tnew:
        if st.button("New course", type="secondary", use_container_width=True):
            for k in ["cg_messages", "cg_driver_id", "cg_sections", "cg_confidence",
                      "cg_sources", "cg_history", "cg_edit_mode",
                      "cg_section_order", "cg_section_labels", "cg_section_meta",
                      "cg_save_id", "cg_case_photos", "cg_cover_photo",
                      "cg_photos_original"]:
                if k in ss:
                    del ss[k]
            ss.cg_phase = "idle"
            ss.cg_target_section = "All sections"
            st.rerun()
    with tsave:
        save_label = "Update save" if ss.cg_save_id else "Save draft"
        if st.button(save_label, use_container_width=True,
                     help="Persist this course (updates in place if already saved)."):
            with st.spinner("Saving…"):
                _save_current()
            st.rerun()

    # Defensive title build — if SPECIALTY or DRIVER somehow ended up
    # empty (stale session, partially-loaded save) we'd otherwise render
    # "Reducing Liability in : ".
    spec = (driver.get("SPECIALTY", "") or "").strip()
    drv_name = (driver.get("DRIVER", "") or "").strip()
    if spec and drv_name:
        course_title = f"Reducing Liability in {spec}: {drv_name}"
    elif drv_name:
        course_title = f"Reducing Liability in {drv_name}"
    elif spec:
        course_title = f"Reducing Liability in {spec}"
    else:
        course_title = "Reducing Liability — untitled draft"
    sections_for_export = {
        ss.cg_section_labels.get(k, k): ss.cg_sections.get(k, "")
        for k in ss.cg_section_order
    }

    # Cache the PDF + SCORM bytes keyed on the section content hash so we
    # don't re-render them on every Streamlit rerun (e.g. clicking a quick
    # action chip). Building the styled PDF with embedded Lato fonts costs
    # 100-200 ms, multiplied by every interaction otherwise.
    import hashlib
    sig = hashlib.sha256(
        (course_title + "|" + repr(sorted(sections_for_export.items())))
        .encode("utf-8")
    ).hexdigest()
    cache = ss.setdefault("cg_export_cache", {})
    factors_for_exports = ss.get("cg_top_factors")
    if cache.get("sig") != sig:
        cache.clear()
        cache["sig"] = sig
        cache["pdf"] = to_pdf_bytes(course_title, sections_for_export)
        cache["scorm"] = build_scorm_zip(
            course_title, ss.cg_driver_id or "course", sections_for_export,
            top_factors=factors_for_exports,
        )
        cache["md"] = to_markdown(course_title, sections_for_export).encode("utf-8")

    with tpdf:
        st.download_button(
            "Export PDF",
            data=cache["pdf"],
            file_name=f"course_{ss.cg_driver_id or 'draft'}.pdf",
            mime="application/pdf",
            use_container_width=True,
            help="Styled like the MagMutual reference course.",
        )
    with tscorm:
        st.download_button(
            "Export SCORM",
            data=cache["scorm"],
            file_name=f"course_{ss.cg_driver_id or 'draft'}_scorm.zip",
            mime="application/zip",
            use_container_width=True,
            help="SCORM 1.2 package — upload to your LMS.",
        )
    with tmd:
        st.download_button(
            "Markdown",
            data=cache["md"],
            file_name=f"course_{ss.cg_driver_id or 'draft'}.md",
            mime="text/markdown",
            use_container_width=True,
            help="Plain markdown source — the same content that’s "
                 "in the styled PDF / SCORM, easy to diff or copy elsewhere.",
        )

    # Save toast
    if ss.cg_save_toast and (time.time() - ss.cg_save_toast[2] < 4):
        st.success(f"Saved as `{ss.cg_save_toast[1]}`. See saved drafts in the sidebar.")

    chat_w, preview_w = ss.cg_split_ratio, 100 - ss.cg_split_ratio
    chat_col, preview_col = st.columns([chat_w, preview_w], gap="large")
    with chat_col:
        _render_chat_pane()
    with preview_col:
        _render_preview_pane()
    sticky_chat_script()


def _render_chat_pane():
    st.markdown("##### Chat")
    # Plain container — newer Streamlit validates `height` against an
    # internal min-height threshold and raises layout errors on certain
    # configurations. Letting the chat flow naturally avoids that.
    msg_container = st.container()
    with msg_container:
        for msg in ss.cg_messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

    # Target dropdown — built from the live dynamic section list
    options = ["All sections"] + [_section_label(sid) for sid in _section_order()]
    ss.cg_target_section = st.selectbox(
        "Apply changes to",
        options,
        index=options.index(ss.cg_target_section) if ss.cg_target_section in options else 0,
    )

    # Quick-action chips — two rows of three so the labels actually fit
    for row_start in (0, 3):
        chip_cols = st.columns(3)
        for i, a in enumerate(QUICK_ACTIONS[row_start:row_start + 3]):
            with chip_cols[i]:
                if st.button(a["label"], key=f"qa_{a['id']}",
                             type="secondary",
                             use_container_width=True, help=a["instruction"]):
                    _handle_quick_action(a["id"])
                    st.rerun()

    user_msg = st.chat_input(
        "Tell me what to change — wording, structure, or layout (e.g. "
        "'add definition cards back', 'turn the takeaways into bullets', "
        "'add a Pause and reflect after the chart')…"
    )
    if user_msg:
        _handle_chat_message(user_msg)
        st.rerun()


def _handle_quick_action(action_id: str):
    sid = _resolve_target_sid()
    if not sid:
        ss.cg_messages.append({
            "role": "assistant",
            "content": "Pick a target section in the dropdown first, then click a quick action.",
        })
        return
    label = _section_label(sid)
    from chat import by_id
    action = by_id(action_id) or {}
    ss.cg_messages.append({
        "role": "user",
        "content": f"**{action.get('label','?')}** → _{label}_",
    })
    _push_history(sid)
    sources_block = "\n\n---\n\n".join(ss.cg_sources.get(sid, []))
    res = apply_quick_action(label, ss.cg_sections.get(sid, ""), sources_block,
                              action_id, section_id=sid,
                              save_id=ss.get("cg_save_id"))
    ss.cg_sections[sid] = res["text"]
    if sid == "course_body":
        _refresh_downstream_sources(get_driver(ss.cg_driver_id) or {})
    ss.cg_confidence[sid] = confidence_score(
        res["text"], ss.cg_sources.get(sid, []), output_type="course_generator",
    )
    ss.cg_messages.append({
        "role": "assistant",
        "content": f"Applied **{action.get('label','?')}** to **{label}**. New confidence: {ss.cg_confidence[sid].grade}.",
    })


def _handle_chat_message(user_msg: str):
    ss.cg_messages.append({"role": "user", "content": user_msg})
    sid = _resolve_target_sid(user_msg)
    if not sid:
        ss.cg_messages.append({
            "role": "assistant",
            "content": "I'm not sure which section to edit. Pick one in the dropdown above and try again.",
        })
        return
    label = _section_label(sid)
    _push_history(sid)
    sources_block = "\n\n---\n\n".join(ss.cg_sources.get(sid, []))
    res = apply_chat_edit(label, ss.cg_sections.get(sid, ""), sources_block,
                           user_msg, section_id=sid,
                           save_id=ss.get("cg_save_id"))
    ss.cg_sections[sid] = res["text"]
    if sid == "course_body":
        _refresh_downstream_sources(get_driver(ss.cg_driver_id) or {})
    ss.cg_confidence[sid] = confidence_score(
        res["text"], ss.cg_sources.get(sid, []), output_type="course_generator",
    )
    ss.cg_messages.append({
        "role": "assistant",
        "content": f"Updated **{label}** ({res['latency_s']:.1f}s). New confidence: **{ss.cg_confidence[sid].grade}**.",
    })


def _resolve_target_sid(user_msg: str = "") -> str | None:
    label_to_sid = {ss.cg_section_labels.get(sid, sid): sid
                    for sid in _section_order()}
    target = ss.cg_target_section
    if target != "All sections":
        return label_to_sid.get(target)
    return _guess_section_from_text(user_msg) if user_msg else "course_body"


def _guess_section_from_text(text: str) -> str:
    t = text.lower()
    if any(k in t for k in ["assess", "question", "quiz", "exam"]):
        return "assessment"
    if any(k in t for k in ["claim", "case", "lesson", "case study", "embedded"]):
        # Pick the first lesson section; the user can re-target after
        for sid in _section_order():
            if sid.startswith("lesson_"):
                return sid
    return "course_body"


def _render_preview_pane():
    driver = get_driver(ss.cg_driver_id) or {}

    # Header row: title + preview-mode toggle
    h_left, h_right = st.columns([3, 2])
    with h_left:
        from carbon import _html_escape
        st.markdown(f"### {_html_escape(driver.get('TITLE',''))}",
                     unsafe_allow_html=True)
        st.markdown(
            f"<span class='source-pill'>{_html_escape(driver.get('SPECIALTY',''))}</span>"
            f"<span class='source-pill'>{_html_escape(ss.cg_driver_id or '')}</span>",
            unsafe_allow_html=True,
        )
    with h_right:
        ss.cg_preview_mode = st.radio(
            "Preview",
            ["Editable", "Live HTML"],
            horizontal=True,
            index=["Editable", "Live HTML"].index(ss.cg_preview_mode),
            label_visibility="collapsed",
            help="Editable shows per-section cards (Re-run / Edit / Undo). "
                 "Live HTML shows the styled course with a clickable assessment.",
        )

    st.markdown("")

    # Show the overall confidence panel at the top of BOTH preview modes
    # so users always see the course-level grade and dimension averages
    # regardless of whether they're inspecting the live HTML or editing
    # markdown.
    _render_overall_confidence_panel()

    if ss.cg_preview_mode == "Live HTML":
        _render_live_html_preview()
    else:
        for sid in _section_order():
            _render_section(sid, _section_label(sid))


def _render_overall_confidence_panel():
    """Compact course-level confidence strip rendered above the preview.

    Designed to take minimal vertical space (~one row) so the course
    preview owns the rest of the viewport. Renders as a single inline
    pill row: overall grade badge + each dimension as "name score/5".
    Detail (per-dimension reasoning, blocking issues) is reachable via
    the Tools menu — this strip is the at-a-glance summary.
    """
    confs = [c for c in ss.cg_confidence.values() if c is not None]
    if not confs:
        return

    # Average each dimension across all sections that have a score for it.
    agg: dict[str, list[float]] = {}
    agg_names: dict[str, str] = {}
    for c in confs:
        if not c.raw:
            continue
        for key, d in (c.raw.get("dimension_scores") or {}).items():
            try:
                v = float(d.get("score"))
            except (TypeError, ValueError):
                continue
            agg.setdefault(key, []).append(v)
            agg_names[key] = d.get("name") or key

    # Overall course grade = mean of per-section letter grades, mapped back.
    grade_to_pct = {"A": 95, "B": 85, "C": 75, "D": 65, "F": 50}
    pct_to_grade = lambda p: ("A" if p >= 90 else "B" if p >= 80
                                else "C" if p >= 70 else "D" if p >= 60 else "F")
    pcts = [grade_to_pct.get(c.grade, 70) for c in confs]
    overall_grade = pct_to_grade(sum(pcts) / len(pcts)) if pcts else "—"
    n = len(confs)

    # Compact pill row: overall badge + per-dimension mini pills.
    # All on one line, gentle background to mark it as a meta strip.
    grade_color = {
        "A": ("#198038", "#defbe6"),
        "B": ("#0f62fe", "#edf5ff"),
        "C": ("#8a3800", "#fff8e1"),
        "D": ("#a2191f", "#fff1f1"),
        "F": ("#a2191f", "#fff1f1"),
    }.get(overall_grade, ("#525252", "#f4f4f4"))

    pills_html = []
    pills_html.append(
        f"<span style='display:inline-flex;align-items:center;justify-content:center;"
        f"min-width:1.8rem;height:1.8rem;padding:0 0.55rem;border-radius:0.35rem;"
        f"font-weight:700;font-size:0.95rem;background:{grade_color[1]};"
        f"color:{grade_color[0]};margin-right:0.55rem;'>{overall_grade}</span>"
    )
    # Short label for the strip
    pills_html.append(
        f"<span style='color:#525252;font-size:0.82rem;margin-right:0.8rem;'>"
        f"Confidence · avg of {n}</span>"
    )
    # Dimension mini-pills sorted by key so the order is stable
    for key in sorted(agg.keys()):
        avg = sum(agg[key]) / len(agg[key])
        name = agg_names.get(key, key)
        # Drop "Dimension N:" prefix if the name carries it
        short_name = re.sub(r"^Dimension\s+\d+\s*[:\-]\s*", "", name).strip()
        # Color the score: green ≥4, blue ≥3, amber ≥2, red below
        if avg >= 4.0:
            sc = "#198038"
        elif avg >= 3.0:
            sc = "#0f62fe"
        elif avg >= 2.0:
            sc = "#8a3800"
        else:
            sc = "#a2191f"
        pills_html.append(
            f"<span style='display:inline-block;margin-right:0.7rem;font-size:0.82rem;"
            f"color:#525252;'>{_html_escape(short_name)} "
            f"<b style='color:{sc};'>{avg:.1f}</b></span>"
        )

    st.markdown(
        "<div style='padding:0.5rem 0.75rem;background:#f4f4f4;border-radius:0.4rem;"
        "margin-bottom:0.8rem;line-height:1.8;'>"
        + "".join(pills_html)
        + "</div>",
        unsafe_allow_html=True,
    )


def _render_live_html_preview():
    """Embed the styled, interactive HTML preview in an iframe."""
    driver = get_driver(ss.cg_driver_id) or {}
    spec = (driver.get("SPECIALTY", "") or "").strip()
    drv_name = (driver.get("DRIVER", "") or "").strip()
    if spec and drv_name:
        course_title = f"Reducing Liability in {spec}: {drv_name}"
    elif drv_name:
        course_title = f"Reducing Liability in {drv_name}"
    elif spec:
        course_title = f"Reducing Liability in {spec}"
    else:
        course_title = "Reducing Liability — untitled draft"
    sections_for_preview = {
        ss.cg_section_labels.get(sid, sid): ss.cg_sections.get(sid, "")
        for sid in _section_order()
    }
    # If the course was generated by the new pipeline, ss.cg_top_factors
    # holds the 6 contributing-factor categories for this driver. Pass
    # them through so the preview renders the bar chart inside Lesson 2.
    # Recovery for sessions saved before the chart-from-playbook helper
    # landed: rebuild from the current driver's playbook factors.
    factors = ss.get("cg_top_factors")
    if not factors:
        pb = playbook_factors(driver.get("RISK_BRIEF", "") or "")
        factors = chart_factors_from_playbook(
            ss.cg_driver_id, [f["title"] for f in pb],
        )

    # Per-case-study + cover photos: ss.cg_case_photos maps section-label
    # → {url, label, id}; ss.cg_cover_photo holds the cover hero. We
    # ALSO maintain ss.cg_photos_original — a frozen snapshot of the
    # auto-picks computed in deterministic order on first render. The
    # Reset button restores from that snapshot, so clicking Reset on the
    # cover always brings back the SAME original photo (not whatever
    # auto-pick would compute now with case-studies excluded).
    ss.setdefault("cg_case_photos", {})
    ss.setdefault("cg_cover_photo", None)
    ss.setdefault("cg_photos_original", {})  # {"cover": {...}, "<case_label>": {...}}

    # Compute the canonical auto-picks ONCE per course (cover first,
    # then case studies in order). Subsequent renders just reuse the
    # snapshot — keeps Reset deterministic regardless of user picks.
    if "cover" not in ss.cg_photos_original:
        used_for_originals: list[str] = []
        drv_name = driver.get("DRIVER", "") or ""
        spec_name = driver.get("SPECIALTY", "") or ""
        # Cover uses driver+specialty as the topic itself.
        cphoto = auto_pick_for_topic(
            f"{drv_name} {spec_name}", used_ids=used_for_originals,
        )
        if cphoto:
            ss.cg_photos_original["cover"] = {
                "id": cphoto.id, "url": cphoto.url, "label": cphoto.label,
            }
            used_for_originals.append(cphoto.id)
        for label in sections_for_preview:
            if not _is_case_study_label(label):
                continue
            topic = _topic_from_label(label)
            # Pass driver + specialty as context so case-study picks
            # favor photos relevant to BOTH the topic AND the course
            # (e.g. an "Error in non-medication intervention" case in
            # an Airway course favors airway photos).
            photo = auto_pick_for_topic(
                topic, used_ids=used_for_originals,
                driver_context=drv_name, specialty_context=spec_name,
            )
            if photo:
                ss.cg_photos_original[label] = {
                    "id": photo.id, "url": photo.url, "label": photo.label,
                }
                used_for_originals.append(photo.id)

    # Apply the originals to any slot the user hasn't explicitly set.
    if not (ss.cg_cover_photo and ss.cg_cover_photo.get("id")):
        if "cover" in ss.cg_photos_original:
            ss.cg_cover_photo = dict(ss.cg_photos_original["cover"])
    for label in sections_for_preview:
        if not _is_case_study_label(label):
            continue
        if ss.cg_case_photos.get(label, {}).get("id"):
            continue
        if label in ss.cg_photos_original:
            ss.cg_case_photos[label] = dict(ss.cg_photos_original[label])

    # Render the picker UI in a compact expander above the iframe.
    _render_photo_pickers(sections_for_preview)

    html_doc = render_course_html(course_title, sections_for_preview,
                                   top_factors=factors,
                                   case_photos=ss.cg_case_photos,
                                   cover_photo=ss.cg_cover_photo)
    # Components iframe: tall enough to scroll the whole course internally.
    components.html(html_doc, height=900, scrolling=True)
    st.caption(
        "This preview is what learners will see. The same HTML is bundled "
        "into the SCORM export."
    )


def _is_case_study_label(label: str) -> bool:
    """A case-study section label looks like 'Lesson 3 · 1 of 3 · Topic'."""
    return bool(re.match(r"\s*Lesson\s*3\s*[·\-]", label or "", re.I))


def _topic_from_label(label: str) -> str:
    """Pull the topic out of a 'Lesson 3 · 1 of 3 · Topic' label."""
    m = re.match(
        r"\s*Lesson\s*3\s*[·\-]\s*\d+\s*of\s*\d+\s*[·\-]\s*(.+?)$",
        label or "", re.I,
    )
    return (m.group(1) if m else (label or "")).rstrip(" …").strip()


def _render_photo_pickers(sections_for_preview: dict[str, str]) -> None:
    """Render the cover-hero picker AND one picker per case-study section.

    Each picker has:
      - A select-box with all library photos (label + category)
      - A small file_uploader to bring your own image
      - A "Reset" link to drop back to auto-pick

    Selections persist in `ss.cg_cover_photo` (course-level hero) and
    `ss.cg_case_photos[label]` (per-case study), and both feed back into
    `render_course_html(cover_photo=..., case_photos=...)`.
    """
    case_labels = [lbl for lbl in sections_for_preview if _is_case_study_label(lbl)]
    library = list_photos()
    # Don't short-circuit on empty library — the upload UI must always be
    # available so users can add a photo even when the stage listing fails
    # (network blip, stage permissions, empty stage, etc.).
    with st.expander("Photos", expanded=False):
        if library:
            st.caption(
                "The course uses photos from the library by default. "
                "Pick a different one or upload your own. "
                "The first picker controls the cover hero at the top of the course."
            )
        else:
            st.caption(
                "No photos found in @HACKATHON_DWH.ADVICE.COURSE_PHOTOS. "
                "You can still upload a cover image below; the per-case "
                "photo pickers reappear once the stage has at least one "
                "image. See the **Status & tools** menu for stage errors."
            )

        # ----- Library preview gallery with search -----
        # NOTE: was a nested st.expander — Streamlit forbids that. The
        # parent "Photos" expander already contains this gallery, so we
        # use a markdown subheading + toggle to gate the gallery instead.
        st.markdown("###### Browse library")
        ss.setdefault("cg_show_photo_gallery", False)
        ss.cg_show_photo_gallery = st.checkbox(
            "Show all photos",
            value=ss.cg_show_photo_gallery,
            key="cg_show_photo_gallery_toggle",
            help="Reveal a search + grid of every photo in the library.",
        )
        if ss.cg_show_photo_gallery:
            st.caption(
                "All photos available to the picker dropdowns below. "
                "Type to filter by title, tag, category, or description. "
                "In production, the library comes from a Snowflake stage "
                "with a metadata side-table for tags."
            )
            query = st.text_input(
                "Search photos",
                key="cg_photo_search",
                placeholder="e.g. airway, chest pain, handoff, pediatric ICU",
                label_visibility="collapsed",
            )
            results = search_photos(query, limit=48) if query else library
            if not results:
                st.caption(f"No photos match '{query}'.")
            else:
                if query:
                    st.caption(f"{len(results)} match{'es' if len(results) != 1 else ''} for '{query}'.")
                gallery_cols = st.columns(min(4, max(1, len(results))))
                for idx, p in enumerate(results):
                    with gallery_cols[idx % len(gallery_cols)]:
                        # Caption shows tags so users can see WHY it
                        # matched the search.
                        tag_preview = ""
                        if p.tags:
                            tag_preview = "  ·  " + ", ".join(p.tags[:3])
                            if len(p.tags) > 3:
                                tag_preview += f", +{len(p.tags) - 3}"
                        st.image(p.url, use_container_width=True,
                                 caption=f"{p.label}{tag_preview}")

        st.markdown("")

        # ----- Cover hero (course-level) -----
        current = ss.cg_cover_photo or {}
        current_id = current.get("id", "")
        thumb, picker, uploader, resetter = st.columns([3, 2, 2, 1])
        with thumb:
            if current.get("url"):
                st.image(current["url"], use_container_width=True)
            else:
                st.empty()
        with picker:
            if library:
                ids = [p.id for p in library]
                labels_for_box = [
                    p.label + (("  ·  " + " · ".join(p.tags[:3])) if p.tags else "")
                    for p in library
                ]
                default_idx = ids.index(current_id) if current_id in ids else 0
                pick = st.selectbox(
                    "**Cover hero (top of course)**",
                    options=list(range(len(library))),
                    format_func=lambda i: labels_for_box[i],
                    index=default_idx,
                    key="photo_pick_cover",
                )
                picked = library[pick]
                if picked.id != current_id:
                    ss.cg_cover_photo = {
                        "id": picked.id, "url": picked.url, "label": picked.label,
                    }
                    st.rerun()
            else:
                # No library — picker placeholder so the layout still aligns
                st.markdown("**Cover hero**")
                st.caption("(library empty — upload to set a cover)")
        with uploader:
            up = st.file_uploader(
                "Upload your own",
                type=["png", "jpg", "jpeg", "webp", "svg"],
                key="photo_upload_cover",
                label_visibility="collapsed",
            )
            # Streamlit's file_uploader retains the value across every
            # rerun, so a naive `if up is not None: rerun()` creates an
            # infinite upload-rerun loop that wipes session state and
            # bounces the user back to the splash. Track the file_id of
            # the LAST processed upload and only re-process when it
            # changes.
            if up is not None and ss.get("_photo_upload_cover_seen") != up.file_id:
                raw = up.getvalue()
                photo = add_uploaded_photo(up.name, raw, up.type or "image/png")
                ss.cg_cover_photo = {
                    "id": photo.id, "url": photo.url, "label": photo.label,
                }
                ss["_photo_upload_cover_seen"] = up.file_id
                st.rerun()
        with resetter:
            if st.button("Reset", key="photo_reset_cover",
                          help="Auto-pick from the library by driver/specialty"):
                ss.cg_cover_photo = None
                st.rerun()

        if not case_labels:
            return
        st.markdown("")
        st.caption("Per-case-study heroes (one per Lesson 3 case):")
        for case_label in case_labels:
            current = ss.cg_case_photos.get(case_label, {})
            current_id = current.get("id", "")
            thumb, picker, uploader, resetter = st.columns([3, 2, 2, 1])
            with thumb:
                if current.get("url"):
                    st.image(current["url"], use_container_width=True)
                else:
                    st.empty()
            with picker:
                ids = [p.id for p in library]
                # Shorter dropdown labels — the thumbnail next to the picker
                # already shows the photo, so we only need the title.
                # Include the first ~3 tags in the option text so Streamlit's
                # type-to-filter on the selectbox matches on tag keywords too.
                labels_for_box = [
                    p.label + (("  ·  " + " · ".join(p.tags[:3])) if p.tags else "")
                    for p in library
                ]
                # Map id → index in the selectbox
                if current_id in ids:
                    default_idx = ids.index(current_id)
                else:
                    default_idx = 0
                pick = st.selectbox(
                    f"**{_topic_from_label(case_label)}**",
                    options=list(range(len(library))),
                    format_func=lambda i: labels_for_box[i],
                    index=default_idx,
                    key=f"photo_pick_{case_label}",
                )
                picked = library[pick]
                if picked.id != current_id:
                    ss.cg_case_photos[case_label] = {
                        "id": picked.id, "url": picked.url, "label": picked.label,
                    }
                    st.rerun()
            with uploader:
                up = st.file_uploader(
                    "Upload your own",
                    type=["png", "jpg", "jpeg", "webp", "svg"],
                    key=f"photo_upload_{case_label}",
                    label_visibility="collapsed",
                )
                # Same dedupe pattern as the cover uploader — file_uploader
                # keeps the file across reruns; only process new uploads.
                seen_key = f"_photo_upload_{case_label}_seen"
                if up is not None and ss.get(seen_key) != up.file_id:
                    raw = up.getvalue()
                    photo = add_uploaded_photo(up.name, raw, up.type or "image/png")
                    ss.cg_case_photos[case_label] = {
                        "id": photo.id, "url": photo.url, "label": photo.label,
                    }
                    ss[seen_key] = up.file_id
                    st.rerun()
            with resetter:
                if st.button("Reset", key=f"photo_reset_{case_label}",
                              help="Auto-pick from the library by topic"):
                    ss.cg_case_photos.pop(case_label, None)
                    st.rerun()

    # "Add lesson" affordance — append a custom abridged lesson at the end.
    st.markdown("")
    with st.expander(":material/add: Add another abridged lesson", expanded=False):
        st.caption(
            "Pick any topic to add a new case-study lesson, grounded in the latest "
            "course body and the next available claim."
        )
        new_topic = st.text_input(
            "Topic",
            placeholder="e.g. ECG re-evaluation timing",
            key="cg_new_topic_input",
            label_visibility="collapsed",
        )
        if st.button("Generate lesson", key="cg_add_lesson"):
            if new_topic.strip():
                _append_topic_lesson(new_topic.strip())
                st.rerun()


def _append_topic_lesson(topic: str):
    """Append a new abridged lesson tied to `topic`, placed at the end."""
    driver = get_driver(ss.cg_driver_id) or {}
    course_body = ss.cg_sections.get("course_body", "")
    # Pick a claim — cycle through available ones, prefer one not used yet
    claims_df = claims_for_driver(ss.cg_driver_id, top_n=10)
    used_ids = {meta.get("claim_id") for meta in ss.cg_section_meta.values()}
    if len(claims_df) == 0:
        return
    pick = None
    for _, row in claims_df.iterrows():
        if row.get("DOCUMENT_ID") not in used_ids:
            pick = row.to_dict()
            break
    if pick is None:
        pick = claims_df.iloc[0].to_dict()

    # Compute next lesson sid
    n = sum(1 for sid in ss.cg_section_order if sid.startswith("lesson_"))
    sid = f"lesson_{n + 1}"
    # Match the same "Lesson 3 · K of N · {topic}" labeling pattern used in
    # kickoff_generation. We re-derive K based on the new total.
    new_total = n + 1
    label = f"Lesson 3 · {new_total} of {new_total} · {topic[:36]}{'…' if len(topic) > 36 else ''}"
    # Insert BEFORE the Assessment section so order stays
    # body → Lesson 3 case studies → Assessment → Closing.
    if "assessment" in ss.cg_section_order:
        idx = ss.cg_section_order.index("assessment")
        ss.cg_section_order.insert(idx, sid)
    else:
        ss.cg_section_order.append(sid)
    ss.cg_section_labels[sid] = label
    # Renumber sibling Lesson-3 case studies so labels stay coherent
    siblings = [s for s in ss.cg_section_order if s.startswith("lesson_")]
    total = len(siblings)
    for k, ssid in enumerate(siblings, start=1):
        meta = ss.cg_section_meta.get(ssid, {})
        topic_for = meta.get("topic", "") if ssid != sid else topic
        ss.cg_section_labels[ssid] = (
            f"Lesson 3 · {k} of {total} · {topic_for[:36]}"
            f"{'…' if len(topic_for) > 36 else ''}"
        )

    from prompts import build_embedded_lesson_for_topic
    # The new case study takes the next available 1-based index
    new_idx = len(siblings)
    res = complete(
        build_embedded_lesson_for_topic(
            course_body, topic, pick, index=new_idx),
        kind="embedded_lesson",
    )
    ss.cg_sections[sid] = res.text
    ss.cg_sources[sid] = [
        course_body,
        f"Topic anchor: {topic}",
        f"Claim summary:\n{pick.get('SUMMARY','')}",
    ]
    ss.cg_history[sid] = []
    ss.cg_section_meta[sid] = {
        "topic": topic, "claim_id": pick.get("DOCUMENT_ID", ""),
        "claim_summary": pick.get("SUMMARY", ""),
    }
    ss.cg_confidence[sid] = confidence_score(
        res.text, ss.cg_sources[sid], output_type="course_generator",
    )
    ss.cg_messages.append({
        "role": "assistant",
        "content": f"Added new lesson on **{topic}**. The card is at the bottom of the preview.",
    })


def _render_section(sid: str, label: str):
    """Render one section as part of a continuous flowing document.

    No bordered card. Just a thin divider above the section, a quiet header
    row (title + confidence pill + tiny action menu), then the content.
    """
    conf = ss.cg_confidence.get(sid)
    badge_grade = conf.grade if conf else None
    history = ss.cg_history.get(sid, [])
    content = ss.cg_sections.get(sid, "_(empty)_")
    is_target = ss.cg_target_section == label

    # Subtle divider between sections to maintain flow
    st.markdown(
        "<hr style='border:none; border-top:1px solid #e0e0e0; margin:1.4rem 0 1rem 0'/>",
        unsafe_allow_html=True,
    )

    # Header row: title + meta + confidence pill
    h1, h2 = st.columns([3, 1])
    with h1:
        st.markdown(f"#### {label}")
        section_meta(_est_tokens(content), _word_count(content))
    with h2:
        st.markdown(
            f"<div style='text-align:right;padding-top:0.4rem'>{confidence_badge(badge_grade)}</div>",
            unsafe_allow_html=True,
        )

    # Editable mode IS edit mode — the textarea is always visible.
    # Action row: Re-run | Undo (no separate "Edit" toggle, since being
    # in Editable mode already means "show the textarea").
    a1, a2 = st.columns([1, 1])
    with a1:
        if st.button("Re-run", key=f"regen_{sid}", use_container_width=True,
                     help="Regenerate this section from the original prompt"):
            with st.spinner(f"Regenerating {label}…"):
                regenerate_section(sid)
            st.rerun()
    with a2:
        disabled = not history
        undo_label = f"Undo · {len(history)}" if history else "Undo"
        if st.button(undo_label, key=f"undo_{sid}",
                     use_container_width=True, disabled=disabled,
                     help="Restore the previous version"):
            ss.cg_sections[sid] = history[0]
            ss.cg_history[sid] = history[1:]
            ss.cg_confidence[sid] = confidence_score(
                history[0], ss.cg_sources.get(sid, []), output_type="course_generator",
            )
            st.rerun()

    # Per-section dimension-by-dimension confidence breakdown removed —
    # the overall course-level confidence panel at the top of the
    # Editable view summarises everything. The grade pill in this
    # section's header (above) shows the per-section letter grade.

    # Drop the user straight into the markdown textarea — that's the
    # whole point of Editable mode.
    new_text = st.text_area(
        "Edit markdown directly",
        value=content, height=360,
        key=f"editor_{sid}", label_visibility="collapsed",
    )
    if new_text != content:
        # Show a Save bar only when the user has actually changed text;
        # otherwise the form footer would be visual noise on every card.
        sb1, sb2, _sb3 = st.columns([1, 1, 4])
        with sb1:
            if st.button("Save", key=f"save_{sid}",
                          type="primary", use_container_width=True):
                _push_history(sid)
                ss.cg_sections[sid] = new_text
                if sid == "course_body":
                    _refresh_downstream_sources(get_driver(ss.cg_driver_id) or {})
                ss.cg_confidence[sid] = confidence_score(
                    new_text, ss.cg_sources.get(sid, []), output_type="course_generator",
                )
                st.rerun()
        with sb2:
            if st.button("Discard", key=f"discard_{sid}",
                          use_container_width=True,
                          help="Throw away your edits and restore the saved text."):
                # Drop the textarea's preserved widget state so the next
                # rerun shows the saved content instead of the unsaved
                # typed value.
                st.session_state.pop(f"editor_{sid}", None)
                st.rerun()
        st.caption(
            f"_Unsaved changes — {abs(len(new_text) - len(content))} chars different._"
        )


# ---------------------------------------------------------------------------
# Router (callable from the unified app.py or standalone)
# ---------------------------------------------------------------------------
def render():
    _init_state()  # idempotent; safe to call every rerun
    # Sidebar was retired; tool controls now live in the toolbar popover.
    if ss.cg_phase == "idle":
        render_idle()
    elif ss.cg_phase == "generating":
        render_generating()
    else:
        render_editing()


# Auto-run when this is the streamlit entry point. In unified mode, app.py
# calls render() itself based on the mode selector.
if not st.session_state.get("_advice_unified_mode"):
    render()
