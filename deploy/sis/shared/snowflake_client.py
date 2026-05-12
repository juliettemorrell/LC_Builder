"""Data access layer.

Reads from `HACKATHON_DWH` if a Snowflake session is available, otherwise falls
back to the JSON mock files under `data/`.

All four required tables plus the per-claim risk-driver tag table are exposed
as cached pandas DataFrames.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

from .cortex import _try_get_session

import os

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# ---- Real table names ----
# Defaults match the Buildathon brief. Override per-deployment with env vars
# without touching the code, e.g.:
#   export ADVICE_T_CLAIM_RISK_TAGS="HACKATHON_DWH.ADVICE.MY_REAL_TAG_TABLE"
T_RISK_LIBRARY = os.getenv(
    "ADVICE_T_RISK_LIBRARY", "HACKATHON_DWH.ADVICE.RISK_LIBRARY_DRAFT"
)
T_RISK_DRIVER_STATS = os.getenv(
    "ADVICE_T_RISK_DRIVER_STATS", "HACKATHON_DWH.ADVICE.RISK_DRIVER_STATS"
)
T_CLAIM_SUMMARIES = os.getenv(
    "ADVICE_T_CLAIM_SUMMARIES", "HACKATHON_DWH.ADVICE.CLMS_IR_OCR_DOCUMENT_SUMMARIES"
)
T_CLAIM_FULL = os.getenv(
    "ADVICE_T_CLAIM_FULL", "HACKATHON_DWH.ADVICE.CLMS_IR_OCR_MFQ_SCRUBBED"
)
# The "tagging table" — confirm name with DESCRIBE TABLE in your warehouse and
# override via ADVICE_T_CLAIM_RISK_TAGS env var if it differs.
T_CLAIM_RISK_TAGS = os.getenv(
    "ADVICE_T_CLAIM_RISK_TAGS", "HACKATHON_DWH.ADVICE.CLAIM_RISK_DRIVER_TAGS"
)


def _load_mock(name: str) -> pd.DataFrame:
    """Read the local mock JSON for offline / pre-deploy dev. Returns
    an empty DataFrame if the file doesn't exist — keeps SiS deploys
    that skip the mock files from crashing when a real query errors.
    """
    path = DATA_DIR / f"{name}.json"
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.DataFrame(json.loads(path.read_text()))
    except Exception:
        return pd.DataFrame()


def _query_or_mock(sql: str, mock_name: str,
                    fallback_sqls: tuple[str, ...] = ()) -> pd.DataFrame:
    """Run a SQL query against Snowflake, fall back to local mock JSON on
    any error. Errors are appended to st.session_state['_snowflake_errors']
    so the sidebar can surface them.

    `fallback_sqls` lets a caller pass relaxed variants of the query
    (e.g. without a STATUS filter) — we try each in order before giving
    up. Useful when the real Snowflake schema doesn't have every
    column the canonical query expects.
    """
    session = _try_get_session()
    if session is None:
        return _load_mock(mock_name)
    queries = (sql, *fallback_sqls)
    last_err = None
    for q in queries:
        try:
            rows = session.sql(q).collect()
            df = pd.DataFrame([r.as_dict() for r in rows])
            df = _normalize_columns(df)
            # Strip U+FFFD replacement chars from EVERY string column at the
            # source so downstream consumers (prompt assembly, course render,
            # claim_block, etc.) never see them. The MagMutual prose loaded
            # into Snowflake has these bytes baked in where em-dashes and
            # non-breaking spaces were mangled during the original ingest.
            for c in df.columns:
                if df[c].dtype == object:
                    df[c] = df[c].apply(
                        lambda v: _clean_text(v) if isinstance(v, str) else v
                    )
            return df
        except Exception as e:
            last_err = e
            continue
    try:
        st.session_state.setdefault("_snowflake_errors", []).append(
            f"{sql.splitlines()[0][:120]} → {str(last_err)[:240]}"
        )
    except Exception:
        pass
    return _load_mock(mock_name)


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Upper-case + underscore-normalise column names so downstream code
    can read e.g. d['RISK_BRIEF'] regardless of whether the source
    column was 'Risk Brief', '"Risk Brief"', or 'risk_brief'."""
    if df.empty:
        return df
    rename = {}
    for c in df.columns:
        canon = re.sub(r"[^A-Za-z0-9]+", "_", str(c)).strip("_").upper()
        if canon != c:
            rename[c] = canon
    return df.rename(columns=rename) if rename else df


import re  # noqa: E402  — used by _normalize_columns above


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def get_risk_library() -> pd.DataFrame:
    df = _query_or_mock(
        f"SELECT * FROM {T_RISK_LIBRARY} WHERE STATUS IN ('Final', 'Internal Review')",
        "risk_library_mock",
        # Fallback for tables without a STATUS column or where every row
        # is approved — try without the filter before falling to mock.
        fallback_sqls=(f"SELECT * FROM {T_RISK_LIBRARY}",),
    )
    return _ensure_driver_id(df)


@st.cache_data(ttl=3600, show_spinner=False)
def get_risk_driver_stats() -> pd.DataFrame:
    df = _query_or_mock(
        f"SELECT * FROM {T_RISK_DRIVER_STATS}",
        "risk_driver_stats_mock",
    )
    return _ensure_driver_id(df)


# ---------------------------------------------------------------------------
# Schema helpers — keep DRIVER_ID consistent regardless of source
# ---------------------------------------------------------------------------
_SPEC_INITIALS = {
    "Anesthesiology": "AN", "Cardiology": "CA", "Dermatology": "DE",
    "Diagnostic Radiology": "DR", "Emergency Medicine": "EM",
    "Family Medicine": "FM", "Gastroenterology": "GA", "General Surgery": "GS",
    "Hospital Medicine": "HM", "Internal Medicine": "IM", "Neurology": "NE",
    "OBGYN": "OB", "Obstetrics and Gynecology": "OB", "Ophthalmology": "OP",
    "Orthopedics": "OR", "Otolaryngology": "OT", "Pediatrics": "PE",
    "Plastic Surgery": "PS", "Urology": "UR",
}


def _slugify(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", str(s)).strip("-").upper()
    return s[:40] or "DRIVER"


def _synth_driver_id(specialty: str, driver: str) -> str:
    init = _SPEC_INITIALS.get(str(specialty).strip(),
                                str(specialty).strip()[:2].upper())
    return f"{init}-{_slugify(driver)}"


def _ensure_driver_id(df: pd.DataFrame) -> pd.DataFrame:
    """Some Snowflake schemas don't carry a DRIVER_ID column. Synthesize
    one from (SPECIALTY, DRIVER) so foreign keys (claim_risk_tags.DRIVER_ID
    etc.) hold across all data sources.
    """
    if df.empty:
        return df
    if "DRIVER_ID" not in df.columns and "SPECIALTY" in df.columns and "DRIVER" in df.columns:
        df = df.copy()
        df["DRIVER_ID"] = df.apply(
            lambda r: _synth_driver_id(r.get("SPECIALTY"), r.get("DRIVER")),
            axis=1,
        )
    return df


@st.cache_data(ttl=3600, show_spinner=False)
def get_claim_summaries() -> pd.DataFrame:
    """Claim summaries — aliases `CLAIM_NUMBER` → `DOCUMENT_ID` so the
    rest of the app (joins, lesson selectors, save records) stays
    unchanged.  Real table uses CLAIM_NUMBER as the primary identifier;
    the MFQ full-text table uses its own DOCUMENT_ID that does NOT join
    to CLAIM_NUMBER, so full-text drill-down is best-effort.
    """
    return _query_or_mock(
        f"SELECT *, CLAIM_NUMBER AS DOCUMENT_ID "
        f"FROM {T_CLAIM_SUMMARIES} "
        f"LIMIT 200",
        "claim_summaries_mock",
        # Fallback for envs that don't have CLAIM_NUMBER (mock data)
        fallback_sqls=(f"SELECT * FROM {T_CLAIM_SUMMARIES} LIMIT 200",),
    )


@st.cache_data(ttl=3600, show_spinner=False)
def get_claim_risk_tags() -> pd.DataFrame:
    return _query_or_mock(
        f"SELECT * FROM {T_CLAIM_RISK_TAGS}",
        "claim_risk_tags_mock",
    )


def _summary_for(document_id: str) -> Optional[str]:
    """Return the SUMMARY column from claim_summaries for `document_id`,
    or None if no row matches. Used as a graceful fallback when the MFQ
    full-text lookup can't find a matching DOCUMENT_ID (the two tables
    don't share a join key in this warehouse — CLAIM_RISK_DRIVER_TAGS
    carries CLAIM_NUMBER aliased as DOCUMENT_ID, while CLMS_IR_OCR_MFQ
    uses its own DOCUMENT_ID)."""
    df = get_claim_summaries()
    if df.empty or "DOCUMENT_ID" not in df.columns:
        return None
    match = df[df["DOCUMENT_ID"].astype(str) == str(document_id)]
    if len(match) == 0:
        return None
    return match.iloc[0].get("SUMMARY")


@st.cache_data(ttl=3600, show_spinner=False)
def get_full_claim(document_id: str) -> Optional[str]:
    """Drill-down into MFQ scrubbed text for a specific claim.

    The MFQ table's DOCUMENT_ID and the summaries view's DOCUMENT_ID (the
    latter is actually CLAIM_NUMBER) don't share a join key, so the MFQ
    lookup will usually return no rows. We then fall back to the summary
    text so the caller always has *something* to ground on.

    Returns None only when neither MFQ nor summaries has a matching row.
    """
    session = _try_get_session()
    if session is None:
        return _summary_for(document_id)
    sql = (
        f"SELECT MASKED_EXTRACTED_TEXT FROM {T_CLAIM_FULL} "
        f"WHERE DOCUMENT_ID = '{document_id}' LIMIT 1"
    )
    try:
        rows = session.sql(sql).collect()
        if rows:
            text = rows[0]["MASKED_EXTRACTED_TEXT"]
            if text:
                return str(text)
    except Exception:
        pass
    # MFQ had no row (different ID space) — degrade to summary text.
    return _summary_for(document_id)


# ---------------------------------------------------------------------------
# Convenience helpers used by the UI
# ---------------------------------------------------------------------------
def list_risk_drivers() -> list[dict]:
    """Return [(driver_id, label)] for dropdowns."""
    df = get_risk_library()
    return [
        {"id": r.DRIVER_ID, "label": f"{r.SPECIALTY} · {r.DRIVER}"}
        for r in df.itertuples(index=False)
    ]


def _clean_text(s: str) -> str:
    """Strip Unicode replacement characters (U+FFFD `�`) and common
    mojibake out of a string and tidy whitespace.

    The MagMutual playbook prose loaded into Snowflake contains `�`
    bytes where en-dashes, em-dashes, and non-breaking spaces were
    mangled during the original load. Without this cleanup those
    characters surface in the rendered course as literal `�` glyphs.
    Replace each one with a single space, then collapse runs of
    whitespace so we don't end up with double spaces.
    """
    if not s:
        return s
    out = s
    # The replacement char itself
    out = out.replace("�", " ")
    # Common mojibake from cp1252 → utf-8 mis-decodes that often coexist
    out = out.replace(" ", " ")  # non-breaking space
    out = out.replace("​", "")    # zero-width space
    out = out.replace(" ", "\n")  # line separator → real newline
    out = out.replace(" ", "\n\n")
    # Collapse 2+ consecutive spaces (but preserve newlines)
    import re as _re
    out = _re.sub(r"[ \t]{2,}", " ", out)
    # Don't strip outer whitespace — let callers do that if they need to
    # (we may be cleaning a middle of a longer block).
    return out


def _clean_row(d: dict) -> dict:
    """Coerce pandas NaN to empty strings and strip replacement chars
    out of string values. Lists are preserved (e.g., LEARNING_OBJECTIVES).
    Dicts pass through."""
    import math
    out = {}
    for k, v in d.items():
        if v is None:
            out[k] = ""
        elif isinstance(v, float) and math.isnan(v):
            out[k] = ""
        elif isinstance(v, str):
            if v.lower() == "nan":
                out[k] = ""
            else:
                out[k] = _clean_text(v)
        else:
            out[k] = v
    return out


def get_driver(driver_id: str) -> Optional[dict]:
    df = get_risk_library()
    match = df[df["DRIVER_ID"] == driver_id]
    if len(match) == 0:
        return None
    row = _clean_row(match.iloc[0].to_dict())
    # Defensive: synthesise RISK_BRIEF if it's missing OR too short — the
    # live Snowflake load has RISK_BRIEF as just the title (~30-50 chars)
    # with the actual advice in per-category columns (CLINICAL_DIAGNOSTIC
    # etc.). Anything under 500 chars is treated as title-only and the brief
    # is rebuilt from those columns. Without this, playbook_factors() sees
    # no "Contributing action or omission" markers and the course collapses
    # to a single generic case study.
    existing_brief = (row.get("RISK_BRIEF") or "").strip()
    if len(existing_brief) < 500:
        # 1. Try a directly-aliased column name
        for alt in ("BRIEF", "FULL_TEXT", "FULLTEXT", "TEXT", "BODY",
                    "RISKBRIEF", "RISK_TEXT", "PLAYBOOK", "ADVICE"):
            v = row.get(alt)
            if v and isinstance(v, str) and len(v) > 200:
                row["RISK_BRIEF"] = v
                break
    if len((row.get("RISK_BRIEF") or "").strip()) < 500:
        # 2. Reconstruct from per-category columns (Parsed Sections schema)
        # using the same section-heading conventions playbook_factors() looks
        # for. Order matters — keep the canonical MM advice order so the
        # parsed factor list matches the playbook narrative.
        section_cols = [
            ("PRESENTING CONDITION(S)", "PRESENTING_CONDITION_S"),
            ("ADVERSE OUTCOME(S)",      "ADVERSE_OUTCOME_S"),
            ("",                         "OVERVIEW"),
            ("CLINICAL: DIAGNOSTIC",     "CLINICAL_DIAGNOSTIC"),
            ("CLINICAL: TREATMENT",      "CLINICAL_TREATMENT"),
            ("CLINICAL: PROCEDURAL/SURGICAL", "CLINICAL_PROCEDURAL_SURGICAL"),
            ("ADMINISTRATIVE: COMMUNICATION", "ADMINISTRATIVE_COMMUNICATION"),
            ("ADMINISTRATIVE: DOCUMENTATION", "ADMINISTRATIVE_DOCUMENTATION"),
            ("ADMINISTRATIVE: PATIENT FACTORS",
                                              "ADMINISTRATIVE_PATIENT_FACTORS"),
            ("ADMINISTRATIVE: PROFESSIONAL BEHAVIOR",
                                              "ADMINISTRATIVE_PROFESSIONAL_BEHAVIOR"),
            ("ADMINISTRATIVE: SYSTEMS ISSUES",
                                              "ADMINISTRATIVE_SYSTEMS_ISSUES"),
        ]
        parts: list[str] = []
        title = row.get("TITLE") or row.get("DRIVER") or ""
        if title:
            parts.append(str(title))
            parts.append(f"SPECIALTY: {row.get('SPECIALTY','')}")
        for head, col in section_cols:
            body = row.get(col)
            if not body or not str(body).strip():
                continue
            if head:
                parts.append(f"\n{head}\n\n{body}")
            else:
                parts.append(f"\n{body}")
        synthesised = "\n".join(parts).strip()
        if len(synthesised) > 200:
            row["RISK_BRIEF"] = synthesised
    return row


def get_stats(driver_id: str) -> Optional[dict]:
    df = get_risk_driver_stats()
    match = df[df["DRIVER_ID"] == driver_id]
    if len(match) == 0:
        return None
    return _clean_row(match.iloc[0].to_dict())


_FACTOR_LABELS = {
    "CLINICAL_DX_FAIL_ORDER_TESTING":         "Failure to order testing",
    "CLINICAL_DX_FAIL_RECOGNIZE_FINDING":     "Failure to recognize a finding",
    "CLINICAL_DX_FAIL_OBTAIN_HX_OR_PE":       "Failure to obtain history / physical",
    "CLINICAL_DX_OTHER":                       "Other diagnostic factor",
    "CLINICAL_TX_MEDICATION_ERROR":           "Medication error",
    "CLINICAL_TX_NON_MED_INTERVENTION_ERROR": "Error in non-medication intervention",
    "CLINICAL_TX_FAIL_MONITOR":               "Failure to monitor",
    "CLINICAL_TX_FAIL_CONSULT_OR_TRANSFER":   "Failure to consult or transfer",
    "CLINICAL_TX_OTHER":                       "Other treatment factor",
    "CLINICAL_PROC_TECHNIQUE_ERROR":          "Procedural technique error",
    "CLINICAL_PROC_WRONG_PT_SITE_PROC_IMPLANT":"Wrong patient / site / procedure / implant",
    "CLINICAL_PROC_RETAINED_FOREIGN_BODY":    "Retained foreign body",
    "CLINICAL_PROC_OTHER":                     "Other procedural factor",
    "ADMIN_COMM_BETWEEN_PROVIDERS":           "Communication between providers",
    "ADMIN_COMM_PROVIDER_TO_PATIENT":         "Provider-to-patient communication",
    "ADMIN_DOCUMENTATION_FAILURE":            "Documentation failure",
    "ADMIN_PATIENT_NON_ADHERENCE":            "Patient non-adherence",
    "ADMIN_PROF_INAPPROPRIATE_CONDUCT":       "Inappropriate conduct",
    "ADMIN_PROF_RECKLESS_OR_HEALTH":          "Recklessness or provider health",
    "ADMIN_SYS_LACK_EQUIPMENT_OR_TECH":       "Lack of equipment / technology",
    "ADMIN_SYS_LACK_PROCESS_OR_POLICY":       "Lack of process or policy",
}


# Map playbook factor TITLES (long-form, from the brief) to stats KEYS
# (short categorical, from the CSV). Used to filter the chart to only
# the factors that have case studies.
_PLAYBOOK_TITLE_TO_STATS_KEY: list[tuple[tuple[str, ...], str]] = [
    # Each tuple: (substring keywords that ALL must appear, target stats key).
    # Order matters — first match wins.
    (("history", "physical"),                 "CLINICAL_DX_FAIL_OBTAIN_HX_OR_PE"),
    (("order", "testing"),                    "CLINICAL_DX_FAIL_ORDER_TESTING"),
    (("recognize", "finding"),                "CLINICAL_DX_FAIL_RECOGNIZE_FINDING"),
    (("interpret",),                          "CLINICAL_DX_FAIL_RECOGNIZE_FINDING"),
    (("non-medication",),                     "CLINICAL_TX_NON_MED_INTERVENTION_ERROR"),
    (("non medication",),                     "CLINICAL_TX_NON_MED_INTERVENTION_ERROR"),
    (("medication", "error"),                 "CLINICAL_TX_MEDICATION_ERROR"),
    (("monitor",),                            "CLINICAL_TX_FAIL_MONITOR"),
    (("consult",),                            "CLINICAL_TX_FAIL_CONSULT_OR_TRANSFER"),
    (("transfer",),                           "CLINICAL_TX_FAIL_CONSULT_OR_TRANSFER"),
    (("technique",),                          "CLINICAL_PROC_TECHNIQUE_ERROR"),
    (("wrong",),                              "CLINICAL_PROC_WRONG_PT_SITE_PROC_IMPLANT"),
    (("retained",),                           "CLINICAL_PROC_RETAINED_FOREIGN_BODY"),
    (("between providers",),                  "ADMIN_COMM_BETWEEN_PROVIDERS"),
    (("between patient",),                    "ADMIN_COMM_PROVIDER_TO_PATIENT"),
    (("provider to patient",),                "ADMIN_COMM_PROVIDER_TO_PATIENT"),
    (("provider-to-patient",),                "ADMIN_COMM_PROVIDER_TO_PATIENT"),
    (("communication",),                      "ADMIN_COMM_BETWEEN_PROVIDERS"),
    (("documentation",),                      "ADMIN_DOCUMENTATION_FAILURE"),
    (("non-adherence",),                      "ADMIN_PATIENT_NON_ADHERENCE"),
    (("non adherence",),                      "ADMIN_PATIENT_NON_ADHERENCE"),
    (("inappropriate", "conduct"),            "ADMIN_PROF_INAPPROPRIATE_CONDUCT"),
    (("recklessness",),                       "ADMIN_PROF_RECKLESS_OR_HEALTH"),
    (("recognize responsibility",),           "ADMIN_PROF_INAPPROPRIATE_CONDUCT"),
    (("equipment",),                          "ADMIN_SYS_LACK_EQUIPMENT_OR_TECH"),
    (("technology",),                         "ADMIN_SYS_LACK_EQUIPMENT_OR_TECH"),
    (("process", "policy"),                   "ADMIN_SYS_LACK_PROCESS_OR_POLICY"),
    (("policy",),                             "ADMIN_SYS_LACK_PROCESS_OR_POLICY"),
]


def stats_key_for_playbook_title(title: str) -> Optional[str]:
    """Map a playbook factor title (long-form, from the brief) to its
    matching stats CSV key (short categorical). Returns None when no
    keyword pattern matches — caller should drop those from the chart
    rather than guess.
    """
    if not title:
        return None
    t = title.lower()
    for keywords, key in _PLAYBOOK_TITLE_TO_STATS_KEY:
        if all(kw in t for kw in keywords):
            return key
    return None


def chart_factors_from_playbook(driver_id: str, playbook_titles: list[str],
                                 *, sort_desc: bool = True) -> list[dict]:
    """Build the Lesson 2 chart from a list of playbook factor titles.

    Returns one row per title — 1:1 with the case studies in Lesson 3.
    Each row's `pct` is the matching stats CSV value (looked up via the
    title→key mapping); rows with no stats hit show pct=0.

    `sort_desc=True` (default) returns rows sorted by pct descending —
    biggest-impact factors first. The app uses the SAME sorted order
    for the Lesson 3 case studies, so the chart and Lesson 3 stay
    1:1 in count, titles, AND order.
    """
    stats = get_stats(driver_id) or {}
    out = []
    for title in playbook_titles:
        key = stats_key_for_playbook_title(title)
        pct = 0.0
        if key:
            try:
                pct = float(stats.get(key, 0)) * 100.0
            except (TypeError, ValueError):
                pct = 0.0
        out.append({"key": key or "", "label": title, "pct": round(pct, 1)})
    if sort_desc:
        # Stable sort: ties keep original playbook order so two factors
        # with the same pct don't shuffle randomly between renders.
        out.sort(key=lambda r: -r["pct"])
    return out


def top_contributing_factors(driver_id: str,
                              top_n: int | None = None,
                              filter_to_keys: list[str] | None = None) -> list[dict]:
    """Pull contributing-factor categories for a driver from the stats row.

    Returns `{key, label, pct}` dicts sorted by pct desc, dropping
    zero-pct entries. Default returns all non-zero factors.

    Note: this is the legacy stats-only view. The Lesson 2 chart uses
    `chart_factors_from_playbook()` instead — same factors as Lesson 3's
    case studies, in the same order — so the chart and Lesson 3 stay
    1:1. Use this function only when you want the raw stats picture.
    """
    stats = get_stats(driver_id) or {}
    out = []
    keep = set(filter_to_keys) if filter_to_keys else None
    for key, label in _FACTOR_LABELS.items():
        if keep is not None and key not in keep:
            continue
        v = stats.get(key, 0)
        try:
            pct = float(v) * 100.0
        except (TypeError, ValueError):
            continue
        if pct <= 0:
            continue
        out.append({"key": key, "label": label, "pct": round(pct, 1)})
    out.sort(key=lambda r: -r["pct"])
    if top_n is not None:
        return out[:top_n]
    return out


def claims_for_driver(driver_id: str, top_n: int = 5) -> pd.DataFrame:
    """Return the top-N claims tagged to this driver, sorted by tag confidence.

    The CLAIM_RISK_DRIVER_TAGS view in this warehouse already carries the
    fields we need for case studies (CASE_NARRATIVE, ALLEGATIONS, three
    ACTION_OR_OMISSION fields, PEER_REVIEW_SUMMARY) so we don't need to
    join to CLAIM_SUMMARIES — the join was a holdover from an older schema
    and was DROPPING claims when DOCUMENT_ID didn't round-trip cleanly to
    the summaries table (different ID spaces).

    Falls back to a summaries join only when the tags view is sparse for
    a given driver, so older deploys without the rich tags view still get
    something usable.
    """
    tags = get_claim_risk_tags()
    if tags is None or tags.empty:
        return pd.DataFrame()
    matched = tags[tags["DRIVER_ID"] == driver_id].copy()
    if matched.empty:
        return matched
    # Sort by tag confidence (desc), DOCUMENT_ID for deterministic ties
    if "TAG_CONFIDENCE" in matched.columns:
        matched["__tc__"] = pd.to_numeric(matched["TAG_CONFIDENCE"],
                                            errors="coerce").fillna(0)
        matched = matched.sort_values(["__tc__", "DOCUMENT_ID"],
                                       ascending=[False, True]).drop(columns="__tc__")
    matched = matched.head(top_n)

    # Surface a unified "SUMMARY" field so legacy callers (claim_block etc.)
    # still work without code changes. Prefer CASE_NARRATIVE since it's the
    # full claim story; fall back to ALLEGATIONS, then PEER_REVIEW_SUMMARY.
    def _summary_for_row(row):
        for col in ("CASE_NARRATIVE", "ALLEGATIONS", "PEER_REVIEW_SUMMARY"):
            v = row.get(col)
            if v and str(v).strip():
                return str(v)
        return ""
    if "SUMMARY" not in matched.columns:
        matched["SUMMARY"] = matched.apply(_summary_for_row, axis=1)

    # Optional summaries enrichment for any extra columns (AGE_RANGE etc.)
    # that exist in the legacy summaries table. Won't replace anything
    # already populated from the tags view.
    try:
        summaries = get_claim_summaries()
        if summaries is not None and not summaries.empty:
            extra_cols = [c for c in summaries.columns
                          if c not in matched.columns and c != "DOCUMENT_ID"]
            if extra_cols:
                matched = matched.merge(
                    summaries[["DOCUMENT_ID", *extra_cols]],
                    on="DOCUMENT_ID", how="left",
                )
    except Exception:
        pass

    # Replace NaN in string-y columns with empty strings, and strip
    # Unicode replacement chars (U+FFFD) from the prose fields so they
    # don't surface as ◇? in the rendered course.
    for c in matched.columns:
        if matched[c].dtype == object:
            matched[c] = (
                matched[c]
                .fillna("")
                .apply(lambda v: _clean_text(v) if isinstance(v, str) else v)
            )
    return matched.reset_index(drop=True)


def ranked_claims(top_n: int = 10) -> pd.DataFrame:
    """For App 2: rank all tagged claims by a teaching-value score.

    Real Snowflake tables don't expose driver-level frequency / severity
    aggregates (CLAIMS_FREQUENCY_PCT and AVG_SEVERITY_USD don't exist on
    RISK_DRIVER_STATS in the live schema). Teaching score therefore relies
    on just `TAG_CONFIDENCE` from the claim-risk-driver tags view and a
    deterministic tie-break by DOCUMENT_ID so the ordering is stable.
    """
    tags = get_claim_risk_tags()
    summaries = get_claim_summaries()
    library = get_risk_library()

    if tags is None or tags.empty:
        return pd.DataFrame()

    # Library version of SPECIALTY is canonical (the playbook's specialty).
    df = tags.merge(library[["DRIVER_ID", "DRIVER", "SPECIALTY"]],
                    on="DRIVER_ID", how="left")
    # Drop SPECIALTY from summaries to avoid a column collision in the merge.
    summary_cols = [c for c in summaries.columns if c != "SPECIALTY"]
    df = df.merge(summaries[summary_cols], on="DOCUMENT_ID", how="left")
    # Teaching score now uses only tag confidence — no frequency-weighted
    # multiplier because the underlying stat doesn't exist in prod.
    df["TEACHING_SCORE"] = pd.to_numeric(df.get("TAG_CONFIDENCE"),
                                           errors="coerce").fillna(0).round(3)
    df = df.sort_values(["TEACHING_SCORE", "DOCUMENT_ID"],
                         ascending=[False, True])
    # Clean NaN in object columns so downstream display + text formatting
    # doesn't leak "nan" into user-facing prose.
    for c in df.columns:
        if df[c].dtype == object:
            df[c] = df[c].fillna("")
    return df.head(top_n).reset_index(drop=True)
