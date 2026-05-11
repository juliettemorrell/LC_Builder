# MyAdvice Builder — Snowflake / Cortex hand-off

Everything the data engineer needs to deploy MyAdvice Builder into
Snowflake and point it at real Cortex + real tables. Hand this single
file to a senior Snowflake engineer or paste it into a Cortex Analyst
chat with `WAREHOUSE`, `DATABASE`, and `SCHEMA` access on
`HACKATHON_DWH.ADVICE`.

The app **already runs without Snowflake** — a mock Cortex + mock data
under `data/*.json` keep the full UI demoable. The yellow `MOCK` pill
in the topbar tells you when you're not hitting real Cortex. To go
live, follow the checklist below; no Python code changes are required.


## Table of contents

1. [Quick start](#1-quick-start)
2. [What the app does](#2-what-the-app-does)
3. [Directory structure](#3-directory-structure)
4. [The setup prompt (paste into your Cortex chat)](#4-the-setup-prompt-paste-into-your-cortex-chat)
5. [Required tables + expected columns](#5-required-tables--expected-columns)
6. [Output tables — run before first save](#6-output-tables--run-before-first-save)
7. [Photo library — local mock → Snowflake stage](#7-photo-library--local-mock--snowflake-stage)
8. [Photo metadata + semantic search (Shutterstock library)](#8-photo-metadata--semantic-search-shutterstock-library)
9. [The 8 Cortex prompts the app sends](#9-the-8-cortex-prompts-the-app-sends)
10. [How a Cortex call is shaped](#10-how-a-cortex-call-is-shaped)
11. [Per-prompt model + temperature](#11-per-prompt-model--temperature)
12. [Save / load, audit log, exports](#12-save--load-audit-log-exports)
13. [Validating the wiring](#13-validating-the-wiring)
14. [Failure modes worth pre-checking](#14-failure-modes-worth-pre-checking)
15. [Versioning](#15-versioning)


---

## 1. Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Unified app (splash + both builders behind it)
streamlit run app.py

# Or standalone:
streamlit run app_course_generator.py
streamlit run app_claims_lesson.py
```

Smoke test before deploying:

```bash
python -m tests.test_pipeline
```

End-to-end check: prompt assembly → mock Cortex → PDF → SCORM →
save/load → chat audit log → HTML preview. Exits 0 on success.


## 2. What the app does

Two Streamlit apps that share a single backbone:

- **Course Generator** — Pick a risk playbook → get a full
  CME-format course in MagMutual's "Reducing Liability" 5-lesson
  layout. Each contributing factor MM authored advice for becomes
  its own embedded case study with timeline, allegations, outcome,
  Pause-and-Reflect, and clinical / non-clinical risk-reduction
  strategy tabs.
- **Claims Lesson Generator** — Surfaces ranked candidate claims;
  pick one and the app generates a full lesson grounded in both
  the claim summary and the matching Risk Playbook section.

Both run **chat-first** (centered hero with a playbook grid or claim
table) and switch to a **split view** (chat left, MM-styled HTML
preview right) once content is generated.

What each generated course contains:

1. **Lesson 1 — Course Overview**: What You'll Learn + Objectives
2. **Lesson 2 — Loss Trends**: Why this matters · Clinical vs.
   administrative contributors · Most frequent allegations · Degree
   of injury · Top contributing factors chart · Pause and reflect
3. **Lesson 3 — Key loss drivers & risk reduction strategies**: one
   case study per playbook factor (4-8 per driver) with hero photo,
   medical summary, timeline cards, allegations, outcome,
   Pause-and-Reflect dark banner, REDUCING CLINICAL / NON-CLINICAL
   strategy tab control
4. **Lesson 4 — Assessment**: 10 questions, one-at-a-time UI,
   80% pass threshold, scenario-based + scored by confidence
5. **Lesson 5 — Closing**: 5 takeaways derived from playbook factors,
   Pause-and-Reflect, What's next


## 3. Directory structure

```
advice_buildathon/
├── app.py                          # Unified entry — splash + builders
├── app_course_generator.py         # Course generator (also standalone)
├── app_claims_lesson.py            # Claims lesson (also standalone)
├── home.py                         # Optional landing page
├── requirements.txt                # Python deps
├── shared/
│   ├── carbon.py                   # CSS + UI primitives
│   ├── cortex.py                   # Cortex wrapper · MODELS / TEMPS dicts
│   ├── snowflake_client.py         # Data layer with mock fallback
│   ├── prompts.py                  # All 8 prompts + builders, version-stamped
│   ├── prompt_components.py        # Reusable rule blocks (de-id, MM voice, ...)
│   ├── style_guide.py              # MM Copy Guide constant
│   ├── confidence.py               # Confidence-grade JSON parser
│   ├── chat_orchestrator.py        # Chat / quick-action handlers + audit
│   ├── chat_log.py                 # COURSE_EDIT_LOG audit trail
│   ├── quick_actions.py            # 6 quick-action chip definitions
│   ├── course_preview.py           # Live MM-styled HTML preview
│   ├── photos.py                   # Photo library (local-mock + stage)
│   ├── export.py                   # PDF (Lato-embedded) + markdown export
│   ├── scorm.py                    # SCORM 1.2 zip builder
│   ├── saves.py                    # Save/load (Snowflake VARIANT + local JSON)
│   └── fonts/Lato-*.ttf            # Bundled Lato faces
├── data/
│   ├── *_mock.json                 # Mock data when Snowflake is offline
│   ├── cortex_setup_prompt.md      # ← this file
│   ├── sample_cortex_prompts/      # Ready-to-paste Cortex prompts (8 kinds)
│   ├── photos/                     # Local-mock photo library + manifest.json
│   └── saved/                      # Local-mode draft + edit-log storage
├── scripts/
│   ├── import_real_data.py         # Excel/CSV → mock JSON refresher
│   └── dump_sample_prompts.py      # Re-dump sample_cortex_prompts/ per driver
├── tests/
│   └── test_pipeline.py            # Smoke test (9 groups)
└── .streamlit/config.toml          # Theme
```


---

## 4. The setup prompt (paste into your Cortex chat)

> **Role:** You are a senior Snowflake engineer + Cortex specialist. I just
> dropped a Streamlit app called "MyAdvice Builder" into our Snowflake
> deployment. It generates CME-style risk-mitigation courses and Claims
> Lessons by calling `SNOWFLAKE.CORTEX.COMPLETE` against our existing risk
> playbook + claims tables.
>
> **Your job, in order:**
>
> 1. **Verify Cortex is enabled** for the role/warehouse the Streamlit app
>    runs as. Confirm the role has `USAGE` on `SNOWFLAKE.CORTEX_FUNCTIONS`
>    and that the models in `shared/cortex.py · MODELS` are all available
>    in this region (`claude-opus-4-7`, `claude-3-5-sonnet`). If a model
>    isn't available, propose the closest substitute and tell me which
>    `MODELS[…]` entry to update.
>
> 2. **Confirm every input table exists** at the names listed in section 5
>    below. If any table is named differently, tell me the real name AND
>    the corresponding `ADVICE_T_*` env var to set — do NOT change the
>    Python code.
>
> 3. **Inspect each table's columns** and verify they match the columns
>    the app expects. If a column is missing or named differently, propose
>    either a view that aliases it OR an `ADVICE_T_*` override that points
>    at a different table that does have the column.
>
> 4. **Auto-create the output tables** by running the DDL in section 6.
>    They store user-saved drafts. The Python code already has
>    `CREATE TABLE IF NOT EXISTS` calls, but creating them up-front with
>    the documented grants is cleaner.
>
> 5. **Test one end-to-end Cortex call** — I'll click *Test Cortex
>    connection* in the Tools popover. Watch the warehouse query history
>    and confirm `SNOWFLAKE.CORTEX.COMPLETE` is being called with
>    `max_tokens=32000` (the app sets this to defeat Cortex's silent
>    4096-token truncation). If it returns the literal word "OK", we're
>    wired up.
>
> 6. **Audit the per-prompt model + temperature mapping** in
>    `shared/cortex.py · MODELS / TEMPS`. If our region's Cortex pricing
>    or capacity makes one of these inappropriate, suggest a swap and
>    explain the trade-off (latency vs. accuracy). DO NOT lower clinical-
>    content temperatures below 0.0 or raise them above 0.4 without
>    flagging it back to me.
>
> 7. **Stand up the photo stage** (section 7) and ideally the photo
>    metadata side-table (section 8) so the picker has a real library.
>
> 8. **Verify a draft round-trips.** Save a draft from the app, refresh,
>    confirm it appears in *Tools → Saved drafts*.
>
> 9. **Surface any issues you found** in this exact format:
>
>      ```
>      ✓ <thing that worked>
>      ✗ <thing that didn't work> — fix: <one-sentence remediation>
>      ⚠ <thing to watch> — note: <one-sentence note>
>      ```
>
> Don't proactively rewrite my code. If a fix needs a code change, paste
> the diff for me to review — never apply it directly.


---

## 5. Required tables + expected columns

| Need                        | Where it lives in code                            | Snowflake object               |
|----------------------------|---------------------------------------------------|--------------------------------|
| Risk Playbook (drivers)    | `shared/snowflake_client.py · T_RISK_LIBRARY`     | `RISK_LIBRARY_DRAFT` *(view/table, optionally `STATUS='Approved'`)* |
| Per-driver loss stats      | `T_RISK_DRIVER_STATS`                              | `RISK_DRIVER_STATS`            |
| Claim narratives (summary) | `T_CLAIM_SUMMARIES`                                | `CLMS_IR_OCR_DOCUMENT_SUMMARIES` |
| Claim full text (drill-in) | `T_CLAIM_FULL`                                     | `CLMS_IR_OCR_MFQ_SCRUBBED`     |
| Claim → driver tagging     | `T_CLAIM_RISK_TAGS`                                | `CLAIM_RISK_DRIVER_TAGS`       |
| Saved courses              | `shared/saves.py · T_COURSES`                     | `GENERATED_COURSES` *(auto)*   |
| Saved claims lessons       | `T_LESSONS`                                        | `GENERATED_LESSONS` *(auto)*   |
| Chat audit log             | `shared/chat_log.py · LOG_TABLE`                  | `COURSE_EDIT_LOG` *(auto)*     |
| Cortex calls               | `shared/cortex.py · _real_complete()`             | `SNOWFLAKE.CORTEX.COMPLETE`    |
| Case-study photo library   | `shared/photos.py · PHOTO_STAGE`                  | `COURSE_PHOTOS` *(stage)*      |
| Photo metadata (optional)  | `shared/photos.py · PHOTO_METADATA_TABLE`         | `COURSE_PHOTOS_METADATA`       |

If your real schema uses different names, **don't change the code** —
override per-table with environment variables:

```bash
export ADVICE_T_RISK_LIBRARY='HACKATHON_DWH.ADVICE.MY_REAL_LIBRARY'
export ADVICE_T_RISK_DRIVER_STATS='HACKATHON_DWH.ADVICE.MY_REAL_STATS'
export ADVICE_T_CLAIM_SUMMARIES='HACKATHON_DWH.ADVICE.MY_REAL_SUMMARIES'
export ADVICE_T_CLAIM_FULL='HACKATHON_DWH.ADVICE.MY_REAL_FULL'
export ADVICE_T_CLAIM_RISK_TAGS='HACKATHON_DWH.ADVICE.MY_REAL_TAGS'
export ADVICE_PHOTO_STAGE='HACKATHON_DWH.ADVICE.COURSE_PHOTOS'
export ADVICE_PHOTO_METADATA_TABLE='HACKATHON_DWH.ADVICE.COURSE_PHOTOS_METADATA'
```

### Expected columns by table

**`RISK_LIBRARY_DRAFT`** — minimum required: `SPECIALTY`, `DRIVER`,
`RISK_BRIEF` (multi-section prose containing CLINICAL: DIAGNOSTIC,
ADMINISTRATIVE: DOCUMENTATION, etc.). Optional: `STATUS` (when
present, app filters to `STATUS='Approved'`), `DRIVER_ID` (synthesized
from `(SPECIALTY, DRIVER)` if absent), `OVERVIEW`, `TITLE`,
`PRESENTING_CONDITIONS`, `ADVERSE_OUTCOMES`, and the 8 categorized
`CLINICAL_*` / `ADMINISTRATIVE_*` strategy columns (all optional; if
empty, the slicer extracts them from `RISK_BRIEF`).

**`RISK_DRIVER_STATS`** — `SPECIALTY`, `DRIVER`, optionally
`FULL_DRIVER_NAME`, `TOTAL_CONTRIBUTING_FACTORS`, and 21 contributing-
factor columns (each a fraction 0-1 of the total): `CLINICAL_DX_*` (4),
`CLINICAL_TX_*` (5), `CLINICAL_PROC_*` (4), `ADMIN_COMM_*` (2),
`ADMIN_DOCUMENTATION_FAILURE`, `ADMIN_PATIENT_NON_ADHERENCE`,
`ADMIN_PROF_*` (2), `ADMIN_SYS_*` (2). Drives the Lesson 2 bar chart.

**`CLMS_IR_OCR_DOCUMENT_SUMMARIES`** — `DOCUMENT_ID`, `SPECIALTY`,
`AGE_RANGE`, `SEX`, `PRESENTING_COMPLAINT`, `SUMMARY`,
`ADVERSE_OUTCOME`, `ALLEGATIONS` (array), `RESOLUTION`.

**`CLAIM_RISK_DRIVER_TAGS`** — `DOCUMENT_ID`, `DRIVER_ID`,
`TAG_CONFIDENCE`.

**`CLMS_IR_OCR_MFQ_SCRUBBED`** — `DOCUMENT_ID`, `MASKED_EXTRACTED_TEXT`.


---

## 6. Output tables — run before first save

```sql
USE DATABASE HACKATHON_DWH;
USE SCHEMA ADVICE;

-- Saved courses (course generator)
CREATE TABLE IF NOT EXISTS GENERATED_COURSES (
    SAVE_ID    VARCHAR PRIMARY KEY,
    CREATED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP,
    UPDATED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP,
    TITLE      VARCHAR,
    DRIVER_ID  VARCHAR,
    PAYLOAD    VARIANT  -- includes _audit.prompt_version + _audit.builder_version
);

-- Saved claims lessons (claims lesson generator)
CREATE TABLE IF NOT EXISTS GENERATED_LESSONS (
    SAVE_ID    VARCHAR PRIMARY KEY,
    CREATED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP,
    UPDATED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP,
    TITLE      VARCHAR,
    CLAIM_ID   VARCHAR,
    PAYLOAD    VARIANT
);

-- Chat-edit audit trail (every quick action + chat edit lands here)
CREATE TABLE IF NOT EXISTS COURSE_EDIT_LOG (
    LOG_ID       VARCHAR PRIMARY KEY,
    SAVE_ID      VARCHAR,
    SECTION_ID   VARCHAR,
    OCCURRED_AT  TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP,
    KIND         VARCHAR,    -- 'quick_action' | 'chat_edit' | 'regenerate'
    INSTRUCTION  VARCHAR,
    PROMPT       VARCHAR,    -- truncated to first 4 KB
    BEFORE_TEXT  VARCHAR,
    AFTER_TEXT   VARCHAR,
    MODEL        VARCHAR,
    TEMPERATURE  FLOAT,
    LATENCY_MS   NUMBER,
    PROMPT_VERSION VARCHAR
);

-- Grants — adjust role names to match your environment
GRANT SELECT, INSERT, UPDATE, DELETE ON GENERATED_COURSES TO ROLE ADVICE_BUILDATHON_ROLE;
GRANT SELECT, INSERT, UPDATE, DELETE ON GENERATED_LESSONS TO ROLE ADVICE_BUILDATHON_ROLE;
GRANT SELECT, INSERT          ON COURSE_EDIT_LOG    TO ROLE ADVICE_BUILDATHON_ROLE;
```


---

## 7. Photo library — local mock → Snowflake stage

The repo ships 9 royalty-free medical Unsplash JPEGs under
`data/photos/` (referenced via `data/photos/manifest.json`) so the demo
isn't blank in offline mode. **In Snowflake, replace them with a real
stage** that your team populates with MagMutual-licensed imagery.

### What the local mock does
- `shared/photos.py · _list_local()` reads `data/photos/manifest.json`.
- Each entry maps `{id, label, category, file, description, tags}` to
  a JPEG/PNG/SVG file on disk; the renderer base64-encodes it as a
  `data:` URI for the iframe.
- Categories used by `auto_pick_for_topic()` to keyword-match a topic
  to a photo: `airway, cardiac, surgical, medication, documentation,
  communication, imaging, triage, monitoring`.

### Snowflake stage layout (production)

1. Create the stage:

   ```sql
   CREATE STAGE IF NOT EXISTS HACKATHON_DWH.ADVICE.COURSE_PHOTOS
     DIRECTORY = (ENABLE = TRUE)
     ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE');
   ```

2. Upload photos, **grouped into category sub-directories**. The
   directory name is read by `_list_stage()` as the photo's `category`,
   so it MUST match one of the keyword categories above for the topic
   auto-pick to work:

   ```bash
   # SnowSQL — adjust paths to your local image library
   PUT file:///path/to/your/photos/airway/*.jpg   @COURSE_PHOTOS/airway/   AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
   PUT file:///path/to/your/photos/cardiac/*.jpg  @COURSE_PHOTOS/cardiac/  AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
   PUT file:///path/to/your/photos/surgical/*.jpg @COURSE_PHOTOS/surgical/ AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
   # ... one per category
   ```

3. Grant SELECT + READ on the stage:

   ```sql
   GRANT USAGE ON DATABASE HACKATHON_DWH                  TO ROLE ADVICE_BUILDATHON_ROLE;
   GRANT USAGE ON SCHEMA HACKATHON_DWH.ADVICE             TO ROLE ADVICE_BUILDATHON_ROLE;
   GRANT READ  ON STAGE HACKATHON_DWH.ADVICE.COURSE_PHOTOS TO ROLE ADVICE_BUILDATHON_ROLE;
   ```

4. (Optional) Override the default stage path via env var:

   ```bash
   export ADVICE_PHOTO_STAGE='YOUR_DB.YOUR_SCHEMA.YOUR_PHOTO_STAGE'
   ```

### How the swap works at runtime

`shared/photos.py · list_photos()` does this:
- If a Snowpark session is detected (Streamlit-in-Snowflake or env-var
  connection), it calls `LIST @<PHOTO_STAGE>`, parses the directory
  layout for category, joins against the optional metadata table
  (section 8), and signs each file with
  `GET_PRESIGNED_URL(@stage, path, 3600)`.
- If the stage exists but is empty, it falls back to the local
  `data/photos/` mock so the demo isn't blank during initial setup.
- If no Snowpark session, it uses the local mock.

**No code changes** are required to switch between local mock and
stage-backed photos.

### User-uploaded photos

`add_uploaded_photo(filename, raw_bytes, mime)` is wired to the
"Upload your own" button. Today it caches uploads in-memory for the
session. To persist uploads to the stage, swap the function body to
PUT bytes against the active Snowpark session.


---

## 8. Photo metadata + semantic search (Shutterstock library)

For a real Shutterstock library, the stage holds the bytes and a
**side-table holds the searchable metadata** (title, description,
tags, category). The picker's `search_photos()` and
`auto_pick_for_topic()` functions in `shared/photos.py` already
understand this — they join the stage directory listing against the
metadata table when it exists.

### 1. Create the metadata table

```sql
CREATE TABLE IF NOT EXISTS HACKATHON_DWH.ADVICE.COURSE_PHOTOS_METADATA (
    RELATIVE_PATH   VARCHAR PRIMARY KEY,  -- matches stage LIST output
    TITLE           VARCHAR,              -- shown in picker dropdown
    DESCRIPTION     VARCHAR,              -- 1-2 sentence caption
    TAGS            ARRAY,                -- searchable keywords
    CATEGORY        VARCHAR,              -- maps to topic auto-pick
    SHUTTERSTOCK_ID VARCHAR,              -- traceability
    LICENSE_UNTIL   DATE,                 -- license expiration
    EMBEDDING       VECTOR(FLOAT, 768)    -- (optional) Cortex embedding
);

GRANT SELECT ON TABLE HACKATHON_DWH.ADVICE.COURSE_PHOTOS_METADATA
  TO ROLE ADVICE_BUILDATHON_ROLE;
```

Override the default table FQN via env var if your schema differs:

```bash
export ADVICE_PHOTO_METADATA_TABLE='YOUR_DB.YOUR_SCHEMA.YOUR_PHOTO_METADATA'
```

### 2. Ingest from your photo library's CSV / JSON export

Shutterstock and most stock-photo vendors deliver a metadata file
alongside the image bundle. A typical Shutterstock CSV has columns
like `filename, title, description, keywords, category, license_id`.

```sql
PUT file:///path/to/shutterstock_export.csv
    @COURSE_PHOTOS/_metadata/ AUTO_COMPRESS=FALSE OVERWRITE=TRUE;

COPY INTO COURSE_PHOTOS_METADATA (
    RELATIVE_PATH, TITLE, DESCRIPTION, TAGS, CATEGORY, SHUTTERSTOCK_ID
)
FROM (
    SELECT $1, $2, $3, SPLIT($4, ','), $5, $6
    FROM @COURSE_PHOTOS/_metadata/shutterstock_export.csv
)
FILE_FORMAT = (TYPE = CSV SKIP_HEADER = 1 FIELD_OPTIONALLY_ENCLOSED_BY = '"');
```

### 3. (Optional) Semantic search via Cortex embeddings

```sql
UPDATE COURSE_PHOTOS_METADATA
SET EMBEDDING = SNOWFLAKE.CORTEX.EMBED_TEXT_768(
    'snowflake-arctic-embed-m',
    TITLE || ' . ' || DESCRIPTION || ' . ' || ARRAY_TO_STRING(TAGS, ', ')
);

-- Then rank by cosine similarity:
SELECT RELATIVE_PATH, TITLE,
       VECTOR_COSINE_SIMILARITY(
           EMBEDDING,
           SNOWFLAKE.CORTEX.EMBED_TEXT_768('snowflake-arctic-embed-m', :query)
       ) AS SCORE
FROM COURSE_PHOTOS_METADATA
ORDER BY SCORE DESC
LIMIT 24;
```

`shared/photos.py · search_photos()` uses keyword-token scoring
today; swap it to call the SQL above when EMBEDDING is populated.


---

## 9. The 8 Cortex prompts the app sends

The app issues one of 8 Cortex prompt kinds per generation step. Each
has a hardcoded model + temperature (section 11). Ready-to-paste
prompt files live in [`data/sample_cortex_prompts/`](sample_cortex_prompts/) —
re-generate them for any driver with:

```bash
python3 scripts/dump_sample_prompts.py [DRIVER_ID]
```

| Kind | Builds | Used by |
|---|---|---|
| `course_body` | Lessons 1, 2, and Lesson 3 intro of the course | Course Generator (one call per course) |
| `embedded_lesson` | One Lesson-3 case study | Course Generator (N calls — one per playbook factor, 4-8 per driver) |
| `assessment` | 10-question HTML5 post-test | Course Generator (one call per course) |
| `closing` | Lesson 5 (Key takeaways + Pause and reflect + What's next), derived from playbook factor titles | Course Generator (one call per course) |
| `lesson` | Full claims-lesson (Summary → Key drivers → Advice → The Case → Patient Outcome → Allegations → Legal Disposition → Peer Review → Best Practices) | Claims Lesson Generator |
| `claim_selection` | Ranks candidate claims against the risk-driver library | Claims Lesson Generator (idle state) |
| `confidence` | JSON grader (returns `overall_grade`, `publication_decision`, dimension scores) | Both apps, after each generation/edit |
| `edit_section` | Applies a chat-style edit to a section. Wording-only by default; structural changes ONLY on unambiguous explicit requests (add/remove/restore/move/reorder/change layout) | Both apps, when the user types into the chat input |

Each prompt is composed from rule blocks in `shared/prompt_components.py`:
`DEID_RULES`, `FINANCIAL_RANGES`, `MM_VOICE`, `EDUCATIONAL_TONE`,
`OUTPUT_DISCIPLINE`, `LENGTH_GUIDANCE`, `GROUNDING_RULES`. The
`GROUNDING_RULES` block is the anti-hallucination guard: every clinical
fact, strategy, recommendation, statistic, or citation in the output
must trace to the PLAYBOOK section provided in the prompt — the model
can rewrite for flow but cannot invent new clinical content.


---

## 10. How a Cortex call is shaped

From `shared/cortex.py · _real_complete`:

```sql
SELECT SNOWFLAKE.CORTEX.COMPLETE(?, ?, PARSE_JSON(?))
-- positional params:
--   1: model          (e.g. 'claude-opus-4-7')
--   2: prompt         (full prompt text)
--   3: options JSON   ('{"temperature": 0.20, "max_tokens": 32000}')
```

`max_tokens=32000` is **required** — Cortex silently truncates at 4096
otherwise.

Transient errors (rate-limit / 503 / timeout) retry with exponential
backoff (0.4s, 0.8s, 1.6s). Non-transient errors fall back to the mock
path and surface in `cortex_status()['errors']` for the Tools popover.


---

## 11. Per-prompt model + temperature

Hardcoded in [`shared/cortex.py`](../shared/cortex.py) (`MODELS` +
`TEMPS` dicts). **Not user-adjustable** — clinical accuracy must stay
consistent across users + runs.

| Prompt kind | Model | Temperature |
|---|---|---:|
| `course_body` | claude-opus-4-7 | 0.20 |
| `embedded_lesson` | claude-opus-4-7 | 0.25 |
| `lesson` | claude-opus-4-7 | 0.20 |
| `assessment` | claude-opus-4-7 | 0.15 |
| `closing` | claude-opus-4-7 | 0.20 |
| `claim_selection` | claude-opus-4-7 | 0.10 |
| `edit_section` | claude-opus-4-7 | 0.30 |
| `confidence` | claude-3-5-sonnet | 0.00 |
| `quick_action` | claude-3-5-sonnet | 0.25 |

Clinical content stays at 0.15-0.25. Confidence is fully deterministic
at 0.00 so JSON parsing is stable. Do NOT lower clinical-content
temperatures below 0.0 or raise them above 0.4 without explicit
sign-off.


---

## 12. Save / load, audit log, exports

### Save / load
Each app has a **Save draft** button in the toolbar. Saves go to
`GENERATED_COURSES` / `GENERATED_LESSONS` (or `data/saved/*.json` when
Snowflake isn't connected). Audit columns (`prompt_version`,
`builder_version`, `saved_at`) are stamped into the PAYLOAD VARIANT:

```sql
SELECT PAYLOAD:_audit:prompt_version FROM GENERATED_COURSES;
```

The first save creates a record; subsequent saves on the same session
**update in place** (the button text changes to "Update save").

### Audit log
Every chat instruction + quick-action click is captured to
`COURSE_EDIT_LOG` (Snowflake) or `data/saved/edit_log.jsonl` (local).
Captured fields: section, kind, instruction, prompt, before/after
text, model, temperature, latency, prompt version. View recent edits
in the Tools popover; download the session as a CSV with one click.

### Exports
Each app's toolbar has three export buttons:
- **Export PDF** — Lato-embedded PDF styled to match the MM
  "Reducing Liability" reference. Built with `reportlab`. Bytes are
  content-hashed and cached so they don't rebuild on every Streamlit
  interaction.
- **Export SCORM** — SCORM 1.2 conformant zip with `imsmanifest.xml`
  + an `index.html` rendered in the same visual style + a SCORM
  runtime that posts `incomplete → completed` to the LMS API.
- **Markdown** — plain `.md` source.


---

## 13. Validating the wiring

### Smoke before deploying

```bash
python -m tests.test_pipeline
```

9 test groups. Exits 0 on success.

### Confirm Cortex SQL is being sent

```sql
SELECT
    QUERY_ID, START_TIME, EXECUTION_TIME / 1000 AS LATENCY_S,
    LEFT(QUERY_TEXT, 200) AS PROMPT_PREVIEW
FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
WHERE QUERY_TEXT ILIKE '%CORTEX.COMPLETE%'
ORDER BY START_TIME DESC
LIMIT 50;
```

Pair this with *Tools popover → Connection* counters in the app
(real-vs-mock call totals + last latency).

### Confirm the exact prompt the app sends

```sql
SELECT QUERY_TEXT
FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
WHERE QUERY_TEXT ILIKE '%CORTEX.COMPLETE%'
ORDER BY START_TIME DESC LIMIT 5;
```

The `QUERY_TEXT` column should contain the same prompt body shown in
[`data/sample_cortex_prompts/`](sample_cortex_prompts/) with
placeholders (`{COURSE BODY GOES HERE}`, etc.) replaced by real
generated content.


---

## 14. Failure modes worth pre-checking

| Symptom                                  | Likely cause                                   | Fix |
|------------------------------------------|------------------------------------------------|------|
| Status panel says "Mock" forever          | No Snowpark session                            | Confirm the role / warehouse has `USAGE` on the schema; check `.streamlit/secrets.toml` |
| Live + falling back to mock               | Cortex call errored                             | Check Cortex errors expander in the Tools popover; usually model name not enabled in region |
| Generated text feels short / cut off      | `max_tokens` got overridden                     | Confirm payload includes `"max_tokens": 32000` |
| Save draft errors                         | Output table missing                            | Re-run the DDL in section 6 with the right role |
| No claims show under a driver             | Tag table column name mismatch                   | Set `ADVICE_T_CLAIM_RISK_TAGS` to the right table |
| Photo picker is blank                     | Stage empty or unreadable                       | Stage falls back to local mock; verify `READ ON STAGE` grant; check `LIST @COURSE_PHOTOS` |
| Picker shows generic photos in production | Metadata table missing                          | Without `COURSE_PHOTOS_METADATA`, photos use directory-derived defaults. Create the table + ingest CSV (section 8) |
| Lesson 3 has 18 case studies              | Pre-fix code reading stats CSV directly         | App now caps Lesson 3 to `playbook_factors()` count (4-8 per driver). Pull latest. |


---

## 15. Versioning

Every assembled prompt embeds a `<prompt_version>` tag. The version is
`PROMPTS_VERSION` + `+c` + `COMPONENTS_VERSION` from
`shared/prompts.py` and `shared/prompt_components.py`. Bump those
whenever a prompt body changes; future audit queries can filter by it:

```sql
SELECT
    PAYLOAD:_audit:prompt_version AS PROMPT_VERSION,
    COUNT(*) AS COURSES_SAVED
FROM GENERATED_COURSES
GROUP BY 1
ORDER BY 1 DESC;
```

The `shared/prompt_components.py · COMPONENTS_VERSION` covers shared
style blocks (DEID_RULES, MM_VOICE, LENGTH_GUIDANCE, GROUNDING_RULES,
OUTPUT_DISCIPLINE). Changes to any of those bump the combined version
string.


---

_That's everything. Ping the buildathon team if anything in here is
ambiguous._
