"""Save & load drafts.

Real backend: writes to Snowflake tables under HACKATHON_DWH.ADVICE:
  - GENERATED_COURSES (course generator)
  - GENERATED_LESSONS (claims lesson generator)

Schema (auto-creates if your role can):
    CREATE TABLE IF NOT EXISTS GENERATED_COURSES (
        SAVE_ID      VARCHAR PRIMARY KEY,
        CREATED_AT   TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP,
        UPDATED_AT   TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP,
        TITLE        VARCHAR,
        DRIVER_ID    VARCHAR,
        PAYLOAD      VARIANT
    );

    CREATE TABLE IF NOT EXISTS GENERATED_LESSONS (
        SAVE_ID      VARCHAR PRIMARY KEY,
        CREATED_AT   TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP,
        UPDATED_AT   TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP,
        TITLE        VARCHAR,
        CLAIM_ID     VARCHAR,
        DRIVER_ID    VARCHAR,
        PAYLOAD      VARIANT
    );

Mock fallback: writes JSON files under data/saved/.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

from cortex import _try_get_session

SAVED_DIR = Path(__file__).resolve().parent.parent / "data" / "saved"
SAVED_DIR.mkdir(parents=True, exist_ok=True)

T_COURSES = "HACKATHON_DWH.ADVICE.GENERATED_COURSES"
T_LESSONS = "HACKATHON_DWH.ADVICE.GENERATED_LESSONS"


@dataclass
class SavedItem:
    save_id: str
    kind: str            # "course" or "lesson"
    title: str
    created_at: str      # ISO string
    updated_at: str
    driver_id: str | None
    claim_id: str | None
    payload: dict
    # Audit metadata so future reviewers can tell which prompts +
    # builder version produced a saved draft. Default empty for
    # backward compat with older saves on disk.
    prompt_version: str = ""
    builder_version: str = ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def save_item(kind: str, title: str, payload: dict,
              save_id: str | None = None,
              driver_id: str | None = None,
              claim_id: str | None = None,
              prompt_version: str | None = None,
              builder_version: str = "MyAdvice Builder 1.0") -> SavedItem:
    """Persist a draft. If save_id is provided, update in place.

    `prompt_version` defaults to the current `shared.prompts.PROMPT_VERSION`
    so future reviewers can tell which prompt set produced this content
    even after the constants are bumped.
    """
    now = datetime.utcnow().isoformat(timespec="seconds")
    sid = save_id or _new_id()
    if prompt_version is None:
        try:
            from prompts import PROMPT_VERSION
            prompt_version = PROMPT_VERSION
        except Exception:
            prompt_version = ""
    item = SavedItem(
        save_id=sid, kind=kind, title=title[:200],
        created_at=now, updated_at=now,
        driver_id=driver_id, claim_id=claim_id,
        payload=payload,
        prompt_version=prompt_version,
        builder_version=builder_version,
    )
    session = _try_get_session()
    if session is not None:
        try:
            _save_snowflake(session, item)
            return item
        except Exception:
            # fall through to mock
            pass
    _save_local(item)
    return item


def list_saves(kind: str) -> list[SavedItem]:
    """Return all saves of a given kind, newest first."""
    session = _try_get_session()
    if session is not None:
        try:
            return _list_snowflake(session, kind)
        except Exception:
            pass
    return _list_local(kind)


def load_save(save_id: str) -> SavedItem | None:
    session = _try_get_session()
    if session is not None:
        try:
            for kind in ("course", "lesson"):
                items = _list_snowflake(session, kind)
                for it in items:
                    if it.save_id == save_id:
                        return it
        except Exception:
            pass
    # Local
    for f in SAVED_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            if data.get("save_id") == save_id:
                return SavedItem(**data)
        except Exception:
            continue
    return None


def delete_save(save_id: str) -> bool:
    session = _try_get_session()
    if session is not None:
        try:
            for table in (T_COURSES, T_LESSONS):
                session.sql(f"DELETE FROM {table} WHERE SAVE_ID = ?",
                            params=[save_id]).collect()
            return True
        except Exception:
            pass
    for f in SAVED_DIR.glob(f"*_{save_id}.json"):
        try:
            f.unlink()
            return True
        except Exception:
            return False
    return False


# ---------------------------------------------------------------------------
# Local-JSON backend
# ---------------------------------------------------------------------------
def _local_path(item: SavedItem) -> Path:
    return SAVED_DIR / f"{item.kind}_{item.save_id}.json"


def _save_local(item: SavedItem) -> None:
    path = _local_path(item)
    path.write_text(json.dumps(asdict(item), indent=2, default=str))


def _list_local(kind: str) -> list[SavedItem]:
    out: list[SavedItem] = []
    for f in SAVED_DIR.glob(f"{kind}_*.json"):
        try:
            data = json.loads(f.read_text())
            out.append(SavedItem(**data))
        except Exception:
            continue
    out.sort(key=lambda x: x.updated_at, reverse=True)
    return out


# ---------------------------------------------------------------------------
# Snowflake backend
# ---------------------------------------------------------------------------
def _ensure_table(session, table: str, claim_or_driver: str) -> None:
    other_col = "CLAIM_ID VARCHAR" if claim_or_driver == "claim" else "DRIVER_ID VARCHAR"
    sql = f"""
    CREATE TABLE IF NOT EXISTS {table} (
        SAVE_ID    VARCHAR PRIMARY KEY,
        CREATED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP,
        UPDATED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP,
        TITLE      VARCHAR,
        {other_col},
        PAYLOAD    VARIANT
    )
    """
    session.sql(sql).collect()


def _save_snowflake(session, item: SavedItem) -> None:
    table = T_COURSES if item.kind == "course" else T_LESSONS
    _ensure_table(session, table, "driver" if item.kind == "course" else "claim")
    other_col = "DRIVER_ID" if item.kind == "course" else "CLAIM_ID"
    other_val = item.driver_id if item.kind == "course" else item.claim_id

    # Stash audit fields inside the PAYLOAD VARIANT so they survive without
    # requiring a schema migration on the existing GENERATED_COURSES /
    # GENERATED_LESSONS tables. Reviewers can pull them back out with:
    #   SELECT PAYLOAD:prompt_version FROM GENERATED_COURSES;
    audited_payload = dict(item.payload or {})
    audited_payload.setdefault("_audit", {})
    audited_payload["_audit"].update({
        "prompt_version": item.prompt_version,
        "builder_version": item.builder_version,
        "saved_at": item.updated_at,
    })
    payload_json = json.dumps(audited_payload)
    # Upsert
    merge_sql = f"""
    MERGE INTO {table} t
    USING (SELECT ? AS SAVE_ID, ? AS TITLE, ? AS {other_col},
                  PARSE_JSON(?) AS PAYLOAD) s
      ON t.SAVE_ID = s.SAVE_ID
    WHEN MATCHED THEN UPDATE SET
        UPDATED_AT = CURRENT_TIMESTAMP,
        TITLE = s.TITLE,
        {other_col} = s.{other_col},
        PAYLOAD = s.PAYLOAD
    WHEN NOT MATCHED THEN INSERT (SAVE_ID, TITLE, {other_col}, PAYLOAD)
        VALUES (s.SAVE_ID, s.TITLE, s.{other_col}, s.PAYLOAD)
    """
    session.sql(merge_sql, params=[item.save_id, item.title,
                                    other_val, payload_json]).collect()


def _list_snowflake(session, kind: str) -> list[SavedItem]:
    table = T_COURSES if kind == "course" else T_LESSONS
    other_col = "DRIVER_ID" if kind == "course" else "CLAIM_ID"
    sql = (f"SELECT SAVE_ID, TITLE, {other_col}, "
           f"TO_VARCHAR(CREATED_AT) AS CREATED_AT, "
           f"TO_VARCHAR(UPDATED_AT) AS UPDATED_AT, PAYLOAD "
           f"FROM {table} ORDER BY UPDATED_AT DESC LIMIT 50")
    rows = session.sql(sql).collect()
    out = []
    for r in rows:
        d = r.as_dict()
        payload = d.get("PAYLOAD")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        out.append(SavedItem(
            save_id=d["SAVE_ID"],
            kind=kind,
            title=d.get("TITLE", ""),
            created_at=d.get("CREATED_AT", ""),
            updated_at=d.get("UPDATED_AT", ""),
            driver_id=d.get("DRIVER_ID") if kind == "course" else None,
            claim_id=d.get("CLAIM_ID") if kind == "lesson" else None,
            payload=payload or {},
        ))
    return out


def _new_id() -> str:
    return uuid.uuid4().hex[:10]
