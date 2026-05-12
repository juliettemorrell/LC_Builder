"""Cortex Complete wrapper.

Provides a single `complete()` function that:
- Calls Snowflake Cortex when a session is available (via st.connection or env)
- Falls back to a local mock generator if not connected, so the UI is fully demoable.

Bakes in `max_tokens=32000` to defeat the 4096-token silent-truncation bug.

Tracks lightweight telemetry (last latency, last model, mock call counter,
connection state) that the UI can read via `cortex_status()`.
"""

from __future__ import annotations

import json
import os
import re
import textwrap
import time
from dataclasses import dataclass
from typing import Optional

DEFAULT_MODEL = "claude-opus-4-7"
DEFAULT_MAX_TOKENS = 32000
DEFAULT_TEMPERATURE = 0.2

# ---------------------------------------------------------------------------
# Per-prompt temperatures, hardcoded for clinical accuracy.
#
# Rationale:
# - Clinical / structured outputs use 0.0-0.2 (deterministic, fact-faithful).
# - Generative / narrative outputs that benefit from a touch of variety use
#   0.25-0.35.
# - Confidence scoring is fully deterministic at 0.0.
# - All values were chosen conservatively. Lower temps reduce hallucination
#   risk; higher temps improve narrative flow but at the cost of factual
#   drift. Risk playbooks already carry the canonical clinical guidance, so
#   the model should NOT be inventing new clinical content, hence very low.
# ---------------------------------------------------------------------------
TEMPS = {
    "course_body":     0.20,  # structured, every fact must be in the playbook
    "embedded_lesson": 0.25,  # narrative case study, slight variety acceptable
    "assessment":      0.15,  # 10 grounded MCQs; near-deterministic
    "closing":         0.20,  # synthesis of body content
    "lesson":          0.20,  # full claims lesson, structured + grounded
    "claim_selection": 0.25,  # ranking with a small amount of judgment
    "confidence":      0.00,  # JSON grader, fully deterministic
    "edit_section":    0.30,  # apply user instruction; some flexibility
    "quick_action":    0.25,  # rewrite under a fixed instruction
    "default":         0.20,
}


def temp_for(kind: str) -> float:
    """Return the hardcoded temperature for a given prompt kind."""
    return TEMPS.get(kind, TEMPS["default"])


# ---------------------------------------------------------------------------
# Per-prompt model selection, hardcoded.
#
# Rationale (based on Cortex's available Claude models):
# - claude-opus-4-7 is the most capable model and the right choice anywhere
#   clinical accuracy + structured output matters most. Course body, embedded
#   case studies, full claims lessons, claim selection, and the assessment
#   all live here.
# - claude-3-5-sonnet is fast and very capable; we use it for the simpler
#   evaluator + small-edit calls (confidence JSON grading, single-paragraph
#   tighten / expand quick actions, surgical section edits) so the user gets
#   sub-second responses for incremental edits without the full Opus latency.
# - We never expose this to the user, they shouldn't have to make this
#   trade-off, and clinical content should never silently downgrade.
# ---------------------------------------------------------------------------
MODELS = {
    "course_body":     "claude-opus-4-7",   # full lessons 1-3, clinical depth
    "embedded_lesson": "claude-opus-4-7",   # case studies, clinical depth
    "lesson":          "claude-opus-4-7",   # claims lesson, clinical depth
    "assessment":      "claude-opus-4-7",   # MCQs, accuracy on clinical scenarios
    "closing":         "claude-opus-4-7",   # synthesizes prior body
    "claim_selection": "claude-opus-4-7",   # high-stakes ranking
    "confidence":      "llama3.1-70b",      # JSON grader (claude-3-5-sonnet not in this region)
    "edit_section":    "claude-opus-4-7",   # surgical edits to clinical content
    "quick_action":    "llama3.1-70b",      # tighten / expand / plain (claude-3-5-sonnet not in region)
    "default":         "claude-opus-4-7",
}


def model_for(kind: str) -> str:
    """Return the hardcoded model for a given prompt kind."""
    return MODELS.get(kind, MODELS["default"])

# ---------------------------------------------------------------------------
# Module-level telemetry (read by the UI for the status panel)
# ---------------------------------------------------------------------------
_telemetry = {
    "last_latency_s": None,   # float | None
    "last_model": None,
    "last_mocked": True,
    "last_kind": None,        # str | None , prompt kind for the most recent call
    "last_temperature": None, # float | None
    "last_prompt_preview": None,  # str | None, first ~2KB of the most recent prompt
    "last_response_preview": None,  # str | None, first ~2KB of the most recent response
    "calls_total": 0,
    "calls_mocked": 0,
    "calls_real": 0,
    "errors": [],             # most recent first
    "retries": 0,             # cumulative count of retried real-Cortex calls
    "connection_checked": False,
    "connection_live": False,
}

_session_cache = {"session": None, "checked": False}


@dataclass
class CortexResult:
    text: str
    model: str
    mocked: bool
    elapsed_s: float


def cortex_status() -> dict:
    """Snapshot of the Cortex telemetry for the UI."""
    return dict(_telemetry)


# ---------------------------------------------------------------------------
# Real Cortex path
# ---------------------------------------------------------------------------
def _try_get_session(force_refresh: bool = False):
    """Cached lookup for a Snowpark session.

    Tries three paths, in order:
      1. Streamlit-in-Snowflake: `get_active_session()` returns the
         ambient Snowpark session the SiS runtime provides. This is
         the production path when the app is deployed inside Snowflake.
      2. Streamlit Community Cloud / local with secrets:
         `st.connection("snowflake").session()` reads
         `.streamlit/secrets.toml` for the `[connections.snowflake]`
         block.
      3. Env-var fallback for raw `streamlit run` outside SiS.

    Returns None if no connection is available — caller falls back to
    the mock Cortex path.
    """
    if not force_refresh and _session_cache["checked"]:
        return _session_cache["session"]

    session = None

    # 1. Streamlit-in-Snowflake (ambient session) — the production path
    try:
        from snowflake.snowpark.context import get_active_session
        session = get_active_session()
    except Exception:
        session = None

    # 2. Streamlit Cloud / local: read from secrets.toml
    if session is None:
        try:
            import streamlit as st  # noqa
            try:
                session = st.connection("snowflake").session()
            except Exception:
                session = None
        except Exception:
            session = None

    # 3. Env-var fallback via Snowpark builder
    if session is None:
        try:
            from snowflake.snowpark import Session
            if all(os.getenv(k) for k in ["SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD"]):
                cfg = {
                    "account": os.getenv("SNOWFLAKE_ACCOUNT"),
                    "user": os.getenv("SNOWFLAKE_USER"),
                    "password": os.getenv("SNOWFLAKE_PASSWORD"),
                    "role": os.getenv("SNOWFLAKE_ROLE"),
                    "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE"),
                    "database": os.getenv("SNOWFLAKE_DATABASE", "HACKATHON_DWH"),
                    "schema": os.getenv("SNOWFLAKE_SCHEMA"),
                }
                cfg = {k: v for k, v in cfg.items() if v}
                session = Session.builder.configs(cfg).create()
        except Exception:
            session = None

    _session_cache["session"] = session
    _session_cache["checked"] = True
    _telemetry["connection_checked"] = True
    _telemetry["connection_live"] = session is not None
    return session


_TRANSIENT_PATTERNS = (
    "rate limit", "ratelimited", "throttle", "throttled", "429",
    "timeout", "timed out", "connection reset", "temporarily unavailable",
    "service unavailable", "503", "internal server error", "500",
)


def _is_transient(msg: str) -> bool:
    m = msg.lower()
    return any(p in m for p in _TRANSIENT_PATTERNS)


def _real_complete(prompt: str, model: str, max_tokens: int,
                    temperature: float, *, max_retries: int = 2) -> Optional[str]:
    """Run Cortex.COMPLETE via Snowpark with exponential-backoff retries
    on transient errors. Returns None on any non-transient failure (caller
    falls back to mock and surfaces the error in `_telemetry['errors']`).
    """
    session = _try_get_session()
    if session is None:
        return None
    options = {"max_tokens": max_tokens, "temperature": temperature}
    # `?` is the portable bind syntax in Snowpark Python. `PARSE_JSON(?)`
    # converts the JSON-string options into a Snowflake VARIANT, which is
    # what CORTEX.COMPLETE expects for its third arg.
    sql = "SELECT SNOWFLAKE.CORTEX.COMPLETE(?, ?, PARSE_JSON(?)) AS RESPONSE"
    last_err = None
    for attempt in range(max_retries + 1):
        try:
            df = session.sql(sql, params=[model, prompt, json.dumps(options)]).collect()
            if df and len(df) > 0:
                response = df[0]["RESPONSE"]
                if isinstance(response, str) and response.strip():
                    return response
                # Cortex may return a dict/struct depending on options; coerce.
                if response is not None:
                    s = str(response).strip()
                    if s:
                        return s
            return None  # empty response, no retry
        except Exception as e:
            msg = str(e)[:280]
            last_err = msg
            if attempt < max_retries and _is_transient(msg):
                _telemetry["retries"] += 1
                # Exponential backoff: 0.4s, 0.8s, ...
                time.sleep(0.4 * (2 ** attempt))
                continue
            break
    if last_err:
        _telemetry["errors"].insert(0, last_err)
        _telemetry["errors"] = _telemetry["errors"][:5]
        try:
            import streamlit as st
            st.session_state.setdefault("_cortex_errors", []).append(last_err)
        except Exception:
            pass
    return None


# ---------------------------------------------------------------------------
# Mock path
# ---------------------------------------------------------------------------
def _mock_complete(prompt: str) -> str:
    p_lower = prompt.lower()

    # Confidence score prompt
    if "publication_decision" in prompt or "dimension_scores" in prompt:
        # Vary slightly on the content to feel less canned
        h = abs(hash(prompt[:200])) % 6
        grade = "ABBCBC"[h]
        decision = {"A": "APPROVED", "B": "APPROVED", "C": "REQUIRES_REVISION"}[grade]
        return json.dumps({
            "output_type": "course_generator" if "course" in p_lower else "claims_lesson",
            "overall_grade": grade,
            "publication_decision": decision,
            "dimension_scores": {
                "dimension_1": {"name": "Source Alignment", "score": 4 if grade != "C" else 3,
                                "reasoning": ["Captures key risk drivers from RISK_BRIEF.",
                                              "One supplementary detail under-addressed.",
                                              "No contradictions with source."]},
                "dimension_2": {"name": "Completeness", "score": 4,
                                "reasoning": ["All sections present.",
                                              "Embedded lesson could include more clinical detail.",
                                              "Learning objectives covered."]},
                "dimension_3": {"name": "Clinical Accuracy", "score": 5,
                                "reasoning": ["Evidence-based and current.",
                                              "No safety concerns.",
                                              "Aligned with standard of care."]},
                "dimension_4": {"name": "Actionability", "score": 4,
                                "reasoning": ["Strategies tied to risk reduction.",
                                              "Implementation steps clear.",
                                              "Could add more specific protocols."]},
                "dimension_5": {"name": "Clarity & Organization", "score": 4 if grade != "C" else 3,
                                "reasoning": ["Logical flow.",
                                              "Headings clear.",
                                              "Some paragraphs run long."]},
            },
            "section_grades": {"course_body": grade, "assessment": grade, "embedded_lesson": "A"},
            "summary": "Mock review: strong draft with minor revisions where prose runs long. RISK_BRIEF alignment is solid.",
            "blocking_issues": None,
        }, indent=2)

    # Per-topic embedded lesson must be checked BEFORE the generic course body
    # match because the topic-lesson prompt also contains "course body" in its
    # source block. Use uniquely-embedded-lesson signals only, "Topic anchor:"
    # is injected by the build_embedded_lesson_for_topic helper, and "Case
    # study [N]" / "Risk reduction strategies for" only appear in the
    # embedded prompt's <structure>. Phrases like "key loss driver:" were
    # removed from this matcher because the course-body prompt now also
    # uses that phrase as the H3 prefix for topic stubs.
    if ("topic anchor:" in p_lower
        or "case study [n]" in p_lower
        or "risk reduction strategies for" in p_lower
        or "anchor the lesson here" in p_lower):
        topic = "Selected topic"
        m = re.search(r"Topic anchor:\s*([^\n]+)", prompt)
        if m:
            topic = m.group(1).strip()
        # Extract the 1-based case-study index that the builder injected
        # into the structure ("### Case study N"). Default to 1.
        cs_idx = 1
        m_idx = re.search(r"###\s+Case study\s+(\d+)", prompt, re.I)
        if m_idx:
            cs_idx = int(m_idx.group(1))
        # Pull the driver name + specialty so the mock can produce
        # topically-aligned content (otherwise it generates ACS chest-
        # pain scenarios for every driver, which reads as duplicated).
        driver = ""
        m_drv = re.search(r"#\s+RISK DRIVER\s*\n+([^\n]+)", prompt)
        if m_drv:
            driver = m_drv.group(1).strip()
        specialty = ""
        m_spec = re.search(r"#\s+SPECIALTY\s*\n+([^\n]+)", prompt)
        if m_spec:
            specialty = m_spec.group(1).strip()
        return _mock_case_study(topic=topic, cs_idx=cs_idx,
                                driver=driver, specialty=specialty)

    # Assessment prompt is UNIQUE on phrases like "10 questions" and "html5".
    # It must be checked before the course body branch because the assessment
    # prompt includes the generated course body as context, which would
    # otherwise match the course-body fallback below.
    if "assessment" in p_lower and ("html5" in p_lower or "10 questions" in p_lower
                                     or "<question_count>10</question_count>" in prompt):
        return textwrap.dedent("""
        <section>
        <h2>Question 1</h2>
        <span class="badge">Beginner</span>
        <div class="lo">Learning Objective: Recognize features prompting expanded cardiac workup.</div>
        <div class="gap">Practice Gap: Under-recognition of atypical ACS presentations.</div>
        <div class="qtype">Scenario-based</div>
        <p>A 68-year-old woman with a history of type 2 diabetes presents to the ED with three hours of fatigue, nausea, and mild epigastric discomfort. Her initial ECG is unremarkable and her first troponin is below the assay limit. Which next step is most consistent with the standard of care?</p>
        <ol type="A">
          <li>Discharge home with a primary care follow-up note</li>
          <li>Order a repeat troponin and serial ECGs and continue observation</li>
          <li>Refer for an outpatient stress test</li>
          <li>Treat empirically for gastritis and reassess</li>
        </ol>
        <p><b>Correct:</b> B</p>
        <p><b>Rationale:</b> In patients with diabetes, ACS commonly presents atypically. A single normal ECG and a single negative troponin within hours of symptom onset are insufficient to rule out ACS. Serial assessments are the standard of care.</p>
        </section>

        <section>
        <h2>Question 2</h2>
        <span class="badge">Intermediate</span>
        <div class="lo">Learning Objective: Apply structured decision-support tools.</div>
        <p>Which component is part of the HEART score?</p>
        <ol type="A">
          <li>Family history of premature CAD alone</li>
          <li>Cumulative cardiovascular risk factors</li>
          <li>BMI over 30</li>
          <li>Resting heart rate trend</li>
        </ol>
        <p><b>Correct:</b> B</p>
        <p><b>Rationale:</b> The HEART score uses History, ECG, Age, Risk factors (cumulative), and Troponin. Family history alone is one of several risk factors but is not a standalone component.</p>
        </section>

        <section>
        <h2>Question 3</h2>
        <span class="badge">Intermediate</span>
        <div class="lo">Learning Objective: Document the reasoning behind disposition decisions.</div>
        <p>A patient with chest pain is being discharged after a negative initial ECG and a HEART score of 4. Which documentation element best supports defensibility?</p>
        <ol type="A">
          <li>"No acute findings."</li>
          <li>"Patient looks well, discharged."</li>
          <li>"Differential considered (ACS, PE, dissection); HEART 4, low-intermediate risk; serial troponins ordered; outpatient stress scheduled in 72 hours; return precautions given and confirmed."</li>
          <li>"Patient was assured."</li>
        </ol>
        <p><b>Correct:</b> C</p>
        <p><b>Rationale:</b> Defensibility hinges on documenting the differential, the reasoning, and the closed-loop plan.</p>
        </section>

        <section>
        <h2>Question 4</h2>
        <span class="badge">Beginner</span>
        <div class="lo">Learning Objective: Recognize patterns of atypical ACS presentation.</div>
        <p>Which patient population is most likely to present with atypical symptoms of acute coronary syndrome?</p>
        <ol type="A">
          <li>A 25-year-old male with classic substernal chest pressure</li>
          <li>An elderly woman with diabetes presenting with fatigue and nausea</li>
          <li>A patient with a recent positive stress test</li>
          <li>A patient with isolated arm pain after weightlifting</li>
        </ol>
        <p><b>Correct:</b> B</p>
        <p><b>Rationale:</b> Women, patients with diabetes, and elderly patients present atypically more often than the general population. Recognizing this pattern is essential to avoid premature diagnostic closure.</p>
        </section>

        <section>
        <h2>Question 5</h2>
        <span class="badge">Intermediate</span>
        <div class="lo">Learning Objective: Implement closed-loop communication.</div>
        <p>An emergency physician discharges a chest-pain patient with a plan for outpatient cardiac workup. Which step best closes the loop?</p>
        <ol type="A">
          <li>Hand the patient a written list of cardiologists in the area.</li>
          <li>Tell the patient to call their primary care doctor in the morning.</li>
          <li>Schedule the outpatient follow-up appointment before discharge and confirm the receiving clinician received the workup plan.</li>
          <li>Ask the patient to acknowledge the return precautions verbally.</li>
        </ol>
        <p><b>Correct:</b> C</p>
        <p><b>Rationale:</b> Closed-loop communication requires that the receiving clinician has actually received the plan, not just that the patient was told to follow up.</p>
        </section>

        <section>
        <h2>Question 6</h2>
        <span class="badge">Advanced</span>
        <div class="lo">Learning Objective: Apply serial assessment principles.</div>
        <p>A patient with ongoing chest pain has an unremarkable initial ECG. Symptoms worsen 30 minutes later. The single troponin is still pending. What is the most appropriate next step?</p>
        <ol type="A">
          <li>Wait for the initial troponin result before further action.</li>
          <li>Repeat the ECG immediately and notify the team.</li>
          <li>Discharge with an outpatient cardiology referral.</li>
          <li>Order a CT pulmonary angiogram only.</li>
        </ol>
        <p><b>Correct:</b> B</p>
        <p><b>Rationale:</b> When symptoms evolve, the ECG should be repeated. Subtle ischemic changes can appear as the clinical picture progresses.</p>
        </section>

        <section>
        <h2>Question 7</h2>
        <span class="badge">Beginner</span>
        <div class="lo">Learning Objective: Identify documentation gaps that drive claims.</div>
        <p>Which of the following is most often a contributing factor in ACS-diagnosis malpractice claims?</p>
        <ol type="A">
          <li>Excessive use of cardiology consultations</li>
          <li>Documentation that does not reflect the differential considered or the disposition reasoning</li>
          <li>Over-reliance on serial troponins</li>
          <li>Aggressive use of low-dose aspirin</li>
        </ol>
        <p><b>Correct:</b> B</p>
        <p><b>Rationale:</b> Documentation gaps frequently appear in closed claims as a contributing factor. The chart shapes defensibility long after the encounter.</p>
        </section>

        <section>
        <h2>Question 8</h2>
        <span class="badge">Intermediate</span>
        <div class="lo">Learning Objective: Apply system-level levers.</div>
        <p>Which system-level intervention is most likely to catch incomplete cardiac workups before discharge?</p>
        <ol type="A">
          <li>An EHR alert when the discharge order is placed without a documented serial troponin or risk score.</li>
          <li>A monthly newsletter reminding clinicians about ACS workup.</li>
          <li>A poster in the breakroom on chest-pain protocols.</li>
          <li>An optional online module on cardiac risk stratification.</li>
        </ol>
        <p><b>Correct:</b> A</p>
        <p><b>Rationale:</b> Active EHR alerts at the moment of discharge interrupt the workflow precisely when an incomplete workup is most consequential.</p>
        </section>

        <section>
        <h2>Question 9</h2>
        <span class="badge">Advanced</span>
        <div class="lo">Learning Objective: Apply de-escalation in low-risk presentations.</div>
        <p>A young patient with reproducible chest wall tenderness, normal ECG, and a HEART score of 1 presents to the ED. Which approach is most appropriate?</p>
        <ol type="A">
          <li>Admit for serial troponins regardless of risk score.</li>
          <li>Document the differential, the score, and the reasoning for discharge with explicit return precautions and follow-up.</li>
          <li>Discharge without documentation since the score is low.</li>
          <li>Refer for invasive cardiac catheterization.</li>
        </ol>
        <p><b>Correct:</b> B</p>
        <p><b>Rationale:</b> Low-risk discharge is appropriate when the workup supports it, but documentation must reflect the considered alternatives and the closed-loop follow-up plan.</p>
        </section>

        <section>
        <h2>Question 10</h2>
        <span class="badge">Intermediate</span>
        <div class="lo">Learning Objective: Recognize practice change priorities.</div>
        <p>Reviewing your last 10 chest-pain discharges, which single practice change is most likely to reduce ACS-related claim risk?</p>
        <ol type="A">
          <li>Adding a longer return-precaution speech.</li>
          <li>Documenting the differential, the score, and the closed-loop plan in every chest-pain note.</li>
          <li>Ordering more advanced imaging routinely.</li>
          <li>Skipping the HEART score for low-risk patients.</li>
        </ol>
        <p><b>Correct:</b> B</p>
        <p><b>Rationale:</b> Most ACS claims hinge on incomplete documentation of the reasoning. Closing this gap is the single highest-leverage practice change.</p>
        </section>
        """).strip()

    # Closing section (Lesson 5 of 5), must check before generic course body match
    if ("lesson 5 of 5" in p_lower and ("closing" in p_lower or "key takeaways" in p_lower)) \
       or "writing lesson 5" in p_lower:
        return _mock_closing(prompt)

    if "course body" in p_lower or "course_body" in p_lower or ("lesson 1 of 5" in p_lower and "course overview" in p_lower):
        return _mock_course_body(prompt)

    # Standalone Claims Lesson (App 2). Matches MagMutual claims-lesson template:
    # Headline → Summary → Key drivers → Advice → Specialties → The Case →
    # Patient outcome → Allegations → Legal Disposition → Peer Review →
    # Best Practices.
    if ("magmutual's exact template" in p_lower or "claims lesson template" in p_lower
        or "key drivers" in p_lower and "advice for providers" in p_lower):
        return textwrap.dedent("""
        # When a Single Troponin Isn't Enough

        ## Summary
        A man in his late 50s with hypertension presented to an emergency department with substernal chest pressure. After a single ECG and a single negative troponin, he was diagnosed with musculoskeletal pain and discharged. Two days later he suffered a major myocardial infarction with permanent reduction in cardiac function.

        ## Key drivers
        - Premature diagnostic closure on a single non-classical ECG and a single negative troponin.
        - Failure to order serial cardiac evaluation in a patient with classic risk factors.
        - Documentation that did not reflect the differential considered or the reasoning behind discharge.
        - No closed-loop communication with primary care for an unresolved chest-pain workup.

        ## Advice for Providers
        - Apply the HEART score (or your institution's equivalent) for every chest-pain presentation and document the score and the reasoning.
        - Order serial troponins per institutional protocol; document each result review.
        - Re-examine and re-evaluate the patient before discharge whenever any element of the workup was non-classical.
        - Document the differential considered, what was excluded, and the disposition rationale.
        - When deferring evaluation to outpatient, schedule the follow-up and confirm receipt with the receiving clinician.

        ## Advice for Administrators
        - Implement a standardized chest-pain order set with EHR alerts for incomplete cardiac workup at discharge.
        - Build a closed-loop communication workflow from the ED to primary care for any abnormal finding deferred outpatient.
        - Use bounce-back review to identify near-miss patterns and feed them back to the clinical team.
        - Align triage scoring to weigh trajectory of vital signs, not single snapshots.

        ## Primary provider specialty
        Emergency Medicine.

        ## Other provider specialties involved
        Cardiology (consultation declined), Primary Care (follow-up not scheduled).

        ## The Case

        **Presenting clinical conditions:** Substernal chest pressure radiating to the left arm; intermittent symptoms over several hours; mildly elevated blood pressure.
        **Procedures:** 12-lead ECG (single); high-sensitivity troponin (single); no serial assessments.
        **Final diagnosis:** Acute myocardial infarction with proximal LAD occlusion; cardiogenic shock at re-presentation.
        **Degree of injury:** Permanent reduction in cardiac function (significantly reduced ejection fraction).

        **Initial presentation**
        Patient reported substernal chest pressure rated 7/10 radiating to the left arm. Vitals: BP 165/95, HR 98, RR 18. Past medical history significant for hypertension and a 25 pack-year smoking history. Symptoms had been intermittent for several hours.

        **First evaluation**
        ECG was read as unchanged from a prior tracing. A single high-sensitivity troponin returned below the assay limit. The emergency physician documented a presumed musculoskeletal diagnosis without recording the differential or the reasoning behind exclusion of ACS. Serial troponins were not ordered.

        **Discharge**
        The patient was discharged home with a recommendation to follow up with primary care. Return precautions were given verbally. No closed-loop communication with primary care occurred. No outpatient cardiac workup was scheduled.

        **Two days later**
        The patient returned via EMS in cardiogenic shock. ECG showed a STEMI; cardiac catheterization revealed a proximal LAD occlusion. He survived but with significantly reduced ejection fraction.

        ## Patient outcome
        The patient survived but with permanent cardiac dysfunction. Functional capacity is significantly reduced, requiring guideline-directed medical therapy and lifestyle modifications. The patient experienced anxiety related to subsequent cardiac symptoms and missed several months of work.

        ## Allegations
        The plaintiff alleged that the emergency medicine physician failed to perform serial cardiac evaluation in a patient with risk factors and ongoing chest pain, inadequately documented the differential and the reasoning behind the discharge decision, and failed to arrange timely outpatient cardiac evaluation.

        ## Legal Disposition of Claim
        The case was settled with a high six figure settlement.

        Cases like this are typically open for 18-24 months. Approximately 8% of the time they proceed to trial. Approximately 65% of the time the case is closed without indemnity. When indemnity is paid, it historically has ranged from 30-45% of policy limits.

        ## Peer Review Commentary
        Reviewing physicians focused on three issues. First, the standard of care in chest-pain evaluation requires more than a single point assessment when the patient carries cardiovascular risk factors and reports ongoing symptoms. Serial troponins and a structured risk score are the expected minimum, and the chart did not reflect either.

        Second, the documentation gap was as consequential as the clinical gap. The chart did not record the differential the clinician considered, the reasoning behind exclusion of ACS, or the discussion with the patient about return precautions. Defensibility hinges on this kind of contemporaneous record.

        Third, the discharge handoff was a missed safety net. A single phone call or order placing the patient on an outpatient cardiac workup would have created the closed-loop the case lacked. The system did not require it; the clinician did not initiate it.

        ## Best Practices to Mitigate Risk

        ### Clinical contributors
        - Use a structured chest-pain risk score (HEART or equivalent) for every presentation.
        - Order serial troponins and repeat ECGs when symptoms persist or evolve.
        - Document the differential considered, the reasoning for exclusion, and the disposition rationale.
        - Re-examine the patient before discharge whenever any element of the workup was non-classical.
        - Treat patients with diabetes, women, and elderly patients with a higher index of suspicion for atypical ACS.

        ### Operational contributors
        - Standardized chest-pain order sets that prompt for the second troponin and a HEART score.
        - EHR alerts for incomplete cardiac workup at discharge.
        - A documented closed-loop communication workflow from the ED to primary care or cardiology.
        - Quarterly review of bounce-back admissions tied back to the original ED clinician for learning.
        - Triage scoring that flags trajectory of vital signs across rechecks rather than treating each as an independent snapshot.
        """).strip()

    # (Removed: this assessment branch was moved earlier in the dispatcher
    # so it doesn't get shadowed by the course-body branch.)

    # (Removed: duplicate fallback was here. The structured case-study branch
    # above (matching "structured case study"/"vertical timeline"/"pivotal
    # moments") now handles the standalone claims-lesson path.)

    if "rank" in p_lower or "candidate" in p_lower or ("claim" in p_lower and "select" in p_lower):
        return textwrap.dedent("""
        ## Top Candidate Claims

        | Rank | Claim ID | Risk Driver | Teaching Value | Confidence |
        |------|----------|-------------|----------------|------------|
        | 1 | CLM-1042 | Missed/delayed diagnosis of ACS | High: classic atypical presentation, multiple decision points, clear closed-loop failure | 0.91 |
        | 2 | CLM-2188 | Failure to recognize sepsis in triage | High: vital sign drift over 4 hours, missed escalation | 0.86 |
        | 3 | CLM-1577 | Anticoagulation reversal delay | Medium: documentation thin in places but pattern is generalizable | 0.74 |
        | 4 | CLM-3301 | Wrong-site procedure | Medium: pre-procedure timeout was performed but did not include correct surgeon | 0.69 |
        | 5 | CLM-2902 | Medication reconciliation gap on admission | Medium: cascading effect, useful for IM audience | 0.62 |

        **Recommended pick:** CLM-1042, strongest alignment to the Emergency Medicine Risk Brief and the most teachable cascade of decisions.
        """).strip()

    # Quick-action edits, echo back the current section with a small marker
    # so the demo shows movement. build_edit_section emits XML-ish tags now
    # (<current_section>...</current_section>), so we extract from those.
    if ("<user_instruction>" in prompt or "user_instruction" in p_lower) \
       and ("<current_section>" in prompt or "current_section" in p_lower):
        marker = "\n\n_(mock revision applied, connect Cortex to see real edits)_"
        # Try the new XML form first
        m = re.search(r"<current_section>\s*(.*?)\s*</current_section>",
                       prompt, re.S)
        if m:
            return m.group(1).strip() + marker
        # Legacy form fallback
        idx = prompt.find("CURRENT SECTION (revise this)")
        if idx > -1:
            tail = prompt[idx:].split("---", 1)[0]
            stripped = "\n".join(tail.splitlines()[2:]).strip()
            return stripped + marker
        return "Updated content (mock).\n\n" + marker

    return f"[Mock Cortex output]\n\nThis is a placeholder response. Configure Snowflake in `.streamlit/secrets.toml` for real output.\n\nPrompt preview:\n\n{prompt[:400]}{'...' if len(prompt) > 400 else ''}"


# ---------------------------------------------------------------------------
# Topic-varied mock case study generator
#
# We vary the patient demographics, timeline shape, allegations, outcome,
# AND the pause-and-reflect question so each of the 5 case studies a
# course generates is distinct. Real Cortex receives the variety
# requirement in the prompt and produces fresh stories per topic; the
# mock just rotates through a fixed library of vignettes so the demo
# also shows differentiation.
# ---------------------------------------------------------------------------


_DRIVER_INTROS = [
    "This loss driver shows up repeatedly in tagged claims for the specialty, with a recurring pattern of contributing factors. The case below shows how the driver plays out at the bedside and the decision points where teams can intervene.",
    "Closed claims tagged to this driver share a common shape, a missed cue, a documentation gap, or a workflow shortcut that compounds. The case below illustrates how the pattern unfolds and where the trajectory could have shifted.",
    "When this driver appears in a closed claim, it almost never appears alone, it interacts with handoff, documentation, and protocol-bypass behaviors. The case below traces those interactions through one real scenario.",
    "This loss driver is one of the highest-leverage levers your team has, because the failure modes are systemic and the corrections are concrete. The case below shows the specific decision points to target.",
    "Tagged claims for this driver tell a consistent story: the standard of care was clear, the protocol existed, and the deviation was small but consequential. The case below shows where teams most often slip.",
]

_CASE_OPENERS = [
    "A real-world scenario anchored to **{topic}**.",
    "A closed claim that turns on **{topic}** at multiple decision points.",
    "A scenario where **{topic}** was the single highest-leverage corrective opportunity.",
    "A case where the trajectory could have shifted at any of three points tied to **{topic}**.",
    "A scenario in which protocol drift around **{topic}** drove the outcome.",
]

# Five structural skeletons (timeline shape + outcome). The clinical
# specifics are filled in at runtime from the topic + driver so the same
# skeleton works for any specialty (anesthesiology, OB, EM, …) and the
# resulting cases stay topically aligned.
_CASE_SKELETONS = [
    {
        "demo": "A patient in their late 50s",
        "tline_shape": [
            ("Initial presentation",
             "Patient arrived with concerns relevant to {driver_lc}. Triage"
             " documented the chief complaint and vitals; the team began an"
             " initial assessment focused on the most likely working"
             " diagnosis without flagging {topic_lc} on the differential."),
            ("First evaluation",
             "An initial workup was completed and the team interpreted the"
             " results as reassuring. The clinical picture relevant to"
             " {topic_lc} was not yet recognised, and the chart did not"
             " document any explicit consideration of it as part of the"
             " differential."),
            ("Disposition",
             "The team finalised the plan based on the available data and"
             " moved the patient to the next phase of care. Return"
             " precautions were given verbally; a written rationale for"
             " the disposition was not entered into the record."),
            ("Two days later",
             "Patient returned with a complication directly attributable"
             " to {topic_lc}. Repeat workup at the second presentation"
             " revealed findings that, in retrospect, would have been"
             " catchable on serial assessment at the index visit."),
        ],
        "outcome":
            "The case was settled in the high six figures. Defensibility"
            " was undermined less by the clinical decision itself than by"
            " the absence of a documented rationale for not pursuing"
            " {topic_lc} at the index visit.",
        "reflect_template":
            "When the early signals point one direction but {topic_lc} is"
            " in play, what would prompt your team to pause the disposition"
            " and re-evaluate? What would have to be documented for an"
            " outside reviewer to reach the same disposition decision your"
            " team did?",
    },
    {
        "demo": "An older patient with multiple comorbidities",
        "tline_shape": [
            ("Triage",
             "Vitals were stable on arrival and the leading working"
             " diagnosis was unrelated to {topic_lc}. The patient's"
             " comorbidity profile placed them in a higher-risk band, but"
             " the triage note did not flag those risk factors as"
             " modifiers of the differential."),
            ("Workup",
             "Initial assessment did not document {topic_lc} as part of"
             " the differential. The reasoning behind narrowing the"
             " differential to the leading diagnosis was not recorded,"
             " and the high-risk demographic context was not addressed."),
            ("Disposition",
             "Patient was moved to the next phase of care under the"
             " working diagnosis with primary-care follow-up. The"
             " disposition plan did not include explicit safety-netting"
             " for {topic_lc}; closed-loop confirmation with the next"
             " care team was not documented."),
            ("Bounce-back at 36 hours",
             "Patient returned with a complication tied to {topic_lc}"
             " that had been missed earlier. Re-evaluation at the second"
             " visit identified the diagnosis quickly, but the delay had"
             " already shaped the trajectory."),
        ],
        "outcome":
            "The case was settled in the low seven figures. The plaintiff's"
            " expert review centred on the absence of documentation that"
            " {topic_lc} had been actively considered at the index visit"
            " given the patient's risk profile.",
        "reflect_template":
            "Atypical and high-risk patients often hide {topic_lc} behind a"
            " more common-looking diagnosis. What in your workflow forces"
            " an explicit consideration of {topic_lc} before disposition,"
            " and what does that consideration look like when it makes it"
            " into the chart?",
    },
    {
        "demo": "A working-age patient with relevant risk factors",
        "tline_shape": [
            ("Initial workup",
             "First evaluation produced findings that required clarification"
             " but were treated as benign. The patient's risk factors were"
             " documented in the social/medical history but not connected"
             " to the differential for {topic_lc}."),
            ("Handoff at shift change",
             "Verbal handoff occurred without explicit flagging of the"
             " unresolved issue tied to {topic_lc}. The receiving clinician"
             " inherited the disposition plan but not the off-going"
             " clinician's concern; pending workup was not surfaced in"
             " writing."),
            ("Discharge / next phase",
             "Patient moved to the next phase under the inherited plan."
             " Closed-loop confirmation with the outpatient receiving team"
             " was not documented; the patient was instructed to follow up"
             " with primary care without a specific timeframe tied to the"
             " unresolved finding."),
            ("Forty-eight hours later",
             "The complication associated with {topic_lc} surfaced and"
             " required emergency intervention. By the time the diagnosis"
             " was made, the window for the most effective treatment had"
             " narrowed considerably."),
        ],
        "outcome":
            "The case was settled in the high six figures. The handoff"
            " conversation was identified by both expert witnesses as the"
            " single highest-leverage corrective opportunity in the case.",
        "reflect_template":
            "When you hand off a patient whose workup tied to {topic_lc} is"
            " incomplete, what specific information do you transfer in"
            " writing, not just verbally, so the receiving clinician"
            " inherits your concern, not just your diagnosis? What does"
            " your handoff template require, and what does it allow"
            " teams to skip?",
    },
    {
        "demo": "A patient in their early 60s",
        "tline_shape": [
            ("First visit",
             "Initial assessment ruled out the leading concern but did not"
             " address {topic_lc} explicitly. The chart documented the"
             " ruled-out diagnoses but not the reasoning behind why other"
             " entries on the differential were excluded."),
            ("Return visit",
             "Patient returned with worsening symptoms; the relationship to"
             " {topic_lc} became clearer on re-examination. The repeat"
             " assessment captured findings that had been present, in"
             " retrospect, at the first visit but were not documented."),
            ("Second discharge",
             "Patient was briefly held for observation, then released"
             " with an outpatient plan that did not account for"
             " {topic_lc}. The discharge note attributed the symptoms to"
             " a non-cardiac / non-acute alternative without a clear"
             " differential."),
            ("Late event",
             "Patient suffered a serious complication directly attributable"
             " to {topic_lc} before the outpatient plan could execute. The"
             " plaintiff's expert review highlighted both visits as missed"
             " opportunities."),
        ],
        "outcome":
            "The case was resolved through a confidential settlement. The"
            " repeated under-documentation of the differential, across two"
            " separate visits, was the single most damaging element of the"
            " defense's review.",
        "reflect_template":
            "For the moments where your chart will be your defense, what"
            " would a future expert reviewer need to see in your note that"
            " would let them say with confidence that {topic_lc} was"
            " actively considered and reasoned through? What's the"
            " minimum documentation that survives the comparison to a"
            " plaintiff's framing of the same facts?",
    },
    {
        "demo": "A patient in their 40s",
        "tline_shape": [
            ("Initial assessment",
             "A finding directly relevant to {topic_lc} was documented but"
             " its implications were minimised in the assessment note. The"
             " clinician interpreted the finding as benign without"
             " documenting why other interpretations had been ruled out."),
            ("Order set deviation",
             "The institution's standard pathway covering {topic_lc} was"
             " not used. Documentation did not justify the deviation; no"
             " clinical contraindication or workflow exception was recorded"
             " in the chart."),
            ("Discharge / next phase",
             "Patient progressed under a non-standard plan; an EHR alert"
             " tied to {topic_lc} was overridden without rationale. The"
             " override defaulted to the most permissive option without"
             " any free-text justification."),
            ("Twelve hours later",
             "Patient suffered a major complication. The override of the"
             " {topic_lc} alert featured prominently in the plaintiff's"
             " expert review, alongside the unjustified deviation from"
             " the standard pathway."),
        ],
        "outcome":
            "The case was settled in the low seven figures. The combination"
            " of order-set deviation and unjustified alert override was"
            " characterised by the defense expert as the highest-impact"
            " corrective opportunity in the case.",
        "reflect_template":
            "When was the last time your team overrode a protocol or alert"
            " tied to {topic_lc}? What documentation would have to exist in"
            " the chart for that override to be defensible to a reviewer"
            " who has only the record to read, not the conversation that"
            " happened in the room?",
    },
]


def _mock_closing(prompt: str) -> str:
    """Topic-and-driver-aware mock closing, grounded in playbook factors.

    Each takeaway is derived from a contributing factor that's actually
    addressed in the playbook (the same factors that drove Lesson 3's
    case studies), not invented best-practice prose. We pull the
    contributing-factor titles from the prompt's PLAYBOOK · slices,
    then synthesise a one-paragraph practice-change takeaway per factor.
    """
    driver = ""
    m = re.search(r"#\s+RISK DRIVER\s*\n+([^\n]+)", prompt)
    if m:
        driver = m.group(1).strip()
    specialty = ""
    m = re.search(r"#\s+SPECIALTY\s*\n+([^\n]+)", prompt)
    if m:
        specialty = m.group(1).strip()
    drv = driver or "this driver"
    drv_lc = drv.lower()
    spec_lc = (specialty or "your specialty").lower()

    # Extract the contributing-factor titles MM authored advice for.
    # The closing prompt receives PLAYBOOK · CLINICAL_DIAGNOSTIC,
    # PLAYBOOK · CLINICAL_TREATMENT, etc. Each section lists one or
    # more "Failure to X" / "Error in Y" titles followed by a
    # "Contributing action or omission:" line.
    factor_titles: list[str] = []
    for sec_match in re.finditer(
        r"#\s+PLAYBOOK\s*·\s*([A-Z_]+)\s*\n+([\s\S]+?)(?=\n#\s|\Z)", prompt
    ):
        body = sec_match.group(2)
        # Allow the first title to sit at section start (no preceding \n)
        # AND allow blank lines between the title and the "Contributing
        # action or omission:" marker (the brief uses both layouts).
        for line_match in re.finditer(
            r"(?:^|\n)([^\n]+)\n[\s\n]*Contributing action or omission\s*:",
            body,
        ):
            title = line_match.group(1).strip()
            # Skip empty matches and skip lines that themselves look like
            # the marker (false positives from regex backtracking).
            if (title and title not in factor_titles
                and "contributing action" not in title.lower()):
                factor_titles.append(title)
    # Cap at 5 (matches MM's "five takeaways" pattern). If we somehow
    # have fewer than 2 factors, fill with structural takeaways that
    # don't make clinical claims.
    factor_titles = factor_titles[:5]

    if factor_titles:
        takeaway_lines = []
        for i, title in enumerate(factor_titles, start=1):
            takeaway_lines.append(
                f"{i}. **{title}.** This contributing factor recurs across"
                f" closed claims for {drv_lc}. Translate it into a workflow"
                f" change your team can adopt this week: identify the"
                f" decision point in your current process where the failure"
                f" mode would most likely surface, and add the one"
                f" documentation step or workflow trigger that would catch"
                f" it before disposition."
            )
        # If we have fewer than 5 factors, pad with structural items
        # (no clinical claims, just process-change framing).
        while len(takeaway_lines) < 5:
            i = len(takeaway_lines) + 1
            takeaway_lines.append(
                f"{i}. **Translate one playbook strategy into your order"
                f" set.** Pick a single mitigation strategy from the"
                f" playbook sections above and embed it in your"
                f" institutional order set or EHR template so it survives"
                f" individual staffing changes."
            )
        takeaways_md = "\n".join(takeaway_lines)
    else:
        # Brief lacks structured factor titles — fall back to a generic
        # process-change takeaway rather than invent clinical specifics.
        takeaways_md = (
            f"1. **Translate the playbook into your team's workflow.** Each"
            f" mitigation strategy in the sections above is most useful when"
            f" it's embedded in an order set, an EHR template, or a"
            f" handoff template that the team uses by default."
        )

    return (
        f"## Lesson 5 of 5: Closing\n\n"
        f"The case studies in Lesson 3 trace the contributing factors MM's"
        f" risk playbook addresses for {drv_lc}. Each takeaway below names"
        f" one of those factors and translates it into a practice change"
        f" your team can act on. Pick the one that would change the most"
        f" about how {drv_lc} is managed in {spec_lc} and build the"
        f" workflow change around it.\n\n"
        f"### Key takeaways\n"
        f"{takeaways_md}\n\n"
        f"### Pause and reflect\n"
        f"Of the takeaways above, which one would change the most for"
        f" your team if you adopted it next month, and what is the"
        f" smallest first step you can take this week to begin? Concrete"
        f" first steps look like adding one checkbox to your existing"
        f" order set, modifying one EHR template, or adding one item to"
        f" your departmental peer-review queue.\n\n"
        f"### What's next\n"
        f"Pull a representative sample of recent {spec_lc} cases tied to"
        f" {drv_lc} and audit how the differential, the assessment"
        f" reasoning, and the disposition rationale were documented in"
        f" the chart. Use the MagMutual risk consultation line and"
        f" online toolkit for ongoing reinforcement, bounce-back review,"
        f" and follow-up support as you implement the changes you've"
        f" chosen from this course.\n"
    )


def _extract_section(prompt: str, header: str) -> str:
    """Pull a single labeled section out of the assembled prompt.

    Strips any embedded markdown horizontal rules (`---`) from the
    extracted body — those come from the brief's own internal section
    breaks and would render as stray `---` divider lines in the course.
    """
    m = re.search(
        rf"#\s+{re.escape(header)}\s*\n+([\s\S]+?)(?=\n#\s|\Z)",
        prompt,
    )
    if not m:
        return ""
    body = m.group(1).strip()
    # Drop standalone HR lines that would render as stray divider rules
    # (and any double-newline-double-newline gaps left behind).
    body = re.sub(r"^[\s]*-{3,}[\s]*$", "", body, flags=re.M)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return body


def _mock_course_body(prompt: str) -> str:
    """Topic-and-driver-aware mock course body.

    Pulls DRIVER + SPECIALTY + the playbook brief slices (Mitigating
    Your Risk intro, Clinical-vs-admin breakdown, Adverse outcomes) AND
    the playbook factor titles + stats percentages from the assembled
    prompt, so Lesson 2 reflects ONLY data we actually have. No
    invented frequency / severity / dollar figures.
    """
    driver = ""
    m = re.search(r"#\s+RISK DRIVER\s*\n+([^\n]+)", prompt)
    if m:
        driver = m.group(1).strip()
    specialty = ""
    m = re.search(r"#\s+SPECIALTY\s*\n+([^\n]+)", prompt)
    if m:
        specialty = m.group(1).strip()

    # Brief slices the prompt builder already injected as labeled sections.
    mitigating_intro = _extract_section(prompt, "MITIGATING YOUR RISK (intro from playbook)")
    breakdown_prose  = _extract_section(prompt, "CLINICAL VS ADMINISTRATIVE BREAKDOWN (from playbook)")
    adverse_prose    = _extract_section(prompt, "ADVERSE OUTCOMES (from data)")

    # Top contributing factors, Lines like "- 17.5%  Failure to ..."
    factors: list[tuple[float, str]] = []
    fblock = re.search(
        r"#\s+TOP CONTRIBUTING FACTORS.*?\n([\s\S]+?)(?=\n#\s|\Z)",
        prompt,
    )
    if fblock:
        for line in fblock.group(1).splitlines():
            mm = re.match(r"\s*-\s*([0-9.]+)%\s+(.+)$", line)
            if mm:
                factors.append((float(mm.group(1)), mm.group(2).strip()))
    factors.sort(reverse=True)
    top_factor_labels = [lbl for _, lbl in factors[:5]]

    # Learning objectives
    los: list[str] = []
    lo_block = re.search(
        r"#\s+LEARNING OBJECTIVES\s*\n+([\s\S]+?)(?=\n#\s|\Z)", prompt
    )
    if lo_block:
        for ln in lo_block.group(1).splitlines():
            ln = ln.strip()
            if ln.startswith("- "):
                los.append(ln[2:].strip())
    if not los:
        los = [
            f"Recognize the clinical features and patient populations most relevant to {driver or 'this driver'}.",
            f"Apply structured decision-support tools tied to {driver or 'the driver'} that reduce premature closure.",
            "Communicate, document, and hand off in a way that protects the patient and supports defensibility.",
        ]

    drv = driver or "this risk driver"
    spec = specialty or "your specialty"
    drv_lc = drv.lower()
    spec_lc = spec.lower()
    objectives_md = "\n".join(f"{i+1}. {lo}" for i, lo in enumerate(los))

    # Why this matters, pull the FULL playbook intro paragraph so this
    # section reads at MM's depth rather than a skim.
    if mitigating_intro:
        why_md = mitigating_intro.strip()
    else:
        why_md = (
            f"Closed claims tagged to {drv_lc} are a recurring source of"
            f" liability for {spec_lc} teams. They typically arise not from"
            f" a single dramatic mistake but from a sequence of small"
            f" decisions, a missed cue at intake, an incomplete workup,"
            f" a verbal handoff that leaves the next clinician without the"
            f" full picture. The recurring patterns below show where the"
            f" highest-leverage corrective opportunities sit, and the"
            f" mitigation strategies in Lesson 3 translate each of them"
            f" into a concrete change you can put in front of your team"
            f" this week."
        )

    # Clinical vs. admin contributors, pull the FULL breakdown section
    # from the brief (typically 5-8 sentences with the % split AND the
    # contextual prose MM authored around it).
    if breakdown_prose:
        breakdown_md = breakdown_prose.strip()
    else:
        breakdown_md = (
            f"Closed claims tagged to {drv_lc} typically split between"
            f" clinical decision-making errors and administrative"
            f" breakdowns. Clinical contributors include diagnostic"
            f" closure, treatment selection, and procedural technique."
            f" Administrative contributors include communication between"
            f" providers, communication with the patient, documentation,"
            f" and adherence to standardized protocols. Both categories"
            f" deserve focused mitigation effort, most claims involve at"
            f" least one of each, and the cases that go to settlement"
            f" usually have multiple."
        )

    # Most frequent allegations, derive from playbook factor titles
    # with each allegation written as a 2-sentence behavior-tied
    # statement (not a single-phrase label).
    allegation_phrases = [
        ("surfaced repeatedly across closed claims for this driver. Defensibility"
         " hinges on whether the chart documents that the team considered it"
         " and ruled it in or out with reasoning."),
        ("appeared in the majority of closed claims for this driver, often"
         " compounded by a documentation gap that made the reasoning hard to"
         " reconstruct after the fact."),
        ("recurred across the cases in this driver's claim file, frequently"
         " in combination with a handoff in which the inheriting clinician"
         " did not receive the full clinical context."),
        ("tied to a clinically silent but legally consequential decision"
         " point, small enough to feel routine in the moment, large enough"
         " to anchor the plaintiff's expert review later."),
    ]
    if top_factor_labels:
        allegations_md = "\n".join(
            f"- **{lbl}** {allegation_phrases[i % len(allegation_phrases)]}"
            for i, lbl in enumerate(top_factor_labels[:4])
        )
    else:
        allegations_md = (
            f"- Allegations cluster around documentation, communication, and"
            f" decision-making tied to {drv}, frequently appearing together"
            f" rather than in isolation."
        )

    # Degree of injury, pull adverse-outcome prose from brief and
    # frame what it means for defensibility.
    if adverse_prose:
        injury_core = " ".join(re.split(r"(?<=[.!?])\s+", adverse_prose)[:3]).strip()
        injury_md = (
            f"{injury_core} The most severe outcomes drive the high-severity"
            f" tail of the distribution and disproportionately shape what"
            f" plaintiffs and defense experts focus on. Communication gaps"
            f" with the patient and family, separate from the clinical"
            f" outcome itself, often shape whether a claim is filed at all."
        )
    else:
        injury_md = (
            f"Outcomes range from temporary harm and additional procedures"
            f" through permanent functional loss in the most severe claims."
            f" The most severe outcomes drive the high-severity tail of the"
            f" distribution and disproportionately shape what plaintiffs and"
            f" defense experts focus on. Communication gaps with the patient"
            f" and family, separate from the clinical outcome itself,"
            f" often shape whether a claim is filed at all."
        )

    return (
        f"# Reducing Liability in {spec}: {drv}\n\n"

        f"## Lesson 1 of 5: Course Overview\n\n"
        f"### What You'll Learn\n"
        f"This course walks through how {drv_lc} shows up in closed"
        f" {spec_lc} claims and the concrete clinical and operational"
        f" levers that move outcomes. You will see real-world case"
        f" patterns drawn from MagMutual's closed-claim file, the"
        f" specific contributing factors that recur across them, and the"
        f" mitigation strategies tied to each, both clinical (what the"
        f" team does at the bedside) and non-clinical (what the system"
        f" requires, the chart documents, and the handoff transfers in"
        f" writing). The lessons are sized so a clinician can cover the"
        f" material in roughly 60 minutes, about half spent reading the"
        f" substantive content, the rest spent reflecting on cases and"
        f" answering the post-test. By the end of the course you'll"
        f" leave with three to five concrete practice changes you can"
        f" put in front of your team this week, each tied to a specific"
        f" decision point that has anchored claims for {drv_lc} in the"
        f" past. The takeaways aren't theoretical: every strategy in"
        f" Lesson 3 is mapped directly to a contributing factor that"
        f" appears in MagMutual's tagged claim data, so the time you"
        f" invest reading the course is time spent on the failure modes"
        f" most likely to surface in your own practice.\n\n"
        f"### Objectives\n"
        f"{objectives_md}\n\n"

        f"---\n\n"

        f"## Lesson 2 of 5: Loss Trends\n\n"
        f"### Why this matters\n"
        f"{why_md}\n\n"
        f"### Clinical vs. administrative contributors\n"
        f"{breakdown_md}\n\n"
        f"### Most frequent allegations\n"
        f"{allegations_md}\n\n"
        f"### Degree of injury\n"
        f"{injury_md}\n\n"
        f"### Pause and reflect\n"
        f"When did your team last review a near-miss or closed claim"
        f" tied to {drv_lc}, and what specifically changed in your"
        f" workflow as a result? If nothing changed, or if the change"
        f" was scoped to a single individual rather than the team,"
        f" what would it take to translate the lesson into a workflow"
        f" or order-set update that survives the next staffing change?\n\n"

        f"---\n\n"

        f"## Lesson 3 of 5: Key loss drivers & risk reduction strategies\n\n"
        f"The contributing factors below, each addressed in MagMutual's"
        f" risk playbook, recur across closed claims for {drv_lc}."
        f" Each section walks through one factor with a case scenario"
        f" (timeline, allegations, outcome, pause-and-reflect) and the"
        f" clinical and non-clinical strategies that would have changed"
        f" the trajectory. The factors are ordered by their share of"
        f" tagged contributing factors in the claim file, so the"
        f" highest-leverage corrective opportunities are addressed"
        f" first.\n"
    )


def _topic_strategies(topic: str, cs_idx: int) -> tuple[list[str], list[str]]:
    """Return per-case clinical + non-clinical strategy bullets that are
    grounded in the topic itself, not in a hardcoded specialty.

    The bullets vary by `cs_idx` so cases 1..5 don't share strategy text
    even when the topic is the same. Each bullet names the topic so the
    learner sees a clear connection to the case above.
    """
    t = topic.strip().rstrip(".")
    # Five rotating templates per side, keyed by cs_idx.
    clinical_templates = [
        [
            f"Build a structured trigger that forces explicit reconsideration of {t} at every disposition decision.",
            f"Order the targeted assessment(s) tied to {t} on serial timepoints rather than a single point in time.",
            f"Re-evaluate the patient before any handoff or discharge whenever {t} remains on the differential.",
        ],
        [
            f"Maintain a heightened index of suspicion for {t} in high-risk demographic populations even when the leading diagnosis points elsewhere.",
            f"Force an explicit rule-out workup for {t} whenever any high-risk feature is present, regardless of the working diagnosis.",
            f"Document the differential considered AND the specific reasoning for excluding {t}, not just the leading diagnosis.",
        ],
        [
            f"Re-evaluate any patient with unresolved findings related to {t} at the moment of handoff before the off-going provider signs out.",
            f"Treat known risk factors for {t} as reasons to broaden, not narrow, the differential.",
            f"Repeat targeted testing tied to {t} before discharge whenever the clinical picture is evolving.",
        ],
        [
            f"Order the appropriate assessment for {t} on every patient where the chief complaint or comorbidity is consistent with the driver.",
            f"Treat a single negative result as insufficient to rule out {t} when the clinical trajectory is worsening.",
            f"Escalate to inpatient observation rather than outpatient deferral when symptoms tied to {t} have evolved between visits.",
        ],
        [
            f"Treat reassuring local findings as insufficient to rule out {t} on their own, the standardized workup must run.",
            f"Treat any positive component of the {t} risk score as a trigger for continued observation, not a confirmation of low risk.",
            f"Run the full standardized pathway whenever any element of the {t} workup is positive.",
        ],
    ]
    nonclinical_templates = [
        [
            f"Make 'workup for {t} complete' an explicit checkbox in the order set so dispositioning before it auto-flags.",
            f"Capture written return precautions for {t} with a teach-back signature; verbal-only instructions are a recurring claim driver.",
            f"Audit a sample of discharges weekly and review compliance with the {t} protocol with the team.",
        ],
        [
            f"Build a one-tap template into the EHR that pre-populates the differential considered and the rule-out reasoning for {t}.",
            f"Run a recurring case-review on bounce-backs to surface the cognitive patterns most often involved in missed {t}.",
            f"Tag returns within 72 hours for automatic peer review when the index visit ruled out a {t}-related cause.",
        ],
        [
            f"Use a written I-PASS-style handoff template that requires explicit fields for pending workup tied to {t} and contingency plans.",
            f"Implement closed-loop referral tracking: the discharging team confirms outpatient receipt within 48 hours when {t} is on the plan.",
            f"Require the receiving provider to co-sign the disposition plan when an unresolved finding tied to {t} is in play.",
        ],
        [
            f"Flag any second visit within 30 days for the same complaint as a 'return-precaution failure' for peer review whenever {t} is in the differential.",
            f"Document a rule-out narrative on every relevant discharge: what was considered, what was excluded, and why for {t}.",
            f"Reserve outpatient deferrals for patients whose serial workup for {t} was clearly negative AND symptoms have resolved.",
        ],
        [
            f"Require a free-text justification field whenever a decision-support alert tied to {t} is overridden.",
            f"Audit alert-override rates monthly with the pathway owner to surface protocol drift around {t}.",
            f"Reserve order-set deviation for documented medical contraindications, not workflow convenience, when {t} is in scope.",
        ],
    ]
    idx = (cs_idx - 1) % len(clinical_templates)
    return clinical_templates[idx], nonclinical_templates[idx]


def _mock_case_study(*, topic: str, cs_idx: int,
                     driver: str = "", specialty: str = "") -> str:
    """Return a topic-and-driver-aware case-study block matching the
    MagMutual reference structure.

    Cases 1..5 each pull a different timeline shape, demographic frame,
    and outcome from `_CASE_SKELETONS`, then template the actual driver
    and topic into the clinical content so the case is topically aligned
    (e.g. an Anesthesiology · Airway Management course gets airway-
    relevant cases, not chest-pain ones).

    Per-case strategy bullets come from `_topic_strategies` so the
    Reducing-clinical-risks / Reducing-non-clinical-risks tabs are also
    distinct across cases AND tied to the topic by name.
    """
    skel = _CASE_SKELETONS[(cs_idx - 1) % len(_CASE_SKELETONS)]
    driver_lc = (driver or topic).strip().rstrip(".").lower()
    topic_lc = (topic or "the loss driver").strip().rstrip(".").lower()
    timeline = "\n\n".join(
        f"**{date}**\n{body.format(driver_lc=driver_lc, topic_lc=topic_lc)}"
        for date, body in skel["tline_shape"]
    )
    # Allegations are derived from topic + skeleton index so they vary
    # per case AND name the topic explicitly.
    allegation_templates = [
        [
            f"Failure to perform serial assessment for {topic_lc} when concerning features persisted.",
            f"Inadequate documentation of the differential and the reasoning around {topic_lc}.",
            f"Failure to arrange timely follow-up for the unresolved finding tied to {topic_lc}.",
        ],
        [
            f"Premature diagnostic closure on a non-classical presentation of {topic_lc}.",
            f"Failure to expand the differential to include {topic_lc} when atypical features were present.",
            f"Documentation did not reflect the differential considered or the reasoning behind discharge given {topic_lc}.",
        ],
        [
            f"Inadequate handoff communication leading to loss of clinical context around {topic_lc}.",
            f"Failure to close the loop on a high-risk outpatient referral related to {topic_lc}.",
            f"No documented re-evaluation of the differential at handoff despite ongoing concerns about {topic_lc}.",
        ],
        [
            f"Failure to document the reasoning behind exclusion of {topic_lc}.",
            f"Reliance on a single test result despite an evolving clinical picture suggestive of {topic_lc}.",
            f"Inadequate safety-netting for an outpatient workup of {topic_lc}.",
        ],
        [
            f"Bypassing the standardized pathway for {topic_lc} without documented justification.",
            f"Overriding an EHR alert tied to {topic_lc} without a recorded rationale.",
            f"Failure to escalate when the assessment placed the patient in an at-risk band for {topic_lc}.",
        ],
    ]
    allegations = "\n".join(f"- {a}"
                            for a in allegation_templates[(cs_idx - 1) % len(allegation_templates)])
    intro = _DRIVER_INTROS[(cs_idx - 1) % len(_DRIVER_INTROS)]
    opener = _CASE_OPENERS[(cs_idx - 1) % len(_CASE_OPENERS)].format(topic=topic)
    reflect = skel["reflect_template"].format(topic_lc=topic_lc, driver_lc=driver_lc)
    clinical_strats, nonclinical_strats = _topic_strategies(topic, cs_idx)
    clinical = "\n".join(f"- {s}" for s in clinical_strats)
    nonclinical = "\n".join(f"- {s}" for s in nonclinical_strats)
    # Patient sentence, names the driver explicitly so an Anesthesiology
    # course doesn't read like an EM course.
    driver_phrase = (driver or "the relevant condition").rstrip(".")
    medical_summary = (
        f"{skel['demo']} required care relating to {driver_phrase}. "
        f"The case illustrates how decisions tied to **{topic}** drove the outcome."
    )
    # NOTE: write the f-string flush-left (no textwrap.dedent). Multi-line
    # interpolations like {timeline} and {allegations} contain newlines
    # that lack the indent dedent expects, which previously left H3/H4
    # subheadings indented (and the renderer treated them as plain text).
    return (
        f"## Key loss driver: {topic}\n\n"
        f"{intro}\n\n"
        f"### Case study {cs_idx}\n\n"
        f"{opener}\n\n"
        f"#### Medical summary\n"
        f"{medical_summary}\n\n"
        f"#### Timeline\n"
        f"{timeline}\n\n"
        f"#### Allegations\n"
        f"{allegations}\n\n"
        f"#### Outcome\n"
        f"{skel['outcome']}\n\n"
        f"#### Pause and reflect\n"
        f"{reflect}\n\n"
        f"#### Risk reduction strategies for {topic}\n"
        f"Use each tab to explore strategies for reducing clinical and non-clinical risks tied to {topic}.\n\n"
        f"#### Reducing clinical risks\n"
        f"{clinical}\n\n"
        f"#### Reducing non-clinical risks\n"
        f"{nonclinical}\n"
    ).strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def complete(
    prompt: str,
    *,
    kind: Optional[str] = None,
    model: Optional[str] = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: Optional[float] = None,
    force_mock: bool = False,
) -> CortexResult:
    """Run a Cortex Complete call. Falls back to mock if Snowflake isn't connected.

    Pass `kind=...` to use the hardcoded model + temperature for that prompt
    kind (see MODELS / TEMPS dicts at the top of this module). Explicit
    `model=` / `temperature=` overrides take precedence when supplied —
    primarily for ad-hoc calls (status pings, debugging).
    """
    if model is None:
        model = model_for(kind) if kind else DEFAULT_MODEL
    if temperature is None:
        temperature = temp_for(kind) if kind else DEFAULT_TEMPERATURE

    t0 = time.time()
    text: Optional[str] = None
    mocked = True

    if not force_mock:
        text = _real_complete(prompt, model, max_tokens, temperature)
        if text is not None and text.strip():
            mocked = False

    if text is None or not text.strip():
        text = _mock_complete(prompt)
        mocked = True

    elapsed = time.time() - t0
    _telemetry["last_latency_s"] = elapsed
    _telemetry["last_model"] = model
    _telemetry["last_mocked"] = mocked
    _telemetry["last_kind"] = kind
    _telemetry["last_temperature"] = temperature
    # Truncate previews so the audit panel stays compact even on long
    # course-body prompts (~7 KB) and long responses.
    _telemetry["last_prompt_preview"] = (prompt or "")[:2000]
    _telemetry["last_response_preview"] = (text or "")[:2000]
    _telemetry["calls_total"] += 1
    if mocked:
        _telemetry["calls_mocked"] += 1
    else:
        _telemetry["calls_real"] += 1
    return CortexResult(text=text, model=model, mocked=mocked, elapsed_s=elapsed)


def is_connected() -> bool:
    return _try_get_session() is not None


# =====================================================================
# Confidence scorer (merged from confidence.py)
# =====================================================================

import json
import re
from dataclasses import dataclass
from typing import Optional

from cortex import complete
from prompts import build_confidence


@dataclass
class ConfidenceResult:
    grade: str  # 'A' .. 'F'
    publication_decision: str  # APPROVED | REQUIRES_REVISION | BLOCKED
    summary: str
    raw: dict
    mocked: bool


def confidence_score(generated_text: str, sources: list[str],
                     output_type: str = "course_generator") -> ConfidenceResult:
    """Grade `generated_text` against `sources`. Returns a ConfidenceResult."""
    prompt = build_confidence(generated_text, sources, output_type)
    res = complete(prompt, kind="confidence")
    parsed = _parse_json(res.text)
    return ConfidenceResult(
        grade=parsed.get("overall_grade", "C"),
        publication_decision=parsed.get("publication_decision", "REQUIRES_REVISION"),
        summary=parsed.get("summary", ""),
        raw=parsed,
        mocked=res.mocked,
    )


def _parse_json(text: str) -> dict:
    """Pull a JSON object out of a Cortex response, even if wrapped in fences/prose."""
    if not text:
        return {}
    # Try plain parse first
    try:
        return json.loads(text)
    except Exception:
        pass
    # Strip fenced code blocks
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        try:
            return json.loads(fence.group(1))
        except Exception:
            pass
    # Greedy curly-brace match as last resort
    obj = re.search(r"\{[\s\S]*\}", text)
    if obj:
        try:
            return json.loads(obj.group(0))
        except Exception:
            pass
    return {}
