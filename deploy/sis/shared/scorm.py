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
from __future__ import annotations

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
    from .course_preview import render_course_html
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
