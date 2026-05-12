"""Interactive HTML course preview.

Renders the full course (course body + Lesson 4 assessment + embedded lessons)
as a styled HTML page that closely matches the MagMutual reference PDF:
  - Cover with eyebrow + title + course-overview TOC
  - Lesson dividers with blue eyebrows
  - Yellow Pause-and-Reflect callouts
  - Definition cards (clickable flip)
  - Reducing-clinical / Reducing-non-clinical tabs per case study
  - Interactive multiple-choice assessment with answer reveal + score

Embed via `streamlit.components.v1.html(html, height=N, scrolling=True)`.
"""
from __future__ import annotations

import html
import json
import re

# Re-use the base64 @font-face block from carbon so the iframe-rendered
# preview gets the same embedded Lato as the parent page. (SiS warehouse
# runtime can't serve static files; data URIs are the only path.)
try:
    from .carbon import _font_face_css as _carbon_font_face_css
except ImportError:
    try:
        from carbon import _font_face_css as _carbon_font_face_css
    except ImportError:
        def _carbon_font_face_css() -> str:  # type: ignore[misc]
            return ""


def render_course_html(title: str, sections: dict[str, str],
                        accent: str = "#0f62fe", height_hint: int = 1200,
                        top_factors: list[dict] | None = None,
                        case_photos: dict[str, dict] | None = None,
                        cover_photo: dict | None = None) -> str:
    """Build a full-page HTML preview of the course.

    `sections` is an ordered dict of section_label -> markdown content. Sections
    whose label starts with "Assessment" get the interactive-quiz treatment;
    other sections render the markdown with MagMutual-style typography.

    `top_factors` is a list of `{label, pct}` dicts (from
    `snowflake_client.top_contributing_factors`). When provided, a
    horizontal bar chart "Top contributing factors" is injected into
    Lesson 2 right after the stats prose.

    `case_photos` is an optional dict mapping section_label to
    `{"url": str, "label": str}`. When the section's case study renders,
    the dict value swaps the gray hero placeholder for that photo.

    `cover_photo` is an optional `{"url": str, "label": str}` that
    renders a wide hero image at the very top of the course (above the
    eyebrow). Both kinds of photos are editable from the app's picker.

    Note: CME meta (time estimate, credit hours, effective/expiration
    dates) and accreditation disclosures are NOT embedded in the course
    artifact. They live in the LMS wrapper page that launches this
    SCORM. Keeping them out matches the MM SCORM reference exactly.
    """
    safe_title = html.escape(title)

    # TOC — flatten body lessons into top-level entries so the cover shows
    # 5 lessons (matching MagMutual / the PDF cover). Embedded case studies
    # are nested under Lesson 3 and don't get a separate TOC entry.
    toc_entries = []  # list of (anchor_id, label)
    for i, (name, md) in enumerate(sections.items()):
        if re.match(r"\s*Lesson\s*3\s*[·\-]", name or "", re.I):
            continue  # embedded case study — nested
        lesson_headers = re.findall(
            r"^##\s+(Lesson\s+\d+\s+of\s+5[^\n]*)$", md or "", re.M)
        if lesson_headers:
            for j, h in enumerate(lesson_headers):
                toc_entries.append((f"sec-{i}-l{j}", h))
        else:
            toc_entries.append((f"sec-{i}", name))
    toc_items = "".join(
        f"<li><a href='#{anchor}'>{html.escape(label)}</a></li>"
        for anchor, label in toc_entries
    )

    # First-lesson anchor target for the Start-course CTA.
    first_anchor = toc_entries[0][0] if toc_entries else None
    cta_html = (
        f"<a class='cover-cta' href='#{first_anchor}'>Start course "
        f"<span class='cover-cta-arrow'>&rarr;</span></a>"
        if first_anchor else ""
    )

    # Body
    body_parts = [
        f"""<header class="cover">
            {_render_cover_hero(cover_photo)}
            <div class="eyebrow">MagMutual · Risk Management</div>
            <h1>{safe_title}</h1>
            <p class="cover-sub">Reducing liability through evidence-based education.</p>
            <nav class="toc">
                <div class="toc-label">Course Overview</div>
                <ol>{toc_items}</ol>
            </nav>
            {cta_html}
        </header>"""
    ]

    section_keys = list(sections.keys())
    for i, (name, md) in enumerate(sections.items()):
        body_parts.append(
            f"<section class='lesson-block' id='sec-{i}'>"
            f"<div class='lesson-eyebrow'>{html.escape(name)}</div>"
        )
        # Assessment dispatch: match Lesson 4 of 5 OR HTML5 question
        # markers. We can't just substring-match "assessment" in the label
        # because embedded case studies sometimes have titles like
        # "Single-point assessment vs. serial evaluation".
        is_assessment = bool(
            re.search(r"\blesson\s*4\s*of\s*5\b", name, re.I)
            or re.search(r"<h2[^>]*>\s*Question\s*\d", md or "", re.I)
        )
        if is_assessment:
            body_parts.append(_render_assessment(md))
        else:
            # Strip the leading H1 from body content (course title is
            # already the cover headline — don't duplicate it).
            cleaned_md = _strip_leading_h1(md or "")
            # Look up an optional photo for this section (by name or
            # by section_{i} index key so callers can pick either).
            photo_url = ""
            photo_label = ""
            if case_photos:
                ph = case_photos.get(name) or case_photos.get(f"section_{i}")
                if ph:
                    photo_url = ph.get("url", "")
                    photo_label = ph.get("label", "")
            section_html = _md_to_html_with_anchors(
                cleaned_md, base_id=f"sec-{i}",
                photo_url=photo_url, photo_label=photo_label,
            )
            # Inject the contributing-factors chart right after the
            # "## Lesson 2 of 5: Loss Trends" subtree so it lands where
            # MagMutual places its [insert chart] anchor.
            if top_factors and re.search(
                    r"<h2[^>]*class=['\"]lesson-title['\"][^>]*>\s*Loss Trends",
                    section_html, re.I):
                section_html = _inject_factor_chart(section_html, top_factors)
            body_parts.append(
                f"<div class='lesson-body'>{section_html}</div>"
            )
        # Continue → button at the end of each section (except the last,
        # which gets the course-complete panel below). Mirrors MM's
        # per-lesson "Continue" placement so learners don't have to find
        # the next lesson via the TOC.
        if i + 1 < len(section_keys):
            next_label = _continue_label(section_keys[i + 1])
            body_parts.append(
                f"<div class='lesson-foot'>"
                f"<a class='continue-cta' href='#sec-{i + 1}'>"
                f"{html.escape(next_label)} <span class='cover-cta-arrow'>&rarr;</span>"
                f"</a></div>"
            )
        body_parts.append("</section>")

    body_parts.append(
        "<footer class='course-foot'>"
        "<p>Generated by MyAdvice Builder · Use the menu to move through each lesson at your own pace.</p>"
        "</footer>"
    )

    body = "\n".join(body_parts)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>{safe_title}</title>
  <!-- Lato embedded as base64 @font-face — SiS CSP blocks external CDNs and
       its warehouse runtime can't serve static files, so the only path to
       custom typography inside this preview iframe is to inline the bytes. -->
  <style>{_carbon_font_face_css()}\n{_CSS.replace('__ACCENT__', accent)}</style>
</head>
<body>
  <div class="reading-progress" aria-hidden="true"><span class="reading-progress-fill"></span></div>
  <main class="course">{body}</main>
  <script>{_JS}</script>
</body>
</html>
"""


def render_claims_lesson_html(title: str, lesson_md: str,
                                eyebrow: str = "MagMutual · Claims Lesson",
                                subtitle: str = (
                                    "One-claim deep dive grounded in the matching"
                                    " MagMutual Risk Playbook."),
                                accent: str = "#0f62fe") -> str:
    """Render a single claims lesson with the SAME visual scaffolding as
    `render_course_html` — same Lato typography, color palette, corner
    radii, card borders, dark-gray Pause-and-reflect banner, accent
    color. Use when the user picks "Live HTML" preview in the Claims
    Lesson app so it stays visually consistent with the Course
    Generator output.

    Layout: a slim header (eyebrow + H1 + sub) followed by the lesson
    body rendered via the same `_md_to_html` parser the Course Generator
    uses (so flip cards, callouts, lists, and the Pause-and-reflect
    banner all look identical).
    """
    safe_title = html.escape(title)
    safe_eye = html.escape(eyebrow)
    safe_sub = html.escape(subtitle)
    cleaned = _strip_leading_h1(lesson_md or "")
    body_html = _md_to_html_with_anchors(cleaned, base_id="cl-0")
    body = (
        f"<header class='cover'>"
        f"<div class='eyebrow'>{safe_eye}</div>"
        f"<h1>{safe_title}</h1>"
        f"<p class='cover-sub'>{safe_sub}</p>"
        f"</header>"
        f"<section class='lesson-block' id='sec-0'>"
        f"<div class='lesson-body'>{body_html}</div>"
        f"</section>"
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>{safe_title}</title>
  <!-- Lato embedded as base64 @font-face — SiS CSP blocks external CDNs and
       its warehouse runtime can't serve static files, so the only path to
       custom typography inside this preview iframe is to inline the bytes. -->
  <style>{_carbon_font_face_css()}\n{_CSS.replace('__ACCENT__', accent)}</style>
</head>
<body>
  <div class="reading-progress" aria-hidden="true"><span class="reading-progress-fill"></span></div>
  <main class="course">{body}</main>
  <script>{_JS}</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Assessment HTML → interactive widget
# ---------------------------------------------------------------------------
def _render_assessment(md_or_html: str) -> str:
    """Convert the assessment output (HTML5 from team prompt) into clickable
    multiple-choice questions with an answer-reveal mechanic.
    """
    questions = _parse_assessment(md_or_html)
    if not questions:
        # Fall back to plain rendering if we can't parse
        return f"<div class='lesson-body'>{_md_to_html(md_or_html)}</div>"

    total = len(questions)
    cards = []
    for idx, q in enumerate(questions):
        qid = f"q{idx}"
        opts_html = "".join(
            f"""<label class='qa-opt'>
                <input type='radio' name='{qid}' value='{i}'/>
                <span class='qa-letter'>{chr(65 + i)}</span>
                <span class='qa-text'>{html.escape(opt)}</span>
            </label>"""
            for i, opt in enumerate(q["options"])
        )
        meta = []
        if q.get("difficulty"):
            meta.append(f"<span class='qa-pill'>{html.escape(q['difficulty'])}</span>")
        meta_html = "".join(meta)

        # One card visible at a time, MagMutual-style. We mark the first card
        # as active; the JS swaps active state on Next.
        active_class = " qa-card--active" if idx == 0 else ""
        cards.append(f"""
        <article class="qa-card{active_class}"
                 data-q-index="{idx}"
                 data-correct="{q['correct_idx']}"
                 data-rationale="{html.escape(q['rationale'])}">
            <div class="qa-progress">{idx + 1:02d} / {total:02d} &middot; Question</div>
            <div class="qa-head">
                <h3 class="qa-num">Question {idx + 1}</h3>
                {meta_html}
            </div>
            <p class="qa-stem">{html.escape(q['stem'])}</p>
            <div class="qa-opts">{opts_html}</div>
            <div class="qa-actions">
                <button type="button" class="qa-submit">Submit</button>
                <button type="button" class="qa-next" disabled>{'Finish' if idx == total - 1 else 'Next'}</button>
            </div>
            <div class="qa-feedback"></div>
        </article>
        """)

    # Final summary card shown after the last question is answered
    cards.append(f"""
    <article class="qa-card qa-card--summary">
        <h3 class="qa-num">Assessment complete</h3>
        <p class="qa-stem">You answered <span class="qa-correct">0</span> of
        <span class="qa-total">{total}</span> questions correctly.</p>
        <div class="qa-pct"><span class="qa-pct-fill"></span></div>
        <p class="qa-pass-msg" data-passed="false"></p>
        <div class="qa-actions">
            <button type="button" class="qa-reset">Restart assessment</button>
        </div>
    </article>
    """)

    return f"<div class='qa-wrap-single'><div class='qa-cards'>{''.join(cards)}</div></div>"


_CORRECT_RE = re.compile(r"<b>\s*correct\s*:\s*</b>\s*([A-D])", re.I)
_RATIONALE_RE = re.compile(r"<b>\s*rationale\s*:\s*</b>\s*(.+?)(?:</p>|$)", re.I | re.S)
_LO_RE = re.compile(r"learning objective\s*:\s*(.+?)(?:</div>|<|$)", re.I | re.S)
_DIFFICULTY_RE = re.compile(r"\b(beginner|intermediate|advanced)\b", re.I)


def _parse_assessment(text: str) -> list[dict]:
    """Pull questions out of the HTML-ish assessment output produced by the
    team-authored prompt. Tolerates markdown fences and slight variations.
    """
    if not text:
        return []
    # Strip code fences if any
    text = re.sub(r"```\w*", "", text)
    # Normalise to one big string we can chunk by question
    chunks = re.split(r"<section[^>]*>", text, flags=re.I)
    questions = []
    for chunk in chunks:
        if "<h2" not in chunk.lower() and "question" not in chunk.lower():
            continue
        # Find stem: paragraph immediately before the answer list
        ol_match = re.search(r"<ol[^>]*>(.+?)</ol>", chunk, re.I | re.S)
        if not ol_match:
            continue
        opts_html = ol_match.group(1)
        opts = [
            re.sub(r"<[^>]+>", "", li).strip()
            for li in re.findall(r"<li[^>]*>(.+?)</li>", opts_html, re.I | re.S)
        ]
        if len(opts) < 2:
            continue
        # Stem: the last <p> before the <ol>, OR the first <p> in chunk
        before_ol = chunk[: ol_match.start()]
        p_matches = re.findall(r"<p[^>]*>(.+?)</p>", before_ol, re.I | re.S)
        stem = ""
        if p_matches:
            stem = re.sub(r"<[^>]+>", "", p_matches[-1]).strip()
        # Correct
        correct_letter = None
        m = _CORRECT_RE.search(chunk)
        if m:
            correct_letter = m.group(1).upper()
        # Rationale
        rationale = ""
        m = _RATIONALE_RE.search(chunk)
        if m:
            rationale = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        # Difficulty
        difficulty = ""
        m = _DIFFICULTY_RE.search(chunk)
        if m:
            difficulty = m.group(1).capitalize()
        # Learning objective
        learning_objective = ""
        m = _LO_RE.search(chunk)
        if m:
            learning_objective = re.sub(r"<[^>]+>", "", m.group(1)).strip()[:160]

        questions.append({
            "stem": stem or "(missing question stem)",
            "options": opts,
            "correct_idx": (ord(correct_letter) - 65) if correct_letter else 0,
            "rationale": rationale,
            "difficulty": difficulty,
            "learning_objective": learning_objective,
        })
    return questions


# ---------------------------------------------------------------------------
# Mini-markdown → HTML
# ---------------------------------------------------------------------------
def _strip_leading_h1(md: str) -> str:
    """Remove the first '# Title' line from a body section. The cover
    already shows the course title; keeping the H1 in body content
    would duplicate it under the lesson eyebrow."""
    if not md:
        return ""
    lines = md.splitlines()
    # Skip leading blanks
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and lines[i].startswith("# "):
        # Drop the H1 line and any blank line that immediately follows it.
        del lines[i]
        if i < len(lines) and not lines[i].strip():
            del lines[i]
    return "\n".join(lines)


def _md_to_html_with_anchors(md: str, base_id: str,
                              photo_url: str | None = None,
                              photo_label: str = "") -> str:
    """Render markdown as HTML, but tag each `## Lesson N of 5` heading
    with id="{base_id}-l{N}" so the cover TOC's anchor links jump to it.

    `photo_url` (optional) is forwarded down to `_render_case_study` so
    the embedded case study's hero image swaps the gray placeholder for
    a real photo. Each section in the course typically owns one case
    study, so one photo per section is enough.

    The lesson H2 was refactored to render as a small italic eyebrow
    `<div class='lesson-marker'>Lesson N of 5</div>` followed by a
    bigger `<h2 class='lesson-title'>Course Overview</h2>`. We attach
    the anchor id to the lesson-marker DIV.
    """
    rendered = _md_to_html(md, photo_url=photo_url, photo_label=photo_label)
    counter = [0]
    def repl(m):
        idx = counter[0]
        counter[0] += 1
        return (
            f"<div id=\"{base_id}-l{idx}\" "
            f"class='lesson-marker'>{m.group(1)}</div>"
        )
    return re.sub(
        r"<div class='lesson-marker'>(.*?)</div>",
        repl, rendered, flags=re.S,
    )


def _inject_factor_chart(section_html: str, top_factors: list[dict]) -> str:
    """Append the contributing-factor bar chart to the end of the
    Lesson 2 body (matches MM's [insert chart] anchor location)."""
    chart = render_factor_chart(top_factors)
    return section_html + chart


def render_factor_chart(top_factors: list[dict],
                         heading: str = "Top contributing factors") -> str:
    """Horizontal bar chart of the top contributing factors.

    Bars are scaled RELATIVE to the largest factor (so the chart fills
    the available width) but each row shows its ACTUAL percentage in the
    value column. This reads as a comparative chart — biggest factor
    spans the row, smaller factors are proportionally shorter — without
    looking like a progress meter.
    """
    if not top_factors:
        return ""
    pcts = [float(f.get("pct", 0)) for f in top_factors]
    max_pct = max(pcts) if pcts else 0
    if max_pct <= 0:
        return ""
    rows = []
    for f, pct in zip(top_factors, pcts):
        # Visual width: relative to the biggest factor.
        # Value label: the actual percentage of all tagged claims.
        width = (pct / max_pct) * 100.0
        rows.append(
            "<div class='fc-row'>"
            f"<div class='fc-label'>{html.escape(f.get('label', ''))}</div>"
            "<div class='fc-track'>"
            f"<div class='fc-bar' style='width:{width:.1f}%'></div>"
            "</div>"
            f"<div class='fc-value'>{pct:.1f}%</div>"
            "</div>"
        )
    return (
        "<div class='factor-chart'>"
        f"<h3 class='fc-h'>{html.escape(heading)}</h3>"
        f"<p class='fc-cap'>Share of tagged claims by primary contributing factor.</p>"
        f"<div class='fc-rows'>{''.join(rows)}</div>"
        "</div>"
    )


def _consume_strategy_block(lines: list[str], i: int) -> tuple[int, list[str], list[str]]:
    """Walk past `#### Reducing clinical risks` (and optionally `#### Reducing
    non-clinical risks`) plus their bullet lists, returning the new index
    and the two bullet lists.
    """
    i += 1  # past the "Reducing clinical risks" heading
    clinical_items: list[str] = []
    while i < len(lines) and not re.match(r"^#{1,4}\s|^---", lines[i].strip()):
        if lines[i].lstrip().startswith("- "):
            clinical_items.append(lines[i].lstrip()[2:])
        i += 1
    non_clinical_items: list[str] = []
    if i < len(lines) and re.match(r"^####\s+Reducing non-clinical risks\b",
                                    lines[i].strip(), re.I):
        i += 1
        while i < len(lines) and not re.match(r"^#{1,4}\s|^---", lines[i].strip()):
            if lines[i].lstrip().startswith("- "):
                non_clinical_items.append(lines[i].lstrip()[2:])
            i += 1
    return i, clinical_items, non_clinical_items


def _render_strategy_tabs(clinical: list[str], non_clinical: list[str],
                           idx: int) -> str:
    """Tab control with REDUCING CLINICAL RISKS / REDUCING NON-CLINICAL RISKS.
    One panel visible at a time, matching the MagMutual reference.
    """
    tab_id = f"strat{idx}"
    has_non = bool(non_clinical)
    cli_li = "".join(f"<li>{_inline(x)}</li>" for x in clinical) or "<li>—</li>"
    non_li = "".join(f"<li>{_inline(x)}</li>" for x in non_clinical)
    head = (
        f"<div class='strat-tabs' data-tab-id='{tab_id}'>"
        f"<div class='strat-tabbar' role='tablist'>"
        f"<button class='strat-tabbtn is-active' role='tab' data-panel='cli'>"
        f"REDUCING CLINICAL RISKS</button>"
    )
    if has_non:
        head += (
            f"<button class='strat-tabbtn' role='tab' data-panel='non'>"
            f"REDUCING NON-CLINICAL RISKS</button>"
        )
    head += "</div>"
    panels = (
        f"<div class='strat-panel is-active' data-panel='cli' role='tabpanel'>"
        f"<ul>{cli_li}</ul></div>"
    )
    if has_non:
        panels += (
            f"<div class='strat-panel' data-panel='non' role='tabpanel'>"
            f"<ul>{non_li}</ul></div>"
        )
    return head + panels + "</div>"


def _render_case_study(lines: list[str], start: int, cs_idx: int,
                       photo_url: str | None = None,
                       photo_label: str = "") -> tuple[str, int]:
    """Render an entire embedded case-study (everything from
    `### Key loss driver: X` until the next H3 / HR / end-of-section)
    as the MagMutual card layout.

    `photo_url` swaps the round-gray hero placeholder for a real image
    (data: URI for local-mock library photos, https: pre-signed URL for
    Snowflake-stage photos, or data: base64 for user uploads).
    """
    n = len(lines)
    # H3 line: "### Key loss driver: Single-point assessment vs..."
    h3 = lines[start].strip()
    m = re.match(r"^###\s+(.+)$", h3)
    title = m.group(1) if m else "Case study"
    i = start + 1
    # Optional intro paragraph (everything until next H4 / HR / EOF)
    intro_buf: list[str] = []
    while i < n:
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if re.match(r"^#{1,4}\s|^---", line):
            break
        intro_buf.append(line)
        i += 1
    intro_html = (
        f"<p class='cs-intro'>{_inline(' '.join(intro_buf))}</p>"
        if intro_buf else ""
    )

    parts: list[str] = []
    parts.append(f"<section class='case-study cs-{cs_idx}'>")
    parts.append(f"<h2 class='cs-title'>{_inline(title)}</h2>")
    if intro_html:
        parts.append(intro_html)
    parts.append(_render_cs_hero(photo_url=photo_url, photo_label=photo_label))

    # Walk the H4 sections inside the case study
    cards_started = False
    while i < n:
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        # Stop at next case study (next H3) or HR
        if re.match(r"^###\s|^---", line):
            break

        # Pause and reflect — emit the dark banner
        if re.match(r"^#{2,4}\s*Pause and reflect\b", line, re.I):
            i += 1
            buf: list[str] = []
            while i < n and not re.match(r"^#{1,4}\s|^---", lines[i].strip()):
                if lines[i].strip():
                    buf.append(lines[i].strip())
                i += 1
            parts.append(
                "<aside class='reflect'>"
                "<div class='reflect-label'>Pause and reflect</div>"
                f"<p>{_inline(' '.join(buf))}</p>"
                "</aside>"
            )
            cards_started = False  # banner breaks the connector chain
            continue

        # Optional "Risk reduction strategies for [topic]" wrapper —
        # MagMutual prefaces the tab control with this short heading
        # plus a 1-line intro. We render it as a small section above
        # the tabs.
        m_strat_wrap = re.match(
            r"^####\s+Risk reduction strategies(?:\s+for\s+(.+))?$",
            line, re.I,
        )
        if m_strat_wrap:
            wrap_topic = (m_strat_wrap.group(1) or "").strip()
            i += 1
            # Pick up the optional intro line(s) until next H4 / H3 / hr
            intro_buf: list[str] = []
            while i < n:
                inner = lines[i].strip()
                if not inner:
                    i += 1
                    continue
                if re.match(r"^#{1,4}\s|^---", inner):
                    break
                intro_buf.append(inner)
                i += 1
            heading = (
                f"Risk reduction strategies for {html.escape(wrap_topic)}"
                if wrap_topic
                else "Risk reduction strategies"
            )
            parts.append(
                f"<h2 class='cs-strat-wrap-h'>{heading}</h2>"
            )
            if intro_buf:
                parts.append(
                    f"<p class='cs-strat-wrap-intro'>"
                    f"{_inline(' '.join(intro_buf))}</p>"
                )
            cards_started = False
            continue

        # Reducing clinical / non-clinical → tab control
        if re.match(r"^####\s+Reducing clinical risks\b", line, re.I):
            i, cli, non = _consume_strategy_block(lines, i)
            parts.append(_render_strategy_tabs(cli, non, cs_idx))
            cards_started = False
            continue

        # H4 sub-sections render as cards. Timeline gets per-entry sub-cards.
        m4 = re.match(r"^####\s+(.+)$", line)
        if m4:
            heading = m4.group(1).strip()
            i += 1
            body_lines: list[str] = []
            while i < n and not re.match(r"^#{1,4}\s|^---", lines[i].strip()):
                body_lines.append(lines[i])
                i += 1

            if cards_started:
                parts.append("<div class='cs-connector'></div>")

            if re.match(r"^Timeline\b", heading, re.I):
                parts.extend(_render_timeline_cards(body_lines))
            else:
                parts.append(_render_cs_card(heading, body_lines))
            cards_started = True
            continue

        # Stray paragraph inside the case study — render as plain prose
        body_lines = [line]
        i += 1
        while i < n and lines[i].strip() and not re.match(
                r"^#{1,4}\s|^---", lines[i].strip()):
            body_lines.append(lines[i].strip())
            i += 1
        parts.append(f"<p>{_inline(' '.join(body_lines))}</p>")

    parts.append("</section>")
    return "\n".join(parts), i


def _render_cover_hero(cover_photo: dict | None) -> str:
    """Wide hero image at the top of the course cover (above the
    eyebrow). When `cover_photo` is None, render an empty placeholder
    so the cover spacing stays consistent. When provided, render an
    `<img>` clipped to the same rounded card shape.
    """
    if not cover_photo or not cover_photo.get("url"):
        return ""
    url = cover_photo.get("url", "")
    label = cover_photo.get("label", "Course hero")
    return (
        f"<div class='cover-hero'>"
        f"<img class='cover-hero-img' src='{html.escape(url)}'"
        f" alt='{html.escape(label)}' />"
        f"</div>"
    )


def _continue_label(next_section_name: str) -> str:
    """Friendly Continue-button label for whatever's next.

    Strips the "Lesson N of N · " prefix so the button reads
    "Continue to Loss Trends" instead of "Continue to Lesson 2 of 5: Loss Trends".
    Keeps it concise to match MM's button-text style.
    """
    s = (next_section_name or "").strip()
    # Drop common scaffolding prefixes
    s = re.sub(r"^Lesson\s+\d+\s*(?:of\s+\d+\s*)?[·\-:]?\s*", "", s, flags=re.I)
    # Drop "1 of 3 · " case-study scaffolding
    s = re.sub(r"^\d+\s*of\s+\d+\s*[·\-:]?\s*", "", s, flags=re.I)
    s = s.strip(" ·-:") or "next section"
    # Truncate so the button doesn't wrap on narrow viewports
    if len(s) > 48:
        s = s[:47].rstrip() + "…"
    return f"Continue to {s}"


def _render_cs_hero(photo_url: str | None = None,
                    photo_label: str = "") -> str:
    """Round hero image for a case study.

    When `photo_url` is provided (data: URI from the local photo library
    or a pre-signed Snowflake-stage URL), renders an <img> clipped into
    the round frame. Falls back to a neutral-gray placeholder otherwise.
    """
    if photo_url:
        alt = html.escape(photo_label or "Case study photo")
        return (
            "<div class='cs-hero'>"
            "<div class='cs-hero-circle cs-hero-circle--img'>"
            f"<img class='cs-hero-img' src='{html.escape(photo_url)}' alt='{alt}'/>"
            "</div>"
            "</div>"
        )
    return (
        "<div class='cs-hero'>"
        "<div class='cs-hero-circle' aria-hidden='true'>"
        "<svg viewBox='0 0 24 24' width='32' height='32' fill='none' "
        "stroke='currentColor' stroke-width='1.6' stroke-linecap='round' "
        "stroke-linejoin='round'>"
        "<path d='M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2'/>"
        "<circle cx='12' cy='7' r='4'/></svg>"
        "</div>"
        "</div>"
    )


def _render_cs_card(heading: str, body_lines: list[str]) -> str:
    """Wrap a `#### heading` + body in a white bordered card."""
    body_md = "\n".join(body_lines).strip()
    if not body_md:
        return f"<div class='cs-card'><div class='cs-card-h'>{html.escape(heading)}</div></div>"
    # Reuse _md_to_html on the body so bullet lists render properly
    body_html = _md_to_html(body_md)
    return (
        "<div class='cs-card'>"
        f"<div class='cs-card-h'>{html.escape(heading)}</div>"
        f"{body_html}"
        "</div>"
    )


def _render_timeline_cards(body_lines: list[str]) -> list[str]:
    """Split a Timeline body into per-entry cards. The prompt emits each
    pivotal moment as `**[Date]**\\n[body]`; we wrap each as its own card
    with vertical connectors between.
    """
    text = "\n".join(body_lines).strip()
    # Split on bold date markers
    chunks = re.split(r"(?=^\s*\*\*[^*]+\*\*\s*$)", text, flags=re.M)
    cards: list[str] = []
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        m = re.match(r"^\*\*([^*]+)\*\*\s*\n?(.*)$", chunk, re.S)
        if m:
            date = m.group(1).strip()
            body = m.group(2).strip()
            body_html = _inline(body) if body else ""
            cards.append(
                "<div class='cs-card cs-card--timeline'>"
                f"<div class='cs-card-h'>{html.escape(date)}</div>"
                f"<p>{body_html}</p>"
                "</div>"
            )
        else:
            # Not a clean date marker — render as a plain card
            cards.append(
                "<div class='cs-card cs-card--timeline'>"
                f"<p>{_inline(chunk)}</p>"
                "</div>"
            )
    # Interleave with connectors
    out: list[str] = []
    for k, c in enumerate(cards):
        if k > 0:
            out.append("<div class='cs-connector'></div>")
        out.append(c)
    return out


def _md_to_html(md: str, photo_url: str | None = None,
                photo_label: str = "") -> str:
    if not md:
        return ""
    lines = md.splitlines()
    out: list[str] = []
    i = 0

    def flush_para(buf: list[str]):
        if buf:
            out.append("<p>" + _inline(" ".join(buf)) + "</p>")
            buf.clear()

    para_buf: list[str] = []
    case_study_idx = [0]  # mutable counter for unique IDs across multiple case studies
    while i < len(lines):
        s = lines[i].rstrip()
        stripped = s.strip()
        if not stripped:
            flush_para(para_buf)
            i += 1
            continue
        # Case study (H3 "Case study N") — render as the MM card layout:
        # hero image + medical-summary card + timeline cards (vertical
        # connectors) + allegations / outcome cards + pause-and-reflect
        # banner + tabbed strategies.
        if re.match(r"^###\s+Case study\b", stripped, re.I):
            flush_para(para_buf)
            case_study_idx[0] += 1
            html_block, i = _render_case_study(
                lines, i, case_study_idx[0],
                photo_url=photo_url, photo_label=photo_label,
            )
            out.append(html_block)
            continue
        # Legacy back-compat: older drafts had `### Key loss driver: X`
        # at the top of each embedded lesson with the case-study cards
        # inline. Detect that form too.
        if re.match(r"^###\s+Key loss driver\s*:", stripped, re.I):
            flush_para(para_buf)
            case_study_idx[0] += 1
            html_block, i = _render_case_study(
                lines, i, case_study_idx[0],
                photo_url=photo_url, photo_label=photo_label,
            )
            out.append(html_block)
            continue
        # Pause and reflect callout (only when NOT inside a case study —
        # case study handles it above).
        if re.match(r"^#{2,4}\s*Pause and reflect\b", stripped, re.I):
            flush_para(para_buf)
            i += 1
            buf = []
            while i < len(lines) and not re.match(r"^#{1,4}\s|^---", lines[i].strip()):
                if lines[i].strip():
                    buf.append(lines[i].strip())
                i += 1
            content = " ".join(buf)
            out.append(
                "<aside class='reflect'>"
                "<div class='reflect-label'>Pause and reflect</div>"
                f"<p>{_inline(content)}</p></aside>"
            )
            continue
        # Stand-alone "Reducing clinical risks" outside a case study —
        # still render as tabs for consistency.
        m_clinical = re.match(r"^####\s+Reducing clinical risks\b", stripped, re.I)
        if m_clinical:
            flush_para(para_buf)
            i, clinical_items, non_clinical_items = _consume_strategy_block(lines, i)
            case_study_idx[0] += 1
            out.append(_render_strategy_tabs(
                clinical_items, non_clinical_items, case_study_idx[0]))
            continue
        # Definitions list (#### Definitions or ### Definition of key terms)
        # — re.I so sentence-case "Definition of key terms" matches too.
        if re.match(r"^#{2,4}\s*Definitions?(\s+of\s+key\s+terms)?\b",
                     stripped, re.I):
            flush_para(para_buf)
            out.append(f"<h3>{_inline(stripped.lstrip('# '))}</h3>")
            out.append(
                "<p class='def-hint'>Click a card to flip and reveal the definition.</p>"
            )
            i += 1
            cards = []
            while i < len(lines) and not re.match(r"^#{1,4}\s|^---", lines[i].strip()):
                line = lines[i].lstrip()
                m_def = re.match(r"^[-*]\s+\*\*(.+?)\*\*\s*[—-]\s*(.+)$", line)
                if m_def:
                    term, definition = m_def.group(1), m_def.group(2)
                    # Two-sided flip card: front = term, back = definition.
                    # The .def-card-flipped class is toggled by JS on click.
                    cards.append(
                        "<div class='def-card' tabindex='0' role='button' "
                        "aria-label='Flip card'>"
                        "<div class='def-card-inner'>"
                        f"<div class='def-card-front'>"
                        f"<div class='def-term'>{html.escape(term)}</div>"
                        f"<div class='def-flip-hint'>Tap to reveal</div>"
                        f"</div>"
                        f"<div class='def-card-back'>"
                        f"<div class='def-text'>{html.escape(definition)}</div>"
                        f"</div>"
                        f"</div>"
                        f"</div>"
                    )
                i += 1
            if cards:
                out.append("<div class='def-grid'>" + "".join(cards) + "</div>")
            continue
        # Headings — special case: a Lesson H2 is split into a small italic
        # eyebrow + a big bold lesson H1 (matching the MM reference).
        m = re.match(r"^(#{1,4})\s+(.+?)\s*$", stripped)
        if m:
            flush_para(para_buf)
            level = len(m.group(1))
            raw = m.group(2)
            mlesson = re.match(r"^(Lesson\s+\d+\s+of\s+\d+)\s*[:·\-]?\s*(.*)$",
                                raw, re.I)
            if level == 2 and mlesson:
                eyebrow = mlesson.group(1)
                title_only = mlesson.group(2).strip() or eyebrow
                out.append(
                    f"<div class='lesson-marker'>{html.escape(eyebrow)}</div>"
                    f"<h2 class='lesson-title'>{_inline(title_only)}</h2>"
                )
                i += 1
                continue
            cls = ""
            if re.match(r"Lesson\s+\d+\s+of\s+\d+", raw, re.I):
                cls = " class='lesson-marker'"
            out.append(f"<h{level}{cls}>{_inline(raw)}</h{level}>")
            i += 1
            continue
        if re.match(r"^---+$", stripped):
            flush_para(para_buf)
            out.append("<hr/>")
            i += 1
            continue
        if stripped.startswith("- ") or stripped.startswith("* "):
            flush_para(para_buf)
            items = []
            while i < len(lines) and (lines[i].lstrip().startswith("- ") or lines[i].lstrip().startswith("* ")):
                items.append(lines[i].lstrip()[2:])
                i += 1
            out.append("<ul>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + "</ul>")
            continue
        if re.match(r"^\d+\.\s", stripped):
            flush_para(para_buf)
            items = []
            while i < len(lines) and re.match(r"^\s*\d+\.\s", lines[i]):
                items.append(re.sub(r"^\s*\d+\.\s", "", lines[i]))
                i += 1
            out.append("<ol class='num-circle'>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + "</ol>")
            continue
        para_buf.append(stripped)
        i += 1
    flush_para(para_buf)
    return "\n".join(out)


def _inline(s: str) -> str:
    s = html.escape(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*(?!\*)(.+?)\*(?!\*)", r"<em>\1</em>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return s


# ---------------------------------------------------------------------------
# Static assets
# ---------------------------------------------------------------------------
_CSS = """
/* MagMutual / Articulate Rise inspired stylesheet.
   Lato font, white background, near-black text, subtle gray dividers,
   and one quiet accent color (configurable). */
:root {
  --accent: __ACCENT__;
  --accent-dark: #002a3f;
  --bg: #ffffff;
  --text: #303030;        /* matches Articulate Rise body text */
  --text-strong: #161616;
  --text-muted: #707070;  /* matches Rise meta text */
  --line: #eaeaeb;        /* matches Rise dividers */
  --bg-soft: #f7f7f7;
  --bg-softer: #fafafa;
  /* Pause-and-reflect now matches the MagMutual reference: a dark gray
     full-width banner with white text (not the old yellow callout). */
  --reflect-bg: #525252;
  --reflect-bd: #525252;
  --reflect-text: #ffffff;
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  /* Emoji fonts named explicitly so 🩺 📘 📕 🧑 etc. render via the OS
     emoji font instead of falling back to ◇? when Lato lacks the glyph. */
  font-family: 'Lato', 'Helvetica Neue', Helvetica, Arial, sans-serif,
               'Apple Color Emoji', 'Segoe UI Emoji', 'Segoe UI Symbol',
               'Noto Color Emoji', 'Twemoji Mozilla';
  color: var(--text);
  background: var(--bg);
  font-size: 17px;
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}
.course { max-width: 760px; margin: 0 auto; padding: 2.5rem 1.4rem 4rem; }

/* ---------- Cover ---------- */
.cover {
  padding-bottom: 2.2rem;
  margin-bottom: 2.4rem;
  border-bottom: 1px solid var(--line);
}
/* Wide cover hero photo at the very top, above the eyebrow.
   Keeps the same rounded look as the round case-study heroes
   but wider and shorter (banner shape) so it doesn't push the
   title below the fold on first paint. */
.cover-hero {
  margin: 0 0 1.6rem;
  border-radius: 8px;
  overflow: hidden;
  background: #e6e8ec;
  aspect-ratio: 16 / 6;
  max-height: 280px;
}
.cover-hero-img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.cover .eyebrow {
  font-size: 0.78rem;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--accent);
  margin-bottom: 1rem;
  font-weight: 700;
}
.cover h1 {
  font-size: 2.4rem;
  font-weight: 900;
  line-height: 1.18;
  margin: 0 0 0.6rem 0;
  color: var(--text-strong);
  letter-spacing: -0.005em;
}
.cover-sub { color: var(--text-muted); font-size: 1.05rem; margin: 0 0 1.8rem; font-weight: 400; }

.toc {
  background: var(--bg-soft);
  padding: 1.4rem 1.6rem;
  border-radius: 8px;
}
.toc-label {
  font-size: 0.74rem;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--text-muted);
  margin-bottom: 0.5rem;
  font-weight: 700;
}
.toc ol { margin: 0; padding-left: 0; counter-reset: tocitem; list-style: none; }
.toc li {
  margin-bottom: 0.45rem;
  list-style: none;
  counter-increment: tocitem;
  font-size: 0.96rem;
  display: flex;
  align-items: center;
  gap: 0.7rem;
}
.toc li::before {
  content: counter(tocitem);
  color: var(--text-muted);
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  min-width: 1.1rem;
  text-align: right;
}
.toc a { color: var(--text-strong); text-decoration: none; border-bottom: 1px solid transparent; }
.toc a:hover { border-bottom-color: var(--accent); color: var(--accent); }

/* Start course CTA on the cover */
.cover-cta {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  margin-top: 1.6rem;
  padding: 0.75rem 1.4rem;
  background: var(--accent);
  color: #ffffff !important;
  font-weight: 700;
  font-size: 0.95rem;
  letter-spacing: 0.02em;
  border-radius: 999px;
  text-decoration: none;
  border-bottom: none !important;
  transition: background 0.15s ease, transform 0.15s ease;
}
.cover-cta:hover {
  background: var(--accent-dark);
  transform: translateX(2px);
}
.cover-cta-arrow { font-weight: 700; font-size: 1.05rem; line-height: 1; }

/* ---------- Reading-progress bar (fixed top of viewport) ----------
   Thin accent-colored bar that fills as the user scrolls — a common
   Articulate Rise / MM cue that the course has length to it and you're
   making progress. */
.reading-progress {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: rgba(0, 0, 0, 0.04);
  z-index: 60;
}
.reading-progress-fill {
  display: block;
  height: 100%;
  width: 0;
  background: var(--accent);
  transition: width 0.08s linear;
}

/* ---------- Continue → CTA at end of each lesson ---------- */
.lesson-foot {
  margin: 2.4rem 0 1.6rem;
  display: flex;
  justify-content: flex-end;
  border-top: 1px solid var(--line);
  padding-top: 1.6rem;
}
.continue-cta {
  display: inline-flex;
  align-items: center;
  gap: 0.55rem;
  padding: 0.7rem 1.4rem;
  background: var(--accent);
  color: #ffffff !important;
  text-decoration: none;
  font-weight: 700;
  font-size: 0.95rem;
  letter-spacing: 0.01em;
  border-radius: 999px;
  border-bottom: none !important;
  transition: background 0.15s ease, transform 0.15s ease;
}
.continue-cta:hover {
  background: var(--accent-dark);
  transform: translateX(2px);
}

/* ---------- TOC completion checkmarks (Articulate Rise behavior) ----
   When the user has scrolled past a lesson, its TOC entry's number is
   replaced with a filled accent circle + check. */
.toc li.is-done::before {
  content: '\2713';
  background: var(--accent);
  color: #ffffff;
  border-radius: 50%;
  width: 1.1rem;
  height: 1.1rem;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 0.7rem;
  text-align: center;
  min-width: 1.1rem;
  font-weight: 700;
}

/* ---------- TOC anchor flash ----------
   Brief highlight when a TOC link scrolls the user to a lesson, so
   they have an "I arrived" cue. */
.toc-flash {
  animation: tocFlash 1s ease-out 1;
}
@keyframes tocFlash {
  0%   { background-color: rgba(15, 98, 254, 0.10); }
  100% { background-color: transparent; }
}

/* ---------- Lesson blocks ---------- */
.lesson-block { margin-bottom: 2.6rem; scroll-margin-top: 1rem; }
.lesson-eyebrow {
  /* MagMutual style: italic gray, mixed case ("Lesson 3 of 5"), not
     uppercase blue. The lesson H1 carries the visual weight. */
  font-size: 0.95rem;
  letter-spacing: 0;
  text-transform: none;
  font-style: italic;
  color: var(--text-muted);
  margin-bottom: 0.6rem;
  font-weight: 400;
}
.lesson-body h1 {
  font-size: 2rem;
  font-weight: 900;
  margin: 0.4rem 0 1rem;
  letter-spacing: -0.005em;
  color: var(--text-strong);
  line-height: 1.2;
}
.lesson-body h2.lesson-marker, .lesson-body h2 {
  font-size: 1.4rem;
  font-weight: 900;
  margin: 2rem 0 0.7rem;
  padding-top: 1.5rem;
  border-top: 1px solid var(--line);
  color: var(--text-strong);
  line-height: 1.25;
}
.lesson-body h2.lesson-marker {
  /* "Lesson N of 5" markers inside the body — match the PDF style:
     italic gray mixed case, no separator rule above. */
  font-size: 0.95rem;
  font-weight: 400;
  font-style: italic;
  text-transform: none;
  letter-spacing: 0;
  color: var(--text-muted);
  border-top: none;
  padding-top: 0;
  margin: 1.6rem 0 0.4rem;
}
.lesson-body .lesson-marker:not(h2) {
  /* Eyebrow ("Lesson N of 5") rendered as a div above .lesson-title.
     Matches the PDF's section_eyebrow style. */
  font-size: 0.95rem;
  font-style: italic;
  color: var(--text-muted);
  margin: 1.6rem 0 0.2rem;
  font-weight: 400;
}
.lesson-body h2.lesson-title {
  /* Big bold lesson title under the italic eyebrow — matches the
     reference's oversized lesson headline (Lato Black, ~30pt). */
  font-size: 2.4rem;
  font-weight: 900;
  margin: 0 0 0.6rem;
  padding-top: 0;
  border-top: none;
  letter-spacing: -0.005em;
  color: var(--text-strong);
  line-height: 1.15;
}
.lesson-body h2.lesson-title + * {
  /* Add the short decorative gray rule under the title, matching the
     PDF's 1.6-inch _color_rule(LINE) below each lesson H1. */
  position: relative;
}
.lesson-body h2.lesson-title::after {
  content: "";
  display: block;
  width: 140px;
  height: 1px;
  background: var(--line);
  margin: 1rem 0 1.4rem;
}
.lesson-body h3 {
  font-size: 1.15rem;
  font-weight: 700;
  margin: 1.4rem 0 0.5rem;
  color: var(--text-strong);
  line-height: 1.3;
}
.lesson-body h4 {
  font-size: 0.78rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: var(--text-muted);
  margin: 1.2rem 0 0.4rem;
}
.lesson-body p { margin: 0 0 1rem; color: var(--text); font-size: 1rem; line-height: 1.65; }
.lesson-body strong { color: var(--text-strong); font-weight: 700; }
.lesson-body ul, .lesson-body ol { padding-left: 1.4rem; margin: 0.7rem 0 1.2rem; }
.lesson-body li { margin-bottom: 0.45rem; line-height: 1.55; }

/* Numbered lists rendered as gray-circle bubble + text (MM "Objectives"
   and "Key takeaways" pattern). Applied via class='num-circle' on <ol>. */
.lesson-body ol.num-circle {
  list-style: none;
  padding-left: 0;
  margin: 1rem 0 1.4rem;
  counter-reset: nc;
}
.lesson-body ol.num-circle li {
  counter-increment: nc;
  position: relative;
  padding-left: 3rem;
  min-height: 2rem;
  margin-bottom: 0.9rem;
  line-height: 1.55;
  color: var(--text);
}
.lesson-body ol.num-circle li::before {
  content: counter(nc);
  position: absolute;
  left: 0;
  top: 0.05rem;
  width: 2rem;
  height: 2rem;
  border-radius: 50%;
  background: #525252;
  color: #ffffff;
  font-weight: 700;
  font-size: 0.95rem;
  display: flex;
  align-items: center;
  justify-content: center;
  line-height: 1;
}
.lesson-body hr { border: none; border-top: 1px solid var(--line); margin: 1.8rem 0; }

/* ---------- Pause and reflect callout (MagMutual hero banner) ---------- */
.reflect {
  background: var(--reflect-bg);
  padding: 1.8rem 2rem;
  margin: 2rem 0;
  border-radius: 0;
  /* Span beyond the standard text column so it reads as a full-width
     section divider, like the MM reference. */
  margin-left: calc(50% - 50vw);
  margin-right: calc(50% - 50vw);
  padding-left: max(1.4rem, calc(50vw - 380px));
  padding-right: max(1.4rem, calc(50vw - 380px));
}
.reflect-label {
  /* Heading-scale "Pause and reflect" in mixed case, white, on the gray
     banner — matches the MM reference exactly. */
  font-size: 1.4rem;
  text-transform: none;
  letter-spacing: -0.005em;
  color: var(--reflect-text);
  margin-bottom: 0.5rem;
  font-weight: 900;
}
.reflect p { margin: 0; color: var(--reflect-text); font-style: normal; font-size: 1rem; line-height: 1.55; }

/* ---------- Case study (MagMutual / Articulate Rise card layout) ----
   Pixel values pulled from the MM SCORM CSS bundle:
   - Card radius: 8px (--arc-border-radius-lg)
   - Card padding: ~30px (Rise default padding-block:3rem)
   - Subtle shadow on hover (block-card.bg--range-light)
   - Section margins: 3rem (padding-block:3rem in Rise) */
.case-study { margin: 2.4rem 0 3rem; }
.cs-title {
  font-size: 1.75rem;
  font-weight: 900;
  color: var(--text-strong);
  margin: 0 0 0.8rem;
  line-height: 1.2;
  letter-spacing: -0.005em;
}
.cs-intro {
  color: var(--text);
  font-size: 1rem;
  line-height: 1.65;
  margin: 0 0 2rem;
}
.cs-hero { display: flex; justify-content: center; margin: 2rem 0 2.4rem; }
.cs-hero-circle {
  width: 240px; height: 240px;
  border-radius: 50%;
  background: #e6e8ec;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #9aa0a6;
  overflow: hidden;
}
.cs-hero-circle--img { background: #f4f5f7; }
.cs-hero-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.cs-card {
  border: 1px solid var(--line);
  background: var(--bg);
  border-radius: 8px;
  padding: 1.6rem 1.8rem;
  margin: 0;
  position: relative;
  transition: box-shadow 0.18s ease, transform 0.18s ease;
}
.cs-card:hover {
  box-shadow: 0 0.2rem 1.4rem rgba(0,0,0,0.06);
}
.cs-card-h {
  /* Rise's block-card sub-heading weight is 700 (their .arc-font-heading
     custom property). Reserved 900/Black for the display lesson title
     and the "Pause and reflect" hero banner. */
  font-weight: 700;
  color: var(--text-strong);
  font-size: 1.05rem;
  margin-bottom: 0.5rem;
  letter-spacing: 0;
  line-height: 1.25;
}
.cs-card p {
  margin: 0 0 0.7rem;
  color: var(--text);
  font-size: 1rem;
  line-height: 1.65;
}
.cs-card p:last-child { margin-bottom: 0; }
.cs-card ul { padding-left: 1.2rem; margin: 0.4rem 0; }
.cs-card li { margin-bottom: 0.5rem; line-height: 1.55; }
.cs-card--timeline { background: var(--bg); }
/* Vertical connector line between cards — matches Rise's thin vertical
   line (1px wide, ~30px tall) anchored to card center. */
.cs-connector {
  width: 1px;
  height: 32px;
  background: var(--line);
  margin: 0 auto;
}

/* ---------- Contributing-factor bar chart (Lesson 2 stats) ---------
   Renders as another Lesson-2 sub-section: an H3 heading + caption +
   horizontal bars, no boxed container. Matches MM's [insert chart]
   placement exactly — inline with the surrounding sub-sections. */
.factor-chart {
  margin: 1.4rem 0 1.6rem;
}
.fc-h {
  /* Match .lesson-body h3 — same size, weight, color, spacing. */
  font-size: 1.15rem;
  font-weight: 700;
  color: var(--text-strong);
  margin: 1.4rem 0 0.3rem;
  line-height: 1.3;
}
.fc-cap {
  font-size: 0.86rem;
  color: var(--text-muted);
  margin: 0 0 1rem;
}
.fc-rows { display: flex; flex-direction: column; gap: 0.7rem; }
/* Three-column row: label · bar track · value (outside the bar).
   Track has NO background and NO border so the bar reads as a chart
   bar on a baseline, not a filled progress meter. */
.fc-row {
  display: grid;
  grid-template-columns: 220px 1fr 56px;
  gap: 0.9rem;
  align-items: center;
}
@media (max-width: 720px) {
  .fc-row { grid-template-columns: 1fr; gap: 0.2rem; }
  .fc-row .fc-value { text-align: left; }
}
.fc-label {
  font-size: 0.92rem;
  color: var(--text);
  text-align: right;
  line-height: 1.4;
}
.fc-track {
  position: relative;
  height: 14px;
  background: transparent;
  border: none;
  border-bottom: 1px solid var(--line);
  border-radius: 0;
  overflow: visible;
}
.fc-bar {
  height: 100%;
  background: var(--accent);
  border-radius: 0;
}
.fc-value {
  font-size: 0.86rem;
  font-weight: 700;
  color: var(--text-strong);
  font-variant-numeric: tabular-nums;
  text-align: left;
}

/* ---------- Strategy wrapper (heading + intro that precedes tabs) ----
   Matches MM: "Risk reduction strategies for [topic]" subhead + brief
   line introducing the two-tab panel below. */
.cs-strat-wrap-h {
  font-size: 1.4rem;
  font-weight: 900;
  color: var(--text-strong);
  margin: 2.4rem 0 0.5rem;
  letter-spacing: -0.005em;
  line-height: 1.2;
}
.cs-strat-wrap-intro {
  color: var(--text);
  font-size: 1rem;
  line-height: 1.6;
  margin: 0 0 1.2rem;
}

/* ---------- Strategy tabs (REDUCING CLINICAL / NON-CLINICAL RISKS) ----
   Tab control with one panel visible at a time, matching MagMutual. */
.strat-tabs {
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--bg);
  margin: 1.8rem 0 2.4rem;
  overflow: hidden;
}
.strat-tabbar {
  display: flex;
  background: var(--bg);
  border-bottom: 1px solid var(--line);
}
.strat-tabbtn {
  flex: 1;
  padding: 1rem 1.2rem;
  background: var(--bg-soft);
  border: none;
  border-right: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
  font-family: inherit;
  font-size: 0.78rem;
  font-weight: 700;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--text-muted);
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.strat-tabbtn:last-child { border-right: none; }
.strat-tabbtn:hover { color: var(--text-strong); }
.strat-tabbtn.is-active {
  background: var(--bg);
  color: var(--text-strong);
  border-bottom-color: transparent;
  margin-bottom: -1px;
  position: relative;
}
.strat-panel {
  display: none;
  padding: 1.4rem 1.6rem 1.6rem;
  background: var(--bg);
}
.strat-panel.is-active { display: block; }
.strat-panel ul { padding-left: 1.2rem; margin: 0; }
.strat-panel li { margin-bottom: 0.7rem; font-size: 0.97rem; line-height: 1.6; color: var(--text); }
/* Backwards-compat selectors for the older two-column .strat-grid layout */
.strat-grid { display: none; }

/* ---------- Definition cards (clickable flip flashcards) ----------
   Matches the MagMutual Articulate Rise pattern: each card shows the
   term on the front; click (or press Enter / Space) to flip and reveal
   the definition. */
.def-hint {
  font-size: 0.85rem;
  color: var(--text-muted);
  margin: 0 0 0.8rem;
  font-style: italic;
}
.def-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 0.9rem;
  margin: 0.4rem 0 1.6rem;
  perspective: 1000px;
}
.def-card {
  position: relative;
  height: 130px;
  cursor: pointer;
  outline: none;
}
.def-card:focus-visible .def-card-inner {
  box-shadow: 0 0 0 2px var(--accent);
}
.def-card-inner {
  position: relative;
  width: 100%;
  height: 100%;
  transition: transform 0.55s cubic-bezier(0.2, 0.8, 0.2, 1);
  transform-style: preserve-3d;
}
.def-card.def-card-flipped .def-card-inner {
  transform: rotateY(180deg);
}
.def-card-front,
.def-card-back {
  position: absolute;
  inset: 0;
  border: 1px solid var(--line);
  background: var(--bg);
  border-radius: 8px;
  padding: 1.2rem 1.3rem;
  display: flex;
  flex-direction: column;
  justify-content: center;
  backface-visibility: hidden;
  -webkit-backface-visibility: hidden;
  transition: box-shadow 0.15s ease;
}
.def-card:hover .def-card-front,
.def-card:hover .def-card-back { box-shadow: 0 4px 12px rgba(0,0,0,0.05); }
.def-card-back {
  background: var(--bg-soft);
  transform: rotateY(180deg);
}
.def-term {
  /* Sub-card heading weight (Rise: 700). 900 reserved for display titles. */
  font-weight: 700;
  font-size: 1.05rem;
  color: var(--text-strong);
  line-height: 1.25;
}
.def-flip-hint {
  font-size: 0.72rem;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: var(--text-muted);
  margin-top: 0.6rem;
  font-weight: 700;
}
.def-text { font-size: 0.92rem; color: var(--text); line-height: 1.5; }

/* ---------- Assessment (one-at-a-time quiz, MagMutual style) ---------- */
.qa-wrap-single { margin-top: 1.4rem; }
.qa-cards { position: relative; min-height: 320px; }

/* Hide all cards by default; show only the active one. */
.qa-card {
  display: none;
  border: 1px solid var(--line);
  background: var(--bg);
  padding: 1.8rem 2rem;
  border-radius: 4px;
  animation: qaFade 0.25s ease;
}
.qa-card.qa-card--active { display: block; }
@keyframes qaFade {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: translateY(0); }
}

.qa-progress {
  font-size: 0.75rem;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--text-muted);
  margin-bottom: 1.4rem;
  font-weight: 700;
}

.qa-pct {
  height: 4px;
  background: var(--line);
  margin: 1rem 0 1.4rem;
  position: relative;
  overflow: hidden;
  border-radius: 2px;
}
.qa-pct-fill {
  position: absolute;
  inset: 0 100% 0 0;
  background: var(--accent);
  transition: inset 0.5s ease;
}
.qa-pct-fill.s-pass { background: #43a047; }
.qa-pct-fill.s-fail { background: #d0021b; }
.qa-reset {
  background: transparent;
  border: 1px solid var(--line);
  padding: 0.6rem 1.4rem;
  font-size: 0.9rem;
  font-weight: 700;
  cursor: pointer;
  font-family: inherit;
  color: var(--text);
  transition: all 0.15s ease;
  border-radius: 4px;
}
.qa-reset:hover { border-color: var(--accent); color: var(--accent); }

.qa-card--summary { text-align: center; padding: 2.4rem 2rem; }
.qa-card--summary .qa-num { font-size: 1.4rem; margin-bottom: 0.8rem; }
.qa-card--summary .qa-stem { font-size: 1.05rem; margin-bottom: 1rem; }
.qa-card--summary .qa-correct { font-weight: 900; color: var(--text-strong); }
.qa-pass-msg {
  font-size: 0.9rem;
  margin: 0.5rem 0 1.4rem;
  color: var(--text-muted);
  font-weight: 700;
}
.qa-pass-msg[data-passed="true"] { color: #2c6f30; }
.qa-pass-msg[data-passed="false"]:not(:empty) { color: #8e0414; }
.qa-head {
  display: flex;
  align-items: baseline;
  gap: 0.8rem;
  margin-bottom: 0.7rem;
  flex-wrap: wrap;
}
.qa-num { font-size: 1.05rem; font-weight: 900; margin: 0; color: var(--text-strong); }
.qa-pill {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  background: var(--bg-soft);
  color: var(--text-muted);
  padding: 0.2rem 0.6rem;
  border-radius: 999px;
  font-weight: 700;
}
.qa-meta { font-size: 0.82rem; color: var(--text-muted); }
.qa-num { font-size: 1.4rem; font-weight: 900; margin: 0; color: var(--text-strong); line-height: 1.2; }
.qa-stem { font-size: 1.1rem; line-height: 1.55; margin: 1rem 0 1.6rem; color: var(--text-strong); font-weight: 400; }
.qa-opts { display: flex; flex-direction: column; gap: 0.6rem; margin-bottom: 1.4rem; }
.qa-opt {
  display: flex;
  align-items: flex-start;
  gap: 0.7rem;
  border: 1px solid var(--line);
  padding: 0.75rem 1rem;
  cursor: pointer;
  transition: all 0.12s ease;
  background: var(--bg);
  border-radius: 4px;
}
.qa-opt:hover { border-color: var(--accent); background: var(--bg-soft); }
.qa-opt input { margin: 0.25rem 0 0 0; accent-color: var(--accent); }
.qa-opt.selected { border-color: var(--accent); background: var(--bg-soft); }
.qa-opt.correct { border-color: #43a047; background: #e9f5ea; }
.qa-opt.incorrect { border-color: #d0021b; background: #fbeaec; }
.qa-letter {
  font-weight: 900;
  color: var(--text-muted);
  min-width: 20px;
  text-align: center;
  font-size: 0.95rem;
}
.qa-text { flex: 1; font-size: 0.95rem; line-height: 1.5; }
.qa-actions { display: flex; gap: 0.7rem; align-items: center; }
.qa-submit, .qa-next {
  padding: 0.7rem 1.6rem;
  font-family: inherit;
  font-size: 0.95rem;
  font-weight: 700;
  cursor: pointer;
  transition: all 0.15s ease;
  border-radius: 4px;
}
.qa-submit {
  background: var(--text-strong);
  color: var(--bg);
  border: 1px solid var(--text-strong);
}
.qa-submit:hover:not(:disabled) {
  background: var(--accent);
  border-color: var(--accent);
}
.qa-submit:disabled {
  background: var(--bg-soft);
  color: var(--text-muted);
  border-color: var(--line);
  cursor: not-allowed;
}
.qa-next {
  background: transparent;
  color: var(--text-strong);
  border: 1px solid var(--line);
}
.qa-next:hover:not(:disabled) {
  border-color: var(--accent);
  color: var(--accent);
}
.qa-next:disabled {
  color: var(--text-muted);
  border-color: var(--line);
  cursor: not-allowed;
  opacity: 0.5;
}
.qa-feedback {
  margin-top: 1rem;
  padding: 0.85rem 1rem;
  font-size: 0.92rem;
  display: none;
  border-left: 3px solid var(--line);
  background: var(--bg-soft);
  border-radius: 0 4px 4px 0;
  line-height: 1.55;
}
.qa-feedback.show { display: block; }
.qa-feedback.fb-correct { border-left-color: #43a047; background: #e9f5ea; }
.qa-feedback.fb-incorrect { border-left-color: #d0021b; background: #fbeaec; }
.qa-feedback-label {
  font-size: 0.72rem;
  text-transform: uppercase;
  letter-spacing: 0.14em;
  margin-bottom: 0.4rem;
  font-weight: 700;
}
.qa-feedback.fb-correct .qa-feedback-label { color: #2c6f30; }
.qa-feedback.fb-incorrect .qa-feedback-label { color: #8e0414; }

code {
  font-family: 'Lato', monospace;
  background: var(--bg-soft);
  padding: 0.05rem 0.4rem;
  border-radius: 3px;
  font-size: 0.94em;
}

.course-foot {
  margin-top: 4rem;
  padding-top: 1.4rem;
  border-top: 1px solid var(--line);
  font-size: 0.85rem;
  color: var(--text-muted);
}
"""

_JS = r"""
(function(){
  /* MagMutual-style one-at-a-time quiz:
     1. User sees one question at a time with NN / NN progress
     2. Picks answer, clicks Submit → gets feedback (correct / not quite + rationale)
     3. Clicks Next/Finish → advances to next question or summary
     4. Final summary shows score + pass/fail vs 80% threshold + Restart
  */
  function setupQA(){
    var cards = document.querySelectorAll('.qa-card');
    if (cards.length === 0) return;
    // The last card is the summary; question cards are everything else
    var qcards = Array.prototype.slice.call(cards).filter(function(c){
      return !c.classList.contains('qa-card--summary');
    });
    var summary = document.querySelector('.qa-card--summary');
    var total = qcards.length;
    var answered = {};
    var currentIdx = 0;

    function showCard(idx) {
      cards.forEach(function(c){ c.classList.remove('qa-card--active'); });
      if (idx < total) {
        qcards[idx].classList.add('qa-card--active');
        currentIdx = idx;
      } else {
        // Show summary
        summary && summary.classList.add('qa-card--active');
        renderSummary();
      }
    }

    function renderSummary() {
      if (!summary) return;
      var correctEl = summary.querySelector('.qa-correct');
      var totalEl = summary.querySelector('.qa-total');
      var pctFill = summary.querySelector('.qa-pct-fill');
      var passMsg = summary.querySelector('.qa-pass-msg');
      var n = Object.keys(answered).filter(function(k){ return answered[k]; }).length;
      var pct = total ? (n / total * 100) : 0;
      if (correctEl) correctEl.textContent = n;
      if (totalEl) totalEl.textContent = total;
      if (pctFill) {
        pctFill.style.inset = '0 ' + (100 - pct) + '% 0 0';
        pctFill.classList.toggle('s-pass', pct >= 80);
        pctFill.classList.toggle('s-fail', pct < 80);
      }
      if (passMsg) {
        var passed = pct >= 80;
        passMsg.setAttribute('data-passed', String(passed));
        passMsg.textContent = passed
          ? 'Passed (' + Math.round(pct) + '%) — you cleared the 80% threshold.'
          : 'Did not pass (' + Math.round(pct) + '%). 80% is the passing threshold.';
      }
    }

    qcards.forEach(function(card, idx){
      var correct = parseInt(card.getAttribute('data-correct'), 10) || 0;
      var rationale = card.getAttribute('data-rationale') || '';
      var radios = card.querySelectorAll('input[type=radio]');
      var opts = card.querySelectorAll('.qa-opt');
      var submit = card.querySelector('.qa-submit');
      var nextBtn = card.querySelector('.qa-next');
      var feedback = card.querySelector('.qa-feedback');

      // Submit starts disabled until an answer is picked
      submit.disabled = true;

      opts.forEach(function(opt){
        opt.addEventListener('click', function(){
          if (submit.disabled === false && submit.dataset.answered === '1') return;
          opts.forEach(function(o){ o.classList.remove('selected'); });
          opt.classList.add('selected');
          var inp = opt.querySelector('input');
          if (inp) inp.checked = true;
          submit.disabled = false;
        });
      });

      submit.addEventListener('click', function(){
        var picked = card.querySelector('input[type=radio]:checked');
        if (!picked) return;
        var pickedIdx = parseInt(picked.value, 10);
        opts.forEach(function(o, i){
          o.classList.remove('correct', 'incorrect');
          if (i === correct) o.classList.add('correct');
          else if (i === pickedIdx) o.classList.add('incorrect');
        });
        var isCorrect = pickedIdx === correct;
        feedback.className = 'qa-feedback show ' + (isCorrect ? 'fb-correct' : 'fb-incorrect');
        feedback.innerHTML = '<div class="qa-feedback-label">' +
          (isCorrect ? 'Correct' : 'Not quite') + '</div>' +
          (rationale ? rationale : '');
        radios.forEach(function(r){ r.disabled = true; });
        submit.disabled = true;
        submit.dataset.answered = '1';
        nextBtn.disabled = false;
        answered[idx] = isCorrect;
      });

      nextBtn.addEventListener('click', function(){
        showCard(idx + 1);
      });
    });

    // Reset
    var resetBtn = summary && summary.querySelector('.qa-reset');
    if (resetBtn) {
      resetBtn.addEventListener('click', function(){
        qcards.forEach(function(card){
          card.querySelectorAll('input[type=radio]').forEach(function(r){
            r.disabled = false; r.checked = false;
          });
          card.querySelectorAll('.qa-opt').forEach(function(o){
            o.classList.remove('selected', 'correct', 'incorrect');
          });
          var fb = card.querySelector('.qa-feedback');
          if (fb) { fb.className = 'qa-feedback'; fb.innerHTML = ''; }
          var sb = card.querySelector('.qa-submit');
          if (sb) { sb.disabled = true; delete sb.dataset.answered; }
          var nb = card.querySelector('.qa-next');
          if (nb) nb.disabled = true;
        });
        answered = {};
        showCard(0);
      });
    }
  }

  // ---------- TOC anchor interception ----
  // Inside Streamlit's components.html iframe, a plain <a href='#...'>
  // click can cause the iframe's window to commit a navigation, which
  // makes Streamlit's parent route as a "back" (the user lands back at
  // the splash). Capture the click, scroll smoothly to the target
  // ourselves, and call preventDefault so no navigation happens.
  function setupTocLinks() {
    document.querySelectorAll("a[href^='#']").forEach(function(a){
      a.addEventListener('click', function(e){
        var target = a.getAttribute('href') || '';
        if (target.length < 2) return;
        var el = document.querySelector(target);
        if (!el) return;
        e.preventDefault();
        el.scrollIntoView({behavior: 'smooth', block: 'start'});
        // Visually flag the destination so the user sees they landed
        el.classList.add('toc-flash');
        setTimeout(function(){ el.classList.remove('toc-flash'); }, 1200);
      });
    });
  }

  // ---------- Strategy tab control (REDUCING CLINICAL / NON-CLINICAL) ----
  function setupStratTabs() {
    document.querySelectorAll('.strat-tabs').forEach(function(wrap){
      var btns = wrap.querySelectorAll('.strat-tabbtn');
      var panels = wrap.querySelectorAll('.strat-panel');
      btns.forEach(function(b){
        b.addEventListener('click', function(){
          var key = b.getAttribute('data-panel');
          btns.forEach(function(x){ x.classList.toggle('is-active', x === b); });
          panels.forEach(function(p){
            p.classList.toggle('is-active', p.getAttribute('data-panel') === key);
          });
        });
      });
    });
  }

  // ---------- Definition flip cards ----
  function setupDefCards() {
    document.querySelectorAll('.def-card').forEach(function(card){
      function toggle(){ card.classList.toggle('def-card-flipped'); }
      card.addEventListener('click', toggle);
      card.addEventListener('keydown', function(e){
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          toggle();
        }
      });
    });
  }

  // ---------- Reading-progress + TOC completion (Articulate Rise pattern) ----
  // Top-of-page progress bar fills as the user scrolls, and each TOC
  // entry is marked done once the corresponding section has scrolled
  // above the viewport mid-line.
  function setupProgress() {
    var tocItems = document.querySelectorAll('.toc li');
    var fill = document.querySelector('.reading-progress-fill');
    var targets = [];
    tocItems.forEach(function(li){
      var a = li.querySelector("a[href^='#']");
      if (!a) return;
      var id = (a.getAttribute('href') || '').slice(1);
      var sec = id ? document.getElementById(id) : null;
      if (!sec) {
        var hashed = document.querySelector("[id='" + id + "']");
        if (hashed) sec = hashed.closest('section') || hashed;
      }
      if (sec) targets.push({ tocItem: li, sec: sec });
    });

    function onScroll() {
      // Reading-progress bar
      if (fill) {
        var docH = document.documentElement.scrollHeight - window.innerHeight;
        var pct = docH > 0 ? Math.min(100, Math.max(0, window.scrollY * 100 / docH)) : 0;
        fill.style.width = pct.toFixed(2) + '%';
      }
      // TOC completion checkmarks
      if (!targets.length) return;
      var doneLine = window.innerHeight * 0.5;
      var doneCount = 0;
      for (var i = 0; i < targets.length; i++) {
        var b = targets[i].sec.getBoundingClientRect().bottom;
        if (b < doneLine) doneCount++;
      }
      if ((window.innerHeight + window.scrollY) >= (document.body.scrollHeight - 8)) {
        doneCount = targets.length;
      }
      targets.forEach(function(t, i){
        t.tocItem.classList.toggle('is-done', i < doneCount);
      });
    }

    var ticking = false;
    window.addEventListener('scroll', function(){
      if (!ticking) {
        window.requestAnimationFrame(function(){ onScroll(); ticking = false; });
        ticking = true;
      }
    }, { passive: true });
    window.addEventListener('resize', onScroll, { passive: true });
    onScroll();
  }

  function bootAll() {
    setupQA();
    setupStratTabs();
    setupDefCards();
    setupTocLinks();
    setupProgress();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bootAll);
  } else {
    bootAll();
  }
})();
"""
