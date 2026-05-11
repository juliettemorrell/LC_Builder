"""Migrate user-provided risk library + stats into the local mock data.

Reads:
  - ~/Downloads/Risk Library.xlsx                 (specialty / driver / risk_brief)
  - ~/Downloads/Risk_Drivers_Consolidated_2.csv   (contributing-factor stats)

Writes:
  - data/risk_library_mock.json
  - data/risk_driver_stats_mock.json
  - data/claim_risk_tags_mock.json   (synthesised tags so the
                                       claim-driven UI still demos)
  - data/claim_summaries_mock.json   (kept; we only enrich tags)

Run with:
    python scripts/import_real_data.py

Re-run any time the source files change. The script is idempotent and
only writes JSON files in the `data/` directory.
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd


REPO = Path(__file__).resolve().parent.parent
DATA_DIR = REPO / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Inputs (override with CLI args if needed)
LIB_XLSX = Path(sys.argv[1]) if len(sys.argv) > 1 else \
    Path.home() / "Downloads" / "Risk Library.xlsx"
STATS_CSV = Path(sys.argv[2]) if len(sys.argv) > 2 else \
    Path.home() / "Downloads" / "Risk_Drivers_Consolidated_2.csv"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_SPEC_INITIALS = {
    "Anesthesiology": "AN",
    "Cardiology": "CA",
    "Dermatology": "DE",
    "Diagnostic Radiology": "DR",
    "Emergency Medicine": "EM",
    "Family Medicine": "FM",
    "Gastroenterology": "GA",
    "General Surgery": "GS",
    "Hospital Medicine": "HM",
    "Internal Medicine": "IM",
    "Neurology": "NE",
    "OBGYN": "OB",
    "Obstetrics and Gynecology": "OB",
    "Ophthalmology": "OP",
    "Orthopedics": "OR",
    "Otolaryngology": "OT",
    "Pediatrics": "PE",
    "Plastic Surgery": "PS",
    "Urology": "UR",
}


def _slugify(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").upper()
    return s[:40] or "DRIVER"


def _driver_id(specialty: str, driver: str) -> str:
    init = _SPEC_INITIALS.get(specialty.strip(), specialty.strip()[:2].upper())
    return f"{init}-{_slugify(driver)}"


def _adverse_outcomes(brief: str) -> str:
    """Pull the ADVERSE OUTCOME(S) line out of the risk brief."""
    if not isinstance(brief, str):
        return ""
    m = re.search(r"ADVERSE OUTCOME\(?S?\)?\s*:\s*([^\n]+)", brief, re.I)
    return (m.group(1).strip() if m else "")


def _presenting_conditions(brief: str) -> str:
    if not isinstance(brief, str):
        return ""
    m = re.search(r"PRESENTING CONDITION\(?S?\)?\s*:\s*([^\n]+)", brief, re.I)
    return (m.group(1).strip() if m else "")


# ---------------------------------------------------------------------------
# Load risk library (Excel)
# ---------------------------------------------------------------------------
print(f"→ reading {LIB_XLSX.name}")
lib_df = pd.read_excel(LIB_XLSX)
# Normalise column names — the user said only Specialty / Driver / Risk Brief
# are reliably parsed; ignore everything else.
lib_df.columns = [c.strip() for c in lib_df.columns]
keep = [c for c in lib_df.columns
        if c.lower() in ("specialty", "driver", "risk brief")]
lib_df = lib_df[keep].dropna(subset=keep[:2]).reset_index(drop=True)
# Standardise to the schema the rest of the app expects
library_rows = []
# Use iterrows() so we can access "Risk Brief" (column with space) by
# string key — itertuples mangles spaces and gives unreliable attribute
# names like _2 / _3 across pandas versions.
for _, r in lib_df.iterrows():
    spec = str(r.get("Specialty", "")).strip()
    drv = str(r.get("Driver", "")).strip()
    rec_brief = r.get("Risk Brief", "")
    if not isinstance(rec_brief, str):
        rec_brief = "" if pd.isna(rec_brief) else str(rec_brief)
    drv_id = _driver_id(spec, drv)
    title = drv  # use the bare driver phrase as TITLE
    library_rows.append({
        "DRIVER_ID":             drv_id,
        "SPECIALTY":             spec,
        "DRIVER":                drv,
        "TITLE":                 title,
        "RISK_BRIEF":            rec_brief,
        "OVERVIEW":              rec_brief[:600],   # preview slice
        "PRESENTING_CONDITIONS": _presenting_conditions(rec_brief),
        "ADVERSE_OUTCOMES":      _adverse_outcomes(rec_brief),
        # The rest of the playbook strategy fields aren't reliably parsed
        # from this version of the export. The risk-brief prose carries
        # the strategies inline; the prompt builder's
        # `playbook_strategies_text(driver)` reads RISK_BRIEF when these
        # fields are empty.
        "CLINICAL_DIAGNOSTIC":               "",
        "CLINICAL_TREATMENT":                "",
        "CLINICAL_PROCEDURAL_SURGICAL":      "",
        "ADMINISTRATIVE_COMMUNICATION":      "",
        "ADMINISTRATIVE_DOCUMENTATION":      "",
        "ADMINISTRATIVE_PATIENT_FACTORS":    "",
        "ADMINISTRATIVE_PROFESSIONAL_BEHAVIOR": "",
        "ADMINISTRATIVE_SYSTEMS_ISSUES":     "",
        "STATUS":                "Approved",
    })

# Deduplicate by DRIVER_ID (Excel sometimes has duplicates)
seen = set()
unique = []
for row in library_rows:
    if row["DRIVER_ID"] in seen:
        continue
    seen.add(row["DRIVER_ID"])
    unique.append(row)
library_rows = unique
print(f"  → {len(library_rows)} risk-library rows")


# ---------------------------------------------------------------------------
# Load stats (CSV)
# ---------------------------------------------------------------------------
print(f"→ reading {STATS_CSV.name}")
stats_df = pd.read_csv(STATS_CSV).dropna(how="all")
# Build a lookup from (specialty, driver) → DRIVER_ID consistent with the
# library rows above. This way the foreign key holds across both files.
spec_drv_to_id: dict[tuple[str, str], str] = {}
for row in library_rows:
    spec_drv_to_id[(row["SPECIALTY"].lower(), row["DRIVER"].lower())] = row["DRIVER_ID"]

# Stats columns (everything past DRIVER + FULL_DRIVER_NAME + TOTAL)
factor_cols = [c for c in stats_df.columns
               if c not in ("SPECIALTY", "DRIVER", "FULL_DRIVER_NAME",
                            "TOTAL_CONTRIBUTING_FACTORS")]

# Compute CLAIMS_FREQUENCY_PCT per specialty: a driver's share of all
# claims tagged to that specialty (TOTAL_CONTRIBUTING_FACTORS as the
# weight). Severity isn't in this file — leave as 0; the UI will display
# "—" / suppress the figure.
spec_total: dict[str, float] = {}
for r in stats_df.itertuples(index=False):
    spec = str(r.SPECIALTY).strip()
    spec_total[spec] = spec_total.get(spec, 0.0) + float(r.TOTAL_CONTRIBUTING_FACTORS or 0)

stats_rows = []
for r in stats_df.itertuples(index=False):
    spec = str(r.SPECIALTY).strip()
    drv = str(r.DRIVER).strip()
    drv_id = spec_drv_to_id.get((spec.lower(), drv.lower()))
    if not drv_id:
        # Stats row didn't match the library — synth a plausible id so we
        # don't drop it, and warn so the user can reconcile.
        drv_id = _driver_id(spec, drv)
        print(f"  ⚠ stats-row has no matching library row: {spec} · {drv} → synth {drv_id}")
    total = float(r.TOTAL_CONTRIBUTING_FACTORS or 0)
    spec_tot = spec_total.get(spec, 0.0)
    freq_pct = round(total / spec_tot * 100, 2) if spec_tot else 0.0
    # Mirror the Snowflake table shape — flat columns, not nested. The
    # data layer mirrors this so the prompt builder + charts can read
    # the same field names whether we're hitting Snowflake or the JSON
    # mock.
    row = {
        "DRIVER_ID":                  drv_id,
        "SPECIALTY":                  spec,
        "DRIVER":                     drv,
        "FULL_DRIVER_NAME":           str(r.FULL_DRIVER_NAME).strip(),
        "TOTAL_CONTRIBUTING_FACTORS": int(total),
        "CLAIMS_FREQUENCY_PCT":       freq_pct,
        # Severity isn't in this file. Leave as 0 — the UI suppresses 0
        # so we don't show "$0K" in the playbook card.
        "AVG_SEVERITY_USD":           0,
    }
    # Spread the 21 contributing-factor columns flat onto the row so
    # `SELECT * FROM RISK_DRIVER_STATS` returns identical fields.
    for col in factor_cols:
        row[col] = round(float(getattr(r, col) or 0), 4)
    stats_rows.append(row)
print(f"  → {len(stats_rows)} stats rows")


# ---------------------------------------------------------------------------
# Synthesise per-driver claim tags so the claim-picker UI still has data
# to show. Real per-claim tagging will overwrite these once Snowflake is
# connected.
# ---------------------------------------------------------------------------
claim_tags = []
existing_summaries_path = DATA_DIR / "claim_summaries_mock.json"
existing_summaries = (
    json.loads(existing_summaries_path.read_text())
    if existing_summaries_path.exists() else []
)
# Map each existing claim summary to the highest-frequency driver in its
# specialty (so the demo claim-picker keeps working without changing the
# claim_summaries file).
spec_to_top_driver: dict[str, str] = {}
for s in sorted(stats_rows, key=lambda x: -x["CLAIMS_FREQUENCY_PCT"]):
    if s["SPECIALTY"] not in spec_to_top_driver:
        spec_to_top_driver[s["SPECIALTY"]] = s["DRIVER_ID"]

for c in existing_summaries:
    drv_id = spec_to_top_driver.get(c.get("SPECIALTY", ""))
    if not drv_id:
        continue
    claim_tags.append({
        "DOCUMENT_ID":   c["DOCUMENT_ID"],
        "DRIVER_ID":     drv_id,
        "TAG_CONFIDENCE": 0.9,
    })


# ---------------------------------------------------------------------------
# Write JSON outputs
# ---------------------------------------------------------------------------
def _write(name: str, rows: list) -> None:
    path = DATA_DIR / name
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False))
    print(f"  ✓ wrote {len(rows)} rows → {path.relative_to(REPO)}")


print()
print("→ writing JSON outputs")
_write("risk_library_mock.json", library_rows)
_write("risk_driver_stats_mock.json", stats_rows)
if claim_tags:
    _write("claim_risk_tags_mock.json", claim_tags)
print()
print("done. Restart Streamlit to pick up the new data.")
