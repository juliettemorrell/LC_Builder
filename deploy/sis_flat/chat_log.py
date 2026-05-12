"""Chat-edit audit log.

Captures every chat-driven edit (free-text instructions + quick actions)
applied to a course/lesson section so the team can review what users
edit, refine the prompts, and meet auditing expectations.

Two backends:
- **Snowflake** (when a Snowpark session is available): writes to
  `HACKATHON_DWH.ADVICE.COURSE_EDIT_LOG` (DDL in `data/setup.sql`).
- **Local JSON fallback**: appends to `data/saved/edit_log.jsonl`.

The entries record:
- log_id          short uuid
- save_id         course / lesson save id (null if unsaved draft)
- section_id      'course_body' | 'assessment' | 'lesson_1' | ...
- kind            'quick_action' | 'chat_edit' | 'regenerate'
- instruction     the user's instruction (or quick-action label)
- prompt          first 4 KB of the assembled prompt
- before_text     first 4 KB of the section before the edit
- after_text      first 4 KB of the section after the edit
- model           which Cortex model handled the call
- temperature
- latency_ms
- prompt_version  shared/prompts.PROMPT_VERSION
"""
from __future__ import annotations

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
