"""Reusable prompt components.

Each constant below is a self-contained block of instructions intended to be
composed into the larger task-specific prompts. Centralizing here means a
team-wide style change happens in one place.

Tag with version markers so audit logs can identify which iteration of a
component produced which output.
"""
from __future__ import annotations

# Bumping COMPONENTS_VERSION invalidates downstream prompt versions.
COMPONENTS_VERSION = "2026-05-4"


# ---------------------------------------------------------------------------
# De-identification rules — used in every claim-grounded prompt
# ---------------------------------------------------------------------------
DEID_RULES = """
<deidentification>
1. Replace all provider names with generic labels: [Provider], [Physician],
   [Nurse], [Specialist].
2. Replace facility names with: [Hospital], [Clinic], [Facility],
   [Emergency Department].
3. Remove or generalize specific geographic locations.
4. Use age ranges instead of exact ages
   (e.g., "a man in his late 50s", "an elderly woman").
5. Remove specific dates; use relative time markers
   (e.g., "Initial presentation", "Day 3", "Two days later").
6. Generalize rare conditions or procedures that could identify the case.
7. Remove case numbers, file references, or any identifying codes.
</deidentification>
""".strip()


# ---------------------------------------------------------------------------
# Financial-amount generalization — used wherever a settlement appears
# ---------------------------------------------------------------------------
FINANCIAL_RANGES = """
<financial_ranges>
Never name a specific dollar amount. Use these ranges:
- Under $20,000: "less than $20,000" or "a nominal amount"
- $20,000-$99,999: "low five figure"
- $100,000-$249,999: "low six figure"
- $250,000-$499,999: "mid-six figure"
- $500,000-$999,999: "high six figure"
- $1,000,000-$2,499,999: "low seven figure"
- $2,500,000+: "seven figure"
- Unknown / confidential: "a confidential amount"
- Or: "policy limit", "amount average for claims in the specialty"
</financial_ranges>
""".strip()


# ---------------------------------------------------------------------------
# MagMutual brand voice (excerpt) — what the prompt model needs to know
# ---------------------------------------------------------------------------
MM_VOICE = """
<mm_voice>
Follow the MagMutual Brand Copy Guide:
- AP Stylebook is the foundation; the rules below are MagMutual deviations.
- Conciseness, simplicity, preciseness. Trust the reader. Cut unnecessary words.
- NO Oxford comma (e.g., "Janet, Kiran and Trevor wrote a book.").
- Use em dashes (—) sparingly. Default to a comma, period, parentheses, or colon when the clause relationship is clear without one. Em dashes ARE acceptable for the term/definition pattern in flip cards (`**Term** — Definition`) and rare emphasis where another mark would be ambiguous. Overuse signals AI generation.
- No exclamation points in body copy.
- Numbers under 10 spelled out; 10 and above as numerals.
- Percent: % sign, no space, never spelled out.
- Headlines / subheads: title case, no terminal punctuation, 1-7 words preferred.
- Bullets: capitalize first word; no period unless complete sentence; parallel
  construction; alpha-order unless sequence matters.
- "Healthcare" one word. "Email" not "e-mail." Use "physician" not "doctor."
  "OB/GYN" with slash. "U.S." not "US." Capitalize the X in "X-ray."
- Singular "they/their" for gender-neutral references; never "he or she."
- Person-first language ("a patient with diabetes," not "a diabetic").
- Never write "patient suffers from" or "victim of" a disease.
- Use active voice. Second person ("you") when giving guidance.
- Avoid jargon and buzzwords ("stakeholder", "leverage", "robust").
</mm_voice>
""".strip()


# ---------------------------------------------------------------------------
# Tone for case-study / lesson content (educational, not blame-focused)
# ---------------------------------------------------------------------------
EDUCATIONAL_TONE = """
<tone>
- Educational focus, not blame-focused. Objective and learning-oriented.
- Include specific clinical detail when it enhances understanding (vital
  signs, lab values, exam findings).
- Active voice; past tense for case narratives.
- Maintain narrative flow.
- Hedge language ("may", "can") only when the underlying evidence does.
</tone>
""".strip()


# ---------------------------------------------------------------------------
# Output discipline — applies to every generation prompt
# ---------------------------------------------------------------------------
OUTPUT_DISCIPLINE = """
<output_discipline>
- Return ONLY the requested content. No preamble, no closing remarks,
  no "Here is the course..." framing, no "I hope this helps" trailer.
- Match the structure section-for-section, heading-for-heading. Do not
  add, drop, or rename sections. Heading levels must match exactly
  (## for lesson titles, ### for sub-sections, #### for case-study
  inner sections).
- Use plain markdown unless the structure specifies HTML.
- Bullets MUST start with "- " (single hyphen + space). Do NOT use "*",
  "•", or numbered lists for sections that the structure marks as
  bulleted. Numbered lists are reserved for the Objectives section and
  the Closing's Key takeaways.
- Each bulleted list item is a single sentence on a single line. Do not
  wrap a bullet across multiple paragraphs.
- One blank line between sections. No double-blank-line runs.
- Never wrap headings in bold (**Heading** is wrong; "## Heading" is
  right).
- Never quote the user-facing structure markers like "[topic_label]" or
  "[N]" literally — fill them in. If you cannot fill in a placeholder,
  omit the sentence rather than ship the brackets.
</output_discipline>
""".strip()


# ---------------------------------------------------------------------------
# GROUNDING — clinical facts MUST come from the playbook, not the model
# ---------------------------------------------------------------------------
GROUNDING_RULES = """
<grounding_rules>
You may freely choose the WORDING — sentence structure, transitions,
voice, examples — but every CLINICAL FACT, STRATEGY, RECOMMENDATION,
STATISTIC, or CITATION in the output MUST trace to the PLAYBOOK
section(s) provided in this prompt. The model's job is to make MM's
playbook content readable, not to add new clinical content.

CRITICAL — source alignment scoring evaluates whether each user-facing
sentence reflects a corresponding sentence in the PLAYBOOK input. Aim
for HIGH overlap of specific noun phrases between your output and the
playbook prose. Use the playbook's own terminology ("airway risk
assessment", "second-generation supraglottic airway", "closed-loop
communication") instead of generic paraphrase. When a "Mitigation
Strategy" block in the playbook lists 4 concrete tactics, paraphrase
those 4 specific tactics — do NOT replace them with generic advice.

Specifically:
- DO NOT invent risk-mitigation strategies. Every "Reducing clinical
  risks" / "Reducing non-clinical risks" bullet must paraphrase ONE
  specific named mitigation tactic from the relevant PLAYBOOK section,
  using at least two of the playbook's distinctive noun phrases for
  that tactic.
- DO NOT invent statistics, percentages, dollar amounts, frequencies,
  severity figures, or claim counts. If the playbook gives a "Clinical
  contributors account for 83%" line, you may quote it. If it does not
  give a specific number for what you want to say, write a qualitative
  statement instead ("clinical contributors are the dominant share")
  or omit the claim. Never make up a figure.
- DO NOT invent named guidelines, journal citations, or organisational
  recommendations (AHA, ACOG, ASA, etc.) unless they appear in the
  playbook. If the playbook references a guideline, you may name it;
  otherwise use generic language ("the standard pathway", "institutional
  protocol").
- DO NOT invent contributing factors, allegation categories, or named
  loss drivers beyond those listed in the PLAYBOOK and TOP CONTRIBUTING
  FACTORS sections.
- DO NOT echo back the input headings as content ("CLINICAL: DIAGNOSTIC"
  is a structural marker, not user-facing text). Translate it into a
  natural-language phrase like "the diagnostic decisions at the bedside".
- For case study scenarios (Lesson 3), the patient demographics, the
  timeline shape, and the clinical specifics MUST be plausible
  representations of the kind of case the named loss driver produces,
  drawn from the patterns described in the playbook prose. Anchor the
  timeline in CASE_NARRATIVE when one is provided. Write the case as an
  anonymised composite (no real patient identifiers, generalised dollar
  amounts).
- It IS acceptable to: reorganise playbook content into the lesson
  structure, paraphrase MM's sentences for flow, add transitional
  phrases, write the "Pause and reflect" prompts as original text,
  and provide qualitative interpretation of what the playbook content
  means for daily practice.

If a section requires content the playbook does not cover, write a
short qualitative paragraph noting what's at stake without citing
specifics, rather than padding with invented detail.
</grounding_rules>
""".strip()


# ---------------------------------------------------------------------------
# Content depth — substantive output, not skeletal
# ---------------------------------------------------------------------------
LENGTH_GUIDANCE = """
<length_and_depth>
The total course is sized to be worth ~1.0 AMA PRA Category 1 Credit™.
ACCME credit assumes ~250 words of substantive reading per minute, so
60 minutes of engagement maps to ~6,000-7,000 words of body content
(plus ~12 min of post-test and ~3 min of reflection prompts on top).

Hit these per-section targets; pad only if real clinical depth
requires it. Trade words across sections to hit the total — don't
inflate one section and starve another.

- **Lesson 1 (Course Overview)**: 500-700 words. "What You'll Learn"
  is 4-6 sentences explaining the *why*, not just listing topics.
  Objectives are 3 numbered items, each 1-2 sentences.
- **Lesson 2 (Loss Trends)**: 1,000-1,400 words. Each stat sub-section
  (Frequency, Indemnity, Allegations, Degree of Injury) is 3-5
  sentences. Use the playbook numbers AND explain what they mean for
  daily practice.
- **Lesson 3 (Key loss drivers + case studies)**: TOTAL across all
  case studies should be 3,200-4,500 words. Per-case length scales
  inversely to the case count to keep the course at ~1.0 credit:
    - 3 case studies → 1,100-1,500 words each
    - 4-5 case studies → 700-900 words each
    - 6-8 case studies → 400-550 words each
    - 9-12 case studies → 280-380 words each
    - 13+ case studies → 200-280 words each (tight, claim-grounded)
  Each case still carries ALL the required sub-sections (Medical
  summary, Timeline, Allegations, Outcome, Pause and reflect, Risk
  reduction strategies). Where word budget is tight, write fewer
  timeline cards (3 instead of 5-7) rather than skipping any
  sub-section. Allegations / clinical / non-clinical lists adapt:
  3-5 items when budget allows, 2-3 items at the tight end.
- **Lesson 5 (Closing)**: 400-600 words. Takeaways are each 1-2
  sentences, not single-word labels.
- **Peer Review Commentary** (claims lessons): 2-4 paragraphs.
- **Patient Outcome** (claims lessons): 2-4 paragraphs covering
  physical, emotional, and where relevant economic consequences.

This sizing keeps every course at the ~1.0 CME-credit-equivalent depth
regardless of how many contributing factors the driver has. Final
credit hours are designated by the accredited provider in the LMS — the
prompt's job is to produce content of that total depth.

Don't pad. Cut filler. Density of useful information beats word-count
gaming.
</length_and_depth>
""".strip()


# ---------------------------------------------------------------------------
# Helper to compose: stitches blocks with newline separators
# ---------------------------------------------------------------------------
def compose(*blocks: str) -> str:
    """Join prompt component blocks. Filters out empty blocks."""
    return "\n\n".join(b for b in blocks if b and b.strip())
