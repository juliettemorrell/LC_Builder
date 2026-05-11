# LC Builder — Streamlit-in-Snowflake bundle

The minimum file set to deploy LC Builder inside Snowflake. ~24 files,
~700 KB total (vs. the full repo's 58 files / 9.5 MB).

## What's in here

```
deploy/sis/
├── streamlit_app.py             ← SiS entry point
├── app.py                       ← splash + router
├── environment.yml              ← SiS package manifest (just reportlab)
├── .streamlit/
│   └── config.toml              ← Carbon theme
├── shared/                      ← all business logic + builders
│   ├── __init__.py
│   ├── course_app.py            ← Course Generator
│   ├── claims_app.py            ← Claims Lesson Generator
│   ├── cortex.py
│   ├── snowflake_client.py
│   ├── prompts.py
│   ├── prompt_components.py
│   ├── course_preview.py
│   ├── photos.py
│   ├── export.py
│   ├── scorm.py
│   ├── saves.py
│   ├── chat_log.py
│   ├── chat_orchestrator.py
│   ├── confidence.py
│   ├── quick_actions.py
│   ├── carbon.py
│   ├── style_guide.py
│   └── fonts/                   ← 5 Lato TTFs for the PDF export
└── data/                        ← LIGHTWEIGHT mock fallback
    ├── risk_library_mock.json   (4 drivers; full table has 73)
    ├── risk_driver_stats_mock.json
    ├── claim_summaries_mock.json
    ├── claim_risk_tags_mock.json
    └── photos/
        └── manifest.json        (empty stub — picker uses COURSE_PHOTOS stage)
```

## What's intentionally NOT here

- The 9 mock JPEGs in `data/photos/` — you're using a Snowflake stage
- The full 73-driver mock library — you're using `RISK_LIBRARY_DRAFT`
- `scripts/`, `tests/`, `home.py`, `README.md` (repo root), the
  `data/sample_cortex_prompts/` folder, `data/cortex_setup_prompt.md`
- The mocks are minimal: when your real tables resolve, none of the
  mock data is touched. It's only a safety net so the app boots cleanly
  during the first deploy before grants land.

## Deploy steps

1. Open Snowsight → **Projects → Streamlit → + Streamlit App**
2. Title: `LC Builder` · Location: `HACKATHON_DWH.ADVICE` · Warehouse:
   any size
3. Files icon (📁) → **+ Add → Upload from local** → drag ALL contents
   of this `deploy/sis/` folder (Cmd-A in Finder, then drag)
4. Packages tab → search **reportlab** → click to add
5. Confirm Main file = `streamlit_app.py`
6. Run

When the green `LIVE` pill shows in the topbar instead of yellow
`MOCK`, you're hitting real Cortex.

## What the data engineer needs separately

`data/cortex_setup_prompt.md` in the main repo
(<https://github.com/juliettemorrell/LC_Builder>) — the full hand-off
doc with grants, env vars, table DDL, and Cortex setup checklist.
