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

from cortex import _try_get_session

import os

DATA_DIR = Path(__file__).resolve().parent

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


_INLINE_MOCKS = {
    "risk_library_mock":        lambda: _MOCK_RISK_LIBRARY,
    "risk_driver_stats_mock":   lambda: _MOCK_RISK_DRIVER_STATS,
    "claim_summaries_mock":     lambda: _MOCK_CLAIM_SUMMARIES,
    "claim_risk_tags_mock":     lambda: _MOCK_CLAIM_RISK_TAGS,
}

def _load_mock(name: str) -> pd.DataFrame:
    """Inline mock fallback so the app boots cleanly even when no data
    files are shipped alongside the code (flat SiS deploys)."""
    if name in _INLINE_MOCKS:
        try:
            return pd.DataFrame(_INLINE_MOCKS[name]())
        except Exception:
            pass
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
            return _normalize_columns(df)
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


@st.cache_data(ttl=3600, show_spinner=False)
def get_full_claim(document_id: str) -> Optional[str]:
    """Drill-down into MFQ scrubbed text for a specific claim.

    Returns None when no full record is available (mock mode just returns the
    summary text).
    """
    session = _try_get_session()
    if session is None:
        # Mock: return the summary so the lesson prompt still has substance
        df = get_claim_summaries()
        match = df[df["DOCUMENT_ID"] == document_id]
        if len(match) == 0:
            return None
        return match.iloc[0].get("SUMMARY")
    sql = (
        f"SELECT MASKED_EXTRACTED_TEXT FROM {T_CLAIM_FULL} "
        f"WHERE DOCUMENT_ID = '{document_id}' LIMIT 1"
    )
    try:
        rows = session.sql(sql).collect()
        if rows:
            return str(rows[0]["MASKED_EXTRACTED_TEXT"])
    except Exception:
        pass
    return None


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


def _clean_row(d: dict) -> dict:
    """Coerce pandas NaN to empty strings so downstream string formatting
    doesn't end up with 'nan' showing up in user-facing prose. Lists are
    preserved (e.g., LEARNING_OBJECTIVES). Dicts pass through."""
    import math
    out = {}
    for k, v in d.items():
        if v is None:
            out[k] = ""
        elif isinstance(v, float) and math.isnan(v):
            out[k] = ""
        elif isinstance(v, str) and v.lower() == "nan":
            out[k] = ""
        else:
            out[k] = v
    return out


def get_driver(driver_id: str) -> Optional[dict]:
    df = get_risk_library()
    match = df[df["DRIVER_ID"] == driver_id]
    if len(match) == 0:
        return None
    return _clean_row(match.iloc[0].to_dict())


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

    Drops rows where the summary join failed (so callers don't get NaN-only
    claims). Cleans remaining NaNs in object columns to empty strings.
    """
    tags = get_claim_risk_tags()
    summaries = get_claim_summaries()
    matched = tags[tags["DRIVER_ID"] == driver_id].sort_values(
        "TAG_CONFIDENCE", ascending=False
    ).head(top_n)
    out = matched.merge(summaries, on="DOCUMENT_ID", how="inner")
    # Replace NaN in string-y columns with empty strings
    for c in out.columns:
        if out[c].dtype == object:
            out[c] = out[c].fillna("")
    return out.reset_index(drop=True)


def ranked_claims(top_n: int = 10) -> pd.DataFrame:
    """For App 2: rank all tagged claims by a teaching-value score.

    Score = tag_confidence * driver_frequency_pct (severity used as tiebreak).
    """
    tags = get_claim_risk_tags()
    summaries = get_claim_summaries()
    stats = get_risk_driver_stats()
    library = get_risk_library()

    df = tags.merge(stats[["DRIVER_ID", "CLAIMS_FREQUENCY_PCT", "AVG_SEVERITY_USD"]],
                    on="DRIVER_ID", how="left")
    # The library version of SPECIALTY is canonical (the playbook's specialty).
    df = df.merge(library[["DRIVER_ID", "DRIVER", "SPECIALTY"]],
                  on="DRIVER_ID", how="left")
    # Drop SPECIALTY from summaries to avoid a column collision in the merge.
    summary_cols = [c for c in summaries.columns if c != "SPECIALTY"]
    df = df.merge(summaries[summary_cols], on="DOCUMENT_ID", how="left")
    df["TEACHING_SCORE"] = (df["TAG_CONFIDENCE"] * df["CLAIMS_FREQUENCY_PCT"] / 100.0).round(3)
    df = df.sort_values(["TEACHING_SCORE", "AVG_SEVERITY_USD"], ascending=[False, False])
    # Clean NaN in object columns so downstream display + text formatting
    # doesn't leak "nan" into user-facing prose.
    for c in df.columns:
        if df[c].dtype == object:
            df[c] = df[c].fillna("")
    return df.head(top_n).reset_index(drop=True)

# ---------------------------------------------------------------
# INLINE MOCK FALLBACKS (formerly data/*_mock.json)
# Used only when no Snowpark session is available.
# ---------------------------------------------------------------

_MOCK_RISK_LIBRARY = [
  {
    "DRIVER_ID": "AN-AIRWAY-MANAGEMENT-COMPLICATIONS",
    "SPECIALTY": "Anesthesiology",
    "DRIVER": "Airway management complications",
    "TITLE": "Airway management complications",
    "RISK_BRIEF": "Airway Management Complications\nSPECIALTY: Anesthesiology\n\nPRESENTING CONDITION(S): Airway Management\n\nADVERSE OUTCOME(S): Permanent brain damage, cardiac arrest or death\nAirway management complications are significant drivers of malpractice risk and severe patient harm in anesthesiology.\nMitigating Your Risk\u202f\u00a0\nWith\u00a0appropriate technique selection, vigilant monitoring and effective communication and documentation, anesthesiologists can mitigate the risks associated with difficult airway management and reduce malpractice exposure. This report examines recurring clinical and administrative failures that can lead to malpractice claims for airway management complications and provides strategies to help prevent them.\n\nClinical and Administrative Breakdowns\n\nMalpractice claims involving difficult airway complications in anesthesiology typically stem from a recurring set of clinical and administrative failures. Clinical contributors account for 71% of the risk, while administrative contributors account for the remaining 29%.\u00a0Any one of these\u2014or a combination\u2014can lead to severe patient harm and a malpractice claim, even if the other aspects of care were appropriate.\nActions and Omissions Driving Airway Management Complications\u00a0[insert chart]\nWhile multiple actions and omissions drive risk, our advice discusses the top contributors to them in each specialty. This helps physicians focus on the mitigation strategies that can most improve patient care and reduce potential liability.\nCLINICAL: TREATMENT\n\nError in Non-Medication Therapeutic Intervention\n\nContributing action or omission: Selection of inappropriate airway management strategy, failure to escalate to alternative techniques or fixation on a single approach despite repeated failures increase the risk of adverse outcomes.\n\nMitigation Strategies\n\nThe strategies below work together to support appropriate technique selection, timely escalation and avoidance of fixation errors.\nConduct a comprehensive preoperative airway risk assessment \nPerform and document an airway evaluation before every anesthetic, assessing multiple predictors of difficulty with direct laryngoscopy, video laryngoscopy, face-mask ventilation, supraglottic airway use and front-of-neck access. Preoperative predictors of difficult intubation are frequently identifiable in retrospective analyses of difficult airway claims; systematic assessment enables appropriate planning and reduces unanticipated difficulty.\nFormulate and communicate a primary and backup airway strategy\nEstablish a documented plan before induction that includes the primary technique, alternative approaches and explicit triggers for escalation. A documented strategy helps ensure that the team understands an alternative approach and reduces delays in escalation when the primary approach fails.\nLimit intubation attempts and avoid perseveration\nRestrict the number of laryngoscopy attempts\u2014generally no more than three optimized attempts before transitioning to alternative techniques or declaring failure\u2014consistent with CAFG and DAS recommendations. Repeated attempts cause airway edema and bleeding, progressively worsening conditions. Limiting attempts preserves rescue options and prevents \"cannot intubate, cannot oxygenate\" (CICO) scenarios.\nRecognize indications for awake intubation and implement when appropriate\nWhen appropriate, perform awake intubation when the patient is suspected to be a difficult intubation and one or more of the following apply: difficult ventilation predicted, increased aspiration risk, patient unlikely to tolerate brief apnea or expected difficulty with emergency invasive airway rescue.\u00a0This approach aligns with the 2022 ASA Difficult Airway Algorithm decision tree, which uses these specific criteria to guide pathway selection.\u00a0Recent studies from experienced centers demonstrate high success rates and low complication rates, though success rates vary with operator experience and institutional volume.\u00a0While video laryngoscopy has expanded options for managing difficult airways after induction, awake intubation remains an important technique that provides an extra margin of safety when multiple risk factors converge or when impossible intubation is predicted.\nCLINICAL: TREATMENT\n\nFailure to Monitor Patient Following Treatment or Intervention\n\nContributing action or omission: Insufficient monitoring after extubation or airway intervention increases the risk of unrecognized deterioration and delayed reintubation.\n\nMitigation Strategies\n\nThe strategies below work together to support early identification of post-extubation complications and prompt intervention.\nPerform structured extubation risk assessment\nBefore extubation, evaluate the patient\u2019s ability to tolerate removal of airway support and the potential difficulty of reintubation. Extubation failure occurs in 10%\u201320% of critically ill patients who meet weaning criteria, with rates varying by clinical context. In the perioperative setting, identifying at-risk patients allows for appropriate monitoring and rescue planning.\nMaintain enhanced monitoring in the immediate post-extubation period\nIn at-risk patients, continue pulse oximetry, capnography (when available) and frequent clinical assessment for an appropriate period post-extubation, recognizing that upper airway obstruction from laryngeal edema typically occurs soon after extubation. The duration of enhanced monitoring should be individualized based on patient risk factors and clinical trajectory. Early detection allows intervention before respiratory failure develops.\nUse airway exchange catheters for high-risk extubations\nIn patients with known difficult intubation or anticipated difficult reintubation, consider leaving an airway exchange catheter in place after extubation. The catheter provides a conduit for immediate reintubation if extubation fails, avoiding the need for a repeat difficult laryngoscopy.\nEstablish clear criteria for reintubation\nDefine objective postoperative thresholds, such as oxygen saturation, respiratory rate or work of breathing, that trigger reintubation rather than relying on subjective assessment. Delayed reintubation is associated with increased morbidity and mortality. Objective criteria reduce hesitation and prevent prolonged hypoxia.\n\n\nCLINICAL: TREATMENT\nMedication Error\n\nContributing action or omission: Incorrect drug selection, dosing errors or administration of look-alike medications during airway management increases the risk of adverse outcomes.\n\nMitigation Strategies\n\nThe strategies below work together to reduce medication errors during high-stress airway management situations.\nStandardize medication organization and labeling in the anesthesia workspace\nMaintain standardized medications that are pre-labeled and color-coded according to international standards and organized for rapid access. This may include pre-prepared medication trays, designated medication drawers with standardized layout, or automated dispensing cabinet configurations with emergency airway medication sets. The specific implementation should match institutional resources while ensuring that emergency medications are immediately accessible, clearly labeled and organized to reduce time pressure and cognitive load during emergencies.\u00a0Substitution errors and incorrect dosing are the most common error types in anesthesia medication management, and standardized organization reduces swap errors.\u00a0\nStore high-risk medications separately\nStore neuromuscular blocking agents and other high-risk medications separately from routine induction agents. Physical separation creates a forcing function that prevents inadvertent selection of paralytic agents when sedatives are intended.\nVerify medications verbally before administration\nRead the drug name and dose aloud before administration, particularly during emergencies. Verbal verification creates a cognitive pause that interrupts automatic behavior, allowing error detection before administration.\nCLINICAL: PROCEDURAL/SURGICAL\n\nTechnique-Related Error\n\nContributing action or omission: Technical errors during laryngoscopy, supraglottic airway placement or emergency surgical airway access increase the risk of failed airway management and patient harm.\n\nMitigation Strategies\n\nThe strategies below work together to ensure technical proficiency, appropriate device selection and timely escalation to definitive airway management when standard approaches fail.\nMaintain proficiency with video laryngoscopy as a primary technique\nDevelop and maintain expertise in video laryngoscopy for both routine and difficult airway management. Video laryngoscopy reduces failed intubation rates (risk ratio 0.41-0.51), improves first-pass success and provides better glottic visualization compared to direct laryngoscopy across patient populations and clinical settings. Video laryngoscopy appears to provide the greatest benefit for less experienced operators and in patients with difficult airway features. Maintain competency in direct laryngoscopy as a backup technique when video laryngoscopy fails or is unavailable\nEnsure competency in supraglottic airway device placement\nMaintain proficiency with second-generation supraglottic airway devices as both primary airway management tools and rescue devices. Supraglottic airways provide a critical rescue option when intubation fails. Second-generation devices offer advantages over first-generation devices, including higher seal pressures and gastric access.\nMaintain competency in emergency surgical airway techniques through simulation-based training\nParticipate in simulation-based cricothyrotomy training, preferably using the scalpel-bougie-tube technique, which is recommended by most airway societies. Initial training should include a minimum of five practice attempts, thought evidence suggests performance may continue to improve through 7-10 iterations. Cannula-based techniques may be considered by practitioners experienced in their use.\nWhen formal simulation programs are unavailable, consider low-cost alternatives such as porcine larynx models or self-made simulators, which have demonstrated effectiveness in improving procedural speed and accuracy.\nDeclare CICO early and proceed immediately to surgical airway\nWhen oxygenation is failing and both intubation and ventilation have failed, declare a CICO emergency and proceed immediately to front-of-neck access. Delay in attempting a surgical airway is a common error that contributes to claims. Early declaration and action prevent the hypoxic brain injury that occurs with prolonged failed rescue attempts.\n\nADMINISTRATIVE: COMMUNICATION\n\nCommunication Failure Between Providers\n\nContributing action or omission: Provider-to-provider communication failures, including inadequate team briefings and handoff breakdowns, increase the likelihood of clinical errors.\n\nMitigation Strategies\n\nThe strategies below work together to standardize communication processes and support coordinated team response.\nConduct pre-induction team briefings for anticipated difficult airways\nBefore induction in patients with known or suspected difficult airways, brief the entire team on the airway plan, backup strategies and role assignments. Briefings ensure that each team member understands their role during emergencies, reducing response time and improving outcomes.\nUse structured handoff tools during intraoperative care transitions\nImplement handoff protocols with electronic checklists that include airway management details, current airway status and any difficulties encountered. Intraoperative transitions are associated with adverse outcomes. Structured tools improve information transfer and reduce the risk of critical information loss.\nEmploy closed-loop communication during airway emergencies\nVerbalize instructions clearly to team members and require verbal confirmation and acknowledgement of task completion. Closed-loop communication ensures that instructions are heard, understood and executed, which is critical during high-stress CICO situations.\nCommunicate airway status at all care transitions\nShare airway assessment findings and management difficulties during handoffs to the PACU, ICU and ward staff. Post-extubation complications may occur after operating room transfer; receiving providers must understand the patient's airway risk profile to ensure appropriate monitoring and response to deterioration.\n\nADMINISTRATIVE: DOCUMENTATION\n\nDocumentation Failure\n\nContributing action or omission: Insufficient documentation of airway assessment, management attempts and complications compromises continuity of care and create liability exposure.\n\nMitigation Strategies\n\nThe strategies below work together to support comprehensive, contemporaneous documentation that protects both patients and practitioners.\nSystematically document preoperative airway assessment findings\nRecord specific airway examination findings (such as Mallampati class, mouth opening, thyromental distance, neck mobility and dentition) rather than documenting \"airway normal.\" Detailed documentation confirms an appropriate assessment and provides critical information for future anesthetics. Inadequate airway evaluation remains the most cited judgment failure in claims.\nSample documentation language: \"Airway assessment: Mallampati III, mouth opening 3 cm, thyromental distance 5 cm, limited neck extension, BMI 42, full dentition with prominent upper incisors. Short thick neck, no prior history of radiation or surgical airway changes. Prior anesthetic record reviewed \u2014 Grade 3 view with direct laryngoscopy (2019, Dr. Smith, Community Hospital). Predictors of difficult mask ventilation: BMI >40, Mallampati III, limited jaw protrusion. Predictors of difficult supraglottic airway: limited mouth opening, short thyromental distance.\nAssessment: Multiple predictors of difficult intubation and difficult mask ventilation. Awake intubation indicated per ASA criteria (suspected difficult intubation with predicted difficult ventilation).\"\nCreate a detailed record of airway management attempts\nDocument each intubation attempt intra- and postoperatively, including the device used, laryngoscopic view, reason for failure and complications. Detailed documentation demonstrates appropriate escalation, explains clinical decision-making and provides essential information for future airway management.\nSample documentation language: \"Induction: Propofol 200 mg, rocuronium 50 mg. First attempt: C-MAC hyperangulated blade, Grade 2a view, ETT 7.0 passed with bougie, confirmed with ETCO2 and bilateral breath sounds. SpO2 remained >95% throughout.\"\nCarefully document and communicate incidents of difficult airway management \nIn cases of difficult airway management, document findings postoperatively in a standard format, notify the patient both verbally and in writing and ensure that the information is prominently flagged in the medical record. Future anesthesiologists depend on this information to plan safe airway management. Failure to communicate difficult airway status to patients and future providers is a preventable source of harm.\nDocument the rationale for clinical decisions\nRecord the reasoning behind technique selection, particularly when deviating from standard approaches or managing anticipated difficulty. Documenting clinical reasoning demonstrates thoughtful decision-making and provides context that may be critical in defending care decisions.\nADMINISTRATIVE: PROFESSIONAL BEHAVIOR\nFailure to Recognize Responsibility \nContributing action or omission: Failure to perform adequate preoperative airway assessment, modify the airway plan based on risk factors or recognize indications for awake intubation increases the risk of unanticipated difficult airway emergencies.\n\nMitigation Strategies\n\nThe strategies below work together to ensure appropriate recognition of airway risk and selection of the safest management approach.\n\n\u2022 Perform comprehensive airway assessment before every anesthetic\n\nBefore initiating anesthetic care, ensure that an airway risk assessment is performed to identify patient, medical, surgical, environmental and anesthetic factors that may indicate potential for a difficult airway. Evaluate demographic information, clinical conditions, prior airway history and physical examination findings. No single characteristic is consistently more predictive of risk than another; assess and consider multiple factors. \n\n\u2022 Recognize indications for awake intubation\n\nPerform awake intubation when the patient is suspected to be a difficult intubation and one or more of the following apply: increased aspiration risk, patient unlikely to tolerate brief apnea, or expected difficulty with emergency invasive airway rescue. Recent studies demonstrate 95%\u201399% success rates and low complication rates (1.6%\u20135.4%) with awake intubation, but awake intubation is underused when difficult airways are clearly recognizable.\u00a0Video laryngoscopy can be used to perform awake intubation with success rates comparable to flexible bronchoscopy in available studies, with potentially shorter intubation times, making the technique more accessible to practitioners already familiar with VL. Proficiency with flexible bronchoscopy remains valuable, particularly for complex airway pathology. Awake intubation remains the ASA-recommended approach when appropriate and when the criteria above are met. The ASA acknowledges that clinical judgment, patient cooperation and clinical context may support proceeding with intubation after induction when benefits outweigh risks.\n\n\u2022 Modify the airway management plan based on identified risk factors\n\nWhen airway assessment identifies predictors of difficulty, adjust the management plan accordingly. This may include: ensuring that difficult airway equipment is immediately available, having a skilled assistant present, considering awake intubation or planning for video laryngoscopy as the primary technique. Document the rationale for the chosen approach.\n\n\u2022 Recognize high-risk clinical contexts\n\nMaintain heightened vigilance in settings associated with increased airway complications: emergency procedures, non-operating room locations, morbidly obese patients and patients with multiple comorbidities. These contexts are overrepresented in closed claims analyses and warrant additional preparation.\n\n\u2022 Participate in regular airway management training and simulation\n\nMaintain competency in both routine and rescue airway techniques through ongoing education and simulation. Human factors, including situational awareness, decision-making and avoidance of perseveration, play essential roles in airway management safety. Institutions should designate an airway lead to help institute protocols and ensure adequate training.",
    "OVERVIEW": "Airway Management Complications\nSPECIALTY: Anesthesiology\n\nPRESENTING CONDITION(S): Airway Management\n\nADVERSE OUTCOME(S): Permanent brain damage, cardiac arrest or death\nAirway management complications are significant drivers of malpractice risk and severe patient harm in anesthesiology.\nMitigating Your Risk\u202f\u00a0\nWith\u00a0appropriate technique selection, vigilant monitoring and effective communication and documentation, anesthesiologists can mitigate the risks associated with difficult airway management and reduce malpractice exposure. This report examines recurring clinical and administrative failu",
    "PRESENTING_CONDITIONS": "Airway Management",
    "ADVERSE_OUTCOMES": "Permanent brain damage, cardiac arrest or death",
    "CLINICAL_DIAGNOSTIC": "",
    "CLINICAL_TREATMENT": "",
    "CLINICAL_PROCEDURAL_SURGICAL": "",
    "ADMINISTRATIVE_COMMUNICATION": "",
    "ADMINISTRATIVE_DOCUMENTATION": "",
    "ADMINISTRATIVE_PATIENT_FACTORS": "",
    "ADMINISTRATIVE_PROFESSIONAL_BEHAVIOR": "",
    "ADMINISTRATIVE_SYSTEMS_ISSUES": "",
    "STATUS": "Approved"
  },
  {
    "DRIVER_ID": "CA-AORTIC-DISSECTION",
    "SPECIALTY": "Cardiology",
    "DRIVER": "Aortic Dissection",
    "TITLE": "Aortic Dissection",
    "RISK_BRIEF": "Aortic Dissection\n\n\nSPECIALTY: Cardiology\n\nPRESENTING CONDITION(S): Chest Pain, Back Pain, Syncope, Acute Cardiovascular Emergency\n\nADVERSE OUTCOME(S): Aortic Rupture, Stroke, End-Organ Malperfusion, Death\n\nAortic dissection\u00a0is\u202fa\u202fsignificant driver of medical malpractice risk and severe patient\u00a0harm in cardiology.\u202f\u00a0\n\nMitigating Your Risk\n\nWith timely identification of acute aortic syndrome, rapid imaging and vigilant monitoring during the acute phase,\u00a0cardiologists\u202fcan mitigate the risks associated with aortic dissection\u00a0and reduce malpractice exposure. This report examines recurring clinical and administrative failures that can lead to malpractice claims for aortic dissection\u202fand provides strategies to help prevent them.\u202f\u202f\u202f\u00a0\n\nClinical and Administrative Breakdowns\nMalpractice claims involving\u00a0aortic dissection stem from a recurring set of clinical and administrative failures.\u00a0Clinical contributors account for\u00a089%\u00a0of the risk, while administrative contributors account for the remaining\u00a011%.\u00a0Any one of these factors\u2014or a combination of them\u2014can lead to severe patient harm and a malpractice claim, even if the other aspects of care were\u00a0appropriate.\u00a0\u00a0\u00a0\nActions and Omissions Driving Aortic Dissection Risk [insert chart] \nWhile multiple actions and omissions drive risk, our advice discusses the top contributors to them in each specialty. This helps physicians focus on the mitigation strategies that can most improve patient care and reduce potential liability.\nCLINICAL: DIAGNOSTIC\n\nFailure to Order Indicated Testing\n\nContributing action or omission: Failing to obtain appropriate imaging in patients with clinical features suggestive of aortic dissection delays diagnosis and increases the risk of mortality.\n\nMitigation Strategies\n\nThe strategies below work together to ensure timely identification of acute aortic syndrome and appropriate escalation to definitive imaging.\n\n\u2022 Systematically apply the Aortic Dissection Detection Risk Score (ADD-RS)\n\nWhen patients present with back or abdominal pain, syncope or malperfusion, calculate the ADD-RS using three categories:\nPredisposing conditions, including Marfan syndrome, family history of aortic disease, known aortic valve disease, recent aortic manipulation and known thoracic aortic aneurysm\nPain features that are abrupt onset with severe intensity or a ripping/tearing quality\nExam findings, such as pulse deficit, systolic blood pressure differential, focal neurologic deficit, new aortic regurgitation murmur and hypotension/shock \nAssign 1 point for each category with at least one risk marker. Applying this score systematically increases detection of acute aortic syndrome and demonstrates adherence to guideline-recommended risk stratification. \n\u2022 Obtain CT angiography promptly in patients with ADD-RS >1\n\nIn patients with a high probability of acute aortic dissection (ADD-RS score >1), perform CT angiography (CTA) of the chest, abdomen and pelvis immediately. Do not wait for other test results. CTA is highly sensitive and specific for diagnosis, providing key surgical planning information, including the extent of dissection, branch vessel involvement and the presence of malperfusion. Delays in definitive imaging in high-risk patients are a common failure point.\n\n\u2022 Use D-dimer testing judiciously in low-risk patients\n\nIn patients with ADD-RS \u22641 (low- to intermediate-risk), order a highly sensitive D-dimer <500 ng/mL to help exclude acute aortic dissection without advanced imaging. Combining ADD-RS \u22641 with a negative D-dimer has a failure rate of 0.3%. However, do not use D-dimer to rule out dissection in high-risk patients (ADD-RS >1), as it lacks sufficient sensitivity. \n\n\u2022 Consider bedside echocardiography as an adjunct in unstable patients\n\nIn hemodynamically unstable patients who cannot be safely transported to CT, use transthoracic echocardiography (TTE) to identify direct signs of dissection (intimal flap, aortic wall thickening) and indirect signs (pericardial effusion, aortic regurgitation and aortic dilatation). In patients with high clinical suspicion and positive TTE findings, proceed directly to surgical consultation. Perform TEE in the operating room or ICU when CT is not feasible for unstable patients. \n\nCLINICAL: DIAGNOSTIC\n\nFailure to Recognize, Interpret or Act On Diagnostic Finding\nContributing action or omission: Failing to recognize atypical presentations or symptoms and misinterpreting imaging increase the risk of delayed or missed diagnosis.\n\nMitigation Strategies\n\nThe strategies below work together to improve recognition of aortic dissection in its varied presentations and reduce diagnostic anchoring on alternative diagnoses.\n\n\u2022 Maintain a high index of suspicion for atypical presentations\n\nBecause aortic dissection does not always present with classic tearing pain, remain alert to unusual symptoms during diagnosis. IRAD data show that 6.4% of patients had painless dissection, presenting with syncope (33.9%), congestive heart failure (19.7%) or stroke (11.3%). Patients with isolated abdominal pain, neurologic deficits or limb ischemia may have an underlying dissection. Always consider aortic dissection in the differential for patients with unexplained hypotension, pulse deficits or new aortic regurgitation murmur. \n\u2022 Avoid using chest radiographs to rule out aortic dissection\nProceed directly to CT angiography, MRI or transesophageal echocardiography when aortic dissection is suspected. Chest x-rays lack sensitivity and specificity to reliably rule out aortic dissection, with up to 15% of confirmed cases showing normal films. Mediastinal widening appears in only 63%\u201364% of type A dissections and has poor specificity: nearly half (48.6%) of patients with confirmed dissection who had\u00a0no\u00a0high-risk clinical features still showed mediastinal widening. \n\u2022 Proceed to definitive imaging if clinical suspicion for dissection persists, regardless of troponin results\n\nRecognize that the combination of normal ECG, normal troponins and elevated D-dimer in a patient with chest pain should raise suspicion for acute aortic syndrome rather than acute coronary syndrome.\u00a0Troponin elevation does not rule out aortic dissection, because it may result from coronary artery involvement by the dissection flap or hypotension-induced ischemia. If clinical suspicion for dissection persists, proceed to definitive imaging regardless of troponin results. \nPrioritize ECG-gated CT techniques when possible\nRecognize that non-gated scans frequently introduce motion artifacts, which can lead to false-positive or false-negative errors. Utilize ECG-triggered CTA whenever available to achieve superior image clarity and maximize diagnostic accuracy.\n\u2022 Consider transfer of patients to a high-volume aortic center\nFor patients with suspected or confirmed type A aortic dissection who are hemodynamically stable, initiate transfer when feasible. High-volume centers have multidisciplinary aorta teams with expertise in cardiac surgery, vascular surgery, advanced imaging and interventional radiology, enabling rapid diagnosis and coordinated treatment.\u00a0Interfacility transfer does not increase operative mortality.\u00a0\nCLINICAL: TREATMENT\n\nFailure to Monitor Patient Following Treatment or Intervention\n\nContributing action or omission: Inadequate post-operative or post-discharge surveillance increases the risk of missed complications and aneurysms and emergent reintervention.\n\nMitigation Strategies\n\nThe strategies below work together to ensure appropriate monitoring during the acute phase of aortic dissection and long-term surveillance to detect complications. \n\n\u2022 Monitor for signs of malperfusion and hemodynamic instability\n\nContinuously evaluate patients for end-organ malperfusion (altered mental status, oliguria, abdominal pain and limb ischemia) and hemodynamic deterioration during the acute phase. Patients with type A dissection remain at risk for rupture, tamponade and sudden death even when initially stable. Mortality in the first 48 hours is approximately 0.5% per hour for patients who have not undergone surgery. \n\u2022 Follow recommended surveillance imaging protocols\n\nPerform CT or MRI surveillance imaging at 1 month, 6 months and 12 months post-event, then annually if aortic findings remain stable (whether treated surgically, endovascularly or medically). About 50% of patients are lost to follow-up by 28 months, and 38% of medically treated type B dissection patients require later intervention. Systematic surveillance detects threatening enlargement and identifies the need for reintervention. \n\u2022 Establish follow-up at a high-volume aortic center for long-term surveillance\nConsider referral for patients initially treated at low-volume facilities. High volume centers provide structured surveillance through dedicated aortic care clinics with multidisciplinary expertise. They have demonstrated significantly better long-term outcomes, likely reflecting not only superior acute surgical care but also more systematic long-term monitoring and timely reintervention when needed.\n\nADMINISTRATIVE: DOCUMENTATION\n\nDocumentation Failure\n\nContributing action or omission: Inadequate documentation of clinical reasoning, risk stratification and time-critical decision-making compromises continuity of care and defensibility.\n\nMitigation Strategies\n\nThe strategies below work together to support comprehensive documentation that adheres to the standard of care and provides a defensible record.\n\n\u2022 Document ADD-RS calculation and clinical reasoning\n\nDetail the risk factors across all three ADD-RS categories (predisposing conditions, pain features and exam findings) and the final score. Document the reasoning for the diagnostic pathway selected based on those assessed risk levels. This process ensures a systematic, defensible assessment and further evaluation and treatment. \n\nSample documentation language: \"ADD-RS calculated: Predisposing conditions (0)\u2014no known connective tissue disease, aortic valve disease or family history of aortic disease. Pain features (1)\u2014abrupt onset of severe chest pain. Examination findings (0)\u2014no pulse deficit, BP symmetric, no neurologic deficit, no new murmur. ADD-RS = 1 (intermediate risk). Given intermediate risk, obtained D-dimer which was elevated at 1,200 ng/mL. Proceeding to CTA chest/abdomen/pelvis.\"\n\n\u2022 Record the timing of key clinical events\n\nDocument the time of symptom onset, presentation, first suspicion of dissection, image ordering and completion, surgical consultation and start of treatment. Documenting these intervals is critical to demonstrating necessary urgency with a time-dependent, high-mortality condition like aortic dissection. \n\n\u2022 Document communication with consultants and transfer decisions\n\nLog all interactions with cardiovascular surgery, including time of contact, clinical details, recommendations and disposition decisions. When considering transfer to a higher-level center, document the rationale for transfer or for local management. \n\n\u2022 Note patient education and follow-up plans at discharge\n\nAfter aortic dissection (post-operative or medically managed), document education on warning signs needing immediate evaluation, blood pressure goals, medications and a follow-up plan with dates for surveillance imaging and appointments.",
    "OVERVIEW": "Aortic Dissection\n\n\nSPECIALTY: Cardiology\n\nPRESENTING CONDITION(S): Chest Pain, Back Pain, Syncope, Acute Cardiovascular Emergency\n\nADVERSE OUTCOME(S): Aortic Rupture, Stroke, End-Organ Malperfusion, Death\n\nAortic dissection\u00a0is\u202fa\u202fsignificant driver of medical malpractice risk and severe patient\u00a0harm in cardiology.\u202f\u00a0\n\nMitigating Your Risk\n\nWith timely identification of acute aortic syndrome, rapid imaging and vigilant monitoring during the acute phase,\u00a0cardiologists\u202fcan mitigate the risks associated with aortic dissection\u00a0and reduce malpractice exposure. This report examines recurring clinical ",
    "PRESENTING_CONDITIONS": "Chest Pain, Back Pain, Syncope, Acute Cardiovascular Emergency",
    "ADVERSE_OUTCOMES": "Aortic Rupture, Stroke, End-Organ Malperfusion, Death",
    "CLINICAL_DIAGNOSTIC": "",
    "CLINICAL_TREATMENT": "",
    "CLINICAL_PROCEDURAL_SURGICAL": "",
    "ADMINISTRATIVE_COMMUNICATION": "",
    "ADMINISTRATIVE_DOCUMENTATION": "",
    "ADMINISTRATIVE_PATIENT_FACTORS": "",
    "ADMINISTRATIVE_PROFESSIONAL_BEHAVIOR": "",
    "ADMINISTRATIVE_SYSTEMS_ISSUES": "",
    "STATUS": "Approved"
  },
  {
    "DRIVER_ID": "DE-CANCER",
    "SPECIALTY": "Dermatology",
    "DRIVER": "Cancer",
    "TITLE": "Cancer",
    "RISK_BRIEF": "Skin Cancer\n\n\nSPECIALTY: Dermatology\n\nPRESENTING CONDITION(S): Suspicious Skin Lesions, Pigmented Lesions, Non-Healing Wounds\n\nADVERSE OUTCOME(S): Delayed Diagnosis, Metastatic Disease, Disfigurement, Death\nSkin cancer is a significant driver of medical malpractice risk and severe patient harm in dermatology.\n\nMitigating Your Risk\n\nWith systematic approaches to lesion evaluation, appropriate biopsy technique, timely communication of results and structured follow-up, dermatologists can mitigate the risks associated with skin cancer and reduce malpractice exposure. This report examines recurring clinical and administrative failures that can lead to malpractice claims for skin cancer and provides strategies to help prevent them.\n\nClinical and Administrative Breakdowns\nMalpractice claims involving skin cancer typically stem from a recurring set of clinical and administrative failures. Clinical contributors account for 60% of the risk, while administrative contributors account for the remaining 40%. Any one of these factors\u2014or a combination of them\u2014can lead to severe patient harm and a malpractice claim, even if the other aspects of care were appropriate.\nActions and Omissions Driving Skin Cancer Risk [insert chart]\nWhile multiple actions and omissions drive risk, our advice discusses the top contributors to them in each specialty. This helps physicians focus on the mitigation strategies that can most improve patient care and reduce potential liability.\nCLINICAL: DIAGNOSTIC\n\nFailure to Obtain Relevant Medical History or Perform Pertinent Physical Exam\n\nContributing action or omission: Failing to fully assess patient risk factors, perform comprehensive skin examinations or adequately evaluate suspicious lesions increases the likelihood of missed or delayed skin cancer diagnosis.\n\nMitigation Strategies\n\nThe strategies below work together to ensure systematic identification of high-risk patients and thorough evaluation of suspicious lesions.\n\n\u2022 Obtain comprehensive skin cancer risk history at initial and periodic visits\n\nAt the initial visit and subsequent checkups, document the patient\u2019s history of skin cancer, blistering sunburns, tanning bed use, immunosuppression status and genetic syndromes. Document family history of melanoma or keratinocyte carcinoma. Risk stratification informs examination intensity and surveillance intervals. Patients with a prior keratinocyte carcinoma have approximately a 40% risk of developing a new primary keratinocyte carcinoma within five years, and this risk increases substantially with each additional skin cancer. \n\n\u2022 Perform complete skin examination with dermoscopy for high-risk patients\n\nFor patients with risk factors for melanoma (atypical nevi, high mole count, personal/family history), conduct a total body skin examination using dermoscopy. The NCCN recommends dermoscopy, total-body photography and sequential digital dermoscopy to detect new primary melanoma, particularly in patients with high mole counts. Document findings, including the number and location of atypical lesions.\n\n\u2022 Apply systematic clinical criteria when evaluating pigmented lesions\n\nUse the ABCDE criteria (asymmetry, border irregularity, color variation, diameter >6 mm, evolution) and \"ugly duckling\" sign to identify lesions warranting biopsy. Document the clinical features that prompted a biopsy or the rationale for observation. Maintain high clinical suspicion of amelanotic or pink lesions, as they can delay early detection. \n\n\u2022 Perform a comprehensive examination of anatomically challenging areas\n\nInclude scalp, ears, interdigital spaces, nail beds, soles and genital areas in examinations, as melanomas in those locations are frequently diagnosed at later stages. Subungual melanoma requires specialized biopsy of the nail matrix. Document examination of these areas.\n\n\nCLINICAL: DIAGNOSTIC\nFailure to Order Indicated Testing\n\nContributing action or omission: Failing to biopsy suspicious lesions, re-biopsy when needed, or use appropriate biopsy technique increases the risk of delayed diagnosis and understaging.\n\nMitigation Strategies\n\nThe strategies below work together to ensure appropriate tissue sampling and accurate pathologic diagnosis.\n\n\u2022 Maintain a low threshold for biopsy of clinically suspicious lesions\n\nBiopsy any lesion with clinical or dermoscopic features suggestive of malignancy rather than opting for observation. Failure to biopsy and diagnose is the most common cause of keratinocyte carcinoma litigation. In immunosuppressed patients, maintain an especially low threshold for biopsy, as lesions may be difficult to assess clinically. \n\n\u2022 Select biopsy technique appropriate to clinical suspicion and anatomic site\n\nFor lesions suspicious for melanoma, excisional/complete biopsy with 1\u20133 mm margins is preferred to ensure accurate Breslow thickness measurement. Avoid superficial shave biopsy, as it may compromise pathologic diagnosis and Breslow thickness assessment. An exception is suspected melanoma in situ, lentigo maligna type, for which a broad shave biopsy (extending into the deep papillary or superficial reticular dermis) may provide more thorough histologic assessment of potential focal microinvasion than multiple punch biopsies.\u00a0For suspected SCC, ensure that the biopsy extends into the dermis. Document the rationale for the chosen biopsy technique.\n\n\u2022 Rebiopsy when initial specimen is inadequate for diagnosis or microstaging\n\nIf a shave biopsy shows residual tumor or pigment at the base, perform a deeper biopsy (punch or elliptical) immediately, submitting it in a separate container and noting that the initial specimen was transected. If the pathology report indicates an inadequate specimen for staging, arrange rebiopsy before planning treatment.\n\n\u2022 Submit complete clinical information with pathology requisition\n\nInclude patient age, anatomic location of the biopsy, clinical diameter, lesion status (primary or recurrent), immunosuppression status and history of radiation at the site. This information is essential for accurate pathologic interpretation and risk stratification.\n\n\n\nCLINICAL: PROCEDURAL/SURGICAL\n\nTechnique-Related Error\n\nContributing action or omission: Procedural errors, including inadequate biopsy depth, wrong-site procedures, failure to achieve clear margins and surgical complications, increase the risk of adverse outcomes.\n\nMitigation Strategies\n\nThe strategies below work together to ensure technically appropriate procedures and minimize complications.\n\n\u2022 Verify lesion identity and location before biopsy or excision\n\nTake prebiopsy photographs to aid clinical/pathologic correlation and prevent wrong-site surgery. Include a regional view with anatomic landmarks. Confirm lesion identity with the patient, particularly when biopsying multiple lesions in one visit.\n\n\u2022 Plan excisional biopsy orientation with definitive treatment in mind\n\nFor elliptical/fusiform excisional biopsy of suspected melanoma, orient the incision longitudinally (axially) and parallel to underlying lymphatics on extremities to permit accurate lymphatic mapping if needed. Avoid wider margins that could interfere with sentinel lymph node biopsy.\n\n\u2022 Ensure adequate depth for accurate pathologic staging\n\nFor suspected melanoma, extend the biopsy to a depth sufficient to avoid transection at the deep margin, usually to the deep reticular dermis. For suspected SCC, ensure that the biopsy extends into the dermis. If the tumor appears to extend beyond the dermis, perform surgical excision instead of curettage and electrodesiccation or shave removal. \n\n\u2022 Confirm margin clearances before performing tissue rearrangement\n\nFor standard excision (where margins are sent for permanent section pathology), delay tissue rearrangement (flap reconstruction or extensive undermining) until clear margins are confirmed on final pathology. Use second intention healing, linear repair or skin grafting pending margin confirmation. If tissue rearrangement is required for closure, Mohs micrographic surgery or other forms of PDEMA is preferred to prevent the need for a staged procedure. For Mohs or other forms of PDEMA, margins are confirmed intraoperatively through real-time histologic assessment of the entire peripheral and deep margin surface. Once clear margins are confirmed\u2014typically within the same operative session\u2014reconstruction including flap repair and tissue rearrangement can proceed immediately. \n\n \n\n\nADMINISTRATIVE: COMMUNICATION\n\nCommunication Failure Between Patient and Provider\n\nContributing action or omission: Failing to promptly communicate biopsy results, adequately explain diagnosis and treatment options or ensure patient understanding of follow-up requirements increases the risk of delayed treatment and harm.\n\nMitigation Strategies\n\nThe strategies below work together to ensure timely, clear communication that supports informed decision-making and adherence to treatment.\n\n\u2022 Establish and follow a systematic process for communicating biopsy results\n\nDevelop a standardized workflow for result notification, including tracking pending pathology results and documenting patient contact. Up to one-third of physicians fail to notify patients of abnormal results. Ask patients about their preferred contact method during biopsy consent.\n\n\u2022 Communicate malignant diagnoses directly to patients \n\nFor melanoma or other malignant diagnoses, speak directly with patients in person or by phone rather than by voicemail or patient portals. Allow time for questions and document the conversation.\n\n\u2022 Clearly explain diagnosis, prognosis and treatment options\n\nWhen delivering a skin cancer diagnosis, explain the type of cancer, stage if known, treatment options, expected outcomes, and timeline. For melanoma, discuss the need for wide local excision and sentinel lymph node biopsy when indicated. Document the informed consent discussion and stated patient understanding.\n\n\u2022 Ensure that patients understand follow-up surveillance requirements\n\nEducate patients about the risk of recurrence and new primary skin cancers. Patients with prior keratinocyte carcinoma have a 40% risk of developing a new primary keratinocyte carcinoma within five years. Provide written instructions on self-examination, sun protection and follow-up intervals. \n\n\n\n\nADMINISTRATIVE: DOCUMENTATION\n\nDocumentation Failure\n\nContributing action or omission: Inadequate documentation of clinical findings, biopsy rationale, pathology results, treatment discussions and follow-up plans compromises continuity of care and defensibility.\n\nMitigation Strategies\n\nThe strategies below work together to support comprehensive documentation that reinforces clinical decision-making and provides a defensible record.\n\n\u2022 Document clinical description and rationale for biopsy or observation\n\nRecord the location of each suspicious lesion using anatomic landmarks, size and clinical features (ABCDE criteria, dermoscopic findings). Note the clinical rationale for biopsy or continued observation. When choosing observation, document the follow-up plan and criteria for biopsy.\n\nSample documentation language: \"3 mm pigmented macule on right posterior shoulder with irregular border and color variation on dermoscopy. Clinical features concerning for atypical nevus vs. early melanoma. Shave biopsy performed to deep reticular dermis. Patient to return for results in 7\u201310 days.\"\n\n\u2022 Document informed consent discussions for procedures\n\nBefore biopsy or excision, document discussion of the procedure, risks (scarring, infection, bleeding, incomplete removal), benefits and alternatives. For Mohs surgery or wide local excision, include expected cosmetic outcomes and reconstruction options. Failure to obtain informed consent is a recurring theme in keratinocyte carcinoma litigation cases. \n\u2022 Document pathology results and communication with patient\n\nRecord the date pathology results were received, diagnosis, key pathologic features (Breslow thickness, ulceration, margins for melanoma, differentiation, depth, perineural invasion for SCC) and the date and method of patient notification. Note the patient's stated understanding and questions.\n\n\u2022 Document the follow-up plan with specific intervals and surveillance strategy\n\nRecord the recommended follow-up schedule based on diagnosis and risk stratification. For melanoma, include stage-appropriate surveillance intervals. For keratinocyte carcinoma, document risk category and corresponding follow-up frequency. Include patient education provided regarding self-examination and sun protection.\n\nSample documentation language: \"Discussed diagnosis of pT1b melanoma, stage IB. Wide local excision with 1 cm margins and sentinel lymph node biopsy recommended. Patient understands diagnosis and treatment plan. Follow-up every 6\u201312 months for 5 years, then annually. Self-skin examination and sun protection counseling provided. Patient verbalized understanding.\"\n\n\n\n\nPATIENT FACTORS\n\nPatient Non-Adherence to Recommended Care\n\nContributing action or omission: Failure to identify and address barriers to patient follow-up, delays in seeking evaluation for changing lesions or non-adherence to surveillance recommendations increase the likelihood of advanced disease.\n\nMitigation Strategies\n\nThe strategies below work together to maximize patient engagement and address barriers to timely evaluation and follow-up.\n\n\u2022 Educate patients about warning signs requiring prompt evaluation\n\nProvide clear verbal and written instructions about signs and symptoms that warrant immediate evaluation: new or changing moles, non-healing wounds, lesions that bleed or crust and any rapidly growing skin lesions. Patient delay remains a leading contributor to late-stage melanoma diagnosis, and studies show that patient awareness of melanoma severity is low. \n\n\u2022 Address barriers to patient follow-up\n\nIdentify and attempt to mitigate obstacles such as transportation, work schedules, insurance and health literacy. Teledermatology may serve as a triage or supplemental monitoring tool between in-person visits for select patients, though it is not a substitute for in-person total body skin examination with regional lymph node assessment, particularly in higher-risk patients. Recognize that patients with limited English proficiency and those from communities with lower baseline skin cancer awareness may face additional barriers to timely follow-up.\n\n\u2022 Provide written follow-up instructions in patient's primary language\n\nClearly state the recommended follow-up interval and signs to monitor between visits. Provide office contact information in case the patient has concerns. Ensure that materials are provided at the appropriate literacy level and in the patient's primary language. \n\n\u2022 Document patient non-adherence and continued outreach efforts\n\nWhen a patient misses a follow-up appointment or declines a recommended biopsy, document the recommendation, risks explained and the person\u2019s stated reasons for declining. Continue contacting the patient by phone, patient portal or mail and document all outreach attempts.\n\nSample documentation language: \"Patient declined biopsy of 8 mm irregularly pigmented lesion on left calf. Discussed concern for possible melanoma and risks of delayed diagnosis including metastatic disease. Patient states preference to 'watch it' and return if it changes. Risks and recommendations documented. Patient to return in 3 months for re-evaluation or sooner if lesion changes. Written instructions provided.\"",
    "OVERVIEW": "Skin Cancer\n\n\nSPECIALTY: Dermatology\n\nPRESENTING CONDITION(S): Suspicious Skin Lesions, Pigmented Lesions, Non-Healing Wounds\n\nADVERSE OUTCOME(S): Delayed Diagnosis, Metastatic Disease, Disfigurement, Death\nSkin cancer is a significant driver of medical malpractice risk and severe patient harm in dermatology.\n\nMitigating Your Risk\n\nWith systematic approaches to lesion evaluation, appropriate biopsy technique, timely communication of results and structured follow-up, dermatologists can mitigate the risks associated with skin cancer and reduce malpractice exposure. This report examines recurring",
    "PRESENTING_CONDITIONS": "Suspicious Skin Lesions, Pigmented Lesions, Non-Healing Wounds",
    "ADVERSE_OUTCOMES": "Delayed Diagnosis, Metastatic Disease, Disfigurement, Death",
    "CLINICAL_DIAGNOSTIC": "",
    "CLINICAL_TREATMENT": "",
    "CLINICAL_PROCEDURAL_SURGICAL": "",
    "ADMINISTRATIVE_COMMUNICATION": "",
    "ADMINISTRATIVE_DOCUMENTATION": "",
    "ADMINISTRATIVE_PATIENT_FACTORS": "",
    "ADMINISTRATIVE_PROFESSIONAL_BEHAVIOR": "",
    "ADMINISTRATIVE_SYSTEMS_ISSUES": "",
    "STATUS": "Approved"
  },
  {
    "DRIVER_ID": "EM-ACUTE-MYOCARDIAL-INFARCTION",
    "SPECIALTY": "Emergency Medicine",
    "DRIVER": "Acute myocardial infarction",
    "TITLE": "Acute myocardial infarction",
    "RISK_BRIEF": "Acute Myocardial Infarction\nSPECIALTY: Emergency Medicine\n\nPRESENTING CONDITION(S): Chest Pain, Dyspnea, Suspected Acute Coronary Syndrome\n\nADVERSE OUTCOME: Acute Myocardial Infarction\n\nAcute myocardial infarction is a significant driver of medical malpractice risk and severe patient harm in emergency medicine. \n \nMitigating Your Risk\nWith vigilant diagnostic assessment, timely reperfusion, standardized communication protocols and thorough documentation, emergency medicine physicians can mitigate the risks associated with acute myocardial infarction and reduce malpractice exposure. This report examines recurring clinical and administrative failures that can lead to malpractice claims for acute myocardial infarction and provides strategies to help prevent them.\n \nClinical and Administrative Breakdowns\n \nMalpractice claims involving acute myocardial infarction typically stem from a recurring set of clinical and administrative failures. Clinical contributors account for 83% of the risk, while administrative contributors account for the remaining 17%. Any one of these factors\u2014or a combination of them\u2014can lead to severe patient harm and a malpractice claim, even if the other aspects of care were appropriate.\nActions and Omissions Driving Acute MI Risk [insert chart] \n\nWhile multiple actions and omissions drive risk, our advice discusses the top contributors to them in each specialty. This helps physicians focus on the mitigation strategies that can most improve patient care and reduce potential liability.\nCLINICAL: DIAGNOSTIC\nFailure to Obtain Relevant Medical History or Perform Pertinent Physical Exam\n\nContributing action or omission: Incomplete history-taking or physical examination leads to failure to recognize high-risk features or atypical presentations of AMI.\n\nMitigation Strategies\nThe strategies below work together to ensure appropriate triage and initial assessment, recognition of high-risk populations and identification of atypical presentations that may otherwise be missed.\nIdentify high-risk patient populations requiring heightened vigilance\n\nDuring triage and initial assessment, flag patients at increased risk for atypical presentations: women (especially those 55 years and older), older adults, patients with diabetes, chronic kidney disease or prior stroke/heart failure. Women report chest pain and diaphoresis less often, with back pain, jaw pain, epigastric pain and lightheadedness being more common. Documentation of risk factors and symptom assessment in these populations demonstrates adherence to the standard of care.\nSystematically elicit ischemic symptoms beyond chest pain\n\nObtain a focused cardiac history at initial presentation that includes not only chest discomfort but also dyspnea, diaphoresis, nausea, jaw/neck/arm pain and unexplained fatigue. About 40% of men and 48% of women with ACS present with nonspecific symptoms such as dyspnea, either alone or with chest pain. Recognizing these presentations prevents anchoring on noncardiac diagnoses and supports defensibility.\nCompare current presentation to prior cardiac history\n\nObtain and document previous cardiac history, including previous MI, revascularization (PCI/CABG), known coronary artery disease and prior ECGs when available. Comparing the current ECG to baseline is essential for identifying new ischemic changes, particularly in patients with chronic ST-T abnormalities. This practice reduces missed diagnoses and demonstrates diligent evaluation.\n\nPerform targeted cardiovascular physical examination\n\nAt initial evaluation, assess vital signs (including bilateral blood pressures if aortic dissection is considered), jugular venous distension, lung auscultation for rales, cardiac auscultation for murmurs or S3 and peripheral perfusion. Physical examination findings inform risk stratification (for example, Killip class) and help identify cardiogenic shock or mechanical complications requiring immediate intervention. Documenting pertinent positives and negatives supports clinical reasoning.\nCLINICAL: DIAGNOSTIC\nFailure to Order Indicated Testing\n\nContributing action or omission: Failing to obtain timely ECG, serial troponins or supplemental leads results in missed or delayed AMI diagnosis.\n\nMitigation Strategies\nThe strategies below work together to ensure timely acquisition and appropriate interpretation of diagnostic studies, reducing the risk of missed or delayed AMI diagnosis.\nObtain and interpret ECG within 10 minutes of presentation\n\nAcquire a 12-lead ECG within 10 minutes of first medical contact for all patients with suspected ACS (Class I recommendation). An initial nondiagnostic ECG does not exclude ACS; 11% of patients ultimately diagnosed with STEMI have an initial nondiagnostic ECG. Timely ECG acquisition is a core quality measure, and failure to meet this standard is difficult to defend.\nPerform serial ECGs when initial ECG is nondiagnostic\n\nRepeat ECG at 15-minute to 30-minute intervals during the initial 1\u20132 hours for patients with persistent symptoms, high clinical suspicion of MI or clinical deterioration. Dynamic ECG changes may reveal evolving STEMI or ischemia not seen on the initial tracing. Document the rationale for serial ECGs and findings to demonstrate ongoing reassessment.\nObtain posterior and right-sided leads when clinically indicated\n\nAcquire posterior leads (V7\u2013V9) in patients with isolated ST-segment depression in V1\u2013V3 or suspected left circumflex occlusion. Obtain right-sided leads (V3R, V4R) in patients with inferior STEMI to evaluate for right ventricular involvement. Posterior MI is often missed because the posterior wall is not visualized on standard 12-lead ECG; up to 20% of patients initially diagnosed with NSTEMI are later found to have posterior transmural infarction. Failure to obtain supplemental leads when indicated represents a deviation from the standard of care.\nOrder serial high-sensitivity cardiac troponin measurements\n\nMeasure hs-cTn at presentation and again at 1\u20132 hours (or 3\u20136 hours for conventional assays) to detect or exclude myocardial injury. A single hs-cTn below the limit of detection (5 ng/L for many assays) in patients presenting \u22653 hours after symptom onset can reliably rule out MI with sensitivity >99%. Facilities that have not yet transitioned to hs-cTn assays should use conventional troponin with 0/3\u20136 hour serial measurement protocols and validated risk scores (e.g., HEART) for risk stratification. Document troponin timing relative to symptom onset and the rationale for serial testing or early rule-out.\nObtain urgent echocardiography for hemodynamically unstable patients\n\nOrder urgent echocardiography (including point-of-care ultrasound by trained clinicians) for patients with cardiogenic shock, hemodynamic instability or suspected mechanical complications. Echocardiography identifies wall motion abnormalities, mechanical complications and alternative diagnoses. Document findings and their impact on management decisions.\nCLINICAL: TREATMENT\nError in Non-Medication Therapeutic Intervention\n\nContributing action or omission: Delays in reperfusion therapy or failure to activate appropriate resources result in preventable myocardial damage and adverse outcomes.\n\nMitigation Strategies\nThe strategies below work together to ensure timely reperfusion, appropriate escalation and adherence to time-based quality metrics that directly impact patient outcomes.\nActivate cardiac catheterization laboratory immediately upon STEMI diagnosis\n\nFor patients with STEMI, activate the catheterization laboratory immediately upon ECG diagnosis with a system goal of first medical contact\u2013to-device time \u226490 minutes. Early advance notification by EMS and immediate activation upon ED arrival reduces time to reperfusion and improves survival. Document time of ECG acquisition, STEMI recognition and catheterization laboratory activation.\nAdminister fibrinolytic therapy when PCI is not available within 120 minutes\n\nIf primary PCI cannot be achieved within 120 minutes of first medical contact, administer fibrinolytic therapy (tenecteplase, alteplase or reteplase) within 30 minutes of arrival for eligible patients, followed by transfer to a PCI-capable facility. Document contraindication assessment and rationale for fibrinolytic administration or deferral.\nRecognize and treat STEMI equivalents with the same urgency as classic STEMI\nTreat patients with posterior MI (ST depression V1\u2013V3 with ST elevation on posterior leads), de Winter's sign (upsloping ST depression with tall T waves in precordial leads) and hyperacute T waves as STEMI equivalents requiring emergent angiography. Delayed recognition of these patterns leads to delayed reperfusion and worse outcomes.\nApply validated clinical decision pathways for risk stratification\nUse validated tools, such as the HEART Pathway, EDACS or ESC 0/1-hour algorithm, to stratify patients into low, intermediate or high-risk categories. A HEART score \u22643, combined with nonischemic ECG and serial troponins 99th percentile, identifies approximately 30% of patients as low risk with 30-day death/MI rate of 0.4%. Structured risk stratification supports safe discharge decisions and provides documentation of clinical reasoning.\n\n\n\n\nADMINISTRATIVE: PROFESSIONAL BEHAVIOR\nFailure to Recognize Responsibility\n\nContributing action or omission: Cognitive biases, premature closure and failure to reconsider the diagnosis contribute to missed AMI.\n\nMitigation Strategies\nThe strategies below work together to reduce cognitive bias, ensure equitable care and promote a culture of diagnostic humility.\nAvoid premature diagnostic closure\n\nMaintain AMI on the differential diagnosis throughout the ED encounter, particularly when initial ECG and troponin are nondiagnostic. Approximately 41% of NSTE-ACS patients have neither ST-segment depression nor T-wave inversion on initial ECG. Document consideration of ACS and rationale for alternative diagnoses to demonstrate ongoing clinical vigilance.\nRecognize and mitigate implicit bias in diagnostic evaluation\n\nApply standardized diagnostic protocols uniformly regardless of patient demographics. Black patients have consistently higher odds of missed AMI diagnosis (OR 1.18\u20134.5 across studies), and women age 55 years and older are more likely to be discharged with undiagnosed AMI. \nReassess patients with persistent or recurrent symptoms\n\nRe-evaluate patients who report ongoing or worsening symptoms during an ED stay, including repeat ECG and troponin measurement. Clinical deterioration should prompt immediate reassessment and escalation. Document reassessment findings and clinical decision-making.\n\n\n\n\nADMINISTRATIVE: SYSTEMS ISSUES\nFailure or Lack of Clinical Process, Policy or Procedure\n\nContributing action or omission: Lack of standardized protocols, inadequate resources or system delays undermine timely AMI diagnosis and treatment.\n\nMitigation Strategies\nThe strategies below work together to standardize processes, reduce variability, and ensure consistent delivery of time-sensitive care.\nImplement standardized chest pain protocols with embedded clinical decision pathways\n\nDevelop and maintain ED protocols that incorporate validated clinical decision pathways (HEART, EDACS, ESC 0/1-hour algorithm) with clear criteria for ECG timing, troponin measurement intervals and disposition decisions. Protocolization reduces ED length of stay by 20\u201345% and standardizes care delivery. Ensure that protocols are accessible and regularly updated.\nEstablish EHR-integrated order sets and alerts\n\nCreate pre-built order sets for suspected ACS that include ECG, serial troponins at appropriate intervals and risk score calculation. EHR integration reduces cognitive burden and ensures that critical tests are not omitted. Implement automated alerts for abnormal troponin results or ECG findings suggestive of ischemia.\nEstablish risk-stratified outpatient follow-up protocols\nFor patients discharged from the ED after evaluation for possible ACS, arrange outpatient follow-up based on risk stratification. Low-risk patients ruled out for MI by a clinical decision pathway (HEART score \u22643 with negative serial troponins) should receive follow-up within 30 days and, if feasible, within 14 days.\u00a0Intermediate-risk patients (HEART score \u22654) discharged after further observation or negative noninvasive testing should receive cardiology or PCP follow-up within 7 days. Notify the patient's PCP and/or cardiologist at the time of ED discharge to facilitate timely follow-up.\u00a0Develop standard discharge order sets that embed follow-up referral based on risk category to ensure that this step is not omitted. Where local follow-up access is limited, consider establishing an acute care follow-up clinic or utilizing telehealth-based cardiology visits for post-discharge symptom assessment and risk factor management.\nMaintain 24/7 access to cardiac catheterization laboratory activation\n\nEnsure that systems are in place for immediate catheterization laboratory activation, including clear communication pathways between ED, cardiology and interventional teams. For non-PCI-capable facilities, establish transfer agreements and protocols with PCI-capable centers. Track and review door-to-balloon times as a quality metric.\nParticipate in regional or national AMI registries\n\nParticipate in at least one regional or national registry (such as NCDR ACTION Registry) to track outcomes, complications and quality of care for AMI patients. Registry participation supports quality improvement and benchmarking against peer institutions.\n\n\nADMINISTRATIVE: COMMUNICATION\nCommunication Failure Between Providers\nContributing action or omission: Provider-to-provider communication failures\u2014including incomplete handoffs, unclear leadership and fragmented information transfer across care transitions\u2014increase the likelihood of diagnostic delays, treatment errors and missed escalation opportunities in AMI care.\n\nMitigation Strategies\nThe strategies below work together to standardize communication processes, ensure critical information transfer and support coordinated team response across the AMI care continuum.\n\u2022 Implement structured handoff protocols at every care transition\n\nUse standardized frameworks such as SBAR or I-PASS for all handoffs involving AMI patients, including EMS-to-ED, ED-to-catheterization laboratory and inpatient transitions. At the EMS-to-ED handoff, verify key details upon arrival, including prehospital medications administered, ECG findings and symptom onset time. Relay confirmed prehospital data immediately to the catheterization laboratory team to facilitate preparation and prevent medication errors (such as duplicate anticoagulation dosing). Structured handoffs reduce miscommunication and care failures by ensuring consistent transfer of critical information. The Joint Commission requires hospitals to maintain a handoff communication process that provides opportunity for discussion between the giver and receiver of patient information. \n\n\u2022 Establish clear leadership and role assignment during AMI emergencies\n\nDesignate a team leader at the start of each shift and when an AMI emergency occurs to coordinate care, assign roles and ensure accountability. Define protocols that clearly outline leadership responsibilities during STEMI activations. This should be established at shift start and reinforced when activations occur. Clear leadership reduces confusion during time-critical interventions and ensures that escalation decisions (proceeding to catheterization laboratory, initiating fibrinolysis) are made decisively. \n\n\u2022 Minimize ED dwell time through proactive catheterization laboratory coordination\n\nWhen immediate transfer to the catheterization laboratory is not feasible, minimize the dwell time in the ED and monitor it as part of quality outcome measures. Direct communication between the ED physician and interventional cardiologist should occur for all STEMI patients. This coordination should begin upon STEMI identification and continue until catheterization laboratory transfer. Reducing ED dwell time through coordinated communication directly impacts door-to-balloon times and patient outcomes. \n\u2022 Deliver structured feedback to EMS and referring facilities\nFollow up with all EMS agencies transporting patients to the STEMI receiving center within 24 to 48 hours of patient arrival. Invite them to attend multidisciplinary meetings and participate in discussions aimed at improving outcomes. Feedback loops enhance system performance, strengthen communication practices, and uncover opportunities for process improvement.\nADMINISTRATIVE: COMMUNICATION\nCommunication Failure Between Patient and Provider\n\nContributing action or omission: Inadequate patient communication\u2014including failure to use shared decision-making, insufficient discharge education and lack of confirmation of patient understanding\u2014increases the risk of treatment non-adherence, delayed recognition of recurrent symptoms and preventable readmissions.\n\nMitigation Strategies\n\nThe strategies below work together to ensure that patients and caregivers understand their diagnosis, treatment plan and self-care responsibilities, supporting informed decision-making and reducing post-discharge adverse events.\n\n\u2022 Use patient decision aids and shared decision-making for risk communication\n\nFor patients with acute chest pain and suspected ACS who are deemed low or intermediate risk by a clinical decision pathway, use patient decision aids such as Chest Pain Choice to facilitate risk communication and shared decision-making regarding the need for admission, observation or discharge with outpatient follow-up. This should occur during ED evaluation once risk stratification is complete. Decision aids increase patient knowledge, engagement and satisfaction while reducing low-value testing and observation unit admissions without increasing adverse events.\n \n\u2022 Provide comprehensive discharge education covering essential components\n\nEnsure that discharge education addresses: reason for hospitalization (diagnosis, tests, procedural results); tailored lifestyle modifications; medications (purpose, dose, frequency, adverse effects, refill instructions, importance of adherence); symptom management (what to monitor, actions to take if symptoms recur, who to call); return to daily activities (physical activity, sexual activity, work, travel); psychosocial considerations (depression, anxiety); and follow-up care (cardiology appointments, cardiac rehabilitation, additional testing). This should occur before discharge and include written materials. Comprehensive education improves disease-related knowledge, healthy behaviors and medication adherence. \n\n\u2022 Address social determinants of health and barriers to care\n\nAssess and address barriers to obtaining and taking prescribed medications, including referral to pharmacy assistance programs or social work as appropriate. This should occur during discharge planning. Addressing social determinants reduces disparities in post-discharge outcomes and supports medication adherence. \n\u2022 Deliver patient-centered discharge communication in preferred language\n\nProvide discharge instructions verbally and in writing in the patient's or caregiver's preferred language and at the appropriate literacy level. Engage in shared decision-making regarding assessment of goals and preferences. This should occur before discharge and be documented in the medical record. Patient-centered communication that respects language preferences and incorporates patient values improves comprehension and adherence to the care plan. \n\n\u2022 Use the teach-back method to confirm understanding of self-care and medications\n\nAssess patient and caregiver capacity for self-care, including secondary prevention, symptom monitoring and medication adherence. Use the teach-back method to confirm understanding of self-care instructions and the treatment regimen. This should occur during discharge education and be documented. Teach-back identifies gaps in understanding before discharge, allowing for targeted re-education and reducing the risk of medication errors and missed warning signs. \n\n\n\nADMINISTRATIVE: DOCUMENTATION\nDocumentation Failure\n\nContributing action or omission: Insufficient or delayed documentation compromises continuity of care and defensibility.\n\nMitigation Strategies\nThe strategies below work together to create a clear, contemporaneous record that enables continuity of care and demonstrates adherence to the standard of care.\nUse standard templates aligned with quality measures\n\nEmploy documentation templates that capture required elements for AMI quality measures, including ECG timing, troponin measurement, medication administration and discharge instructions. Templates ensure completeness and facilitate quality reporting.\n\nExample documentation language: \"52-year-old woman with diabetes presenting with 2 hours of epigastric discomfort and dyspnea. Given atypical presentation and diabetes, maintained high suspicion for ACS. Initial ECG obtained at 08:12 (within 10 minutes of arrival) showed nonspecific ST-T changes; compared to prior ECG from 2024, no acute changes. Initial hs-cTnI 4 ng/L at 08:15. Serial ECG at 08:45 unchanged. Repeat hs-cTnI at 10:15 was 5 ng/L (delta 3 ng/L). HEART score 3 (low risk). Patient asymptomatic, hemodynamically stable. Discharged with PCP follow-up arranged within 14 days per low-risk protocol; return precautions reviewed using teach-back.\"\nMaintain a clear, auditable timeline of key clinical details \n\nRecord the time of key events: symptom onset (as reported by patient), ECG acquisition and interpretation, troponin collection and results, catheterization laboratory activation and reperfusion therapy. Time-stamped documentation demonstrates adherence to guideline-recommended intervals and supports defensibility.\nDocument patient-specific factors that influenced evaluation\n\nRecord patient-specific factors that may affect presentation or interpretation, such as previous cardiac history, baseline ECG abnormalities, chronic kidney disease and diabetes. Documentation of those factors demonstrates individualized assessment and supports clinical reasoning.\nDocument clinical reasoning for disposition decisions\n\nDocument the rationale for admission, observation or discharge, including risk stratification score (if used), interpretation of ECG and troponin results and consideration of alternative diagnoses. For patients discharged after ACS evaluation, document why the patient was deemed low risk, the specific risk stratification tool and score used, and\u00a0the follow-up interval arranged based on risk category\u00a0(for example, 14\u201330 days for low risk, within 7 days for intermediate risk).",
    "OVERVIEW": "Acute Myocardial Infarction\nSPECIALTY: Emergency Medicine\n\nPRESENTING CONDITION(S): Chest Pain, Dyspnea, Suspected Acute Coronary Syndrome\n\nADVERSE OUTCOME: Acute Myocardial Infarction\n\nAcute myocardial infarction is a significant driver of medical malpractice risk and severe patient harm in emergency medicine. \n \nMitigating Your Risk\nWith vigilant diagnostic assessment, timely reperfusion, standardized communication protocols and thorough documentation, emergency medicine physicians can mitigate the risks associated with acute myocardial infarction and reduce malpractice exposure. This report",
    "PRESENTING_CONDITIONS": "Chest Pain, Dyspnea, Suspected Acute Coronary Syndrome",
    "ADVERSE_OUTCOMES": "Acute Myocardial Infarction",
    "CLINICAL_DIAGNOSTIC": "",
    "CLINICAL_TREATMENT": "",
    "CLINICAL_PROCEDURAL_SURGICAL": "",
    "ADMINISTRATIVE_COMMUNICATION": "",
    "ADMINISTRATIVE_DOCUMENTATION": "",
    "ADMINISTRATIVE_PATIENT_FACTORS": "",
    "ADMINISTRATIVE_PROFESSIONAL_BEHAVIOR": "",
    "ADMINISTRATIVE_SYSTEMS_ISSUES": "",
    "STATUS": "Approved"
  }
]

_MOCK_RISK_DRIVER_STATS = [
  {
    "DRIVER_ID": "AN-AIRWAY-MANAGEMENT-COMPLICATIONS",
    "SPECIALTY": "Anesthesiology",
    "DRIVER": "Airway management complications",
    "FULL_DRIVER_NAME": "Airway Management Complications",
    "TOTAL_CONTRIBUTING_FACTORS": 24,
    "CLAIMS_FREQUENCY_PCT": 17.52,
    "AVG_SEVERITY_USD": 0,
    "CLINICAL_DX_FAIL_ORDER_TESTING": 0.0417,
    "CLINICAL_DX_FAIL_RECOGNIZE_FINDING": 0.0417,
    "CLINICAL_DX_FAIL_OBTAIN_HX_OR_PE": 0.0417,
    "CLINICAL_DX_OTHER": 0.0,
    "CLINICAL_TX_MEDICATION_ERROR": 0.125,
    "CLINICAL_TX_NON_MED_INTERVENTION_ERROR": 0.125,
    "CLINICAL_TX_FAIL_MONITOR": 0.125,
    "CLINICAL_TX_FAIL_CONSULT_OR_TRANSFER": 0.0417,
    "CLINICAL_TX_OTHER": 0.0,
    "CLINICAL_PROC_TECHNIQUE_ERROR": 0.1667,
    "CLINICAL_PROC_WRONG_PT_SITE_PROC_IMPLANT": 0.0,
    "CLINICAL_PROC_RETAINED_FOREIGN_BODY": 0.0,
    "CLINICAL_PROC_OTHER": 0.0,
    "ADMIN_COMM_BETWEEN_PROVIDERS": 0.0417,
    "ADMIN_COMM_PROVIDER_TO_PATIENT": 0.0,
    "ADMIN_DOCUMENTATION_FAILURE": 0.1667,
    "ADMIN_PATIENT_NON_ADHERENCE": 0.0,
    "ADMIN_PROF_INAPPROPRIATE_CONDUCT": 0.0417,
    "ADMIN_PROF_RECKLESS_OR_HEALTH": 0.0,
    "ADMIN_SYS_LACK_EQUIPMENT_OR_TECH": 0.0,
    "ADMIN_SYS_LACK_PROCESS_OR_POLICY": 0.0417
  },
  {
    "DRIVER_ID": "CA-AORTIC-DISSECTION",
    "SPECIALTY": "Cardiology",
    "DRIVER": "Aortic Dissection",
    "FULL_DRIVER_NAME": "Aortic Dissection:",
    "TOTAL_CONTRIBUTING_FACTORS": 9,
    "CLAIMS_FREQUENCY_PCT": 3.18,
    "AVG_SEVERITY_USD": 0,
    "CLINICAL_DX_FAIL_ORDER_TESTING": 0.2222,
    "CLINICAL_DX_FAIL_RECOGNIZE_FINDING": 0.1111,
    "CLINICAL_DX_FAIL_OBTAIN_HX_OR_PE": 0.0,
    "CLINICAL_DX_OTHER": 0.0,
    "CLINICAL_TX_MEDICATION_ERROR": 0.1111,
    "CLINICAL_TX_NON_MED_INTERVENTION_ERROR": 0.1111,
    "CLINICAL_TX_FAIL_MONITOR": 0.3333,
    "CLINICAL_TX_FAIL_CONSULT_OR_TRANSFER": 0.0,
    "CLINICAL_TX_OTHER": 0.0,
    "CLINICAL_PROC_TECHNIQUE_ERROR": 0.0,
    "CLINICAL_PROC_WRONG_PT_SITE_PROC_IMPLANT": 0.0,
    "CLINICAL_PROC_RETAINED_FOREIGN_BODY": 0.0,
    "CLINICAL_PROC_OTHER": 0.0,
    "ADMIN_COMM_BETWEEN_PROVIDERS": 0.0,
    "ADMIN_COMM_PROVIDER_TO_PATIENT": 0.0,
    "ADMIN_DOCUMENTATION_FAILURE": 0.1111,
    "ADMIN_PATIENT_NON_ADHERENCE": 0.0,
    "ADMIN_PROF_INAPPROPRIATE_CONDUCT": 0.0,
    "ADMIN_PROF_RECKLESS_OR_HEALTH": 0.0,
    "ADMIN_SYS_LACK_EQUIPMENT_OR_TECH": 0.0,
    "ADMIN_SYS_LACK_PROCESS_OR_POLICY": 0.0
  },
  {
    "DRIVER_ID": "DE-CANCER",
    "SPECIALTY": "Dermatology",
    "DRIVER": "Cancer",
    "FULL_DRIVER_NAME": "Skin Cancer",
    "TOTAL_CONTRIBUTING_FACTORS": 43,
    "CLAIMS_FREQUENCY_PCT": 100.0,
    "AVG_SEVERITY_USD": 0,
    "CLINICAL_DX_FAIL_ORDER_TESTING": 0.1163,
    "CLINICAL_DX_FAIL_RECOGNIZE_FINDING": 0.0698,
    "CLINICAL_DX_FAIL_OBTAIN_HX_OR_PE": 0.1163,
    "CLINICAL_DX_OTHER": 0.0233,
    "CLINICAL_TX_MEDICATION_ERROR": 0.0233,
    "CLINICAL_TX_NON_MED_INTERVENTION_ERROR": 0.0465,
    "CLINICAL_TX_FAIL_MONITOR": 0.0698,
    "CLINICAL_TX_FAIL_CONSULT_OR_TRANSFER": 0.0233,
    "CLINICAL_TX_OTHER": 0.0,
    "CLINICAL_PROC_TECHNIQUE_ERROR": 0.093,
    "CLINICAL_PROC_WRONG_PT_SITE_PROC_IMPLANT": 0.0233,
    "CLINICAL_PROC_RETAINED_FOREIGN_BODY": 0.0,
    "CLINICAL_PROC_OTHER": 0.0,
    "ADMIN_COMM_BETWEEN_PROVIDERS": 0.0,
    "ADMIN_COMM_PROVIDER_TO_PATIENT": 0.1163,
    "ADMIN_DOCUMENTATION_FAILURE": 0.1163,
    "ADMIN_PATIENT_NON_ADHERENCE": 0.0698,
    "ADMIN_PROF_INAPPROPRIATE_CONDUCT": 0.0465,
    "ADMIN_PROF_RECKLESS_OR_HEALTH": 0.0,
    "ADMIN_SYS_LACK_EQUIPMENT_OR_TECH": 0.0,
    "ADMIN_SYS_LACK_PROCESS_OR_POLICY": 0.0465
  },
  {
    "DRIVER_ID": "EM-ACUTE-MYOCARDIAL-INFARCTION",
    "SPECIALTY": "Emergency Medicine",
    "DRIVER": "Acute Myocardial Infarction",
    "FULL_DRIVER_NAME": "Acute Myocardial Infarction: A High-Impact Malpractice Risk",
    "TOTAL_CONTRIBUTING_FACTORS": 66,
    "CLAIMS_FREQUENCY_PCT": 7.85,
    "AVG_SEVERITY_USD": 0,
    "CLINICAL_DX_FAIL_ORDER_TESTING": 0.2273,
    "CLINICAL_DX_FAIL_RECOGNIZE_FINDING": 0.1212,
    "CLINICAL_DX_FAIL_OBTAIN_HX_OR_PE": 0.1515,
    "CLINICAL_DX_OTHER": 0.0152,
    "CLINICAL_TX_MEDICATION_ERROR": 0.0303,
    "CLINICAL_TX_NON_MED_INTERVENTION_ERROR": 0.1364,
    "CLINICAL_TX_FAIL_MONITOR": 0.0455,
    "CLINICAL_TX_FAIL_CONSULT_OR_TRANSFER": 0.0758,
    "CLINICAL_TX_OTHER": 0.0,
    "CLINICAL_PROC_TECHNIQUE_ERROR": 0.0303,
    "CLINICAL_PROC_WRONG_PT_SITE_PROC_IMPLANT": 0.0,
    "CLINICAL_PROC_RETAINED_FOREIGN_BODY": 0.0,
    "CLINICAL_PROC_OTHER": 0.0,
    "ADMIN_COMM_BETWEEN_PROVIDERS": 0.0152,
    "ADMIN_COMM_PROVIDER_TO_PATIENT": 0.0152,
    "ADMIN_DOCUMENTATION_FAILURE": 0.0303,
    "ADMIN_PATIENT_NON_ADHERENCE": 0.0152,
    "ADMIN_PROF_INAPPROPRIATE_CONDUCT": 0.0455,
    "ADMIN_PROF_RECKLESS_OR_HEALTH": 0.0,
    "ADMIN_SYS_LACK_EQUIPMENT_OR_TECH": 0.0,
    "ADMIN_SYS_LACK_PROCESS_OR_POLICY": 0.0455
  }
]

_MOCK_CLAIM_SUMMARIES = [
  {
    "DOCUMENT_ID": "CLM-1042",
    "SPECIALTY": "Emergency Medicine",
    "AGE_RANGE": "Late 50s",
    "SEX": "Male",
    "PRESENTING_COMPLAINT": "Substernal chest pressure with intermittent symptoms over several hours.",
    "SUMMARY": "Patient with hypertension presented to the emergency department with chest pressure radiating to the left arm. Initial ECG was read as unchanged from prior. A single troponin returned below assay limit. The clinician documented presumed musculoskeletal pain and discharged the patient without serial troponins or scheduled outpatient cardiac workup. The patient returned via EMS two days later in cardiogenic shock from a proximal LAD occlusion. He survived but with significantly reduced ejection fraction.",
    "ADVERSE_OUTCOME": "Major myocardial infarction with permanent reduction in cardiac function.",
    "ALLEGATIONS": [
      "Failure to perform serial cardiac evaluation in a patient with risk factors and ongoing chest pain.",
      "Inadequate documentation of differential diagnosis and discharge reasoning.",
      "Failure to arrange timely outpatient cardiac evaluation."
    ],
    "RESOLUTION": "Settled for a confidential amount."
  },
  {
    "DOCUMENT_ID": "CLM-2188",
    "SPECIALTY": "Emergency Medicine",
    "AGE_RANGE": "70s",
    "SEX": "Female",
    "PRESENTING_COMPLAINT": "Generalized weakness, low-grade fever, and altered mentation.",
    "SUMMARY": "Elderly woman with a recent UTI presented to the ED with weakness and confusion. Triage vitals showed mild tachycardia and a temperature of 38.2C. Over the next four hours, vital signs drifted: heart rate increased from 102 to 118, mean arterial pressure dropped from 75 to 62, and respiratory rate rose to 24. Sepsis screen at triage was negative; rechecks did not re-screen. Antibiotics were ordered after the third recheck but not administered for an additional ninety minutes. The patient was transferred to the ICU in septic shock and required vasopressors for five days.",
    "ADVERSE_OUTCOME": "Septic shock with prolonged ICU course; survived with new dialysis dependence.",
    "ALLEGATIONS": [
      "Failure to recognize and act on vital-sign trajectory.",
      "Delay in antibiotic administration.",
      "Failure to escalate care."
    ],
    "RESOLUTION": "Settled in the high six figures."
  },
  {
    "DOCUMENT_ID": "CLM-1577",
    "SPECIALTY": "Internal Medicine",
    "AGE_RANGE": "60s",
    "SEX": "Male",
    "PRESENTING_COMPLAINT": "Admitted for pneumonia; on chronic warfarin for atrial fibrillation.",
    "SUMMARY": "Patient on chronic warfarin admitted for pneumonia. Anticoagulation was held on admission for a planned bronchoscopy that was ultimately not performed. The hold was never re-evaluated and warfarin was not resumed during the seven-day admission. Discharge medication reconciliation listed warfarin as an active home medication but did not address that it had been held throughout the stay. The patient was readmitted twelve days later with an embolic stroke.",
    "ADVERSE_OUTCOME": "Embolic stroke with residual hemiparesis.",
    "ALLEGATIONS": [
      "Failure to maintain anticoagulation in a patient with atrial fibrillation.",
      "Inadequate medication reconciliation at discharge.",
      "Failure to communicate the medication hold to the outpatient prescriber."
    ],
    "RESOLUTION": "Resolved for low six figures."
  },
  {
    "DOCUMENT_ID": "CLM-3301",
    "SPECIALTY": "Surgery",
    "AGE_RANGE": "40s",
    "SEX": "Female",
    "PRESENTING_COMPLAINT": "Elective laparoscopic procedure.",
    "SUMMARY": "Patient underwent an elective laparoscopic procedure that was extended unexpectedly due to adhesions. A sponge count discrepancy was noted at first close-out and resolved verbally without imaging. The patient developed worsening abdominal pain over the following weeks and was found on CT to have a retained surgical sponge. She underwent reoperation and a prolonged course of antibiotics for intra-abdominal infection.",
    "ADVERSE_OUTCOME": "Retained surgical sponge; reoperation; intra-abdominal infection.",
    "ALLEGATIONS": [
      "Failure to resolve the count discrepancy with imaging.",
      "Failure to document the basis for closure despite the discrepancy.",
      "Failure to follow institutional count protocol."
    ],
    "RESOLUTION": "Resolved in the mid-six figures."
  },
  {
    "DOCUMENT_ID": "CLM-2902",
    "SPECIALTY": "Internal Medicine",
    "AGE_RANGE": "80s",
    "SEX": "Male",
    "PRESENTING_COMPLAINT": "Admitted for COPD exacerbation.",
    "SUMMARY": "Patient admitted for COPD exacerbation. Home medication list at admission omitted his rivaroxaban for atrial fibrillation. The omission was not caught at any of three medication reconciliation steps during the admission. He was discharged without rivaroxaban listed on the discharge med list. He suffered an embolic stroke twenty days after discharge.",
    "ADVERSE_OUTCOME": "Embolic stroke with significant functional decline.",
    "ALLEGATIONS": [
      "Medication reconciliation failure across three transition points.",
      "Omission of anticoagulation on the discharge medication list.",
      "Inadequate communication with the patient and outpatient prescriber."
    ],
    "RESOLUTION": "Settled for a confidential amount."
  },
  {
    "DOCUMENT_ID": "CLM-4410",
    "SPECIALTY": "Obstetrics and Gynecology",
    "AGE_RANGE": "30s",
    "SEX": "Female",
    "PRESENTING_COMPLAINT": "Term labor with macrosomic fetus and gestational diabetes.",
    "SUMMARY": "Patient with gestational diabetes presented in active labor at term. Estimated fetal weight by ultrasound was greater than 4500 grams. Shoulder dystocia was encountered at delivery. The team performed maneuvers but the operative note documented only that maneuvers were applied without specifying which, in what order, or with what response. The neonate was born with a brachial plexus injury. At litigation the documentation did not support the standard sequence having been followed.",
    "ADVERSE_OUTCOME": "Neonatal brachial plexus injury; ongoing rehabilitation.",
    "ALLEGATIONS": [
      "Failure to document the maneuvers, their order, and the response.",
      "Concerns regarding traction direction.",
      "Late call for additional help."
    ],
    "RESOLUTION": "Settled in the low seven figures."
  },
  {
    "DOCUMENT_ID": "CLM-5588",
    "SPECIALTY": "Emergency Medicine",
    "AGE_RANGE": "60s",
    "SEX": "Female",
    "PRESENTING_COMPLAINT": "Fatigue and nausea, no chest pain.",
    "SUMMARY": "Diabetic woman in her 60s presented with fatigue, nausea, and mild epigastric discomfort. Initial ECG and a single troponin were unremarkable. She was discharged with a diagnosis of gastritis. Twelve hours later she was found unresponsive at home and pronounced dead in the field. Autopsy revealed an acute myocardial infarction.",
    "ADVERSE_OUTCOME": "Out-of-hospital death from acute MI.",
    "ALLEGATIONS": [
      "Failure to recognize atypical presentation of ACS in a diabetic patient.",
      "Failure to perform serial assessment.",
      "Inadequate discharge instructions."
    ],
    "RESOLUTION": "Settled in the high seven figures."
  },
  {
    "DOCUMENT_ID": "CLM-6701",
    "SPECIALTY": "Emergency Medicine",
    "AGE_RANGE": "40s",
    "SEX": "Male",
    "PRESENTING_COMPLAINT": "Sore throat and neck stiffness.",
    "SUMMARY": "Patient presented with sore throat and neck stiffness. Initial workup was attributed to viral pharyngitis. Vital signs at recheck three hours later showed worsening tachycardia and a new fever. Sepsis screen was not repeated. The patient deteriorated rapidly and was diagnosed with Lemierre syndrome on imaging the following day.",
    "ADVERSE_OUTCOME": "Septic emboli to lungs; prolonged ICU stay; survived with sequelae.",
    "ALLEGATIONS": [
      "Failure to broaden the differential when symptoms did not improve.",
      "Failure to repeat sepsis screen.",
      "Delay in advanced imaging."
    ],
    "RESOLUTION": "Resolved for mid-six figures."
  }
]

_MOCK_CLAIM_RISK_TAGS = [
  {
    "DOCUMENT_ID": "CLM-1042",
    "DRIVER_ID": "EM-CVA",
    "TAG_CONFIDENCE": 0.9
  },
  {
    "DOCUMENT_ID": "CLM-2188",
    "DRIVER_ID": "EM-CVA",
    "TAG_CONFIDENCE": 0.9
  },
  {
    "DOCUMENT_ID": "CLM-1577",
    "DRIVER_ID": "IM-CANCER",
    "TAG_CONFIDENCE": 0.9
  },
  {
    "DOCUMENT_ID": "CLM-2902",
    "DRIVER_ID": "IM-CANCER",
    "TAG_CONFIDENCE": 0.9
  },
  {
    "DOCUMENT_ID": "CLM-5588",
    "DRIVER_ID": "EM-CVA",
    "TAG_CONFIDENCE": 0.9
  },
  {
    "DOCUMENT_ID": "CLM-6701",
    "DRIVER_ID": "EM-CVA",
    "TAG_CONFIDENCE": 0.9
  }
]

