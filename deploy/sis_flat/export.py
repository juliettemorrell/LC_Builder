"""PDF + markdown export.

PDF rendering targets the MagMutual "Reducing Liability" Articulate Rise
visual style: big cover, eyebrow + title hierarchy on each lesson, page
break per lesson, definition flashcards, two-column strategy tabs,
yellow Pause-and-Reflect callouts, page numbers in the footer, and an
answer key page after the assessment.

Fonts/colors mirror `course_preview.py` exactly so the PDF and the HTML
preview look like the same artefact in different shells.
"""
from __future__ import annotations

import os
import re
from io import BytesIO

# ---------------------------------------------------------------------------
# Bundled Lato fonts (matches CSS body font in course_preview.py).
# Registered exactly once, lazily, so importing this module is cheap.
# ---------------------------------------------------------------------------
_FONTS_DIR = os.path.dirname(os.path.abspath(__file__))
_LATO_REGISTERED = False
_LATO_AVAILABLE = False


def _ensure_fonts_registered() -> bool:
    """Register Lato (Regular/Bold/Italic/BoldItalic/Black) once.

    Returns True if Lato is available (so styles can use it), False if
    the TTFs aren't on disk and we should fall back to Helvetica.
    """
    global _LATO_REGISTERED, _LATO_AVAILABLE
    if _LATO_REGISTERED:
        return _LATO_AVAILABLE
    _LATO_REGISTERED = True
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.lib.fonts import addMapping
    except ImportError:
        return False
    faces = {
        "Lato-Regular": "Lato-Regular.ttf",
        "Lato-Bold": "Lato-Bold.ttf",
        "Lato-Italic": "Lato-Italic.ttf",
        "Lato-BoldItalic": "Lato-BoldItalic.ttf",
        "Lato-Black": "Lato-Black.ttf",
    }
    for face, fn in faces.items():
        path = os.path.join(_FONTS_DIR, fn)
        if not os.path.isfile(path):
            return False
        pdfmetrics.registerFont(TTFont(face, path))
    # Tell reportlab how to swap weights for inline <b>/<i> in Paragraphs.
    # ps2tt() (used by paraparser when it sees inline tags) looks up the
    # family by way of addMapping; without this the parser raises
    # "Can't map determine family/bold/italic for lato".
    addMapping("Lato", 0, 0, "Lato-Regular")
    addMapping("Lato", 1, 0, "Lato-Bold")
    addMapping("Lato", 0, 1, "Lato-Italic")
    addMapping("Lato", 1, 1, "Lato-BoldItalic")
    # Heading family — when a style uses fontName="Lato-Black", inline
    # <b> stays Black; <i> swaps to BoldItalic to stay close in weight.
    addMapping("Lato-Black", 0, 0, "Lato-Black")
    addMapping("Lato-Black", 1, 0, "Lato-Black")
    addMapping("Lato-Black", 0, 1, "Lato-BoldItalic")
    addMapping("Lato-Black", 1, 1, "Lato-BoldItalic")
    # Bold family — when a style uses fontName="Lato-Bold" (e.g. h3),
    # inline <b> stays Bold and <i> swaps to BoldItalic.
    addMapping("Lato-Bold", 0, 0, "Lato-Bold")
    addMapping("Lato-Bold", 1, 0, "Lato-Bold")
    addMapping("Lato-Bold", 0, 1, "Lato-BoldItalic")
    addMapping("Lato-Bold", 1, 1, "Lato-BoldItalic")
    # Italic family — primarily used for the reflect-body italic style.
    addMapping("Lato-Italic", 0, 0, "Lato-Italic")
    addMapping("Lato-Italic", 1, 0, "Lato-BoldItalic")
    addMapping("Lato-Italic", 0, 1, "Lato-Italic")
    addMapping("Lato-Italic", 1, 1, "Lato-BoldItalic")
    _LATO_AVAILABLE = True
    return True


# ---------------------------------------------------------------------------
# Markdown shortcut — used for raw markdown export
# ---------------------------------------------------------------------------
def to_markdown(title: str, sections: dict[str, str]) -> str:
    parts = [f"# {title}", ""]
    for name, body in sections.items():
        parts.append(f"## {name}")
        parts.append("")
        parts.append(body or "")
        parts.append("")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
def to_pdf_bytes(title: str, sections: dict[str, str]) -> bytes:
    """Render the course as a styled PDF.

    `sections` is an ordered dict of section_label -> markdown content.
    The first section is the course body (with multiple "## Lesson N of 5"
    headings — those each get their own page); subsequent sections map to
    one page (Assessment, Closing, per-topic case studies).
    """
    try:
        from reportlab.lib.pagesizes import LETTER
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.lib.enums import TA_LEFT, TA_CENTER
        from reportlab.platypus import (
            BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer,
            ListFlowable, ListItem, PageBreak, Table, TableStyle,
            KeepTogether,
        )
        from reportlab.lib.colors import HexColor
    except ImportError:
        return to_markdown(title, sections).encode("utf-8")

    # ---------------- Font registration -------------------------------------
    has_lato = _ensure_fonts_registered()
    # Map our semantic font roles onto either Lato or Helvetica fallback.
    # We use the registered PS names ("Lato-Regular", etc.) as fontName so
    # reportlab's paraparser can resolve them via _PS2TTfontMap and run the
    # inline <b>/<i> swap through the addMapping table.
    if has_lato:
        FONT_BODY = "Lato-Regular"       # body / italic-aware paragraphs
        FONT_BOLD = "Lato-Bold"           # eyebrows, h3, strong meta
        FONT_BLACK = "Lato-Black"         # display headings (h1, h2, cover)
        FONT_ITALIC = "Lato-Italic"
    else:
        FONT_BODY = "Helvetica"
        FONT_BOLD = "Helvetica-Bold"
        FONT_BLACK = "Helvetica-Bold"
        FONT_ITALIC = "Helvetica-Oblique"

    # ---------------- Color tokens (match course_preview.py CSS exactly) -----
    # See course_preview.py :root for the canonical palette.
    ACCENT = HexColor("#0f62fe")           # --accent
    TEXT_STRONG = HexColor("#161616")      # --text-strong (display headings)
    TEXT_BODY = HexColor("#303030")        # --text          (body copy)
    TEXT_MUTED = HexColor("#707070")       # --text-muted    (eyebrows, meta)
    LINE = HexColor("#eaeaeb")             # --line          (rules + borders)
    BG_SOFT = HexColor("#f7f7f7")          # --bg-soft       (soft fill)
    BG_SOFTER = HexColor("#fafafa")        # --bg-softer
    REFLECT_BG = HexColor("#fcf4d6")       # --reflect-bg
    REFLECT_BD = HexColor("#f1c21b")       # --reflect-bd
    REFLECT_TXT = HexColor("#4a3a00")      # --reflect-text
    GREEN_50 = HexColor("#43a047")
    GREEN_BG = HexColor("#e9f5ea")         # match .qa-opt.correct
    RED_50 = HexColor("#d0021b")
    WHITE = HexColor("#ffffff")
    # Aliases preserved so the older helpers below don't need renaming.
    GRAY_100 = TEXT_STRONG
    GRAY_90 = TEXT_BODY
    GRAY_70 = TEXT_MUTED
    GRAY_20 = LINE
    GRAY_10 = BG_SOFT
    GRAY_05 = BG_SOFTER
    YELLOW_30 = REFLECT_BD

    # ---------------- Page geometry + footer with page numbers --------------
    page_w, page_h = LETTER
    left_m, right_m, top_m, bottom_m = 0.85 * inch, 0.85 * inch, 0.95 * inch, 0.95 * inch

    def _header_footer(canvas, doc):
        canvas.saveState()
        # Footer: page number + brand mark, in body font
        canvas.setFont(FONT_BODY, 8)
        canvas.setFillColor(TEXT_MUTED)
        canvas.drawString(left_m, 0.55 * inch,
                           "MyAdvice Builder · MagMutual format")
        page = canvas.getPageNumber()
        canvas.drawRightString(page_w - right_m, 0.55 * inch, f"{page}")
        # Footer rule
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.5)
        canvas.line(left_m, 0.75 * inch, page_w - right_m, 0.75 * inch)
        canvas.restoreState()

    buf = BytesIO()
    doc = BaseDocTemplate(
        buf, pagesize=LETTER,
        leftMargin=left_m, rightMargin=right_m,
        topMargin=top_m, bottomMargin=bottom_m,
        title=title, author="MyAdvice Builder",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin,
                   doc.width, doc.height, id="body")
    doc.addPageTemplates([
        PageTemplate(id="default", frames=frame, onPage=_header_footer)
    ])

    # ---------------- Paragraph styles --------------------------------------
    # Sizing follows the CSS in course_preview.py (17px base, 1.6 line-height
    # for body). Reportlab points ≈ CSS px at 1:1 for our purposes.
    cover_eyebrow = ParagraphStyle(
        "CoverEyebrow", fontName=FONT_BOLD, fontSize=10,
        textColor=ACCENT, leading=13, alignment=TA_LEFT,
    )
    cover_title = ParagraphStyle(
        # Lato Black at the cover scale matches the .cover h1 weight: 900
        "CoverTitle", fontName=FONT_BLACK, fontSize=34,
        textColor=TEXT_STRONG, leading=40, spaceAfter=14, alignment=TA_LEFT,
    )
    cover_sub = ParagraphStyle(
        "CoverSub", fontName=FONT_BODY, fontSize=12,
        textColor=TEXT_MUTED, leading=18, spaceAfter=22,
    )
    toc_eyebrow = ParagraphStyle(
        "TocEyebrow", fontName=FONT_BOLD, fontSize=9,
        textColor=TEXT_MUTED, leading=11, spaceAfter=8,
    )
    toc_item = ParagraphStyle(
        "TocItem", fontName=FONT_BODY, fontSize=11,
        textColor=TEXT_STRONG, spaceAfter=5,
    )
    section_eyebrow = ParagraphStyle(
        # Matches the MagMutual reference exactly: italic, light gray,
        # mixed case ("Lesson 3 of 5" — not "LESSON 3 OF 5"). Quietly sets
        # the page; the lesson title carries the visual weight.
        "SectionEyebrow", fontName=FONT_ITALIC, fontSize=10,
        textColor=TEXT_MUTED, leading=13, spaceAfter=6,
    )
    h1 = ParagraphStyle(
        # MagMutual reference uses an oversized lesson title (~32pt).
        # Lato Black at 30pt with 1.05 leading reads close to the reference.
        "H1", fontName=FONT_BLACK, fontSize=30, leading=34,
        textColor=TEXT_STRONG, spaceBefore=4, spaceAfter=10,
    )
    h2 = ParagraphStyle(
        # Matches .lesson-body h2: 1.4rem (≈22px), font-weight 900
        "H2", fontName=FONT_BLACK, fontSize=16, leading=21,
        textColor=TEXT_STRONG, spaceBefore=20, spaceAfter=8,
    )
    h3 = ParagraphStyle(
        # Matches .lesson-body h3: 1.15rem, font-weight 700
        "H3", fontName=FONT_BOLD, fontSize=12.5, leading=16,
        textColor=TEXT_STRONG, spaceBefore=12, spaceAfter=4,
    )
    h4 = ParagraphStyle(
        # Matches .lesson-body h4: 0.78rem, uppercase, 0.12em letter-spacing
        "H4", fontName=FONT_BOLD, fontSize=9, leading=12,
        textColor=TEXT_MUTED, spaceBefore=10, spaceAfter=3,
    )
    body = ParagraphStyle(
        # Matches .lesson-body p: 1rem, line-height 1.65, var(--text)
        "Body", fontName=FONT_BODY, fontSize=11, leading=17,
        textColor=TEXT_BODY, spaceAfter=8,
    )
    bullet_s = ParagraphStyle(
        "Bullet", parent=body, leftIndent=14, bulletIndent=4, spaceAfter=3,
    )
    code_s = ParagraphStyle(
        # CSS uses Lato for code inline; reportlab renders it as a styled span
        "Code", parent=body, fontName="Courier", fontSize=9.5, leading=13,
        backColor=BG_SOFT, leftIndent=4, rightIndent=4,
    )
    # MagMutual reference renders "Pause and reflect" as a dark gray
    # full-width banner with white text — NOT a yellow callout. Override
    # the previous yellow styling.
    reflect_label = ParagraphStyle(
        "ReflectLabel", fontName=FONT_BLACK, fontSize=18,
        textColor=WHITE, leading=22, spaceAfter=8,
    )
    reflect_body = ParagraphStyle(
        "ReflectBody", parent=body, fontName=FONT_BODY,
        textColor=WHITE, leading=18,
    )
    def_term = ParagraphStyle(
        # Matches .def-term: weight 900
        "DefTerm", fontName=FONT_BLACK, fontSize=11,
        textColor=TEXT_STRONG, leading=14, spaceAfter=3,
    )
    def_def = ParagraphStyle(
        "DefText", fontName=FONT_BODY, fontSize=9.5, leading=13.5,
        textColor=TEXT_BODY,
    )
    strat_label = ParagraphStyle(
        # Matches .strat-label (uppercase, muted)
        "StratLabel", fontName=FONT_BOLD, fontSize=8.5,
        textColor=TEXT_MUTED, leading=11, spaceAfter=4,
    )
    strat_item = ParagraphStyle(
        # Matches .strat-tab li
        "StratItem", fontName=FONT_BODY, fontSize=10, leading=14,
        textColor=TEXT_BODY, leftIndent=10, bulletIndent=2,
    )
    answer_key_h = ParagraphStyle(
        "AKHeading", fontName=FONT_BLACK, fontSize=18, leading=22,
        textColor=TEXT_STRONG, spaceAfter=12,
    )
    answer_key_item = ParagraphStyle(
        "AKItem", fontName=FONT_BODY, fontSize=11, leading=15,
        textColor=TEXT_BODY, spaceAfter=3,
    )

    story = []

    # ---------------- Cover page --------------------------------------------
    story.append(Spacer(1, 0.3 * inch))
    # Brand bar (colored rule)
    story.append(_color_rule(ACCENT, page_w - left_m - right_m, 4))
    story.append(Spacer(1, 0.2 * inch))
    story.append(Paragraph("MAGMUTUAL · RISK MANAGEMENT", cover_eyebrow))
    story.append(Spacer(1, 0.3 * inch))
    story.append(Paragraph(_inline(title), cover_title))
    story.append(Paragraph(
        "Reducing liability through evidence-based education.<br/>"
        "Generated by MyAdvice Builder.", cover_sub))
    story.append(Spacer(1, 0.4 * inch))
    story.append(Paragraph("COURSE OVERVIEW", toc_eyebrow))
    # Build TOC from real H2 lessons + section labels
    toc_entries = _build_toc(sections)
    for n, label in toc_entries:
        story.append(Paragraph(f"<b>{n}.</b> &nbsp; {_inline(label)}", toc_item))
    story.append(Spacer(1, 0.6 * inch))
    # Time estimate disclosure (CME requirement) — clamp credits to
    # 0.25 minimum so the cover never reads "~0.0 credit-hour-equivalent".
    minutes = _estimate_minutes(sections)
    credits = max(0.25, round(minutes / 60.0 * 4) / 4)
    story.append(Paragraph(
        f"<font color='#707070' size='9'>Estimated time to complete: "
        f"≈ {minutes} minutes &nbsp;·&nbsp; ~{credits} credit-hour-equivalent</font>",
        body))
    story.append(Paragraph(
        "<font color='#9a9a9a' size='8'>This is content sized for ~60-min "
        "engagement. Final CME credit hours are designated by an "
        "accredited provider after pilot testing.</font>", body))
    story.append(PageBreak())

    # ---------------- Body pages --------------------------------------------
    for idx, (name, content) in enumerate(sections.items()):
        # Special-case the Assessment section: parse the HTML5 questions and
        # render each as its own page. The helper guards against false
        # positives like embedded case studies titled "Single-point
        # assessment vs. serial evaluation".
        if _is_assessment_section(name, content or ""):
            _render_assessment_pages(
                content or "", story, section_eyebrow, h1, h2, h3, body,
                Paragraph=Paragraph, Spacer=Spacer, PageBreak=PageBreak,
                color_rule=_color_rule, ACCENT=ACCENT, GRAY_100=GRAY_100,
                GRAY_70=GRAY_70, GRAY_20=GRAY_20, GRAY_10=GRAY_10,
                GREEN_50=GREEN_50, total_w=page_w - left_m - right_m,
                inch=inch,
            )
            if idx < len(sections) - 1:
                story.append(PageBreak())
            continue

        blocks = list(_parse_blocks(content or ""))

        # The course title H1 is already the cover headline — drop the
        # leading H1 from body content so we don't waste a page on it.
        if blocks and blocks[0][0] == "h1":
            blocks = blocks[1:]

        # Now decide whether the first remaining block is a Lesson H2
        # (then the lesson eyebrow takes over, no need for a section
        # eyebrow + accent rule). Otherwise show the section eyebrow.
        first_kind, first_payload = (blocks[0] if blocks else (None, None))
        first_is_lesson = (first_kind == "h2"
                            and re.match(r"Lesson\s+\d+\s+of\s+5",
                                          first_payload or "", re.I))
        if not first_is_lesson:
            # Mixed-case italic eyebrow + short gray rule, matching the
            # MagMutual reference (not uppercase blue + full-width accent).
            story.append(Paragraph(name, section_eyebrow))
            story.append(_color_rule(LINE, 1.6 * inch, 1.5))
            story.append(Spacer(1, 0.18 * inch))

        # Walk the markdown blocks
        i = 0
        while i < len(blocks):
            kind, payload = blocks[i]

            # === Lesson H2 markers force a page break + visual eyebrow ===
            # Suppress the break on the FIRST block of a section: either
            # the cover already broke us (idx==0) or the previous section
            # ended with a PageBreak (idx>0, i==0). Adding another break
            # there would leave a blank page.
            if kind == "h2" and re.match(r"Lesson\s+\d+\s+of\s+5", payload, re.I):
                if i != 0:
                    story.append(PageBreak())
                m = re.match(r"(Lesson\s+\d+\s+of\s+5)\s*[:·\-]?\s*(.*)$",
                              payload, re.I)
                if m:
                    # MagMutual reference uses MIXED CASE in italic gray,
                    # not uppercase blue. Match that exactly.
                    eyebrow = m.group(1)
                    title_only = m.group(2).strip()
                    story.append(Paragraph(eyebrow, section_eyebrow))
                    story.append(Paragraph(_inline(title_only or eyebrow), h1))
                else:
                    story.append(Paragraph(_inline(payload), h1))
                # Short decorative rule under the title (matches the small
                # ~2-inch gray line in the MM reference, not a full-width
                # accent bar).
                story.append(_color_rule(LINE, 1.6 * inch, 1.5))
                story.append(Spacer(1, 0.18 * inch))
                i += 1
                continue

            # === Case study (H3 "Case study N" — or legacy "Key loss
            # driver:" pattern for back-compat with old drafts) ===
            # Mirror the HTML preview: render the entire embedded lesson
            # as a stack of bordered cards with thin vertical connectors,
            # matching the MagMutual reference. Consumes blocks until the
            # next H3 / hr / end.
            if kind == "h3" and re.match(
                    r"^(?:Case study|Key loss driver\s*:)", payload, re.I):
                consumed = _render_pdf_case_study(
                    blocks, i, story,
                    h1=h1, h3=h3, h4=h4, body=body, bullet_s=bullet_s,
                    reflect_label=reflect_label, reflect_body=reflect_body,
                    strat_label=strat_label, strat_item=strat_item,
                    accent=ACCENT, line=LINE, bg_soft=BG_SOFT,
                    text_muted=TEXT_MUTED, total_w=page_w - left_m - right_m,
                    inch=inch,
                )
                i += consumed
                continue

            # === Definition flashcards (look-ahead: H3 "Definition of Key Terms"
            #     followed by a bullet list of "**Term** — definition") ===
            if (kind == "h3"
                and re.match(r"definition of key terms?$", payload, re.I)
                and i + 1 < len(blocks) and blocks[i + 1][0] == "bullets"):
                story.append(Paragraph(_inline(payload), h3))
                cards = _parse_definition_bullets(blocks[i + 1][1])
                if cards:
                    story.append(_definition_grid(cards, def_term, def_def,
                                                    GRAY_20, GRAY_05,
                                                    page_w - left_m - right_m))
                else:
                    story.append(_render_bullets(blocks[i + 1][1],
                                                  bullet_s, ACCENT))
                i += 2
                continue

            # === Strategy tabs: "Reducing clinical risks" + bullets, often
            #     followed by "Reducing non-clinical risks" + bullets ===
            if (kind == "h4"
                and re.match(r"reducing clinical risks?$", payload, re.I)
                and i + 1 < len(blocks) and blocks[i + 1][0] == "bullets"):
                clinical_items = blocks[i + 1][1]
                non_clinical_items = []
                consumed = 2
                if (i + 3 < len(blocks)
                    and blocks[i + 2][0] == "h4"
                    and re.match(r"reducing non-clinical risks?$",
                                  blocks[i + 2][1], re.I)
                    and blocks[i + 3][0] == "bullets"):
                    non_clinical_items = blocks[i + 3][1]
                    consumed = 4
                story.append(_strategy_tabs(
                    clinical_items, non_clinical_items,
                    strat_label, strat_item, ACCENT, GRAY_70,
                    GRAY_20, GRAY_05, page_w - left_m - right_m))
                i += consumed
                continue

            # === Pause and Reflect callout ===
            if kind == "reflect":
                story.append(_reflect_callout(
                    payload, reflect_label, reflect_body,
                    REFLECT_BG, YELLOW_30, page_w - left_m - right_m))
                i += 1
                continue

            # === Standard rendering ===
            if kind == "h1":
                story.append(Paragraph(_inline(payload), h1))
            elif kind == "h2":
                story.append(Paragraph(_inline(payload), h2))
            elif kind == "h3":
                story.append(Paragraph(_inline(payload), h3))
            elif kind == "h4":
                story.append(Paragraph(_inline(payload).upper(), h4))
            elif kind == "bullets":
                story.append(_render_bullets(payload, bullet_s, ACCENT))
            elif kind == "numbered":
                story.append(_render_numbered(payload, bullet_s, GRAY_70))
            elif kind == "code":
                story.append(Paragraph(_escape(payload).replace("\n", "<br/>"),
                                         code_s))
            elif kind == "hr":
                story.append(_color_rule(GRAY_20,
                                           page_w - left_m - right_m, 0.5))
                story.append(Spacer(1, 0.08 * inch))
            elif kind == "para":
                story.append(Paragraph(_inline(payload), body))
            i += 1

        # Page break after each top-level section (each Lesson section gets
        # its own start). Skip after the last one.
        if idx < len(sections) - 1:
            story.append(PageBreak())

    # ---------------- Answer Key (if there's an Assessment section) ---------
    answer_key = _extract_answer_key(sections)
    if answer_key:
        story.append(PageBreak())
        story.append(Paragraph("Assessment · Answer key", section_eyebrow))
        story.append(_color_rule(LINE, 1.6 * inch, 1.5))
        story.append(Spacer(1, 0.18 * inch))
        story.append(Paragraph("Answer Key", answer_key_h))
        for n, letter, snippet in answer_key:
            story.append(Paragraph(
                f"<b>{n}.</b> &nbsp; <font color='#0f62fe'>"
                f"<b>{letter}</b></font> &nbsp;&nbsp; "
                f"<font color='#707070' size='10'>{_inline(snippet)}</font>",
                answer_key_item))

    doc.build(story)
    buf.seek(0)
    return buf.read()


# ---------------------------------------------------------------------------
# Visual building blocks
# ---------------------------------------------------------------------------
def _color_rule(color, width, height, *, h_align: str = "LEFT"):
    """A horizontal colored rule used as a section divider / brand bar.

    Defaults to LEFT alignment (Table's default would center narrow rules,
    which doesn't match the MM reference).
    """
    from reportlab.platypus import Table, TableStyle
    t = Table([[""]], colWidths=[width], rowHeights=[height], hAlign=h_align)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), color)]))
    return t


def _render_bullets(items, bullet_s, color):
    from reportlab.platypus import ListFlowable, ListItem, Paragraph
    list_items = [
        ListItem(Paragraph(_inline(b), bullet_s), bulletColor=color)
        for b in items
    ]
    return ListFlowable(list_items, bulletType="bullet",
                        start="•", leftIndent=14)


def _render_numbered(items, bullet_s, color):
    """Numbered list as a 2-column Table with gray-circle bullets.

    The MagMutual reference renders objectives as filled gray circles
    with the number inside (not just plain "1." text). We approximate
    that with a Table where the left column is a single-cell Table
    sized like a circle (radius ≈ 9pt) with the digit centered. Cells
    sit on a transparent background so the visual reads as a circle.
    """
    from reportlab.platypus import Table, TableStyle, Paragraph
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.colors import HexColor
    GRAY_BUBBLE = HexColor("#525252")
    bubble_text = ParagraphStyle(
        "BubbleNum", fontName=bullet_s.fontName, fontSize=10,
        textColor=HexColor("#ffffff"), alignment=TA_CENTER, leading=11,
    )
    # 2-column table per item: bubble | text
    rows = []
    for n, item in enumerate(items, start=1):
        bubble = Table(
            [[Paragraph(str(n), bubble_text)]],
            colWidths=[18], rowHeights=[18],
        )
        bubble.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), GRAY_BUBBLE),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            # Reportlab can't draw rounded corners; the small square cell
            # at typical viewing scale reads as a "chip" — close enough
            # to the MM reference circle for print fidelity.
        ]))
        rows.append([bubble, Paragraph(_inline(item), bullet_s)])
    tbl = Table(rows, colWidths=[26, None])
    tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        # Padding between bubble column and text column
        ("LEFTPADDING", (1, 0), (1, -1), 14),
    ]))
    return tbl


def _reflect_callout(text, label_s, body_s, bg, border, total_width):
    """MagMutual-style "Pause and reflect" — a dark gray full-width banner
    with a large white heading and a white prompt body. The `bg` and
    `border` colors are kept in the signature for backward compat but are
    overridden here so callers don't need to know the new palette.
    """
    from reportlab.platypus import Paragraph, Table, TableStyle, KeepTogether
    from reportlab.lib.colors import HexColor
    DARK_GRAY = HexColor("#525252")  # matches MM reference banner
    inner = [Paragraph("Pause and reflect", label_s),
             Paragraph(_inline(text), body_s)]
    tbl = Table([[inner]], colWidths=[total_width])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), DARK_GRAY),
        # Generous internal padding so the banner reads as a hero callout
        ("LEFTPADDING", (0, 0), (-1, -1), 26),
        ("RIGHTPADDING", (0, 0), (-1, -1), 26),
        ("TOPPADDING", (0, 0), (-1, -1), 24),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 24),
    ]))
    return KeepTogether(tbl)


def _definition_grid(cards, term_s, def_s, border, bg, total_width):
    """Render the "Definition of Key Terms" cards as a 3-column grid."""
    from reportlab.platypus import Paragraph, Table, TableStyle, KeepTogether
    cols = 3
    col_w = total_width / cols
    rows = []
    cur_row = []
    for term, definition in cards:
        cell = [Paragraph(_inline(term), term_s),
                Paragraph(_inline(definition), def_s)]
        cur_row.append(cell)
        if len(cur_row) == cols:
            rows.append(cur_row)
            cur_row = []
    if cur_row:
        while len(cur_row) < cols:
            cur_row.append("")
        rows.append(cur_row)
    tbl = Table(rows, colWidths=[col_w] * cols)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("BOX", (0, 0), (-1, -1), 0.5, border),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, border),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return KeepTogether(tbl)


def _render_pdf_case_study(blocks, start_idx, story, *,
                            h1, h3, h4, body, bullet_s,
                            reflect_label, reflect_body,
                            strat_label, strat_item,
                            accent, line, bg_soft,
                            text_muted, total_w, inch):
    """Render an embedded case study (H3 'Case study N' / 'Key loss
    driver:' followed by H4 sub-sections) as a stack of bordered cards
    with thin vertical connectors between, mirroring the MagMutual
    reference layout.

    Walks `blocks` from `start_idx` until it sees the next H3, an HR,
    or end-of-blocks. Returns the number of blocks consumed so the
    caller can advance the index.

    The strategy section ("Reducing clinical risks" + "Reducing
    non-clinical risks") still uses the side-by-side `_strategy_tabs`
    layout in print — we can't toggle tabs in a PDF, so showing both
    panels is the next best thing.
    """
    from reportlab.platypus import (
        Paragraph, Spacer, Table, TableStyle, KeepTogether, ListFlowable,
        ListItem,
    )
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.colors import HexColor

    # H3 line that triggered us
    kind, payload = blocks[start_idx]
    title_text = payload
    # Drop a leading "Key loss driver:" prefix so the rendered heading
    # always reads "Case study N" / clean topic when legacy content
    # comes back.
    title_text = re.sub(r"^Key loss driver\s*:\s*", "", title_text, flags=re.I)

    # Card-internal styles. We re-derive these instead of taking them as
    # arguments to keep the function signature compact — they only differ
    # from the outer body styles in their leading + indent.
    card_h_style = ParagraphStyle(
        "CSCardH", parent=h4, fontSize=11, leading=14,
        textColor=HexColor("#161616"), spaceBefore=0, spaceAfter=4,
    )
    # MagMutual sub-headings inside cards are mixed-case, not uppercase.
    # h4 normally uppercases the text via _inline().upper(); override.
    card_body_style = ParagraphStyle(
        "CSCardBody", parent=body, fontSize=10.5, leading=15,
    )

    consumed = 1  # the H3 itself
    intro_buf: list[str] = []
    n = len(blocks)

    # H3 emits as a Lato-Black card heading + intro paragraph BEFORE any
    # cards. Note the "Case study N" prompt structure puts an optional
    # context paragraph here.
    story.append(Paragraph(_inline(title_text), h3))

    # Walk forward to gather the optional intro paragraph(s)
    j = start_idx + 1
    while j < n:
        k, p = blocks[j]
        if k == "para":
            intro_buf.append(p)
            j += 1
            consumed += 1
            continue
        # First H4/H3/HR breaks us out of the intro paragraphs
        break
    if intro_buf:
        story.append(Paragraph(_inline(" ".join(intro_buf)), card_body_style))
    story.append(Spacer(1, 0.18 * inch))

    cards_started = False
    while j < n:
        k, p = blocks[j]
        # Stop on next case study or HR
        if k == "h3" or k == "hr":
            break

        # Pause and reflect — emit the dark hero banner via the existing
        # _reflect_callout. Resets the connector chain.
        if k == "reflect":
            story.append(_reflect_callout(
                p, reflect_label, reflect_body, None, None, total_w))
            story.append(Spacer(1, 0.18 * inch))
            cards_started = False
            j += 1
            consumed += 1
            continue

        # "Risk reduction strategies for [topic]" wrapper — MM precedes
        # the tab control with this short subhead + 1-line intro. Render
        # it as a small section break before the strategy panels.
        if k == "h4" and re.match(
                r"risk reduction strategies(\s+for\s+.+)?$", p, re.I):
            from reportlab.platypus import Paragraph, Spacer
            wrap_h = h3  # use the H3 paragraph style for visual weight
            story.append(Spacer(1, 0.18 * inch))
            story.append(Paragraph(_inline(p), wrap_h))
            j += 1
            consumed += 1
            # Pick up an optional intro para
            if j < n and blocks[j][0] == "para":
                story.append(Paragraph(_inline(blocks[j][1]), body))
                j += 1
                consumed += 1
            cards_started = False
            continue

        # Reducing clinical risks → consume both halves and emit the
        # side-by-side strategy tabs (PDF can't toggle, so show both).
        if (k == "h4" and re.match(r"reducing clinical risks?$", p, re.I)
            and j + 1 < n and blocks[j + 1][0] == "bullets"):
            clinical = blocks[j + 1][1]
            non_clinical: list[str] = []
            advance = 2
            if (j + 3 < n
                and blocks[j + 2][0] == "h4"
                and re.match(r"reducing non-clinical risks?$",
                              blocks[j + 2][1], re.I)
                and blocks[j + 3][0] == "bullets"):
                non_clinical = blocks[j + 3][1]
                advance = 4
            story.append(_strategy_tabs(
                clinical, non_clinical, strat_label, strat_item,
                accent, text_muted, line, bg_soft, total_w))
            j += advance
            consumed += advance
            cards_started = False
            continue

        # H4 sub-sections render as bordered cards. Timeline → split
        # body on `**Date**` markers into per-entry cards.
        if k == "h4":
            heading = p
            j += 1
            consumed += 1
            # Collect the content blocks belonging to this H4 until the
            # next H3 / H4 / HR / strategy block / pause-reflect.
            sub_blocks = []
            while j < n:
                kk, pp = blocks[j]
                if kk in ("h3", "h4", "hr", "reflect"):
                    break
                sub_blocks.append((kk, pp))
                j += 1
                consumed += 1

            if cards_started:
                # Thin vertical connector between cards
                story.append(_color_rule(line, 1, 14, h_align="LEFT"))
                story.append(Spacer(1, 4))
            else:
                story.append(Spacer(1, 0.06 * inch))

            if re.match(r"^Timeline\b", heading, re.I):
                _emit_timeline_cards(
                    story, sub_blocks,
                    card_h_style, card_body_style,
                    line, total_w)
            else:
                _emit_cs_card(
                    story, heading, sub_blocks,
                    card_h_style, card_body_style, bullet_s,
                    line, total_w, accent)
            cards_started = True
            continue

        # Stray paragraph / list inside the case study — render bare
        if k == "para":
            story.append(Paragraph(_inline(p), card_body_style))
        elif k == "bullets":
            story.append(_render_bullets(p, bullet_s, accent))
        j += 1
        consumed += 1

    return consumed


def _emit_cs_card(story, heading: str, sub_blocks: list,
                  h_style, body_style, bullet_s,
                  line_color, total_w, accent_color) -> None:
    """Wrap a sub-section's content in a single bordered Table cell —
    the bordered "card" used throughout the case study."""
    from reportlab.platypus import Paragraph, Table, TableStyle, KeepTogether

    inner = [Paragraph(_inline(heading), h_style)]
    for k, p in sub_blocks:
        if k == "para":
            inner.append(Paragraph(_inline(p), body_style))
        elif k == "bullets":
            inner.append(_render_bullets(p, bullet_s, accent_color))
        elif k == "numbered":
            inner.append(_render_numbered(p, bullet_s, line_color))
        elif k in ("h3", "h4"):
            # Defensive fallback — shouldn't happen given how we slice
            # sub_blocks above.
            inner.append(Paragraph(_inline(p), h_style))

    tbl = Table([[inner]], colWidths=[total_w])
    tbl.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, line_color),
        # Match the HTML preview's ~8px corner feel by adding generous
        # padding (reportlab Tables don't render rounded corners).
        ("LEFTPADDING", (0, 0), (-1, -1), 16),
        ("RIGHTPADDING", (0, 0), (-1, -1), 16),
        ("TOPPADDING", (0, 0), (-1, -1), 14),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
    ]))
    story.append(KeepTogether(tbl))


def _emit_timeline_cards(story, sub_blocks: list,
                         h_style, body_style,
                         line_color, total_w) -> None:
    """Walk the Timeline body and emit one bordered card per
    `**Date**`-prefixed entry, separated by thin vertical connectors.

    The block parser flattens `**Date**\\nbody` into `(para, "**Date**
    body")` because they're on adjacent lines without a blank between.
    We split each para on the first bold marker.
    """
    from reportlab.platypus import Paragraph, Table, TableStyle, KeepTogether

    entries: list[tuple[str, str]] = []
    for k, p in sub_blocks:
        if k != "para":
            continue
        # Split a paragraph that mixes "**Date**" with the body
        m = re.match(r"^\*\*([^*]+)\*\*\s*(.*)$", p, re.S)
        if m:
            entries.append((m.group(1).strip(), m.group(2).strip()))
        else:
            # No clear date marker — append to the previous entry's
            # body, or stand alone with no header.
            if entries:
                date, body = entries[-1]
                entries[-1] = (date, (body + " " + p).strip())
            else:
                entries.append(("", p))

    for idx, (date, body) in enumerate(entries):
        if idx > 0:
            from reportlab.platypus import Spacer
            story.append(_color_rule(line_color, 1, 14, h_align="LEFT"))
            story.append(Spacer(1, 4))
        inner = []
        if date:
            inner.append(Paragraph(_inline(date), h_style))
        if body:
            inner.append(Paragraph(_inline(body), body_style))
        tbl = Table([[inner]], colWidths=[total_w])
        tbl.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.6, line_color),
            ("LEFTPADDING", (0, 0), (-1, -1), 16),
            ("RIGHTPADDING", (0, 0), (-1, -1), 16),
            ("TOPPADDING", (0, 0), (-1, -1), 14),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
        ]))
        story.append(KeepTogether(tbl))


def _strategy_tabs(clinical, non_clinical, label_s, item_s,
                    accent, neutral, border, bg, total_width):
    """Two side-by-side tabs (clinical / non-clinical) with colored top edges."""
    from reportlab.platypus import (Paragraph, Table, TableStyle, ListFlowable,
                                       ListItem, KeepTogether)
    cols = 2 if non_clinical else 1
    col_w = total_width / cols

    def col(label, items, accent_color):
        cell = [Paragraph(label, label_s)]
        if items:
            cell.append(ListFlowable(
                [ListItem(Paragraph(_inline(b), item_s),
                          bulletColor=accent_color) for b in items],
                bulletType="bullet", start="•", leftIndent=12,
            ))
        return cell

    row = [col("REDUCING CLINICAL RISKS", clinical, accent)]
    if non_clinical:
        row.append(col("REDUCING NON-CLINICAL RISKS",
                       non_clinical, neutral))

    tbl = Table([row], colWidths=[col_w] * cols)
    style = [
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("BOX", (0, 0), (-1, -1), 0.5, border),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        # Top accent edge per column
        ("LINEABOVE", (0, 0), (0, 0), 3, accent),
    ]
    if non_clinical:
        style.append(("LINEABOVE", (1, 0), (1, 0), 3, neutral))
        style.append(("LINEAFTER", (0, 0), (0, 0), 0.5, border))
    tbl.setStyle(TableStyle(style))
    return KeepTogether(tbl)


# ---------------------------------------------------------------------------
# Assessment renderer (one styled page per question)
# ---------------------------------------------------------------------------
def _render_assessment_pages(content, story, section_eyebrow, h1, h2, h3, body,
                              *, Paragraph, Spacer, PageBreak, color_rule,
                              ACCENT, GRAY_100, GRAY_70, GRAY_20, GRAY_10,
                              GREEN_50, total_w, inch):
    """Parse the HTML5 assessment output and render each question on its
    own PDF page with a MagMutual-style layout:

      - Section eyebrow ("ASSESSMENT") + accent rule on the first page
      - Per-question page with: small "QUESTION 0X / 10" eyebrow,
        difficulty pill, question stem as the H1, options as a 2-col
        Table (letter | text) with green border on the correct row,
        and a subtle "Rationale" callout below.

    Falls back to plain markdown rendering only if no questions can
    be parsed.
    """
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_LEFT
    from reportlab.platypus import Table, TableStyle, KeepTogether
    from reportlab.lib.colors import HexColor

    # Use the same fonts as the body (Lato when available, Helvetica fallback)
    has_lato = _ensure_fonts_registered()
    if has_lato:
        FONT_BODY = "Lato-Regular"
        FONT_BOLD = "Lato-Bold"
        FONT_BLACK = "Lato-Black"
    else:
        FONT_BODY = "Helvetica"
        FONT_BOLD = "Helvetica-Bold"
        FONT_BLACK = "Helvetica-Bold"

    # Parse using the same parser the HTML preview uses, so the PDF and
    # the in-app preview always agree on what the questions are.
    try:
        from course_preview import _parse_assessment
    except ImportError:
        from course_preview import _parse_assessment  # pragma: no cover
    questions = _parse_assessment(content or "")
    if not questions:
        # Bail to plain text rendering if parsing fails.
        story.append(Paragraph("ASSESSMENT", section_eyebrow))
        story.append(color_rule(ACCENT, total_w, 2))
        story.append(Spacer(1, 0.15 * inch))
        story.append(Paragraph(_inline(content or "(no assessment content)"), body))
        return

    # ---------- Per-question styles (match course_preview.py palette) ----------
    # Eyebrow style matches MM reference: italic gray, mixed case.
    FONT_ITALIC = "Lato-Italic" if has_lato else "Helvetica-Oblique"
    q_eyebrow = ParagraphStyle(
        "QEyebrow", fontName=FONT_ITALIC, fontSize=10,
        textColor=GRAY_70, leading=13, alignment=TA_LEFT, spaceAfter=10,
    )
    q_stem = ParagraphStyle(
        "QStem", fontName=FONT_BLACK, fontSize=18, leading=24,
        textColor=GRAY_100, spaceAfter=18, alignment=TA_LEFT,
    )
    q_pill = ParagraphStyle(
        "QPill", fontName=FONT_BOLD, fontSize=8,
        textColor=GRAY_70, leading=10, spaceAfter=8,
    )
    q_lo = ParagraphStyle(
        "QLO", fontName=FONT_BODY, fontSize=9, leading=13,
        textColor=GRAY_70, spaceAfter=14, alignment=TA_LEFT,
    )
    opt_letter = ParagraphStyle(
        "OptLetter", fontName=FONT_BLACK, fontSize=11,
        textColor=GRAY_70, leading=14, alignment=TA_LEFT,
    )
    opt_letter_correct = ParagraphStyle(
        "OptLetterCorrect", fontName=FONT_BLACK, fontSize=11,
        textColor=GREEN_50, leading=14, alignment=TA_LEFT,
    )
    opt_text = ParagraphStyle(
        "OptText", fontName=FONT_BODY, fontSize=10.5, leading=15,
        textColor=GRAY_100, alignment=TA_LEFT,
    )
    rationale_label = ParagraphStyle(
        "RatLabel", fontName=FONT_BOLD, fontSize=8.5,
        textColor=GREEN_50, leading=11, spaceAfter=4,
    )
    rationale_body = ParagraphStyle(
        "RatBody", fontName=FONT_BODY, fontSize=10, leading=15,
        textColor=GRAY_100,
    )

    total = len(questions)
    for idx, q in enumerate(questions):
        if idx > 0:
            story.append(PageBreak())

        # Eyebrow — italic gray, mixed case ("Assessment · Question 01 / 10"),
        # short decorative rule. Matches the MagMutual reference styling.
        story.append(Paragraph(
            f"Assessment &nbsp;·&nbsp; Question {idx + 1:02d} / {total:02d}",
            q_eyebrow,
        ))
        story.append(color_rule(GRAY_20, 1.6 * inch, 1.5))
        story.append(Spacer(1, 0.22 * inch))

        # Difficulty pill (rendered as a single-cell Table for proper bg).
        # hAlign="LEFT" so the pill sits at the page-left margin instead
        # of being centered (the default for narrow Tables).
        if q.get("difficulty"):
            pill = Table(
                [[Paragraph(q["difficulty"].upper(), q_pill)]],
                colWidths=[0.95 * inch], rowHeights=[0.22 * inch],
                hAlign="LEFT",
            )
            pill.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), GRAY_10),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]))
            story.append(pill)
            story.append(Spacer(1, 0.12 * inch))

        # Stem as the page's headline
        story.append(Paragraph(_inline(q["stem"]), q_stem))

        # Optional: learning objective subline
        if q.get("learning_objective"):
            story.append(Paragraph(
                f"<b>Learning objective:</b> {_inline(q['learning_objective'])}",
                q_lo,
            ))

        # Options as a styled table — letter column + text column, with a
        # left accent rule on the correct row (subtle, MM-style).
        correct_idx = q.get("correct_idx", 0)
        rows = []
        for i, opt_txt in enumerate(q["options"]):
            letter = chr(65 + i)
            letter_style = opt_letter_correct if i == correct_idx else opt_letter
            rows.append([
                Paragraph(letter, letter_style),
                Paragraph(_inline(opt_txt), opt_text),
            ])
        opts_tbl = Table(rows, colWidths=[0.45 * inch, total_w - 0.45 * inch])
        ts = [
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, -1), 11),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 11),
            ("LINEBELOW", (0, 0), (-1, -1), 0.5, GRAY_20),
            ("LINEABOVE", (0, 0), (-1, 0), 0.5, GRAY_20),
        ]
        # Highlight the correct answer with a pale green background +
        # a left accent rule, matching the MagMutual reference styling.
        ts.append(("BACKGROUND", (0, correct_idx), (-1, correct_idx),
                   HexColor("#ecf6ed")))
        ts.append(("LINEBEFORE", (0, correct_idx), (0, correct_idx),
                   2.5, GREEN_50))
        opts_tbl.setStyle(TableStyle(ts))
        story.append(opts_tbl)

        # Rationale callout
        if q.get("rationale"):
            story.append(Spacer(1, 0.2 * inch))
            inner = [
                Paragraph("CORRECT ANSWER · RATIONALE", rationale_label),
                Paragraph(_inline(q["rationale"]), rationale_body),
            ]
            rat_tbl = Table([[inner]], colWidths=[total_w])
            rat_tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), HexColor("#f7faf7")),
                ("LINEBEFORE", (0, 0), (0, 0), 3.0, GREEN_50),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 12),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
            ]))
            story.append(KeepTogether(rat_tbl))


# ---------------------------------------------------------------------------
# Mini-markdown parser
# ---------------------------------------------------------------------------
def _parse_blocks(md: str):
    lines = md.splitlines()
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        if stripped.startswith("```"):
            i += 1
            buf = []
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            yield ("code", "\n".join(buf))
            continue
        if re.match(r"^---+$", stripped):
            yield ("hr", "")
            i += 1
            continue
        # Pause and reflect callout (heading + prose until next heading/hr)
        if re.match(r"^#{2,4}\s+Pause and reflect\b", stripped, re.I):
            i += 1
            buf = []
            while i < n and not (
                re.match(r"^#{1,4}\s", lines[i].strip())
                or re.match(r"^---+$", lines[i].strip())
            ):
                if lines[i].strip():
                    buf.append(lines[i].strip())
                i += 1
            yield ("reflect", " ".join(buf))
            continue
        if stripped.startswith("#### "):
            yield ("h4", stripped[5:])
            i += 1
            continue
        if stripped.startswith("### "):
            yield ("h3", stripped[4:])
            i += 1
            continue
        if stripped.startswith("## "):
            yield ("h2", stripped[3:])
            i += 1
            continue
        if stripped.startswith("# "):
            yield ("h1", stripped[2:])
            i += 1
            continue
        if stripped.startswith("- ") or stripped.startswith("* "):
            items = []
            while i < n and (lines[i].lstrip().startswith("- ")
                              or lines[i].lstrip().startswith("* ")):
                items.append(lines[i].lstrip()[2:])
                i += 1
            yield ("bullets", items)
            continue
        if re.match(r"^\d+\.\s", stripped):
            items = []
            while i < n and re.match(r"^\s*\d+\.\s", lines[i]):
                items.append(re.sub(r"^\s*\d+\.\s", "", lines[i]))
                i += 1
            yield ("numbered", items)
            continue
        para = []
        while i < n and lines[i].strip() and not _is_block_start(lines[i]):
            para.append(lines[i].strip())
            i += 1
        if para:
            yield ("para", " ".join(para))


def _is_block_start(line: str) -> bool:
    s = line.strip()
    return (s.startswith("#") or s.startswith("- ") or s.startswith("* ")
            or s.startswith("```") or bool(re.match(r"^\d+\.\s", s))
            or bool(re.match(r"^---+$", s)))


def _parse_definition_bullets(items):
    """Pull (term, definition) from bullet items shaped like
    `**Term** — Definition` or `**Term** - Definition` or `**Term**: Def`.
    Returns [] if the items don't look like definitions.
    """
    out = []
    for item in items:
        m = re.match(r"^\*\*(.+?)\*\*\s*[—\-:]\s*(.+)$", item)
        if not m:
            return []
        out.append((m.group(1).strip(), m.group(2).strip()))
    return out


def _build_toc(sections):
    """Return a list of (number, label) — one entry per top-level lesson
    in the 5-lesson MagMutual structure.

    - Course body: pull out the "Lesson N of 5" H2 headers.
    - Embedded case-study sections (labelled "Lesson 3 · ...") are nested
      under Lesson 3 in the body and get NO separate TOC entry.
    - Assessment + Closing sections get their own TOC entries.
    """
    toc = []
    counter = 1
    for name, content in sections.items():
        # Skip embedded per-topic case studies — they live inside Lesson 3.
        if re.match(r"\s*Lesson\s*3\s*[·\-]", name or "", re.I):
            continue
        # Body section: pull lesson headers out of the markdown.
        lesson_headers = re.findall(r"^##\s+(Lesson\s+\d+\s+of\s+5[^\n]*)$",
                                     content or "", re.M)
        if lesson_headers:
            for h in lesson_headers:
                toc.append((counter, h))
                counter += 1
        else:
            toc.append((counter, name))
            counter += 1
    return toc


def _is_assessment_section(name: str, content: str) -> bool:
    """A section is the Assessment if it's labelled 'Lesson 4 of 5' OR
    its body contains HTML5 `<h2>Question N` markers. We can't just match
    the substring 'assessment' in the label — embedded case studies are
    sometimes titled e.g. 'Single-point assessment vs. serial evaluation'.
    """
    return bool(
        re.search(r"\blesson\s*4\s*of\s*5\b", name or "", re.I)
        or re.search(r"<h2[^>]*>\s*Question\s*\d", content or "", re.I)
    )


def _estimate_minutes(sections):
    """Conservative engagement-time estimate.
    Reading 140 wpm, MCQs 75 sec, reflection 30 sec each.
    """
    body_words = 0
    mcq_count = 0
    reflect_count = 0
    for name, content in sections.items():
        content = content or ""
        if _is_assessment_section(name, content):
            mcq_count += len(re.findall(r"<h2[^>]*>\s*Question", content, re.I))
        else:
            body_words += len(content.split())
            reflect_count += len(re.findall(
                r"^#{2,4}\s+Pause and Reflect", content, re.I | re.M))
    minutes = body_words / 140 + mcq_count * 75 / 60 + reflect_count * 0.5
    return max(5, round(minutes))


def _extract_answer_key(sections):
    """Find the assessment HTML and pull out a numbered answer key like
    [(1, 'B', 'first-line stem snippet'), ...]."""
    for name, content in sections.items():
        if not _is_assessment_section(name, content) or not content:
            continue
        # Split into question chunks
        chunks = re.split(r"(?=<section[^>]*>)", content, flags=re.I)
        out = []
        n = 1
        for chunk in chunks:
            m = re.search(r"<h2[^>]*>\s*Question\s*(\d+)?", chunk, re.I)
            if not m:
                continue
            qnum = int(m.group(1)) if m.group(1) else n
            n = qnum + 1
            mc = re.search(r"<b>\s*correct\s*:\s*</b>\s*([A-D])",
                            chunk, re.I)
            if not mc:
                continue
            letter = mc.group(1).upper()
            stem_match = re.findall(r"<p[^>]*>(.+?)</p>", chunk, re.I | re.S)
            stem = ""
            for p in stem_match:
                clean = re.sub(r"<[^>]+>", "", p).strip()
                if clean and not clean.lower().startswith(("correct", "rationale")):
                    stem = clean
                    break
            snippet = (stem[:90] + "…") if len(stem) > 90 else stem
            out.append((qnum, letter, snippet))
        return out
    return []


# ---------------------------------------------------------------------------
# Inline markdown → reportlab inline tags
# ---------------------------------------------------------------------------
def _inline(s: str) -> str:
    s = _escape(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<!\*)\*(?!\*)(.+?)\*(?!\*)", r"<i>\1</i>", s)
    s = re.sub(r"`([^`]+)`", r"<font face='Courier'>\1</font>", s)
    return s


def _escape(s: str) -> str:
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;"))


# =====================================================================
# SCORM zip builder (merged from scorm.py)
# =====================================================================
"""SCORM 1.2 export.

Bundles the generated course into a SCORM 1.2 conformant zip:
  - imsmanifest.xml at root
  - index.html (the course content rendered)
  - SCORM 1.2 schema XSDs (we ship minimal stubs that pass most LMS validators)
  - Supporting CSS/JS files

The HTML mirrors the MagMutual visual style: lesson dividers, large heading
hierarchy, blue accent, IBM Plex font, callout blocks for "Pause and reflect".

Usage:
    bytes_ = build_scorm_zip(title, course_id, sections)
"""

import html
import io
import re
import zipfile
from datetime import datetime


SCORM_VERSION = "1.2"


def build_scorm_zip(title: str, course_id: str,
                    sections: dict[str, str],
                    top_factors: list[dict] | None = None) -> bytes:
    """Bundle the rendered course into a SCORM 1.2 zip.

    The HTML uses the same CSS as the in-app Live HTML preview
    (shared.course_preview) so the downloaded SCORM looks identical to
    what the user sees while building. We just append the SCORM runtime
    JS so LMSes can mark the SCO as completed.

    `top_factors` (output of `snowflake_client.top_contributing_factors`)
    is plumbed through so the SCORM build's Lesson 2 includes the same
    contributing-factor bar chart the live preview shows.
    """
    from course_preview import render_course_html
    # Generate the inner HTML body from course_preview, but we need to
    # post-process to add the scorm.js reference and remove the inline
    # <script> (which would conflict with the standalone scorm.js file).
    full_html = render_course_html(title, sections, top_factors=top_factors)
    # Inject scorm.js reference so the LMS API gets pinged
    full_html = full_html.replace(
        "</head>",
        '  <script src="scorm.js" defer></script>\n</head>',
        1,
    )
    manifest = _render_manifest(title, course_id)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("imsmanifest.xml", manifest)
        z.writestr("index.html", full_html)
        z.writestr("scorm.js", _SCORM_JS)
        # SCORM 1.2 schemas — most LMSes don't strictly validate them.
        z.writestr("imsmd_rootv1p2p1.xsd", "<?xml version='1.0'?><!-- stub -->")
        z.writestr("imscp_rootv1p1p2.xsd", "<?xml version='1.0'?><!-- stub -->")
        z.writestr("ims_xml.xsd", "<?xml version='1.0'?><!-- stub -->")
        z.writestr("adlcp_rootv1p2.xsd", "<?xml version='1.0'?><!-- stub -->")
    buf.seek(0)
    return buf.read()


def _render_manifest(title: str, course_id: str) -> str:
    safe_id = re.sub(r"[^A-Za-z0-9_]", "_", course_id) or "course"
    safe_title = html.escape(title)
    return f"""<?xml version="1.0" standalone="no" ?>
<manifest identifier="{safe_id}_MANIFEST" version="1.0"
          xmlns="http://www.imsproject.org/xsd/imscp_rootv1p1p2"
          xmlns:adlcp="http://www.adlnet.org/xsd/adlcp_rootv1p2"
          xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
          xsi:schemaLocation="http://www.imsproject.org/xsd/imscp_rootv1p1p2 imscp_rootv1p1p2.xsd
                              http://www.imsglobal.org/xsd/imsmd_rootv1p2p1 imsmd_rootv1p2p1.xsd
                              http://www.adlnet.org/xsd/adlcp_rootv1p2 adlcp_rootv1p2.xsd">
  <metadata>
    <schema>ADL SCORM</schema>
    <schemaversion>{SCORM_VERSION}</schemaversion>
  </metadata>
  <organizations default="DEFAULT_ORG">
    <organization identifier="DEFAULT_ORG">
      <title>{safe_title}</title>
      <item identifier="ITEM_1" identifierref="RES_1">
        <title>{safe_title}</title>
      </item>
    </organization>
  </organizations>
  <resources>
    <resource identifier="RES_1" type="webcontent" adlcp:scormtype="sco"
              href="index.html">
      <file href="index.html"/>
      <file href="scorm.js"/>
    </resource>
  </resources>
</manifest>
"""


def _render_course_html(title: str, sections: dict[str, str]) -> str:
    safe_title = html.escape(title)

    body_parts = [
        f"<header class='cover'>"
        f"<div class='eyebrow'>MagMutual Risk Management</div>"
        f"<h1>{safe_title}</h1>"
        f"<p class='cover-sub'>Reducing liability through evidence-based education.</p>"
        f"<nav class='toc'><div class='toc-label'>Course Overview</div>"
        f"<ol>"
        + "".join(f"<li>{html.escape(name)}</li>" for name in sections.keys())
        + f"</ol></nav></header>"
    ]
    for name, md in sections.items():
        body_parts.append(
            f"<section class='lesson-block'>"
            f"<div class='lesson-eyebrow'>{html.escape(name)}</div>"
            f"<div class='lesson-body'>{_md_to_html(md)}</div>"
            f"</section>"
        )
    body_parts.append(
        "<footer class='course-foot'>"
        f"<p>Generated {datetime.utcnow().date().isoformat()} · MyAdvice Builder</p>"
        "</footer>"
    )
    body = "\n".join(body_parts)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>{safe_title}</title>
  <link rel="stylesheet" href="style.css" />
  <script src="scorm.js" defer></script>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@300;400;500;600;700&display=swap" rel="stylesheet">
</head>
<body>
  <main class="course">{body}</main>
</body>
</html>
"""


def _md_to_html(md: str) -> str:
    """Tiny markdown→HTML converter tuned for our course content.

    Supports H1/H2/H3/H4, paragraphs, bullet lists, numbered lists, bold,
    italic, inline code, and a "Pause and reflect" callout pattern.
    """
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
    while i < len(lines):
        s = lines[i].rstrip()
        stripped = s.strip()
        if not stripped:
            flush_para(para_buf)
            i += 1
            continue
        # Pause and reflect callout
        if re.match(r"^(####\s+|###\s+|##\s+)?Pause and reflect\b", stripped, re.I):
            flush_para(para_buf)
            i += 1
            buf = []
            while i < len(lines) and not re.match(r"^#{2,4}\s|^---", lines[i].strip()):
                if lines[i].strip():
                    buf.append(lines[i].strip())
                i += 1
            content = " ".join(buf)
            out.append(
                f"<aside class='reflect'>"
                f"<div class='reflect-label'>Pause and reflect</div>"
                f"<p>{_inline(content)}</p></aside>"
            )
            continue
        # Headings
        m = re.match(r"^(#{1,4})\s+(.+?)\s*$", stripped)
        if m:
            flush_para(para_buf)
            level = len(m.group(1))
            text = _inline(m.group(2))
            cls = ""
            if re.match(r"Lesson\s+\d+\s+of\s+\d+", m.group(2), re.I):
                cls = " class='lesson-marker'"
            out.append(f"<h{level}{cls}>{text}</h{level}>")
            i += 1
            continue
        # Horizontal rule
        if re.match(r"^---+$", stripped):
            flush_para(para_buf)
            out.append("<hr/>")
            i += 1
            continue
        # Bullet list
        if stripped.startswith("- ") or stripped.startswith("* "):
            flush_para(para_buf)
            items = []
            while i < len(lines) and (lines[i].lstrip().startswith("- ")
                                       or lines[i].lstrip().startswith("* ")):
                items.append(lines[i].lstrip()[2:])
                i += 1
            out.append("<ul>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + "</ul>")
            continue
        # Numbered list
        if re.match(r"^\d+\.\s", stripped):
            flush_para(para_buf)
            items = []
            while i < len(lines) and re.match(r"^\s*\d+\.\s", lines[i]):
                items.append(re.sub(r"^\s*\d+\.\s", "", lines[i]))
                i += 1
            out.append("<ol>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + "</ol>")
            continue
        # Default: paragraph buffer
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
# Static assets shipped inside the SCORM zip
# ---------------------------------------------------------------------------
_CSS = """
:root {
  --blue-60:#0f62fe; --blue-70:#0043ce; --blue-80:#002d9c;
  --gray-10:#f4f4f4; --gray-20:#e0e0e0; --gray-30:#c6c6c6;
  --gray-70:#525252; --gray-90:#262626; --gray-100:#161616;
  --green-50:#24a148; --yellow-30:#f1c21b;
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  font-family: 'IBM Plex Sans', system-ui, sans-serif;
  color: var(--gray-100);
  background: white;
  line-height: 1.55;
}
.course { max-width: 880px; margin: 0 auto; padding: 2.5rem 1.6rem 4rem; }

.cover {
  border-bottom: 1px solid var(--gray-20);
  padding-bottom: 2rem;
  margin-bottom: 2rem;
}
.cover .eyebrow {
  font-size: 0.75rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--blue-60);
  margin-bottom: 0.75rem;
  font-family: 'IBM Plex Mono', monospace;
}
.cover h1 {
  font-size: 2.6rem;
  font-weight: 300;
  line-height: 1.1;
  margin: 0 0 0.5rem 0;
  letter-spacing: -0.02em;
}
.cover-sub { color: var(--gray-70); font-size: 1.05rem; }

.toc {
  margin-top: 2rem;
  background: var(--gray-10);
  padding: 1rem 1.25rem;
  border-left: 3px solid var(--blue-60);
}
.toc-label {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 0.75rem;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--gray-70);
  margin-bottom: 0.4rem;
}
.toc ol { margin: 0; padding-left: 1.25rem; }
.toc li { margin-bottom: 0.25rem; }

.lesson-block { margin-bottom: 2.5rem; }
.lesson-eyebrow {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 0.75rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--blue-60);
  margin-bottom: 0.75rem;
}

.lesson-body h1 {
  font-size: 1.85rem;
  font-weight: 600;
  margin: 1rem 0 0.6rem;
  letter-spacing: -0.015em;
}
.lesson-body h2.lesson-marker,
.lesson-body h2 {
  font-size: 1.45rem;
  font-weight: 600;
  margin: 1.5rem 0 0.6rem;
  padding-top: 1rem;
  border-top: 1px solid var(--gray-20);
}
.lesson-body h3 {
  font-size: 1.1rem;
  font-weight: 600;
  margin: 1rem 0 0.4rem;
  color: var(--gray-100);
}
.lesson-body h4 {
  font-size: 0.9rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--blue-60);
  margin: 1rem 0 0.4rem;
}
.lesson-body p { margin: 0 0 0.85rem; color: var(--gray-90); }
.lesson-body ul, .lesson-body ol { padding-left: 1.4rem; margin: 0.5rem 0 1rem; }
.lesson-body li { margin-bottom: 0.3rem; }

.lesson-body hr {
  border: none;
  border-top: 1px solid var(--gray-20);
  margin: 1.5rem 0;
}

.reflect {
  background: #fcf4d6;
  border-left: 3px solid var(--yellow-30);
  padding: 0.85rem 1rem;
  margin: 1rem 0;
}
.reflect-label {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: #684e00;
  margin-bottom: 0.3rem;
}
.reflect p { margin: 0; }

code {
  font-family: 'IBM Plex Mono', ui-monospace, monospace;
  background: var(--gray-10);
  padding: 0 0.3rem;
}

.course-foot {
  margin-top: 3rem;
  padding-top: 1rem;
  border-top: 1px solid var(--gray-20);
  font-size: 0.8rem;
  color: var(--gray-70);
}
"""

# Minimal SCORM 1.2 runtime: posts initialize/finish so the LMS marks the SCO
# as completed when the learner closes the course. No assessment scoring.
_SCORM_JS = r"""
(function(){
  function findAPI(win) {
    var n = 0;
    while (win && !win.API && win.parent && win.parent !== win && n++ < 7) {
      win = win.parent;
    }
    return win ? win.API : null;
  }
  var api = null;
  try { api = findAPI(window); } catch (e) { api = null; }

  function init() {
    if (!api) return;
    try { api.LMSInitialize(""); } catch (e) {}
    try { api.LMSSetValue("cmi.core.lesson_status", "incomplete"); } catch (e) {}
    try { api.LMSCommit(""); } catch (e) {}
  }
  function complete() {
    if (!api) return;
    try { api.LMSSetValue("cmi.core.lesson_status", "completed"); } catch (e) {}
    try { api.LMSCommit(""); } catch (e) {}
    try { api.LMSFinish(""); } catch (e) {}
  }

  window.addEventListener("DOMContentLoaded", init);
  window.addEventListener("beforeunload", complete);
})();
"""
