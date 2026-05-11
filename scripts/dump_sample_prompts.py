"""Dump assembled Cortex prompts to disk for an example driver.

Drops one .txt per prompt-kind into `data/sample_cortex_prompts/` so the
team / SME / data engineer can:

  1. Audit exactly what we send to Cortex (no abstraction in the way)
  2. Paste the prompt into Snowflake's Cortex chat to test the model
     behavior end-to-end without the app
  3. Diff the prompt across versions when LENGTH_GUIDANCE / MM_VOICE /
     OUTPUT_DISCIPLINE / etc. change

Run:
    python3 scripts/dump_sample_prompts.py            # default driver: AN-AIRWAY
    python3 scripts/dump_sample_prompts.py UR-CANCER  # any driver_id
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared.snowflake_client import (  # noqa: E402
    list_risk_drivers, get_driver, top_contributing_factors,
    get_claim_summaries, get_full_claim,
)
from shared.prompts import (  # noqa: E402
    build_course_body, build_assessment, build_closing,
    build_embedded_lesson_for_topic, build_lesson, build_claim_selection,
    build_confidence, build_edit_section,
    playbook_strategies_text, playbook_factors,
)
from shared.cortex import MODELS, TEMPS, DEFAULT_MAX_TOKENS  # noqa: E402

OUT = ROOT / "data" / "sample_cortex_prompts"
OUT.mkdir(parents=True, exist_ok=True)


def _wrap(kind: str, prompt: str) -> str:
    """Wrap a prompt with metadata + the SnowSQL one-liner that runs it."""
    model = MODELS.get(kind, "claude-opus-4-7")
    temp = TEMPS.get(kind, 0.20)
    sql_doc = (
        "-- Run this in a Snowflake worksheet to test the prompt below.\n"
        "-- The app issues exactly this SQL via Snowpark.\n"
        f"-- model={model} · temperature={temp} · max_tokens={DEFAULT_MAX_TOKENS}\n"
        "SELECT SNOWFLAKE.CORTEX.COMPLETE(\n"
        f"  '{model}',\n"
        "  $${PASTE THE PROMPT BELOW HERE}$$,\n"
        f"  PARSE_JSON('{{\"temperature\": {temp}, \"max_tokens\": {DEFAULT_MAX_TOKENS}}}')\n"
        ") AS RESPONSE;\n"
    )
    header = (
        f"# Cortex prompt: {kind}\n"
        f"#\n"
        f"# model        : {model}\n"
        f"# temperature  : {temp}\n"
        f"# max_tokens   : {DEFAULT_MAX_TOKENS}\n"
        f"# prompt size  : {len(prompt):,} chars (~{len(prompt.split()):,} words)\n"
        f"#\n"
        f"# How to use:\n"
        f"#   1. Paste the SQL block below into a Snowflake worksheet.\n"
        f"#   2. Replace {{PASTE THE PROMPT BELOW HERE}} with the prompt body.\n"
        f"#   3. Run. The result is the same shape the app receives.\n"
        f"# ---------------------------------------------------------\n\n"
    )
    return header + sql_doc + "\n# ===== PROMPT BODY =====\n\n" + prompt


def main():
    driver_id = sys.argv[1] if len(sys.argv) > 1 else "AN-AIRWAY-MANAGEMENT-COMPLICATIONS"
    drivers = list_risk_drivers()
    if not any(d["id"] == driver_id for d in drivers):
        print(f"Driver {driver_id!r} not found. Available IDs (first 10):")
        for d in drivers[:10]:
            print(f"  {d['id']:42s}  {d['label']}")
        sys.exit(1)

    driver = get_driver(driver_id)
    factors = top_contributing_factors(driver_id)
    los = [
        f"Recognise the clinical features tied to {driver['DRIVER'].lower()}",
        f"Apply structured decision-support tools at key decision points",
        f"Document the differential, the reasoning, and the disposition rationale",
    ]
    pb_factors = playbook_factors(driver["RISK_BRIEF"])
    topic = (pb_factors[0]["title"] if pb_factors
             else "Failure to recognize a finding")

    # Pick a sample claim for the lesson + claim_selection prompts
    claims_df = get_claim_summaries()
    sample_claim = claims_df.iloc[0].to_dict() if len(claims_df) else {}
    full_extract = get_full_claim(sample_claim.get("DOCUMENT_ID", "")) or ""
    candidate_claims = claims_df.to_dict("records")[:5] if len(claims_df) else []

    prompts: dict[str, str] = {
        "course_body": build_course_body(
            driver, playbook_strategies_text(driver), los, top_factors=factors,
        ),
        "embedded_lesson": build_embedded_lesson_for_topic(
            "{COURSE BODY GOES HERE}", topic, sample_claim,
            index=1, total_cases=len(pb_factors) or 5, risk_driver=driver,
        ),
        "assessment": build_assessment("{COURSE BODY GOES HERE}", los),
        "closing": build_closing("{COURSE BODY GOES HERE}", driver),
        "lesson": build_lesson(sample_claim, driver, full_extract=full_extract),
        "claim_selection": build_claim_selection(
            candidate_claims, [], [driver],
        ),
        "confidence": build_confidence(
            "{GENERATED CONTENT TO SCORE}", [driver.get("RISK_BRIEF", "")],
            output_type="course_generator",
        ),
        "edit_section": build_edit_section(
            "Lesson 1", "{CURRENT SECTION TEXT}",
            driver.get("RISK_BRIEF", ""),
            "Tighten the opening paragraph to 3 sentences",
        ),
    }

    print(f"Driver: {driver['SPECIALTY']} · {driver['DRIVER']}  ({driver_id})")
    print(f"Output dir: {OUT}")
    print()
    for kind, prompt in prompts.items():
        path = OUT / f"{kind}.txt"
        path.write_text(_wrap(kind, prompt))
        size_kb = path.stat().st_size / 1024
        print(f"  ✓ {path.name:25s}  {len(prompt):>6,} chars · {size_kb:.1f} KB")
    # Also write a README pointing at the files
    readme = (
        "# Sample Cortex prompts\n\n"
        f"Generated for **{driver['SPECIALTY']} · {driver['DRIVER']}** "
        f"(`driver_id={driver_id}`). Re-run `python3 scripts/dump_sample_prompts.py [DRIVER_ID]` "
        "to refresh for any other driver.\n\n"
        "## Files\n\n"
        + "\n".join(f"- `{kind}.txt` — {kind.replace('_', ' ')} prompt"
                   for kind in prompts.keys())
        + "\n\n"
        "Each file starts with metadata (model, temperature, max_tokens) "
        "plus a SnowSQL block you can paste into a Snowflake worksheet to "
        "test the prompt end-to-end against Cortex.\n\n"
        "## Validating the wiring\n\n"
        "To confirm the app sends exactly this prompt:\n\n"
        "```sql\n"
        "SELECT QUERY_TEXT\n"
        "FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY\n"
        "WHERE QUERY_TEXT ILIKE '%CORTEX.COMPLETE%'\n"
        "ORDER BY START_TIME DESC LIMIT 5;\n"
        "```\n\n"
        "The QUERY_TEXT column should contain the same prompt body shown in "
        "these files (with `{COURSE BODY GOES HERE}` etc. replaced by real "
        "generated content).\n"
    )
    (OUT / "README.md").write_text(readme)
    print(f"  ✓ {('README.md'):25s}  index of the files")


if __name__ == "__main__":
    main()
