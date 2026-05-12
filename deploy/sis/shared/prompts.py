"""All six prompts for the two apps.

Architecture:
- Reusable rule blocks live in `prompt_components.py` (DEID_RULES,
  FINANCIAL_RANGES, MM_VOICE, EDUCATIONAL_TONE, OUTPUT_DISCIPLINE). Edit
  there to update style globally.
- Each prompt below is a thin wrapper that composes those blocks with
  task-specific instructions and (where useful) one few-shot example.
- Builder functions (`build_*`) splice runtime data via section headers
  rather than `.format()`, so brace characters in the prompt body don't
  conflict with substitution.

Each prompt has a PROMPT_VERSION suffix so audit logs can tell when the
prompt changed. Bump it whenever you edit a prompt body.
"""
from __future__ import annotations

import json
from typing import Iterable

from .style_guide import STYLE_GUIDE
from .prompt_components import (
    DEID_RULES, FINANCIAL_RANGES, MM_VOICE, EDUCATIONAL_TONE,
    OUTPUT_DISCIPLINE, LENGTH_GUIDANCE, GROUNDING_RULES,
    compose, COMPONENTS_VERSION,
)

# Bump when any prompt body changes. The components version contributes too.
PROMPTS_VERSION = "2026-05-8"
PROMPT_VERSION = f"{PROMPTS_VERSION}+c{COMPONENTS_VERSION}"


# =============================================================================
# 1) PROMPT_COURSE_BODY  (Michelle, PLACEHOLDER, awaiting team prompt)
# =============================================================================
_COURSE_BODY_TASK = """
<role>
You are a senior medical educator writing a CME-style risk-mitigation
course body for licensed clinicians. (Note: this output is not certified
CME; treat it as educational content in CME format.)
</role>

<task>
Generate Lessons 1, 2, and 3 (intro + topic stubs) of MagMutual's
"Reducing Liability" 5-lesson format. Lesson 3's case studies, Lesson 4
(Assessment), and Lesson 5 (Closing) are generated separately, do NOT
include them.
</task>

<structure>
# Reducing Liability in [Specialty]: [Driver]

## Lesson 1 of 5: Course Overview

### What You'll Learn
[2-3 sentences framing the course around the risk driver.]

### Objectives
1. [Objective tied to the risk brief]
2. [Objective tied to mitigation strategies]
3. [Objective tied to lessons-from-claims]

---

## Lesson 2 of 5: Loss Trends

### Why this matters
[2-3 sentence paraphrase of the playbook's "Mitigating Your Risk" intro
— frame why this driver is a meaningful source of liability for the
specialty and what teams can do about it. Use the playbook prose as
the source of truth; don't invent statistics that aren't in the
PLAYBOOK section provided.]

### Clinical vs. administrative contributors
[2-3 sentences using the playbook's "Clinical and Administrative
Breakdowns" prose, quote the actual percentage split (e.g. 83%
clinical / 17% admin) when the playbook provides one. Frame what the
split means for where your team should focus mitigation effort.]

### Most frequent allegations
[3-5 bullet items derived from the playbook's named contributing
factors (the loss-driver titles addressed below in Lesson 3). Each
allegation is a complete behavior-tied sentence, not a category label.]

### Degree of injury
[2-3 sentences on outcome severity patterns drawn from the playbook
ADVERSE_OUTCOMES section. Avoid invented dollar amounts.]

### Pause and reflect
[One reflection question grounded in the driver, not generic.]

---

## Lesson 3 of 5: Key loss drivers & risk reduction strategies

[1-2 sentence intro paragraph (NO heading) telling the reader they will
see real case-style scenarios with specific clinical and non-clinical
strategies. NO topic stubs here, each "Key loss driver: Topic" section
plus its case study is generated separately and stitched in below.]
</structure>

<example>
A course on "Anticoagulation management errors" might cover 4 separate
loss drivers (med rec at admission, bridging plans, closed-loop
communication, renal-dose adjustments), each rendered as its OWN
"Key loss driver: X" section with a nested "Case study N" inside.
This template generates only the lead-in for Lesson 3; the per-driver
sections are produced by a separate prompt and slotted in afterwards.
</example>
""".strip()


# =============================================================================
# 1b) PROMPT_CLOSING, Lesson 5 of 5
# =============================================================================
_CLOSING_TASK = """
<role>
You are writing Lesson 5 of MagMutual's "Reducing Liability" course —
the Closing. Use sentence case for all sub-headings (matches MagMutual's
copy convention exactly).
</role>

<structure>
## Lesson 5 of 5: Closing

[Open with a 1-2 sentence wrap-up paragraph (NO heading) that frames the
takeaways as practice changes the learner can make tomorrow.]

### Key takeaways
Output a numbered markdown list with FIVE separate items. Numbered
"1.", "2.", "3.", "4.", "5." on five different lines. This is
non-negotiable — fewer than five items is a defect.

Each takeaway:
- Is its own single sentence on its own numbered line
- Names a DIFFERENT contributing factor from the PLAYBOOK / TOP
  CONTRIBUTING FACTORS sections
- Translates that factor into a concrete behavior change the learner
  can make this week (a specific action, a workflow change, a chart
  audit, an order-set tweak)
- Uses 25-45 words. Not one giant comprehensive paragraph.

ANTI-PATTERNS (do NOT do these):
- One long takeaway that "synthesizes everything" into a single bullet
- Three or four items instead of five
- A heading like "**Takeaway 1:**" inside a single paragraph (that's
  prose, not a numbered list)
- Sub-bullets nested under one parent item

### Pause and reflect
[One closing reflection question that prompts the reader to commit to a
specific change in their own practice.]

### What's next
[Two concise sentences. Sentence one names the next action (review your
team's last 10 charts, schedule a chart-review session, etc.). Sentence
two points the reader to MagMutual resources for ongoing reinforcement
(no specific URLs, keep it generic so we don't hard-code links.)]
</structure>

<example_key_takeaways>
For a course on Missed Diagnosis of Acute Myocardial Infarction:

1. Atypical-presentation populations (women, diabetics, the elderly) are
the single highest-leverage place to expand the differential — add a
forcing function to your chest-pain order set that flags these groups.
2. Document the differential AND the reasoning that ruled in or out
acute coronary syndrome at each disposition decision — a sentence in
the note today prevents a deposition tomorrow.
3. Serial troponin and serial ECG are not optional in the borderline
patient — write reminders into your EHR ordering pathway so the second
draw is queued at triage, not at recall.
4. Closed-loop handoff to the receiving service is the highest-impact
non-clinical change — adopt a structured handoff template that names
the differential, the pending workup, and the bounce-back criteria.
5. Pick ONE of the above and put it in front of your team this week.
Audit your last 10 chest-pain discharges against it and bring the gaps
to the next M&M conference.
</example_key_takeaways>

<example_whats_next>
"Pull your last 10 chest-pain discharges this week and audit how the
differential and disposition reasoning were documented. Use the
MagMutual risk consultation line and online toolkit for ongoing
support."
</example_whats_next>
""".strip()


def build_closing(course_body: str, risk_driver: dict) -> str:
    brief_full = risk_driver.get("RISK_BRIEF", "") or ""
    sliced = slice_risk_brief(brief_full)
    sections: dict[str, str] = {
        "RISK DRIVER":  risk_driver.get("DRIVER", ""),
        "SPECIALTY":    risk_driver.get("SPECIALTY", ""),
        "COURSE BODY (synthesize from this)": course_body,
    }
    # Inject every canonical playbook section so the closing's "Key
    # takeaways" and "What's next" can pull from the full advice set,
    # not just the synthesized course body.
    for label, body in sliced.items():
        sections[f"PLAYBOOK · {label}"] = body
    if not sliced:
        sections["RISK BRIEF (raw)"] = brief_full
    sections = {k: v for k, v in sections.items() if (v or "").strip()}
    return _assemble(
        instructions=compose(
            _CLOSING_TASK,
            MM_VOICE,
            LENGTH_GUIDANCE,
            GROUNDING_RULES,
            OUTPUT_DISCIPLINE,
        ),
        sections=sections,
    )


def build_course_body(risk_driver: dict, playbook_strategies: str,
                      learning_objectives: list[str],
                      top_factors: list[dict] | None = None) -> str:
    """Assemble the course-body prompt.

    `top_factors` is the output of `snowflake_client.top_contributing_factors`
    for this driver, a list of `{key, label, pct}` dicts sorted by pct
    desc. Injecting this into the prompt grounds the model's emphasis
    in the actual claim-data shape (e.g. ACS in EM has 23% diagnostic
    test-ordering failures, so the course should weight those strategies
    more heavily). Pass an empty list to skip.
    """
    factors_block = ""
    if top_factors:
        lines = [f"- {f['pct']:.1f}%  {f['label']}" for f in top_factors]
        factors_block = (
            "Top contributing factors for this driver, by share of "
            "tagged claims (real MagMutual data, emphasize the "
            "highest-frequency categories most heavily in the strategies):\n"
            + "\n".join(lines)
        )
    # Slice the RISK_BRIEF into its canonical sections so Cortex sees
    # structured input rather than one giant prose blob. Each canonical
    # section maps to a distinct part of MM's playbook ontology
    # (CLINICAL: DIAGNOSTIC, ADMINISTRATIVE: DOCUMENTATION, …).
    brief_full = risk_driver.get("RISK_BRIEF", "") or ""
    sliced = slice_risk_brief(brief_full)
    sections: dict[str, str] = {
        "RISK DRIVER":       risk_driver.get("DRIVER", ""),
        "SPECIALTY":         risk_driver.get("SPECIALTY", ""),
        "PRESENTING CONDITIONS (from data)":
                              risk_driver.get("PRESENTING_CONDITIONS", "")
                              or sliced.get("PRESENTING_CONDITION", ""),
        "ADVERSE OUTCOMES (from data)":
                              risk_driver.get("ADVERSE_OUTCOMES", "")
                              or sliced.get("ADVERSE_OUTCOME", ""),
        "OVERVIEW":           risk_driver.get("OVERVIEW", ""),
        "MITIGATING YOUR RISK (intro from playbook)":
                              sliced.get("MITIGATING_YOUR_RISK", ""),
        "CLINICAL VS ADMINISTRATIVE BREAKDOWN (from playbook)":
                              sliced.get("CLINICAL_AND_ADMIN_BREAKDOWN", ""),
        "PLAYBOOK · CLINICAL: DIAGNOSTIC":
                              sliced.get("CLINICAL_DIAGNOSTIC", ""),
        "PLAYBOOK · CLINICAL: TREATMENT":
                              sliced.get("CLINICAL_TREATMENT", ""),
        "PLAYBOOK · CLINICAL: PROCEDURAL/SURGICAL":
                              sliced.get("CLINICAL_PROCEDURAL_SURGICAL", ""),
        "PLAYBOOK · ADMINISTRATIVE: COMMUNICATION":
                              sliced.get("ADMINISTRATIVE_COMMUNICATION", ""),
        "PLAYBOOK · ADMINISTRATIVE: DOCUMENTATION":
                              sliced.get("ADMINISTRATIVE_DOCUMENTATION", ""),
        "PLAYBOOK · ADMINISTRATIVE: PATIENT FACTORS":
                              sliced.get("ADMINISTRATIVE_PATIENT_FACTORS", ""),
        "PLAYBOOK · ADMINISTRATIVE: PROFESSIONAL BEHAVIOR":
                              sliced.get("ADMINISTRATIVE_PROFESSIONAL_BEHAVIOR", ""),
        "PLAYBOOK · ADMINISTRATIVE: SYSTEMS ISSUES":
                              sliced.get("ADMINISTRATIVE_SYSTEMS_ISSUES", ""),
        "TOP CONTRIBUTING FACTORS (real data)": factors_block,
        "LEARNING OBJECTIVES": "\n".join(f"- {lo}" for lo in learning_objectives),
    }
    # If the slicer found nothing (unstructured brief), feed the raw
    # blob so Cortex still has the source material.
    if not sliced:
        sections["RISK BRIEF (raw, unstructured)"] = brief_full
    # Drop empty sections so the prompt isn't padded with header-only blocks
    sections = {k: v for k, v in sections.items() if (v or "").strip()}
    return _assemble(
        instructions=compose(
            _COURSE_BODY_TASK,
            MM_VOICE,
            LENGTH_GUIDANCE,
            GROUNDING_RULES,
            OUTPUT_DISCIPLINE,
        ),
        sections=sections,
    )


# =============================================================================
# 2) PROMPT_ASSESSMENT  (Tanner & Deanna, verbatim from team CSV)
# =============================================================================
PROMPT_ASSESSMENT = """
You are an expert clinical education assessment designer specializing in risk mitigation and patient safety. Your task is to generate a comprehensive assessment package for clinicians who have completed a risk mitigation course.

<assessment_parameters>
<target_audience>Clinicians across all specialties and experience levels</target_audience>
<question_count>10</question_count>
<completion_time>15 minutes maximum</completion_time>
<pass_rate_target>80%</pass_rate_target>
<tone>Formal, clinical, professional</tone>
</assessment_parameters>

<assessment_objectives>
The assessment must evaluate three core competencies:
1. Knowledge retention of risk mitigation course concepts
2. Ability to apply risk mitigation strategies to real-world clinical scenarios
3. Likelihood of practice change and implementation of learned strategies
</assessment_objectives>

<practice_gap_focus>
Questions must directly evaluate whether the educational activity addressed specific professional practice gaps identified during planning. Each question should target a documented gap in current clinical practice and measure clinician readiness to close that gap through applied knowledge.
</practice_gap_focus>

<question_design_requirements>
- Create interactive, scenario-based questions using clinical vignettes that require application of knowledge rather than simple fact recall
- Ensure broad coverage across all course modules and identified practice gaps
- Use clinical language appropriate for healthcare professionals
- Design questions at varying difficulty levels to accommodate mixed experience levels
- Include realistic clinical scenarios that reflect actual practice environments
- Balance between knowledge recall and practical application (favor application-based questions at 6:4 ratio)
- Avoid negatively phrased questions using "EXCEPT," "NOT," or similar constructions that increase cognitive load
- Frame all questions positively to test direct knowledge and decision-making
</question_design_requirements>

<output_format>
Generate the complete assessment package as a valid HTML5 document with the following structure:

1. HTML document header with title "Clinical Risk Mitigation Assessment"
2. CSS styling for professional clinical appearance (use inline styles or style tags)
3. For each of the 10 questions, provide the following HTML elements:
   - Question number as heading (h2)
   - Difficulty level as badge/span
   - Learning objective in a div (mapped to course learning objectives)
   - Practice gap addressed in a div (specific gap this question targets)
   - Question type in a div
   - Question text in a div with clinical scenario/vignette
   - Answer options as a list or radio button group
   - Correct answer in a hidden or collapsible section
   - Educational rationale in a collapsible section (explaining why correct answer is right and why incorrect answers are wrong)
   - Practice change indicator in a div (how this question relates to intended behavior modification)
4. Assessment summary table at the end with all required metrics
5. Use semantic HTML5 tags (section, article, aside, etc.)
6. Ensure the document is printable and readable on screen
</output_format>

<question_distribution_guidelines>
- Difficulty Distribution: 3 Beginner, 5 Intermediate, 2 Advanced
- Question Type Mix: At least 6 scenario-based questions, maximum 4 direct knowledge questions
- Coverage: Ensure questions span different risk mitigation domains (communication, documentation, clinical decision-making, team dynamics, error reporting, patient safety protocols, etc.)
- Practice Change Assessment: At least 3 questions should specifically evaluate likelihood of behavior modification and intended practice changes
- Practice Gap Alignment: Each question must address a documented practice gap and measure readiness to implement change
</question_distribution_guidelines>

<quality_standards>
1. Each scenario must be realistic, relevant to multi-specialty practice, and grounded in identified practice gaps
2. Distractors (incorrect answers) should be plausible but clearly distinguishable from correct answers
3. Avoid ambiguous wording, trick questions, and negatively phrased constructions
4. Ensure cultural sensitivity and inclusivity in scenarios
5. Questions should be solvable within 1-2 minutes each on average
6. Educational rationales must reinforce key learning points from the course and explain why each answer option is correct or incorrect
7. Language should match clinical documentation standards
8. Include explicit connection between learning objectives and assessment items
9. Practice change questions should measure behavioral intent and implementation readiness
</quality_standards>

Generate the complete assessment package now as HTML, ensuring all questions are clinically sound, educationally valid, aligned with course learning objectives, address identified practice gaps, and support measurable practice change.
""".strip()


def build_assessment(course_body: str, learning_objectives: list[str]) -> str:
    return _assemble(
        instructions=compose(
            PROMPT_ASSESSMENT,
            MM_VOICE,
            OUTPUT_DISCIPLINE,
        ),
        sections={
            "COURSE BODY (use as the source of truth for content)": course_body,
            "LEARNING OBJECTIVES": "\n".join(f"- {lo}" for lo in learning_objectives),
        },
    )


# =============================================================================
# 3) PROMPT_EMBEDDED_LESSON_TOPIC  (Sogi, PLACEHOLDER, per-topic case)
# =============================================================================
_EMBEDDED_LESSON_TASK = """
<role>
You are writing one case-study lesson within the course's "Lesson 3"
section, in the MagMutual "Reducing Liability" format. The case must be
tied to the specific TOPIC named below.
</role>

<grounding>
The CLAIM SUMMARY section below carries an actual closed-claim
narrative tagged to this loss driver. Use it as the factual spine of
the case study:
- Anchor the Medical Summary and Timeline in the CASE_NARRATIVE.
- Quote the prose ALLEGATIONS where they support 3 bulleted items.
- The "TAGGED CONTRIBUTING FACTORS (from claim coding)" lines are the
  exact factor labels the claim was coded to (e.g. "Failure to
  recognize, interpret or act on diagnostic finding"). Each case
  study's clinical and non-clinical strategies must directly address
  those specific tagged factors. Do NOT invent contributing factors
  that aren't in the claim's tag list or the playbook slice.
- Use PEER_REVIEW_SUMMARY (when present) to ground the outcome and
  pause-and-reflect framing. Do not invent peer-review content.

If CASE_NARRATIVE is empty or only boilerplate, you may write a
plausible illustrative case study but you must NOT invent specific
dollar amounts, dates, jurisdictions, or peer-review verdicts.
</grounding>

<variety_requirement>
This is case study [N] of up to five for the same risk driver. Each
case study in this course MUST tackle a DIFFERENT failure pattern
within the driver, different patient demographics, different setting,
different timeline shape, and different specific contributing factors.
The Pause and reflect question MUST be unique to THIS case, anchor it
to the specific decision point where this case went wrong (NOT a
generic "how does your team handle this kind of case" prompt). Reuse
across cases is a defect.

The Risk reduction strategies (clinical AND non-clinical) MUST be
specific to the failure mode shown in THIS case, not generic
playbook prose copied across cases. Concretely:
- If THIS case turned on serial-evaluation failure, the clinical
  strategies must address serial-evaluation triggers and protocols.
- If THIS case turned on handoff communication, the non-clinical
  strategies must address handoff templates and closed-loop tracking.
- If THIS case turned on protocol bypass, the strategies must address
  alert overrides, justification fields, and audit cadence.
Repeating the same strategy bullets across cases 1, 2, and 3 is a
defect, verify that THIS case study's strategies could not be cut
and pasted into a different case study about a different failure mode.
</variety_requirement>

<structure>
## Key loss driver: [topic_label]

[1-2 sentences framing this loss driver: why it shows up in claims, the
characteristic failure pattern, and what learners should be watching
for. NO inline citation, the case study below carries the evidence.]

### Case study [N]

[One sentence anchoring this specific case to the topic above.]

#### Medical summary
[2-3 sentence summary of the patient, the care provided, what went wrong,
and the consequence, focused through the lens of the named TOPIC.]

#### Timeline
**[Date/time marker 1]**
[1-2 sentences of clinical detail.]

**[Date/time marker 2]**
[1-2 sentences.]

(Up to 5 timeline entries when warranted by the case complexity. The
"Timeline" heading is a structural marker only, it is consumed by the
renderer and is NOT shown to the learner. Each pivotal moment becomes
its own labelled card with the date as the card title, exactly like
the MagMutual reference.)

#### Allegations
[A bulleted list of EXACTLY 3 items, drawn from the claim's
allegations. Each bullet starts with "- " on its own line. Do NOT use
prose. Each bullet is one complete behavior-tied sentence (not a
category label).]

#### Outcome
The case was [resolved/settled/closed] for [generalized amount].

#### Pause and reflect
[One question that asks the clinician to consider how they handle this
kind of case in their own practice.]

#### Risk reduction strategies for [topic_label]
[One sentence introducing the two-tab strategy panel that follows.]

#### Reducing clinical risks
EXACTLY 3 bullets. Each bullet starts with "- " on its own line. Each
bullet is one complete imperative sentence (e.g. "Conduct a comprehensive
preoperative airway assessment before every anesthetic.") drawn from the
playbook prose for THIS contributing factor. Do NOT use prose paragraphs.

- [Strategy 1 — clinical, specific to THIS case's failure mode]
- [Strategy 2 — clinical, different angle]
- [Strategy 3 — clinical, third angle]

#### Reducing non-clinical risks
EXACTLY 3 bullets, same formatting rules as above. These cover
documentation, communication, workflow, audit, or training — NOT
clinical bedside actions.

- [Strategy 1 — non-clinical]
- [Strategy 2 — non-clinical]
- [Strategy 3 — non-clinical]
</structure>

<example>
For TOPIC = "Single-point assessment vs. serial evaluation":
- Medical summary highlights the missed serial troponin
- Timeline cards: initial visit, return-visit, outcome
- Allegations focus on failure to perform serial assessment
- Risk reduction strategies for single-point assessment introduces the
  two tab panels below
- Reducing clinical risks: HEART score, serial troponins
- Reducing non-clinical risks: chest-pain order set, EHR alerts
</example>
""".strip()


_TOPIC_TO_BRIEF_SLICES: list[tuple[str, list[str]]] = [
    # (slice_label, [keywords that, when present in topic, pick this slice])
    ("CLINICAL_DIAGNOSTIC",
     ["diagnos", "recognize", "history", "physical", "test", "imaging",
      "atypical", "presentation", "missed"]),
    ("CLINICAL_TREATMENT",
     ["treatment", "medication", "therapy", "interven", "monitor",
      "consult", "transfer"]),
    ("CLINICAL_PROCEDURAL_SURGICAL",
     ["procedur", "surg", "technique", "implant", "wrong site",
      "retained", "foreign body"]),
    ("ADMINISTRATIVE_COMMUNICATION",
     ["communic", "handoff", "hand-off", "sign-out", "transfer of care",
      "closed loop", "between providers", "to patient"]),
    ("ADMINISTRATIVE_DOCUMENTATION",
     ["document", "record", "note", "ehr", "chart"]),
    ("ADMINISTRATIVE_PATIENT_FACTORS",
     ["adher", "patient factor", "non-compliance", "non-adher"]),
    ("ADMINISTRATIVE_PROFESSIONAL_BEHAVIOR",
     ["conduct", "behav", "reckless", "professional"]),
    ("ADMINISTRATIVE_SYSTEMS_ISSUES",
     ["system", "equipment", "policy", "process", "order set",
      "alert", "protocol", "workflow"]),
]


def _pick_brief_slices(topic_label: str, sliced: dict[str, str]) -> dict[str, str]:
    """Return the 1-2 brief slices most relevant to this topic.

    Falls back to the union of all slices when nothing matches. Keeps
    the embedded-lesson prompt grounded in the actual playbook content
    relevant to THIS case rather than the entire driver brief.
    """
    if not sliced:
        return {}
    topic_lc = (topic_label or "").lower()
    picked: dict[str, str] = {}
    for label, keywords in _TOPIC_TO_BRIEF_SLICES:
        if label not in sliced:
            continue
        if any(kw in topic_lc for kw in keywords):
            picked[label] = sliced[label]
    if picked:
        return picked
    # No keyword hit, return everything so the model has full context.
    return sliced


def _per_case_word_target(total_cases: int) -> tuple[int, int]:
    """Words per case study so the SUM across all cases lands at
    3,200-4,500 words (the Lesson 3 budget for a 1.0-credit course).
    Inversely proportional to case count.
    """
    if total_cases <= 0:
        total_cases = 1
    if total_cases <= 3:
        return (1100, 1500)
    if total_cases <= 5:
        return (700, 900)
    if total_cases <= 8:
        return (400, 550)
    if total_cases <= 12:
        return (280, 380)
    return (200, 280)


def build_embedded_lesson_for_topic(course_body: str, topic_label: str,
                                     claim: dict, *, index: int = 1,
                                     total_cases: int = 1,
                                     risk_driver: dict | None = None) -> str:
    """Assemble the embedded-lesson prompt for one case study.

    `index` / `total_cases` tell Cortex its share of the Lesson 3 word
    budget so the SUM across all case studies fits the 1.0 CME-credit
    target (3,200-4,500 words for Lesson 3 total). Per-case length
    scales inversely to `total_cases`, see `_per_case_word_target`.

    `risk_driver` (optional) lets us inject the playbook-section slice
    of the RISK_BRIEF that is most relevant to `topic_label`, so the
    case-specific Reducing-clinical-risks / Reducing-non-clinical-risks
    bullets are grounded in real playbook prose, not generic advice.
    """
    lo, hi = _per_case_word_target(total_cases)
    instructions_with_topic = (
        _EMBEDDED_LESSON_TASK
        .replace("[topic_label]", topic_label)
        .replace("[N]", str(index))
    )
    sections: dict[str, str] = {
        "TOPIC ANCHOR": f"Topic anchor: {topic_label}",
        "CASE STUDY INDEX": (
            f"This is case study {index} of {total_cases} for the same"
            f" driver. Target length for THIS case: {lo}-{hi} words"
            f" total. Adjust timeline cards (3 instead of 5-7) and"
            f" allegations / strategy list lengths (2-3 items instead of"
            f" 3-5) to hit that target, DO NOT skip any sub-section."
        ),
        "RISK DRIVER":     (risk_driver or {}).get("DRIVER", ""),
        "SPECIALTY":       (risk_driver or {}).get("SPECIALTY", ""),
        "COURSE BODY (do not contradict)": course_body,
        "CLAIM SUMMARY": _claim_block(claim),
    }
    if risk_driver:
        brief = risk_driver.get("RISK_BRIEF", "") or ""
        sliced = slice_risk_brief(brief)
        # Foreground the slice(s) most relevant to THIS topic so Cortex
        # weights them most heavily when generating case-specific
        # strategies.
        picked = _pick_brief_slices(topic_label, sliced)
        for label, body in picked.items():
            sections[f"PLAYBOOK SLICE · {label} (most relevant to this topic)"] = body
        # Also include any remaining sections so the case study has
        # access to the full playbook advice, useful when the topic
        # touches multiple categories (e.g. a documentation failure
        # that also has communication and systems components).
        for label, body in sliced.items():
            if label in picked:
                continue
            sections[f"PLAYBOOK · {label} (additional context)"] = body
    sections = {k: v for k, v in sections.items() if (v or "").strip()}
    return _assemble(
        instructions=compose(
            instructions_with_topic,
            DEID_RULES,
            FINANCIAL_RANGES,
            EDUCATIONAL_TONE,
            MM_VOICE,
            LENGTH_GUIDANCE,
            GROUNDING_RULES,
            OUTPUT_DISCIPLINE,
        ),
        sections=sections,
    )


# =============================================================================
# 4) PROMPT_CLAIM_SELECTION  (Neha, verbatim "comprehensive_report" from CSV)
# =============================================================================
PROMPT_CLAIM_SELECTION = """
You are an expert medical claims analyst and clinical risk assessment specialist. Your task is to analyze medical malpractice claim documents and map them to established risk drivers.

<task_overview>
Generate a comprehensive report identifying claims from the candidate set that align with documented risk drivers from the risk library. Extract relevant text evidence, assign confidence scores, and provide complete risk context for each matched claim.
</task_overview>

<analysis_instructions>

## Step 1: Read the candidate risk drivers
The provided RISK LIBRARY contains the named drivers and their playbook prose (contributing factors, mitigation strategies, presenting conditions, adverse outcomes). Use those as the universe of drivers to match against. Frequency / severity aggregates are not available in this environment — do NOT reference them in your reasoning.

## Step 2: Analyze each candidate claim
For each candidate claim:
- Identify the clinical scenario, adverse outcomes, and contributing factors described.
- Compare against the risk drivers' presenting conditions, adverse outcomes, diagnostic failures, treatment errors, communication breakdowns, documentation deficiencies, and systems failures.

## Step 3: Match claims to risk drivers
For each claim, determine if it aligns with any risk driver by evidence-based matching against the playbook prose.

## Step 4: Assign confidence score
Rate each match on a scale of 0.0 to 1.0:
- **0.9-1.0**: Explicit, unambiguous match with clear terminology and multiple alignment points
- **0.7-0.89**: Strong match with clear conceptual alignment and specific supporting evidence
- **0.5-0.69**: Moderate match with reasonable alignment but some ambiguity
- **Below 0.5**: Weak match - EXCLUDE from output

## Step 5: Rank and select
Order matches by a teaching-value score derived from confidence and contributing-factor alignment with the named driver. Select the strongest 5 to 10 matches.

</analysis_instructions>

<output_format>

Produce a markdown table with these columns:

| Rank | DOCUMENT_ID | DRIVER_ID | DRIVER | TEACHING_VALUE_RATIONALE | MATCHING_TEXT_EXCERPT | CONFIDENCE |

After the table, add a "Recommended pick" line naming the single claim with the strongest combination of teaching value and confidence, with one-sentence rationale.

</output_format>

<quality_requirements>
1. Precision over recall, only include high-confidence matches.
2. Every match must have clear textual evidence in the claim summary.
3. The MATCHING_TEXT_EXCERPT must be an actual quote from the claim summary.
4. Apply matching criteria uniformly across all claims.
</quality_requirements>
""".strip()


def _json_safe(obj):
    """Coerce values that survived the snowflake_client cleanup but are
    still not JSON-serializable (e.g. NaN, pandas Timestamp, set). Decimal
    is normally caught upstream, but we re-coerce here for safety."""
    import decimal as _decimal
    import math
    if isinstance(obj, _decimal.Decimal):
        return float(obj)
    if isinstance(obj, float) and math.isnan(obj):
        return None
    if isinstance(obj, (set, tuple)):
        return list(obj)
    if hasattr(obj, "isoformat"):
        try:
            return obj.isoformat()
        except Exception:
            return str(obj)
    return str(obj)


def build_claim_selection(candidate_claims: list[dict],
                          risk_driver_stats: list[dict],
                          risk_drivers: list[dict]) -> str:
    return _assemble(
        instructions=compose(
            PROMPT_CLAIM_SELECTION,
            # The MATCHING_TEXT_EXCERPT may pull from claim summaries that
            # contain real names / facility details / dollar amounts; enforce
            # the same de-id and financial-range rules used elsewhere so
            # excerpts come back already generalized.
            DEID_RULES,
            FINANCIAL_RANGES,
            MM_VOICE,
            OUTPUT_DISCIPLINE,
        ),
        sections={
            "RISK DRIVERS (library)": _drivers_block(risk_drivers),
            "RISK DRIVER STATS": json.dumps(risk_driver_stats, indent=2,
                                              default=_json_safe),
            "CANDIDATE CLAIMS": "\n\n".join(_claim_block(c) for c in candidate_claims),
        },
    )


# =============================================================================
# 5) PROMPT_LESSON  (Stephanie, MagMutual Claims Lesson template + her rules)
# =============================================================================
_LESSON_TASK = """
<role>
You are an expert medical malpractice claims analyst writing a
substantive, demo-quality Claims Lesson in MagMutual's exact Claims
Lesson template. The output is grounded in (a) the CLAIM SUMMARY block
below — which carries the actual CASE_NARRATIVE, ALLEGATIONS,
ACTION_OR_OMISSION_1/2/3, and PEER_REVIEW_SUMMARY from the live
claim-tags warehouse — and (b) the MATCHING PLAYBOOK SECTION's
contributing-factor advice.
</role>

<grounding_priority>
This is non-negotiable: USE THE PROVIDED CLAIM DATA. The CLAIM SUMMARY
block contains everything you need to anchor the lesson:
- CASE_NARRATIVE → the spine of the Summary, The Case, and Patient
  outcome sections. Quote and paraphrase its specific clinical
  details (presenting symptoms, vital signs, lab values, procedures
  attempted, time elapsed between events). Do NOT invent a generic
  case.
- ALLEGATIONS (prose) → the spine of the Allegations section. Each
  allegation in your output must trace to a sentence in the source
  ALLEGATIONS block. Convert run-on prose into a clean bulleted list.
- ACTION_OR_OMISSION_1/2/3 → the EXACT named contributing factors
  the claim was coded to (e.g. "Failure to recognize, interpret or act
  on diagnostic finding"). Each Key driver, each Clinical contributor
  bullet, and each Peer Review observation must address one of these
  tagged factors by name or by close paraphrase.
- PEER_REVIEW_SUMMARY → the spine of the Peer Review Commentary
  section. Quote standard-of-care verdicts, causation ratings, and
  the specific clinical-actions-or-omissions paragraph when available.
  Treat "NO_PEER_REVIEW_DATA" as a signal to write a generic clinical
  reviewer's view based on the case narrative + playbook.
- MATCHED_DRIVER → the primary risk driver this claim was tagged to.
  Use it verbatim in the lesson where the driver is named.

The MATCHING PLAYBOOK SECTION carries the mitigation strategies. Every
"Advice for Providers", "Advice for Administrators", "Clinical
contributors", and "Operational contributors" bullet must paraphrase a
specific named tactic from the playbook prose using at least two of
that tactic's distinctive noun phrases. Do not substitute generic
clinical wisdom for the playbook's specific recommendations.
</grounding_priority>

<length_target>
This lesson is the showpiece — write it long, write it specific,
write it substantive. Target total length 1,800 - 2,400 words.

Per-section budgets:
- Headline + Summary: 60-100 words
- Key drivers: 3 bullets, each one full sentence (35-50 words each)
- Advice for Providers: 5 bullets, each 30-50 words
- Advice for Administrators: 4 bullets, each 30-50 words
- Specialty + Other specialties: 1-2 sentences each
- The Case: 600-900 words. This is the longest section. Build a
  detailed clinical timeline with bold time markers. Each pivotal
  moment is 60-120 words of clinical detail: what was found, what
  was decided, what was documented, what wasn't.
- Patient outcome: 100-150 words covering physical, mental,
  emotional, and economic consequences.
- Allegations: 3-5 bullets, each one a complete behavior-tied
  sentence (NOT a category label).
- Legal Disposition: 80-130 words, including the "cases like this"
  context paragraph with plausible specialty-typical percentages.
- Peer Review Commentary: 3-4 paragraphs, 80-120 words each.
- Best Practices · Clinical contributors: 5 bullets, each 30-50 words.
- Best Practices · Operational contributors: 4 bullets, each 30-50
  words.

Short, skeletal output (under 1,200 words total) is a defect. If you
think there's not enough source material, mine the playbook section
harder — it has 5,000+ words of mitigation strategy text to draw on.
</length_target>

<structure>
# [Headline]
Active, issues-based headline that spotlights the core clinical problem.
Examples: "When a Single Troponin Isn't Enough", "Documenting Maneuvers
Saved the Defense", "The Differential That Wasn't Documented". Avoid
neutral titles like "Case Study 1" or just naming the diagnosis.

## Summary
Two to three sentences that preview the clinical scenario, name the
contributing factor that drove the outcome, and hint at the
disposition. Should pull the reader in — it is the abstract.

## Key drivers
Bulleted list of 3 items. Each item names one of the
ACTION_OR_OMISSION_* tags from the claim AND translates it into a
plain-language failure mode the reader will recognise at the bedside.
- [Tag 1, with bedside-language explanation]
- [Tag 2, same pattern]
- [Tag 3, same pattern]

## Advice for Providers
Bulleted list of 5 items, each an imperative sentence drawn from the
playbook's clinical strategies. Specific. Behavior-tied. Quote the
playbook's distinctive language ("serial troponins at 0/3/6 h", "video
laryngoscopy as primary technique", "closed-loop handoff").
- [Advice 1]
- [Advice 2]
- [Advice 3]
- [Advice 4]
- [Advice 5]

## Advice for Administrators
Bulleted list of 4 items targeting system / workflow / audit / training
levers (NOT bedside clinical actions). Drawn from the playbook's
administrative or systems-issues sections.
- [Admin advice 1]
- [Admin advice 2]
- [Admin advice 3]
- [Admin advice 4]

## Primary provider specialty
The primary specialty named in the claim (use CLAIM_SPECIALTY or
SPECIALTY from the source block).

## Other provider specialties involved
Any other specialties mentioned in the case narrative; otherwise
"None."

## The Case

**Presenting clinical conditions:** [Pull from CASE_NARRATIVE — the
chief complaint, vital signs at arrival, and any pertinent prior
history.]
**Procedures:** [Significant procedures described in the narrative.]
**Final diagnosis:** [The diagnosis that contributed to the outcome,
as established later in the case.]
**Degree of injury:** [Use the language from PEER_REVIEW_SUMMARY when
available; otherwise describe based on the narrative.]

[Then a detailed clinical timeline. 5-7 bolded pivotal moments. Each
moment is 60-120 words. Cover: patient medical history; conditions;
medical diagnosis and treatment; ongoing or subsequent issues and
complications; communication with patient and other providers;
documentation in the medical record (including informed consent);
patient adherence to treatment and follow-up. Use bold time markers
like **Initial presentation (Day 0, 14:22)**, **First evaluation
(Day 0, 15:40)**, **Disposition (Day 0, 21:10)**, **Return visit
(Day 2)**.]

## Patient outcome
[100-150 words on how the patient was impacted physically, mentally,
emotionally, and economically. Include long-term sequelae when the
narrative supports them.]

## Allegations
Bulleted list of 3-5 items. Each one a complete behavior-tied sentence
naming WHO is alleged to have done WHAT (e.g. "The emergency medicine
physician failed to perform serial troponins despite a clinical
picture suggestive of evolving acute coronary syndrome"). Pull
directly from the source ALLEGATIONS prose — convert run-on text into
clean separate bullets.

## Legal Disposition of Claim
[Type of disposition: not pursued by plaintiff, dismissed, settlement,
award. If settlement/award, do NOT name a specific amount — use the
financial ranges from the FINANCIAL_RANGES component. 50-70 words.]

[Then add a "Cases like this" context paragraph: "Cases involving
[driver] in [specialty] are typically open for X-Y months. XX% of the
time they proceed to trial. XX% of the time the case is closed without
indemnity. When indemnity is paid, it historically has ranged from
$X-$X." Use plausible specialty-typical numbers if not provided in
the source. 50-70 words.]

## Peer Review Commentary
Three to four substantive paragraphs of 80-120 words each. Use the
PEER_REVIEW_SUMMARY content when present — quote the specific
standard-of-care verdict, causation rating, and clinical-actions
paragraph. When PEER_REVIEW_SUMMARY is "NO_PEER_REVIEW_DATA", write
the commentary as a physician reviewer's analysis of the case
narrative + playbook, structured around:
1. Standard of care: did the care meet specialty norms? Where did it
   deviate?
2. Differential diagnosis & timeliness: what should have been
   considered earlier? What documentation would have changed the
   trajectory?
3. Communication & documentation: how did the chart, the handoff, or
   the patient conversation contribute to the outcome and the claim?
4. (Optional) Lessons that generalize beyond this specific case.

## Best Practices to Mitigate Risk

### Clinical contributors
5 bullets drawn from the playbook's clinical-section mitigation
strategies. Each 30-50 words. Each addresses one of the tagged
ACTION_OR_OMISSION factors directly.

### Operational contributors
4 bullets drawn from the playbook's administrative / systems /
documentation sections. Each 30-50 words. Same direct-address pattern.
</structure>

<example>
Headline: "When a Single Troponin Isn't Enough"
Summary: "A man in his late 50s presented to an emergency department
with substernal chest pressure radiating to the left jaw. After a
single ECG read as 'nonspecific T-wave changes' and one negative
high-sensitivity troponin, he was discharged with a working diagnosis
of musculoskeletal chest pain. Two days later he returned in cardiogenic
shock from an extensive anterior wall myocardial infarction."

Key drivers: "Failure to recognize, interpret or act on diagnostic
finding — the team treated the first troponin as definitive when serial
testing was indicated for an evolving syndrome..."

The Case timeline includes: **Initial presentation (Day 0, 22:14)**,
**First evaluation (Day 0, 23:05)**, **Disposition (Day 1, 02:30)**,
**Return visit (Day 3, 09:40)**, **Cardiac catheterization (Day 3,
11:15)**.

Legal Disposition: "Settled in the high six figures. Cases like this
in emergency medicine are typically open for 22-30 months. They proceed
to trial roughly 7% of the time and close without indemnity in about
40% of cases. When indemnity is paid, it has historically ranged from
$300,000 to $1.5M."
</example>
""".strip()


PROMPT_LESSON = compose(
    _LESSON_TASK, DEID_RULES, FINANCIAL_RANGES, EDUCATIONAL_TONE,
    MM_VOICE, LENGTH_GUIDANCE, GROUNDING_RULES, OUTPUT_DISCIPLINE,
)


def build_lesson(claim: dict, playbook_section: dict,
                 full_extract: str | None = None) -> str:
    sections = {
        "CLAIM SUMMARY": _claim_block(claim),
        "MATCHING PLAYBOOK SECTION (educational lens)":
            _drivers_block([playbook_section]),
    }
    if full_extract:
        sections["FULL CLAIM EXTRACT (optional supporting context)"] = full_extract[:8000]
    return _assemble(
        instructions=PROMPT_LESSON,
        sections=sections,
    )


# =============================================================================
# 6) PROMPT_CONFIDENCE  (Muskaan + Diane, verbatim, shared)
# =============================================================================
PROMPT_CONFIDENCE = """
You are an expert medical content quality assurance reviewer specializing in clinical accuracy and publication readiness. Your role is to grade generated outputs against their source material using strict standards.

## Your Task
Evaluate the generated output against source material, prioritizing alignment with the RISK_BRIEF column while ensuring consistency across the entire table. Return a JSON assessment with:
- Overall letter grade (A–F)
- Five dimension scores (1–5 scale) with reasoning
- Section-by-section grades
- Publication decision with actionable feedback

CRITICAL: If the generated output is missing, incomplete, or lacks substantive content, return "publication_decision": "BLOCKED" with specific missing elements listed.

## Dimensions (use the appropriate set based on output_type)

Course Generator Output:
- Dimension 1: Source Alignment
- Dimension 2: Completeness
- Dimension 3: Clinical Accuracy
- Dimension 4: Actionability
- Dimension 5: Clarity & Organization

Claims Lesson Output:
- Dimension 1: Clinical Accuracy
- Dimension 2: Teaching Relevance
- Dimension 3: De-identification Quality
- Dimension 4: Narrative Coherence
- Dimension 5: Completeness

## Grading scale (per dimension, 1-5)
5 Excellent · 4 Good · 3 Acceptable · 2 Poor · 1 Failing.

## Overall grade
A 90-100 · B 80-89 · C 70-79 · D 60-69 · F <60.

## Output format
Return valid JSON only:

{
  "output_type": "course_generator|claims_lesson",
  "overall_grade": "A|B|C|D|F",
  "publication_decision": "APPROVED|REQUIRES_REVISION|BLOCKED",
  "dimension_scores": {
    "dimension_1": {"name": "...", "score": 1-5, "reasoning": ["...", "...", "..."]},
    "dimension_2": {"name": "...", "score": 1-5, "reasoning": ["...", "...", "..."]},
    "dimension_3": {"name": "...", "score": 1-5, "reasoning": ["...", "...", "..."]},
    "dimension_4": {"name": "...", "score": 1-5, "reasoning": ["...", "...", "..."]},
    "dimension_5": {"name": "...", "score": 1-5, "reasoning": ["...", "...", "..."]}
  },
  "section_grades": {"section_name": "A|B|C|D|F"},
  "summary": "...",
  "blocking_issues": ["..."] or null
}
""".strip()


def build_confidence(generated_text: str, sources: list[str],
                     output_type: str = "course_generator") -> str:
    src = "\n\n---\n\n".join(sources)
    return _assemble(
        instructions=PROMPT_CONFIDENCE,
        sections={
            "OUTPUT TYPE": output_type,
            "GENERATED OUTPUT (under review)": generated_text,
            "SOURCE MATERIAL": src,
        },
    )


# =============================================================================
# Section-edit prompt (used by the chat orchestrator)
# =============================================================================
def build_edit_section(section_name: str, current_text: str, sources_block: str,
                       user_instruction: str) -> str:
    """Apply a chat-style instruction to a section and return updated markdown.

    The prompt is permissive about structural changes: if the user
    explicitly asks for them (add a sub-section, reorder, restore the
    definition cards, convert paragraphs to bullets, change the layout,
    etc.), Cortex makes those changes. If the user asks only for tone
    or length changes (tighten, expand, plain language), Cortex
    preserves structure and only edits prose.
    """
    return compose(
        f"""<role>
You are revising a section of medical risk-mitigation content for the
MagMutual "Reducing Liability" course format. Apply the user's
instruction to the CURRENT SECTION below and return ONLY the revised
markdown for that section.
</role>

<edit_scope>
DEFAULT BEHAVIOR: preserve every heading, list, sub-section, and the
overall layout. Edit prose only — wording, tone, length, examples,
fact-checking. Do NOT add, remove, reorder, or restructure sections.

EXCEPTION — STRUCTURAL CHANGES are permitted ONLY when the user's
instruction contains UNAMBIGUOUS structural language. Examples that
qualify:
  - "add a Definition of key terms section"
  - "restore the definition cards"
  - "remove the Pause and reflect"
  - "move the chart above the allegations"
  - "convert the paragraph to a bulleted list"
  - "change the layout"
  - "add a sub-section about [X]"

If the instruction is ambiguous (e.g. "make this better", "polish it",
"clinical"), DO NOT make structural changes — apply wording-only edits
and preserve the existing structure.

CONTENT changes: when the user asks to add a fact, swap a strategy,
or incorporate a guideline, apply the change but keep every clinical
claim traceable to the SOURCE MATERIAL. Do not invent strategies,
statistics, or named guidelines that aren't in the source.
</edit_scope>

<known_structural_components>
The course renderer auto-recognises these markdown patterns and
applies special UI treatment. Use the EXACT phrasing in headings
when the user asks for these structures:

- `### Definition of key terms` followed by `- **Term** — Definition`
  bullets renders as clickable flip cards.
- `### Pause and reflect` followed by paragraph text renders as a
  full-width dark banner.
- `### Case study N` (under a `## Key loss driver:` H2) renders the
  card-grid + timeline-card layout.
- `#### Reducing clinical risks` + `#### Reducing non-clinical risks`
  back-to-back render as a tab control.
- `### Top contributing factors` is the chart placeholder; the app
  injects an SVG bar chart inline.

If the user asks to "add back the definition cards" or "add flip
cards", emit a `### Definition of key terms` section with the
`**Term** — Definition` bullet pattern.
</known_structural_components>

<section_label>{section_name}</section_label>

<user_instruction>
{user_instruction}
</user_instruction>

<current_section>
{current_text}
</current_section>

<source_material>
{sources_block}
</source_material>

<prompt_version>{PROMPT_VERSION}</prompt_version>""",
        MM_VOICE,
        OUTPUT_DISCIPLINE,
    )


# =============================================================================
# Helpers
# =============================================================================
def _assemble(instructions: str, sections: dict[str, str]) -> str:
    parts = [instructions]
    parts.append(f"<prompt_version>{PROMPT_VERSION}</prompt_version>")
    for header, body in sections.items():
        parts.append(f"---\n# {header}\n\n{body}")
    return "\n\n".join(parts)


def _claim_block(claim: dict) -> str:
    """Render a single claim as a labelled block for the prompt.

    Prefers the rich columns from CLAIM_RISK_DRIVER_TAGS (CASE_NARRATIVE,
    ALLEGATIONS prose, three ACTION_OR_OMISSION_* fields, PEER_REVIEW_SUMMARY)
    over the older CLAIM_SUMMARIES schema (SUMMARY / ADVERSE_OUTCOME /
    ALLEGATIONS-as-list). Falls back gracefully when fields are missing —
    the LLM just gets whatever grounding is available.
    """
    parts: list[str] = []
    doc_id = claim.get("DOCUMENT_ID", "") or claim.get("CLAIM_NUMBER", "")
    if doc_id:
        parts.append(f"DOCUMENT_ID: {doc_id}")

    # Specialty / demographics (any of these may be present)
    for key in ("CLAIM_SPECIALTY", "SPECIALTY", "AGE_RANGE", "SEX",
                "PRESENTING_COMPLAINT", "MATCHED_DRIVER"):
        v = claim.get(key)
        if v and str(v).strip():
            parts.append(f"{key}: {v}")

    # Case narrative — primary grounding text. Truncate to keep prompt budget
    # manageable; 3000 chars is enough for full clinical context.
    narrative = claim.get("CASE_NARRATIVE", "") or claim.get("SUMMARY", "")
    if narrative:
        parts.append(f"CASE_NARRATIVE:\n{str(narrative)[:3000]}")

    # Allegations — handle both prose (string from tags view) and list (legacy)
    allegations = claim.get("ALLEGATIONS")
    if isinstance(allegations, list) and allegations:
        parts.append("ALLEGATIONS:\n" +
                     "\n".join(f"- {a}" for a in allegations))
    elif isinstance(allegations, str) and allegations.strip():
        parts.append(f"ALLEGATIONS:\n{allegations[:2000]}")

    # Tagged contributing factors — the key new grounding for case studies.
    # These three fields name the exact actions/omissions the claim was
    # tagged to, e.g. "Failure to recognize, interpret or act on diagnostic
    # finding". Use them to drive the per-case story.
    actions = [claim.get(f"ACTION_OR_OMISSION_{i}", "") for i in (1, 2, 3)]
    actions = [a for a in actions if a and str(a).strip()]
    if actions:
        parts.append("TAGGED CONTRIBUTING FACTORS (from claim coding):\n" +
                     "\n".join(f"- {a}" for a in actions))

    # Peer review summary — optional rich source for case detail
    pr = claim.get("PEER_REVIEW_SUMMARY", "")
    if pr and "NO_PEER_REVIEW_DATA" not in str(pr):
        parts.append(f"PEER_REVIEW_SUMMARY:\n{str(pr)[:2500]}")

    # Legacy fallback fields
    adverse = claim.get("ADVERSE_OUTCOME", "")
    if adverse:
        parts.append(f"ADVERSE_OUTCOME: {adverse}")
    resolution = claim.get("RESOLUTION", "")
    if resolution:
        parts.append(f"RESOLUTION: {resolution}")

    return "\n\n".join(parts) if parts else "(no claim data available)"


def _drivers_block(drivers: Iterable[dict]) -> str:
    out = []
    for d in drivers:
        out.append(
            f"DRIVER_ID: {d.get('DRIVER_ID', '')}\n"
            f"SPECIALTY: {d.get('SPECIALTY', '')}\n"
            f"DRIVER: {d.get('DRIVER', '')}\n"
            f"TITLE: {d.get('TITLE', '')}\n"
            f"RISK_BRIEF: {d.get('RISK_BRIEF', '')}\n"
            f"OVERVIEW: {d.get('OVERVIEW', '')}\n"
            f"PRESENTING_CONDITIONS: {d.get('PRESENTING_CONDITIONS', '')}\n"
            f"ADVERSE_OUTCOMES: {d.get('ADVERSE_OUTCOMES', '')}\n"
            f"CLINICAL_DIAGNOSTIC: {d.get('CLINICAL_DIAGNOSTIC', '')}\n"
            f"CLINICAL_TREATMENT: {d.get('CLINICAL_TREATMENT', '')}\n"
            f"CLINICAL_PROCEDURAL_SURGICAL: {d.get('CLINICAL_PROCEDURAL_SURGICAL', '')}\n"
            f"ADMINISTRATIVE_COMMUNICATION: {d.get('ADMINISTRATIVE_COMMUNICATION', '')}\n"
            f"ADMINISTRATIVE_DOCUMENTATION: {d.get('ADMINISTRATIVE_DOCUMENTATION', '')}\n"
            f"ADMINISTRATIVE_PATIENT_FACTORS: {d.get('ADMINISTRATIVE_PATIENT_FACTORS', '')}\n"
            f"ADMINISTRATIVE_PROFESSIONAL_BEHAVIOR: {d.get('ADMINISTRATIVE_PROFESSIONAL_BEHAVIOR', '')}\n"
            f"ADMINISTRATIVE_SYSTEMS_ISSUES: {d.get('ADMINISTRATIVE_SYSTEMS_ISSUES', '')}"
        )
    return "\n\n".join(out)


def playbook_strategies_text(driver: dict) -> str:
    """Concatenate the playbook strategy fields into a single block.

    The original Risk Library schema split mitigation strategies across
    eight CLINICAL_* / ADMINISTRATIVE_* columns. The current export
    delivered by the team only carries the prose under RISK_BRIEF, so
    those columns are empty, we fall back to slicing the RISK_BRIEF on
    its canonical headings (CLINICAL: DIAGNOSTIC, ADMINISTRATIVE:
    DOCUMENTATION, etc.). Either way the prompt model gets the canonical
    playbook content as its source of truth, structured by category.
    """
    fields = [
        "CLINICAL_DIAGNOSTIC", "CLINICAL_TREATMENT", "CLINICAL_PROCEDURAL_SURGICAL",
        "ADMINISTRATIVE_COMMUNICATION", "ADMINISTRATIVE_DOCUMENTATION",
        "ADMINISTRATIVE_PATIENT_FACTORS", "ADMINISTRATIVE_PROFESSIONAL_BEHAVIOR",
        "ADMINISTRATIVE_SYSTEMS_ISSUES",
    ]
    parts = []
    for f in fields:
        v = driver.get(f, "")
        if v:
            parts.append(f"## {f.replace('_', ' ').title()}\n{v}")
    if parts:
        return "\n\n".join(parts)
    # Fallback: slice the RISK_BRIEF into its canonical sections so
    # Cortex sees structured input even when the per-column fields are
    # empty (which is the current shape of the team's data export).
    brief = driver.get("RISK_BRIEF", "") or driver.get("OVERVIEW", "")
    sliced = slice_risk_brief(brief)
    if sliced:
        return "\n\n".join(
            f"## {label}\n{body}" for label, body in sliced.items()
        )
    return f"## Playbook prose (full risk brief)\n{brief}".strip()


# Canonical RISK_BRIEF section headings that appear in MM playbooks. The
# Excel export delivers these as in-line uppercase headers inside one
# RISK_BRIEF blob (per-section columns are empty), so we slice on them.
_BRIEF_SECTIONS = [
    ("PRESENTING_CONDITION",          [r"PRESENTING\s+CONDITION(?:\(S\)|S)?"]),
    ("ADVERSE_OUTCOME",               [r"ADVERSE\s+OUTCOME(?:\(S\)|S)?"]),
    ("MITIGATING_YOUR_RISK",          [r"Mitigating\s+Your\s+Risk"]),
    ("CLINICAL_AND_ADMIN_BREAKDOWN",  [r"Clinical\s+and\s+Administrative\s+Breakdowns?"]),
    ("CLINICAL_DIAGNOSTIC",           [r"CLINICAL:\s*DIAGNOSTIC"]),
    ("CLINICAL_TREATMENT",            [r"CLINICAL:\s*TREATMENT"]),
    ("CLINICAL_PROCEDURAL_SURGICAL",  [r"CLINICAL:\s*PROCEDURAL(?:[/\s]+SURGICAL)?"]),
    ("ADMINISTRATIVE_COMMUNICATION",  [r"ADMINISTRATIVE:\s*COMMUNICATION"]),
    ("ADMINISTRATIVE_DOCUMENTATION",  [r"ADMINISTRATIVE:\s*DOCUMENTATION"]),
    ("ADMINISTRATIVE_PATIENT_FACTORS",[r"ADMINISTRATIVE:\s*PATIENT\s+FACTORS"]),
    ("ADMINISTRATIVE_PROFESSIONAL_BEHAVIOR",
                                       [r"ADMINISTRATIVE:\s*PROFESSIONAL\s+BEHAVIOR"]),
    ("ADMINISTRATIVE_SYSTEMS_ISSUES", [r"ADMINISTRATIVE:\s*SYSTEMS?\s+ISSUES?"]),
]


def playbook_factors(brief: str) -> list[dict]:
    """Pull the named contributing factors that have advice in this brief.

    Returns ordered list of `{category, title, advice}` dicts, one per
    "Failure to X / Error in Y" sub-heading that has a corresponding
    "Contributing action or omission" + "Mitigation Strategies" block.

    Used by the case-study generator: number of case studies in
    Lesson 3 equals `len(playbook_factors(brief))`, and each topic
    label is the factor's `title` (the actual MM-authored loss-driver
    name, e.g. "Failure to Obtain Relevant Medical History or Perform
    Pertinent Physical Exam"). This is more specific than the stats
    labels (which are short category names like "Failure to obtain
    history / physical") and grounds each case study in real playbook
    prose.
    """
    import re
    if not brief:
        return []
    sliced = slice_risk_brief(brief)
    out: list[dict] = []
    # Map each canonical advice category to its display label.
    category_display = {
        "CLINICAL_DIAGNOSTIC":           "Clinical · Diagnostic",
        "CLINICAL_TREATMENT":            "Clinical · Treatment",
        "CLINICAL_PROCEDURAL_SURGICAL":  "Clinical · Procedural / Surgical",
        "ADMINISTRATIVE_COMMUNICATION":  "Administrative · Communication",
        "ADMINISTRATIVE_DOCUMENTATION":  "Administrative · Documentation",
        "ADMINISTRATIVE_PATIENT_FACTORS":"Administrative · Patient Factors",
        "ADMINISTRATIVE_PROFESSIONAL_BEHAVIOR":
                                          "Administrative · Professional Behavior",
        "ADMINISTRATIVE_SYSTEMS_ISSUES": "Administrative · Systems Issues",
    }
    for cat_key, body in sliced.items():
        if cat_key not in category_display:
            continue
        # Each factor inside a section starts with a bare title line
        # (e.g. "Failure to Obtain Relevant Medical History...") and is
        # followed by a "Contributing action or omission:" line.
        # Split the section on that marker, then walk back to find the
        # title line that immediately precedes it.
        lines = body.split("\n")
        for i, ln in enumerate(lines):
            if not re.match(r"\s*Contributing action or omission\s*:", ln, re.I):
                continue
            # The title is the most recent non-empty line before this one.
            title = ""
            j = i - 1
            while j >= 0:
                cand = lines[j].strip()
                if cand:
                    title = cand
                    break
                j -= 1
            if not title:
                continue
            # Skip if title is just the section header repeating itself
            if title.upper().startswith("CLINICAL") or title.upper().startswith("ADMINISTRATIVE"):
                continue
            # Capture the advice block: from this line until the next
            # title-line that's followed by another "Contributing action".
            start = j  # title line index
            # Find next factor title in this section, if any
            end = len(lines)
            for k in range(i + 1, len(lines)):
                if re.match(r"\s*Contributing action or omission\s*:", lines[k], re.I):
                    # Walk back from k to find that factor's title, that
                    # title line is the END of the current factor's advice.
                    m = k - 1
                    while m > start:
                        if lines[m].strip():
                            end = m
                            break
                        m -= 1
                    break
            advice = "\n".join(lines[start:end]).strip()
            out.append({
                "category": category_display[cat_key],
                "title":    title,
                "advice":   advice,
            })
    return out


def slice_risk_brief(brief: str) -> dict[str, str]:
    """Slice a single-string RISK_BRIEF into its canonical sections.

    Returns an ordered dict {section_label: body_text}. Sections not
    present in the brief are simply omitted. Section bodies aggregate
    across multiple in-text occurrences of the same heading (some briefs
    repeat e.g. "CLINICAL: DIAGNOSTIC" twice with different sub-
    failures, both are concatenated).
    """
    import re
    if not brief:
        return {}
    # Build a single regex that finds any of the section heads. We
    # accept TWO shapes:
    #   "HEADING:" alone on a line (body starts on next line)
    #   "HEADING: inline body text..." (body starts after the colon)
    parts = []
    label_for_group: dict[int, str] = {}
    for i, (label, patterns) in enumerate(_BRIEF_SECTIONS, start=1):
        joined = "|".join(patterns)
        # Group g{i} matches the heading itself; the optional inline body
        # is captured by the slice between m.end() and the next heading.
        parts.append(f"(?P<g{i}>^[ \\t]*(?:{joined})[ \\t]*:?)")
        label_for_group[i] = label
    head_re = re.compile("|".join(parts), re.M | re.I)

    out: dict[str, str] = {}
    matches = list(head_re.finditer(brief))
    if not matches:
        return {}
    for idx, m in enumerate(matches):
        gi = next(i for i in label_for_group
                  if m.group(f"g{i}") is not None)
        label = label_for_group[gi]
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(brief)
        body = brief[start:end].strip()
        if not body:
            continue
        if label in out:
            out[label] = out[label] + "\n\n" + body
        else:
            out[label] = body
    return out


# =============================================================================
# Audit helper, `python -c "from shared.prompts import dump; dump('lesson')"`
# =============================================================================
def dump_prompt(name: str, **kwargs) -> str:
    """Return the assembled prompt text for inspection / audit.

    Names: 'course_body', 'assessment', 'embedded_lesson', 'claim_selection',
    'lesson', 'confidence', 'edit_section'.
    Sample data is used if no kwargs supplied, handy for quick checks.
    """
    sample_driver = {
        "DRIVER_ID": "EM-DX-ACS",
        "SPECIALTY": "Emergency Medicine",
        "DRIVER": "Missed or delayed diagnosis of acute coronary syndrome",
        "RISK_BRIEF": "Sample risk brief.",
        "OVERVIEW": "Sample overview.",
        "LEARNING_OBJECTIVES": ["LO 1", "LO 2", "LO 3"],
        "CLINICAL_DIAGNOSTIC": "Sample diagnostic guidance.",
    }
    sample_claim = {
        "DOCUMENT_ID": "CLM-SAMPLE", "SPECIALTY": "Emergency Medicine",
        "AGE_RANGE": "Late 50s", "SEX": "Male",
        "PRESENTING_COMPLAINT": "Chest pressure",
        "SUMMARY": "Sample claim summary.",
        "ALLEGATIONS": ["Sample allegation"],
        "RESOLUTION": "Sample resolution.",
    }
    if name == "course_body":
        return build_course_body(kwargs.get("driver", sample_driver),
                                  kwargs.get("playbook", "Sample playbook."),
                                  kwargs.get("los", ["LO 1"]))
    if name == "assessment":
        return build_assessment(kwargs.get("course_body", "## Lesson 1..."),
                                 kwargs.get("los", ["LO 1"]))
    if name == "embedded_lesson":
        return build_embedded_lesson_for_topic(
            kwargs.get("course_body", "## Lesson 1..."),
            kwargs.get("topic", "Sample topic"),
            kwargs.get("claim", sample_claim))
    if name == "claim_selection":
        return build_claim_selection([sample_claim], [{}], [sample_driver])
    if name == "lesson":
        return build_lesson(kwargs.get("claim", sample_claim),
                             kwargs.get("driver", sample_driver))
    if name == "confidence":
        return build_confidence(kwargs.get("text", "Sample output."),
                                 kwargs.get("sources", ["Sample source."]))
    if name == "edit_section":
        return build_edit_section("Course body", "Sample current.",
                                   "Sample sources.", "Tighten the prose.")
    raise ValueError(f"Unknown prompt name: {name}")


# Backward compat: keep these symbols around for any external imports
PROMPT_COURSE_BODY_PLACEHOLDER = _COURSE_BODY_TASK
PROMPT_EMBEDDED_LESSON_TOPIC = _EMBEDDED_LESSON_TASK
