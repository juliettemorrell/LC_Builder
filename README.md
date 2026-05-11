# MyAdvice Builder — Buildathon

Two Streamlit apps for the Advice Team's risk-mitigation content workflow:

- **`app_course_generator.py`** — Pick a risk playbook → get a full CME course in MagMutual's "Reducing Liability" 5-lesson format. Each main topic in the playbook becomes its own embedded case-study lesson with timeline, allegations, outcome, pause-and-reflect, and clinical/non-clinical risk-reduction strategies.
- **`app_claims_lesson.py`** — Ranked claim picker. Pick one and the app generates a full lesson grounded in both the claim summary and the matching Risk Playbook section.

Both apps run **chat-first** (centered hero with a playbook grid or claim table) and switch to a **split view** (chat left, preview right, sticky chat, adjustable width) once content is generated.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Unified app (recommended) — splash screen with both builders behind it
streamlit run app.py

# Or run a single builder standalone
streamlit run app_course_generator.py
streamlit run app_claims_lesson.py
```

The apps run **without Snowflake** — a mock Cortex and mock data under `data/*.json` keep the full UI demoable. The yellow `MOCK` pill in the topbar tells you when you're not hitting real Cortex.

### Verify the pipeline before deploying

```bash
python -m tests.test_pipeline
```

Runs an end-to-end smoke test (prompt assembly → mock Cortex → PDF → SCORM → save/load → chat audit log → HTML preview). Exits 0 on success.

## Hooking up real Snowflake + Cortex

Create `.streamlit/secrets.toml`:

```toml
[connections.snowflake]
account = "your-account"
user = "your-user"
password = "your-password"
role = "ADVICE_ROLE"
warehouse = "ADVICE_WH"
database = "HACKATHON_DWH"
schema = "ADVICE"
```

`shared/cortex.py` picks up `st.connection("snowflake")` automatically and routes Cortex calls through SQL. The `max_tokens=32000` fix is baked into every call so you never silently truncate at 4096. Transient errors (rate-limit / 503 / timeout) retry with exponential backoff.

**Per-prompt model + temperature**: hardcoded in [`shared/cortex.py`](shared/cortex.py) (`MODELS` + `TEMPS` dicts). Clinical content (course body, embedded case studies, full claims lesson, assessment) → `claude-opus-4-7`. Faster low-stakes calls (confidence grading, quick actions) → `claude-3-5-sonnet`. **Not user-adjustable** — clinical accuracy must stay consistent across users + runs.

**Output tables + Cortex prep**: see [`data/setup.sql`](data/setup.sql) (DDL for `GENERATED_COURSES` / `GENERATED_LESSONS` / `COURSE_EDIT_LOG` + grants) and [`data/cortex_setup_prompt.md`](data/cortex_setup_prompt.md) (paste-ready prompt for a Cortex assistant).

## What gets generated (Course Generator)

The course matches MagMutual's "Reducing Liability in Dermatology" reference format:

1. **Lesson 1 of 5: Course Overview** — What You'll Learn + Objectives
2. **Lesson 2 of 5: Loss Trends** — Definitions, frequency, severity, top allegations, degree of injury, Pause and Reflect
3. **Lesson 3 of 5: Key Loss Drivers & Risk Reduction Strategies** — Each main topic from the playbook becomes a full embedded case study (renderered as a separate section card in the preview).
4. **Lesson 4 of 5: Assessment** — 10 questions, scenario-based, scored by confidence
5. **Lesson 5 of 5: Closing** — Key takeaways + Pause and Reflect

Per-topic embedded lesson includes:
- Medical summary
- Timeline (4–5 dated entries)
- Allegations (prose or list)
- Outcome ("settled for low six figures", etc.)
- Pause and reflect prompt
- Reducing clinical risks (bullet list)
- Reducing non-clinical risks (bullet list)

## Save / load

Each app has a **Save draft** button in the toolbar. Saves go to:

- **Snowflake** — `HACKATHON_DWH.ADVICE.GENERATED_COURSES` and `GENERATED_LESSONS`. Tables auto-create on first save when your role has CREATE TABLE. Audit columns (`prompt_version`, `builder_version`, `saved_at`) are stamped into the PAYLOAD VARIANT — query them with `PAYLOAD:_audit:prompt_version`.
- **Local JSON** — `data/saved/*.json` when Snowflake isn't connected.

Saved drafts list lives in the **🛠 Tools popover** (top toolbar). Click any to load. Click `✕` to delete.

The first save creates a record; subsequent saves on the same session **update in place** (the button text changes to "Update save").

## Chat audit log

Every chat instruction + quick-action click is captured to `COURSE_EDIT_LOG` (Snowflake) or `data/saved/edit_log.jsonl` (local fallback). Captured fields: section, kind, instruction, prompt, before/after text, model, temperature, latency, prompt version. View recent edits in the Tools popover; download the full session as a CSV with one click.

## Export

Each app's toolbar has three export buttons:

- **Export PDF** — Lato-embedded PDF styled to match the MagMutual "Reducing Liability" reference: cover with brand eyebrow + large title + 5-lesson TOC + CME-time disclosure, italic gray lesson eyebrows, oversized Lato Black titles with a short decorative gray rule, dark gray "Pause and reflect" hero banner, gray-chip numbered objectives, three-column definition flashcards, two-column strategy tabs (clinical / non-clinical), per-question assessment pages with green-highlighted correct answer + rationale callout, answer key. Built with `reportlab` — fonts subset on first generation. Bytes are content-hashed and cached so they don't rebuild on every interaction.
- **Export SCORM** — SCORM 1.2 conformant zip with `imsmanifest.xml`, an `index.html` rendered in the same visual style, and a SCORM runtime that posts `incomplete → completed` to the LMS API on page lifecycle. Drop into Cornerstone, LearnUpon, etc.
- **Markdown** — plain `.md` source — the same content that's in the styled PDF / SCORM, easy to diff or copy elsewhere.

## UI features

- **Chat-first idle state** with playbook grid (Course Generator) or ranked-claim table (Claims Lesson). Specialty filter, search, count caption.
- **Sticky chat panel** (CSS `:has()` rule) so it stays visible while you scroll the preview. Split is fixed at 35/65.
- **Per-section confidence**: badge (A/B green · C yellow · D/F red) + 5 inline dimension bars (Source Alignment, Completeness, Clinical Accuracy, Actionability, Clarity & Organization) with reasoning bullets in expander.
- **Six quick-action chips** above the chat input: Tighten · Expand · Clinical · Example · Fact-check · Plain.
- **Per-section Re-run / Edit / Undo** controls. Edit swaps to a markdown textarea; Save snapshots the prior version into the section's history stack.
- **Add lesson** affordance after generation — type a custom topic and append a new abridged case-study lesson at the bottom of the preview.
- **🛠 Tools popover** in the toolbar (replaces the old sidebar): live connection + model + last-call latency + retry counter, **Inspect last call** (full prompt + response preview for any kind), Test Cortex button, Saved drafts list, MagMutual Copy Guide, **Edit history** (every chat instruction + quick action, with CSS export), recent Cortex / Snowflake errors. Settings are not user-adjustable on purpose.
- **MagMutual visual style**: Lato fonts (bundled in `shared/fonts/`), italic gray "Lesson N of 5" eyebrows, oversized lesson titles with short decorative rule, dark gray "Pause and reflect" hero banner, gray-chip numbered objectives, two-column strategy tabs, definition flashcards.

## Architecture

```
advice_buildathon/
├── app.py                          # Unified entry — splash → builders
├── app_course_generator.py         # Course generator (callable standalone too)
├── app_claims_lesson.py            # Claims lesson (callable standalone too)
├── shared/
│   ├── carbon.py                   # CSS + reusable components, sidebar hidden
│   ├── cortex.py                   # Cortex wrapper · MODELS / TEMPS dicts ·
│   │                               # transient-error retry · last-call telemetry
│   ├── snowflake_client.py         # Data layer (mock fallback)
│   ├── prompts.py                  # All 7 prompts + builders, version-stamped
│   ├── prompt_components.py        # Reusable rule blocks (de-id, MM voice, ...)
│   ├── style_guide.py              # MM Copy Guide constant
│   ├── confidence.py               # Grade + parse JSON
│   ├── chat_orchestrator.py        # Apply chat / quick action + audit log call
│   ├── chat_log.py                 # COURSE_EDIT_LOG audit trail
│   ├── quick_actions.py            # Catalog of 6 quick-action prompts
│   ├── course_preview.py           # Live HTML preview (MM-styled)
│   ├── export.py                   # PDF (Lato-embedded) + markdown export
│   ├── scorm.py                    # SCORM 1.2 zip builder
│   ├── saves.py                    # Save/load (Snowflake VARIANT + local JSON)
│   │                               # with prompt_version + builder_version audit
│   └── fonts/Lato-*.ttf            # Bundled Lato faces (Regular/Bold/Italic/Black)
├── data/
│   ├── *_mock.json                 # Mock data when Snowflake is offline
│   ├── setup.sql                   # DDL for output + audit tables + grants
│   ├── cortex_setup_prompt.md      # Paste-ready Snowflake deployment prompt
│   └── saved/                      # Local-mode draft + edit-log storage
├── tests/
│   └── test_pipeline.py            # `python -m tests.test_pipeline` smoke test
└── .streamlit/config.toml          # Theme
```

## What's stubbed and what's real

- **The 4 team-authored prompts** (Confidence, Assessment, Claim Selection, Lesson) are inlined verbatim from `Prompts.csv`.
- **Course Body** and **Embedded Lesson** prompts are working placeholders that match MagMutual's "Reducing Liability" structure. Drop in Michelle's and Sogi's authored prompts when ready — the function signatures don't need to change.
- **Jasper** is intentionally NOT integrated. The MM Copy Guide ships as a string constant in `shared/style_guide.py` that gets appended to every generation prompt for brand voice.
- **Mock Cortex** produces full MagMutual-format content for the demo, including per-topic case studies. Connect Snowflake to swap in real Cortex output without code changes.
- **Per-prompt model + temperature** are hardcoded and **not user-adjustable** — clinical content uses Opus 4.7, fast small edits use Sonnet 3.5. Tune in `shared/cortex.py` (`MODELS` / `TEMPS`).
- **Chat audit log** writes to Snowflake (`COURSE_EDIT_LOG`) when connected, otherwise local JSONL. Both round-trip.
- **PDF & SCORM exports** are production-ready in style; SCORM runtime posts SCORM 1.2 lesson_status to the LMS API.

## Iterating on a draft

The split view supports four editing patterns:

1. **Quick chips** — pick a target section in the dropdown, click Tighten/Expand/Clinical/Example/Fact-check/Plain. One Cortex call rewrites just that section with the chosen instruction.
2. **Free-text chat** — describe the change you want. We route to the targeted section.
3. **Re-run** — re-execute the section's original prompt with the latest source material. Useful after editing the course body to refresh downstream sections.
4. **Direct edit** — toggle Edit on a section to hand-edit the markdown.

Every edit pushes a snapshot onto the section's history stack. Click **Undo** to roll back. Confidence is re-scored after every change.

## Known gaps

- No dark mode yet.
- SCORM 2004 (current uses SCORM 1.2; broadly supported but not the latest spec).
- Per-section regeneration cascades sources for downstream sections, but doesn't auto-regenerate them — you can click Re-run on each.
- Bar charts and hero photos in the MagMutual reference PDF (e.g. "Drivers by Severity / Frequency" charts on the loss-trends pages) aren't generated — text content only. The MM data viz is image-based.

---

_Built for the Advice Team Buildathon._
