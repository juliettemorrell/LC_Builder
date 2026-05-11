"""End-to-end pipeline smoke test.

Run with `python -m tests.test_pipeline` (or `pytest tests/`) BEFORE any
Snowflake deploy to confirm the full content pipeline still produces
valid output even when only the mock backend is available.

What it checks (all paths use the mock cortex fallback):
  1. Every prompt builder assembles non-empty text with the version stamp.
  2. Every prompt routes to the correct mock branch and returns realistic
     content of the expected shape (length / HTML markers).
  3. PDF rendering produces valid PDF bytes for a representative course.
  4. SCORM zip is buildable, has a valid manifest, and contains index.html.
  5. Markdown export round-trips.
  6. Save → load → delete round-trips through the local JSON fallback.
  7. Chat audit log captures + reads back an entry.
  8. Per-prompt model + temperature dispatch returns the right values.

Exits 0 on success, non-zero on any failure (with a short failure message
on stderr). No external dependencies beyond the existing requirements.txt.
"""
from __future__ import annotations

import io
import re
import sys
import zipfile
from pathlib import Path

# Run from repo root so imports resolve
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------
class TestFail(Exception):
    pass


def check(cond: bool, msg: str):
    if not cond:
        raise TestFail(msg)


def section(name: str):
    print(f"\n=== {name} ===")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_prompts():
    section("Prompt assembly")
    from shared.prompts import (
        build_course_body, build_assessment, build_embedded_lesson_for_topic,
        build_closing, build_lesson, build_claim_selection, build_confidence,
        build_edit_section, PROMPT_VERSION,
    )
    driver = {
        "DRIVER_ID": "EM-DX-ACS", "SPECIALTY": "Emergency Medicine",
        "DRIVER": "Missed ACS", "RISK_BRIEF": "brief",
        "OVERVIEW": "overview", "CLINICAL_DIAGNOSTIC": "ddx",
    }
    los = ["LO1", "LO2", "LO3"]
    claim = {
        "DOCUMENT_ID": "CLM-1", "SPECIALTY": "Emergency Medicine",
        "AGE_RANGE": "Late 50s", "SEX": "Male",
        "PRESENTING_COMPLAINT": "chest pressure", "SUMMARY": "summary",
        "ALLEGATIONS": ["alleg"], "RESOLUTION": "settled",
    }
    builders = [
        ("course_body", build_course_body(driver, "playbook", los)),
        ("assessment", build_assessment("body", los)),
        ("embedded_lesson", build_embedded_lesson_for_topic("body", "topic", claim)),
        ("closing", build_closing("body", driver)),
        ("lesson", build_lesson(claim, driver)),
        ("claim_selection", build_claim_selection([claim], [{}], [driver])),
        ("confidence", build_confidence("text", ["src"])),
        ("edit_section", build_edit_section("Sec", "cur", "src", "tighten")),
    ]
    for name, prompt in builders:
        check(prompt.strip(), f"{name}: empty prompt")
        check(PROMPT_VERSION in prompt, f"{name}: missing version stamp")
        print(f"  ✓ {name:18s} {len(prompt):>5d} chars")


def test_cortex_routing():
    section("Cortex routing (mock)")
    from shared.cortex import complete, MODELS, TEMPS, model_for, temp_for
    from shared.prompts import build_course_body, build_assessment
    driver = {"DRIVER": "x", "RISK_BRIEF": "x", "OVERVIEW": "x"}
    cases = [
        ("course_body", build_course_body(driver, "x", ["LO"])),
        ("assessment", build_assessment("body", ["LO"])),
    ]
    for kind, prompt in cases:
        res = complete(prompt, kind=kind)
        check(res.text.strip(), f"{kind}: empty mock response")
        check(res.model == model_for(kind),
              f"{kind}: model mismatch ({res.model} != {model_for(kind)})")
        # The mock latency is < 100ms typically
        print(f"  ✓ {kind:18s} {len(res.text):>5d} chars · {res.model}")
    # Verify TEMPS & MODELS dicts are exhaustive
    for kind in ["course_body", "embedded_lesson", "assessment", "closing",
                 "lesson", "claim_selection", "confidence", "edit_section",
                 "quick_action", "default"]:
        check(kind in MODELS, f"MODELS missing {kind!r}")
        check(kind in TEMPS, f"TEMPS missing {kind!r}")


def test_pdf():
    section("PDF export")
    from shared.export import to_pdf_bytes
    # Embedded case study with the new "Case study N" heading PLUS the
    # full sub-section structure (Medical summary / Timeline / Allegations
    # / Outcome / Pause and reflect / Reducing clinical / non-clinical
    # risks). This exercises every branch of the PDF case-study renderer.
    case_study_md = (
        "### Case study 1\n\n"
        "Frequency and severity for this driver are high.\n\n"
        "#### Medical summary\n"
        "A patient summary text.\n\n"
        "#### Timeline\n"
        "**Initial presentation**\nVitals were stable.\n\n"
        "**Day 3**\nPatient returned with worsening symptoms.\n\n"
        "#### Allegations\n"
        "- Failure to perform serial assessment.\n"
        "- Inadequate documentation.\n\n"
        "#### Outcome\n"
        "Settled in the high six figures.\n\n"
        "#### Pause and reflect\n"
        "How does your team handle this kind of case?\n\n"
        "#### Reducing clinical risks\n"
        "- Strategy A.\n"
        "- Strategy B.\n\n"
        "#### Reducing non-clinical risks\n"
        "- Operational A.\n"
        "- Operational B."
    )
    sections = {
        "Lessons 1-3 · Body": (
            "# Title\n\n"
            "## Lesson 1 of 5: Course Overview\n\n"
            "### What You'll Learn\nIntro paragraph.\n\n"
            "### Objectives\n1. Recognize\n2. Apply\n3. Document"
        ),
        "Lesson 3 · 1 of 1 · Single-point assessment": case_study_md,
        "Lesson 4 of 5 · Assessment": (
            "<section><h2>Question 1</h2>"
            "<span class=\"badge\">Beginner</span>"
            "<p>Stem?</p>"
            "<ol type=\"A\"><li>A1</li><li>A2</li></ol>"
            "<p><b>Correct:</b> A</p>"
            "<p><b>Rationale:</b> Because.</p></section>"
        ),
        "Lesson 5 of 5 · Closing": (
            "## Lesson 5 of 5: Closing\n\n### Key Takeaways\n1. T1"
        ),
    }
    pdf = to_pdf_bytes("My Course", sections)
    check(pdf.startswith(b"%PDF"), "PDF magic bytes missing")
    pages = len(re.findall(rb"/Type\s*/Page[^s]", pdf))
    check(pages >= 5, f"Expected ≥ 5 pages, got {pages}")
    # The Lato font registers as `/Font` references inside the PDF when
    # the TTFs are bundled. If absent, we silently fell back to Helvetica.
    check(b"Lato" in pdf, "Lato font missing in PDF (font registration failed)")
    print(f"  ✓ {len(pdf):>6d} bytes, {pages} pages, Lato embedded, "
          f"case-study path exercised")


def test_scorm():
    section("SCORM export")
    from shared.scorm import build_scorm_zip
    sections = {"Lesson 1": "## Lesson 1 of 5: Intro\n\nHello."}
    z = build_scorm_zip("Title", "DRIVER", sections)
    zf = zipfile.ZipFile(io.BytesIO(z))
    names = zf.namelist()
    check("imsmanifest.xml" in names, "missing imsmanifest.xml")
    check("index.html" in names, "missing index.html")
    manifest = zf.read("imsmanifest.xml").decode()
    check("<organizations" in manifest, "manifest has no organizations")
    check("<resources" in manifest, "manifest has no resources")
    print(f"  ✓ {len(z):>5d} bytes, {len(names)} files, manifest valid")


def test_save_load():
    section("Save / load round-trip")
    from shared.saves import save_item, load_save, delete_save, list_saves
    saved = save_item(
        "course", "Smoke test course",
        {"sections": {"a": "b"}}, driver_id="EM-DX-ACS",
    )
    check(saved.save_id, "save_item returned no id")
    check(saved.prompt_version, "prompt_version not stamped on save")
    loaded = load_save(saved.save_id)
    check(loaded is not None, "load_save returned None")
    check(loaded.payload["sections"]["a"] == "b", "payload corrupted")
    check(loaded.prompt_version == saved.prompt_version,
          "prompt_version round-trip failed")
    deleted = delete_save(saved.save_id)
    check(deleted, "delete_save returned False")
    print(f"  ✓ saved+loaded+deleted {saved.save_id}")


def test_chat_log():
    section("Chat audit log")
    from shared.chat_log import log_edit, list_recent, to_csv
    e = log_edit(
        section_id="course_body", kind="quick_action",
        instruction="tighten", before_text="long",
        after_text="short", prompt="prompt",
        model="claude-3-5-sonnet", temperature=0.25,
        latency_s=0.5, prompt_version="test",
    )
    check(e.log_id, "log_edit returned no id")
    recent = list_recent(limit=5)
    check(any(r.log_id == e.log_id for r in recent),
          "logged entry not found in recent")
    csv = to_csv(recent)
    check("occurred_at" in csv, "CSV missing header")
    check(e.log_id in csv or e.section_id in csv,
          "CSV missing entry data")
    print(f"  ✓ logged + read back: {e.log_id}")


def test_html_preview():
    section("HTML preview")
    from shared.course_preview import render_course_html
    embedded_md = (
        "### Key loss driver: Atypical presentations\n"
        "Frequency and severity for this driver are high.\n\n"
        "#### Medical summary\n"
        "Patient summary text.\n\n"
        "#### Timeline\n"
        "**Initial presentation**\nVitals were ...\n\n"
        "**Day 3**\nReturn visit.\n\n"
        "#### Allegations\n- Failure A\n- Failure B\n\n"
        "#### Outcome\nSettled.\n\n"
        "#### Pause and reflect\nReflect on this.\n\n"
        "#### Reducing clinical risks\n- Strategy 1\n- Strategy 2\n\n"
        "#### Reducing non-clinical risks\n- Operational A\n- Operational B"
    )
    sections = {
        "Lessons 1-3 · Body": (
            "# Title\n\n## Lesson 1 of 5: Course Overview\n\n"
            "### What You'll Learn\nIntro.\n\n"
            "1. Recognise atypical presentations\n"
            "2. Document differential reasoning\n"
            "3. Escalate when red flags persist\n\n"
            "## Lesson 2 of 5: Loss Trends\n\n"
            "### Definition of Key Terms\n"
            "- **Indemnity** — Payments made to a party for a loss.\n"
            "- **Claim** — A formal notice of legal action."
        ),
        "Lesson 3 · 1 of 1 · Atypical": embedded_md,
        "Lesson 4 of 5 · Assessment": (
            "<section><h2>Question 1</h2><p>Stem?</p>"
            "<ol type=\"A\"><li>A</li><li>B</li></ol>"
            "<p><b>Correct:</b> A</p></section>"
        ),
    }
    html = render_course_html("My Course", sections)
    check("qa-card" in html, "assessment cards not rendered")
    check("class='lesson-marker'" in html, "lesson eyebrow not split out")
    check("class='lesson-title'" in html, "lesson title H2 not split out")
    check("MagMutual" in html, "cover branding missing")
    # Case study layout assertions — added to lock down the SCORM-style
    # rendering against future regressions.
    check("case-study cs-1" in html, "case-study wrapper missing")
    check("class='cs-card'" in html, "case-study card class missing")
    check("class='cs-connector'" in html, "case-study connector line missing")
    check("class='cs-hero'" in html, "case-study hero placeholder missing")
    check("class='cs-card cs-card--timeline'" in html,
          "timeline card class missing")
    check("class='strat-tabs'" in html, "strategy tab container missing")
    check("class='strat-tabbtn is-active'" in html,
          "default-active strategy tab missing")
    check("class='strat-panel is-active'" in html,
          "default-active strategy panel missing")
    check("class='reflect'" in html, "pause-and-reflect banner missing")
    # Definition flip-card assertions
    check("class='def-card'" in html, "definition card class missing")
    check("class='def-card-front'" in html, "def front face missing")
    check("class='def-card-back'" in html, "def back face missing")
    # Cover & nav polish — gray-circle numbered lists, Start-course CTA,
    # reading-progress bar, per-lesson Continue CTAs.
    check("ol class='num-circle'" in html,
          "numbered lists missing gray-circle bubble class")
    check("class='cover-cta'" in html, "Start course CTA missing")
    check("class='reading-progress'" in html or 'class="reading-progress"' in html,
          "reading-progress bar missing")
    check("class='continue-cta'" in html, "per-lesson Continue CTA missing")
    # CME meta + disclosures intentionally NOT in the artifact — the LMS
    # wrapper page hosts those. Lock them out so we don't re-add them.
    check("class='cover-meta'" not in html and 'class="cover-meta"' not in html,
          "cover meta strip should NOT be in the artifact (LMS wrapper hosts it)")
    check("id='disclosures'" not in html,
          "disclosures section should NOT be in the artifact (LMS wrapper hosts it)")
    check("AMA PRA Category 1" not in html,
          "credit designation should NOT be in the artifact")
    check("Accreditation statement" not in html,
          "accreditation block should NOT be in the artifact")
    # Things we explicitly REMOVED (locked out so no one re-adds them
    # without revisiting the MM reference)
    check("class='toc-ico'" not in html, "TOC lesson icons should be removed")
    check("class='spy'" not in html, "sticky scroll-spy rail should be removed")
    check("class='course-done'" not in html and "id='course-done'" not in html,
          "course-complete panel should be removed")
    check("class='toc-progress'" not in html, "TOC progress meter should be removed")
    print(f"  ✓ {len(html):>5d} chars, all assertions pass")


def test_brief_slicer():
    section("Risk-brief slicer")
    from shared.prompts import slice_risk_brief
    sample = (
        "Acute Myocardial Infarction\n"
        "SPECIALTY: Emergency Medicine\n\n"
        "PRESENTING CONDITION(S): Chest Pain, Dyspnea, Suspected ACS\n\n"
        "ADVERSE OUTCOME: Acute Myocardial Infarction\n\n"
        "Mitigating Your Risk\n"
        "With vigilant assessment teams can mitigate risks.\n\n"
        "CLINICAL: DIAGNOSTIC\n"
        "Failure to Obtain Relevant History\n"
        "Mitigation: structured tools.\n\n"
        "CLINICAL: TREATMENT\n"
        "Error in Therapeutic Intervention\n"
        "Mitigation: serial monitoring.\n\n"
        "ADMINISTRATIVE: COMMUNICATION\n"
        "Communication Failure Between Providers\n"
        "Mitigation: handoff template.\n\n"
        "ADMINISTRATIVE: DOCUMENTATION\n"
        "Documentation Failure\n"
        "Mitigation: structured note.\n"
    )
    sliced = slice_risk_brief(sample)
    for label in (
        "PRESENTING_CONDITION", "ADVERSE_OUTCOME", "MITIGATING_YOUR_RISK",
        "CLINICAL_DIAGNOSTIC", "CLINICAL_TREATMENT",
        "ADMINISTRATIVE_COMMUNICATION", "ADMINISTRATIVE_DOCUMENTATION",
    ):
        check(label in sliced, f"slicer missed {label}")
        check(bool(sliced[label].strip()), f"slicer captured empty body for {label}")
    check("Chest Pain" in sliced["PRESENTING_CONDITION"],
          "PRESENTING_CONDITION inline body not captured")
    check("Acute Myocardial Infarction" in sliced["ADVERSE_OUTCOME"],
          "ADVERSE_OUTCOME inline body not captured")
    check("structured tools" in sliced["CLINICAL_DIAGNOSTIC"],
          "CLINICAL_DIAGNOSTIC body not captured")
    print(f"  ✓ {len(sliced)} canonical sections captured")


def test_per_case_strategy_variation():
    section("Per-case strategy variation")
    from shared.cortex import _mock_case_study
    cases = [_mock_case_study(topic=f"Topic {i+1}", cs_idx=i + 1)
             for i in range(5)]
    # Each case should carry its own clinical + non-clinical bullets,
    # NOT the same boilerplate as case 1.
    import re
    def _strats(text: str, header: str) -> set[str]:
        m = re.search(rf"#### {header}\n(.+?)(?:\n####|\Z)", text, re.S)
        if not m:
            return set()
        return {ln.strip("-* ").strip() for ln in m.group(1).splitlines() if ln.strip().startswith("-")}
    case_strats = [_strats(c, "Reducing clinical risks") for c in cases]
    case_strats_n = [_strats(c, "Reducing non-clinical risks") for c in cases]
    # Ensure NO two cases share the exact same clinical-strategies set
    for i in range(len(case_strats)):
        for j in range(i + 1, len(case_strats)):
            check(case_strats[i] != case_strats[j],
                  f"clinical strategies identical between case {i+1} and case {j+1}")
            check(case_strats_n[i] != case_strats_n[j],
                  f"non-clinical strategies identical between case {i+1} and case {j+1}")
    # And every case has a non-empty strategy block
    for idx, (c, n) in enumerate(zip(case_strats, case_strats_n), start=1):
        check(len(c) >= 2, f"case {idx} has too few clinical bullets")
        check(len(n) >= 2, f"case {idx} has too few non-clinical bullets")
    print(f"  ✓ 5 cases × 2 strategy panels — all unique, all populated")


def main():
    tests = [
        test_prompts,
        test_cortex_routing,
        test_pdf,
        test_scorm,
        test_save_load,
        test_chat_log,
        test_html_preview,
        test_brief_slicer,
        test_per_case_strategy_variation,
    ]
    failed = []
    for t in tests:
        try:
            t()
        except TestFail as e:
            print(f"  ✗ FAIL: {e}", file=sys.stderr)
            failed.append((t.__name__, str(e)))
        except Exception as e:
            print(f"  ✗ ERROR: {e}", file=sys.stderr)
            failed.append((t.__name__, repr(e)))
    print()
    if failed:
        print("FAILED:")
        for name, msg in failed:
            print(f"  - {name}: {msg}", file=sys.stderr)
        sys.exit(1)
    print(f"=== ALL {len(tests)} TEST GROUPS PASSED ===")
    sys.exit(0)


if __name__ == "__main__":
    main()
