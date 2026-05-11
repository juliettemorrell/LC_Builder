# Sample Cortex prompts

Generated for **Anesthesiology · Airway management complications** (`driver_id=AN-AIRWAY-MANAGEMENT-COMPLICATIONS`). Re-run `python3 scripts/dump_sample_prompts.py [DRIVER_ID]` to refresh for any other driver.

## Files

- `course_body.txt` — course body prompt
- `embedded_lesson.txt` — embedded lesson prompt
- `assessment.txt` — assessment prompt
- `closing.txt` — closing prompt
- `lesson.txt` — lesson prompt
- `claim_selection.txt` — claim selection prompt
- `confidence.txt` — confidence prompt
- `edit_section.txt` — edit section prompt

Each file starts with metadata (model, temperature, max_tokens) plus a SnowSQL block you can paste into a Snowflake worksheet to test the prompt end-to-end against Cortex.

## Validating the wiring

To confirm the app sends exactly this prompt:

```sql
SELECT QUERY_TEXT
FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
WHERE QUERY_TEXT ILIKE '%CORTEX.COMPLETE%'
ORDER BY START_TIME DESC LIMIT 5;
```

The QUERY_TEXT column should contain the same prompt body shown in these files (with `{COURSE BODY GOES HERE}` etc. replaced by real generated content).
