"""Chat orchestration: quick-action chip catalog, chat-edit handler, and the COURSE_EDIT_LOG audit trail. Merged from chat_log.py, chat_orchestrator.py, and quick_actions.py for the flat SiS bundle."""
from __future__ import annotations


# ---------------------------------------------------------------------
# Quick-action chip catalog (formerly quick_actions.py)
# ---------------------------------------------------------------------

QUICK_ACTIONS = [
    {"id": "tighten",      "label": "Tighten",
     "instruction": "Tighten the prose. Cut anything redundant. Keep every clinical fact. Aim for ~25% fewer words. Preserve structure."},
    {"id": "expand",       "label": "Expand",
     "instruction": "Expand the section with more clinical depth. Add specific examples, decision criteria, or concrete protocols where relevant. Stay grounded in the source material."},
    {"id": "more_clinical","label": "Clinical",
     "instruction": "Increase clinical specificity. Add named decision tools (HEART, qSOFA, etc.), specific lab/imaging modalities, and standard-of-care references where the source supports it."},
    {"id": "add_example",  "label": "Example",
     "instruction": "Add one short illustrative example or vignette that demonstrates the key concept. Keep it under 80 words. Do not introduce new facts beyond the source."},
    {"id": "fact_check",   "label": "Fact-check",
     "instruction": "Audit every clinical claim against the source material. Remove or soften anything not supported. Mark any place where the source is silent."},
    {"id": "more_accessible","label": "Plain",
     "instruction": "Lower the reading level slightly while preserving clinical accuracy. Break long sentences. Define jargon on first use. Keep all clinical terms intact when they are necessary."},
]


def by_id(action_id: str) -> dict | None:
    for a in QUICK_ACTIONS:
        if a["id"] == action_id:
            return a
    return None


def labels() -> list[tuple[str, str]]:
    return [(a["id"], a["label"]) for a in QUICK_ACTIONS]


# ---------------------------------------------------------------------
# Audit log (formerly chat_log.py)
# ---------------------------------------------------------------------

import json
import time
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from cortex import _try_get_session

LOG_TABLE = "HACKATHON_DWH.ADVICE.COURSE_EDIT_LOG"
LOG_DIR = Path(__file__).resolve().parent.parent / "data" / "saved"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "edit_log.jsonl"

# Cap large fields so the log row never blows past Snowflake VARCHAR limits
# or makes the local file unwieldy. 4 KB is enough to identify what was
# changed without storing entire lessons.
_MAX_FIELD = 4_000


@dataclass
class EditLogEntry:
    log_id: str
    occurred_at: str          # ISO timestamp
    save_id: Optional[str]
    section_id: str
    kind: str                 # 'quick_action' | 'chat_edit' | 'regenerate'
    instruction: str
    prompt: str
    before_text: str
    after_text: str
    model: str
    temperature: float
    latency_ms: int
    prompt_version: str


def log_edit(
    *,
    section_id: str,
    kind: str,
    instruction: str,
    before_text: str,
    after_text: str,
    prompt: str,
    model: str,
    temperature: float,
    latency_s: float,
    save_id: Optional[str] = None,
    prompt_version: str = "",
) -> EditLogEntry:
    """Persist one edit. Snowflake when available, JSONL otherwise.

    Errors in the persistence path are swallowed silently — a failed
    audit write must never block a user edit. The entry is still
    returned so the UI can echo it back if useful.
    """
    entry = EditLogEntry(
        log_id=uuid.uuid4().hex[:10],
        occurred_at=datetime.utcnow().isoformat(timespec="seconds"),
        save_id=save_id,
        section_id=section_id,
        kind=kind,
        instruction=(instruction or "")[:_MAX_FIELD],
        prompt=(prompt or "")[:_MAX_FIELD],
        before_text=(before_text or "")[:_MAX_FIELD],
        after_text=(after_text or "")[:_MAX_FIELD],
        model=model,
        temperature=float(temperature),
        latency_ms=int(round(latency_s * 1000)),
        prompt_version=prompt_version,
    )
    session = _try_get_session()
    if session is not None:
        try:
            _write_snowflake(session, entry)
            return entry
        except Exception:
            pass
    try:
        _write_local(entry)
    except Exception:
        pass
    return entry


def list_recent(limit: int = 50, save_id: Optional[str] = None) -> list[EditLogEntry]:
    """Return the most recent edits (newest first). Snowflake first; local
    JSONL fallback. Filters by save_id when provided.
    """
    session = _try_get_session()
    if session is not None:
        try:
            return _read_snowflake(session, limit=limit, save_id=save_id)
        except Exception:
            pass
    return _read_local(limit=limit, save_id=save_id)


def to_csv(entries: list[EditLogEntry]) -> str:
    """Render a CSV string of edit entries. Used by the Tools popover's
    "Export edit log" button so reviewers can pull the data into Sheets/
    Excel without a Snowflake login.
    """
    import csv
    import io
    buf = io.StringIO()
    fields = [
        "occurred_at", "save_id", "section_id", "kind", "instruction",
        "model", "temperature", "latency_ms", "prompt_version",
        "before_text", "after_text",
    ]
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    for e in entries:
        w.writerow(asdict(e))
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Snowflake backend
# ---------------------------------------------------------------------------
def _ensure_table(session) -> None:
    sql = f"""
    CREATE TABLE IF NOT EXISTS {LOG_TABLE} (
        LOG_ID         VARCHAR PRIMARY KEY,
        OCCURRED_AT    TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP,
        SAVE_ID        VARCHAR,
        SECTION_ID     VARCHAR,
        KIND           VARCHAR,
        INSTRUCTION    VARCHAR,
        PROMPT         VARCHAR,
        BEFORE_TEXT    VARCHAR,
        AFTER_TEXT     VARCHAR,
        MODEL          VARCHAR,
        TEMPERATURE    FLOAT,
        LATENCY_MS     NUMBER,
        PROMPT_VERSION VARCHAR
    )
    """
    session.sql(sql).collect()


def _write_snowflake(session, entry: EditLogEntry) -> None:
    _ensure_table(session)
    session.sql(
        f"INSERT INTO {LOG_TABLE} ("
        "LOG_ID, OCCURRED_AT, SAVE_ID, SECTION_ID, KIND, INSTRUCTION, "
        "PROMPT, BEFORE_TEXT, AFTER_TEXT, MODEL, TEMPERATURE, LATENCY_MS, "
        "PROMPT_VERSION) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        params=[
            entry.log_id, entry.occurred_at, entry.save_id, entry.section_id,
            entry.kind, entry.instruction, entry.prompt, entry.before_text,
            entry.after_text, entry.model, entry.temperature,
            entry.latency_ms, entry.prompt_version,
        ],
    ).collect()


def _read_snowflake(session, limit: int, save_id: Optional[str]) -> list[EditLogEntry]:
    where = "WHERE SAVE_ID = ?" if save_id else ""
    params = [save_id] if save_id else []
    rows = session.sql(
        f"SELECT LOG_ID, TO_VARCHAR(OCCURRED_AT) AS OCCURRED_AT, SAVE_ID, "
        f"SECTION_ID, KIND, INSTRUCTION, PROMPT, BEFORE_TEXT, AFTER_TEXT, "
        f"MODEL, TEMPERATURE, LATENCY_MS, PROMPT_VERSION "
        f"FROM {LOG_TABLE} {where} "
        f"ORDER BY OCCURRED_AT DESC LIMIT {int(limit)}",
        params=params,
    ).collect()
    return [
        EditLogEntry(
            log_id=r["LOG_ID"],
            occurred_at=r["OCCURRED_AT"],
            save_id=r.get("SAVE_ID"),
            section_id=r.get("SECTION_ID", ""),
            kind=r.get("KIND", ""),
            instruction=r.get("INSTRUCTION", "") or "",
            prompt=r.get("PROMPT", "") or "",
            before_text=r.get("BEFORE_TEXT", "") or "",
            after_text=r.get("AFTER_TEXT", "") or "",
            model=r.get("MODEL", "") or "",
            temperature=float(r.get("TEMPERATURE") or 0.0),
            latency_ms=int(r.get("LATENCY_MS") or 0),
            prompt_version=r.get("PROMPT_VERSION", "") or "",
        )
        for r in (row.as_dict() for row in rows)
    ]


# ---------------------------------------------------------------------------
# Local JSONL backend
# ---------------------------------------------------------------------------
def _write_local(entry: EditLogEntry) -> None:
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(entry)) + "\n")


def _read_local(limit: int, save_id: Optional[str]) -> list[EditLogEntry]:
    if not LOG_FILE.exists():
        return []
    out: list[EditLogEntry] = []
    with LOG_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if save_id and d.get("save_id") != save_id:
                continue
            out.append(EditLogEntry(**d))
    out.sort(key=lambda e: e.occurred_at, reverse=True)
    return out[:limit]


# ---------------------------------------------------------------------
# Chat-edit orchestrator (formerly chat_orchestrator.py)
# ---------------------------------------------------------------------

from typing import Optional

from cortex import complete, model_for, temp_for
from prompts import build_edit_section, PROMPT_VERSION




def apply_chat_edit(section_name: str, current_text: str,
                    sources_block: str, user_instruction: str,
                    *, kind: str = "edit_section",
                    section_id: Optional[str] = None,
                    save_id: Optional[str] = None) -> dict:
    """Apply a free-text user instruction.

    `kind` lets the caller pick a different prompt-kind for the model/temp
    table (defaults to 'edit_section' which uses Opus). Quick actions use
    `kind='quick_action'` to route to the faster Sonnet model.

    Audit: every successful call writes one row to the COURSE_EDIT_LOG
    (Snowflake or local JSONL fallback) so the team can review what
    users edit and refine prompts. The audit write never blocks the
    user-visible return.
    """
    prompt = build_edit_section(section_name, current_text, sources_block, user_instruction)
    res = complete(prompt, kind=kind)
    log_edit(
        section_id=section_id or section_name,
        kind="quick_action" if kind == "quick_action" else "chat_edit",
        instruction=user_instruction,
        before_text=current_text or "",
        after_text=res.text or "",
        prompt=prompt,
        model=res.model or model_for(kind),
        temperature=temp_for(kind),
        latency_s=res.elapsed_s,
        save_id=save_id,
        prompt_version=PROMPT_VERSION,
    )
    return {
        "text": res.text,
        "mocked": res.mocked,
        "latency_s": res.elapsed_s,
        "instruction": user_instruction,
    }


def apply_quick_action(section_name: str, current_text: str,
                       sources_block: str, action_id: str,
                       *, section_id: Optional[str] = None,
                       save_id: Optional[str] = None) -> dict:
    action = by_id(action_id)
    if not action:
        return {"text": current_text, "mocked": True, "latency_s": 0.0,
                "instruction": f"(unknown action: {action_id})"}
    return apply_chat_edit(
        section_name, current_text, sources_block,
        action["instruction"], kind="quick_action",
        section_id=section_id, save_id=save_id,
    )
