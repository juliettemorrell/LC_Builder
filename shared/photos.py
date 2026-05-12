"""Photo library for case-study heroes.

Architecture:
- Mock backend (default): reads SVG/PNG/JPG files from `data/photos/` and a
  `data/photos/manifest.json` that lists `{id, label, category, file}` for each.
  No network calls, no API keys. Used for the buildathon demo.
- Snowflake backend: when a Snowpark session is available, lists files in
  the named stage `@HACKATHON_DWH.ADVICE.COURSE_PHOTOS` (override via
  ADVICE_PHOTO_STAGE env var). Each file becomes a Photo whose URL is a
  pre-signed `GET_PRESIGNED_URL` for the staged file.
- Uploads: `add_uploaded_photo(name, bytes, mime)` writes to a per-session
  cache (local) or PUTs the file into the stage (Snowflake). Returns a
  Photo whose `id` starts with "uploaded:" so callers can distinguish.

Per-case selection lives in Streamlit session state — see the
`cg_case_photos` dict in `app_course_generator.py`.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "photos"
MANIFEST = DATA_DIR / "manifest.json"

PHOTO_STAGE = os.getenv(
    "ADVICE_PHOTO_STAGE", "HACKATHON_DWH.ADVICE.COURSE_PHOTOS"
)

# Per-process cache for in-memory uploaded photos (since local-mock mode
# has no shared filesystem write target during the demo).
_UPLOAD_CACHE: dict[str, "Photo"] = {}


@dataclass(frozen=True)
class Photo:
    id: str
    label: str
    category: str
    url: str  # data: URI for local files, https: for staged
    description: str = ""    # 1-2 sentence caption (used for search + tooltips)
    tags: tuple[str, ...] = ()  # searchable keywords

    def to_dict(self) -> dict:
        return {"id": self.id, "label": self.label,
                "category": self.category, "url": self.url,
                "description": self.description, "tags": list(self.tags)}


# ---------------------------------------------------------------------------
# Mock backend (default)
# ---------------------------------------------------------------------------
def _read_manifest() -> list[dict]:
    if not MANIFEST.exists():
        return []
    try:
        return json.loads(MANIFEST.read_text())
    except Exception:
        return []


def _file_to_data_uri(path: Path) -> str:
    """Encode a local image file as a data: URI so Streamlit's
    components.html iframe can render it without us hosting anything."""
    suffix = path.suffix.lower().lstrip(".")
    mime = {
        "svg": "image/svg+xml", "png": "image/png", "jpg": "image/jpeg",
        "jpeg": "image/jpeg", "webp": "image/webp", "gif": "image/gif",
    }.get(suffix, "application/octet-stream")
    raw = path.read_bytes()
    if mime == "image/svg+xml":
        # SVGs can be inlined as utf-8; smaller than base64.
        body = raw.decode("utf-8", errors="ignore")
        # data: URI with utf-8 SVG
        return f"data:{mime};utf8," + _safe_url_svg(body)
    enc = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{enc}"


def _safe_url_svg(svg: str) -> str:
    """URL-encode the bare-minimum bytes that browsers refuse in a
    data:image/svg+xml;utf8 URI without ballooning length like base64."""
    # Encoding # and % is required; encoding < > " keeps it readable.
    return (
        svg.replace("%", "%25")
           .replace("#", "%23")
           .replace("\n", "%0A")
    )


def _list_local() -> list[Photo]:
    """Return Photos from `data/photos/manifest.json`. Each manifest
    entry is `{id, label, category, file, description, tags}`. Missing
    files are skipped; description and tags are optional."""
    out: list[Photo] = []
    for entry in _read_manifest():
        f = DATA_DIR / entry.get("file", "")
        if not f.exists():
            continue
        tags_raw = entry.get("tags") or []
        tags = tuple(str(t).lower().strip() for t in tags_raw if str(t).strip())
        out.append(Photo(
            id=entry.get("id") or f.stem,
            label=entry.get("label") or entry.get("id") or f.stem,
            category=entry.get("category") or "general",
            url=_file_to_data_uri(f),
            description=str(entry.get("description") or ""),
            tags=tags,
        ))
    return out


# ---------------------------------------------------------------------------
# Snowflake stage backend
# ---------------------------------------------------------------------------
def _try_snowpark_session():
    """Lazy session probe shared with cortex.py. Returns None when not
    running inside Snowpark / Streamlit-in-Snowflake."""
    try:
        from .cortex import _try_get_session
        return _try_get_session()
    except Exception:
        return None


PHOTO_METADATA_TABLE = os.getenv(
    "ADVICE_PHOTO_METADATA_TABLE",
    "HACKATHON_DWH.ADVICE.COURSE_PHOTOS_METADATA",
)


def _list_stage_metadata(session) -> dict[str, dict]:
    """Pull `{relative_path: {label, description, tags, category}}` from
    the metadata side-table. Returns empty dict if the table doesn't
    exist (graceful — the stage listing still works on its own)."""
    try:
        rows = session.sql(
            f"SELECT RELATIVE_PATH, TITLE, DESCRIPTION, TAGS, CATEGORY "
            f"FROM {PHOTO_METADATA_TABLE}"
        ).collect()
        out: dict[str, dict] = {}
        for r in rows:
            d = r.as_dict()
            path = (d.get("RELATIVE_PATH") or "").strip("/")
            if not path:
                continue
            tags_raw = d.get("TAGS") or []
            if isinstance(tags_raw, str):
                # Snowflake VARIANT array may come back JSON-encoded
                import json as _json
                try:
                    tags_raw = _json.loads(tags_raw)
                except Exception:
                    tags_raw = [t.strip() for t in tags_raw.split(",") if t.strip()]
            out[path] = {
                "label": d.get("TITLE") or "",
                "description": d.get("DESCRIPTION") or "",
                "tags": tuple(str(t).lower().strip() for t in (tags_raw or [])),
                "category": d.get("CATEGORY") or "",
            }
        return out
    except Exception:
        return {}


def _record_photo_error(msg: str) -> None:
    """Push a photo-loading error onto session state for UI surfacing.
    Keeps the listing path resilient — we never raise from photo logic
    because a missing image must not crash the whole course generator."""
    try:
        import streamlit as st  # noqa
        st.session_state.setdefault("_photo_errors", []).append(str(msg)[:300])
    except Exception:
        pass


def _list_stage(session) -> list[Photo]:
    """List files in the photo stage and return Photos with pre-signed URLs.

    Uses the DIRECTORY() table function (the canonical SiS-compatible
    pattern) instead of LIST — DIRECTORY returns RELATIVE_PATH, which is
    the file path WITHOUT the stage prefix, exactly what GET_PRESIGNED_URL
    expects as its second argument. LIST's `name` column includes the
    lowercased stage name, which has caused presign failures in prod when
    the path was passed verbatim.

    All photo listing happens in ONE round-trip query (LIST + presign
    were previously N+1). Requires DIRECTORY = TRUE on the stage.

    Metadata layering is optional — if COURSE_PHOTOS_METADATA exists and
    has rows for a file, those override the auto-derived label/category.
    """
    rows = []
    sql = (
        f"SELECT RELATIVE_PATH, "
        f"GET_PRESIGNED_URL(@{PHOTO_STAGE}, RELATIVE_PATH, 3600) AS URL "
        f"FROM DIRECTORY(@{PHOTO_STAGE})"
    )
    try:
        rows = session.sql(sql).collect()
    except Exception as e:
        # DIRECTORY() requires DIRECTORY=TRUE on the stage. If the stage
        # was created without it, fall back to LIST + per-file presign.
        _record_photo_error(
            f"DIRECTORY(@{PHOTO_STAGE}) failed: {e} — falling back to LIST"
        )
        return _list_stage_via_list(session)
    if not rows:
        _record_photo_error(
            f"DIRECTORY(@{PHOTO_STAGE}) returned 0 files. Confirm the "
            "stage has photos uploaded and DIRECTORY = TRUE is enabled."
        )
        return []

    metadata = _list_stage_metadata(session)
    out: list[Photo] = []
    for r in rows:
        try:
            d = r.as_dict()
        except Exception:
            d = {}
        rel_path = d.get("RELATIVE_PATH") or d.get("relative_path") or ""
        url = d.get("URL") or d.get("url") or ""
        if not rel_path or not url:
            continue
        meta = metadata.get(rel_path) or {}
        parts = Path(rel_path).parts
        category = meta.get("category") or (parts[-2] if len(parts) >= 2 else "general")
        stem = Path(rel_path).stem
        photo_id = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-") or stem
        out.append(Photo(
            id=photo_id,
            label=meta.get("label") or stem.replace("_", " ").replace("-", " ").title(),
            category=category,
            url=str(url),
            description=meta.get("description", ""),
            tags=meta.get("tags", ()),
        ))
    return out


def _list_stage_via_list(session) -> list[Photo]:
    """Fallback path when DIRECTORY() isn't available on the stage.

    Uses LIST to enumerate files, then strips the stage-name prefix from
    each entry's `name` field so the remaining relative path can be
    passed to GET_PRESIGNED_URL. Slower (one extra query per file) but
    works on stages without DIRECTORY = TRUE.
    """
    try:
        rows = session.sql(f"LIST @{PHOTO_STAGE}").collect()
    except Exception as e:
        _record_photo_error(f"LIST @{PHOTO_STAGE} failed: {e}")
        return []
    if not rows:
        _record_photo_error(f"LIST @{PHOTO_STAGE} returned 0 files")
        return []
    metadata = _list_stage_metadata(session)
    out: list[Photo] = []
    presign_errors = 0
    for r in rows:
        try:
            d = r.as_dict()
        except Exception:
            d = {}
        path = d.get("name") or d.get("NAME") or ""
        if not path:
            continue
        parts = Path(path).parts
        # Strip the leading stage-name segment (e.g. "course_photos/...").
        rel_path = "/".join(parts[1:]) if len(parts) > 1 else parts[0]
        meta = metadata.get(rel_path) or metadata.get(path) or {}
        stem = Path(path).stem
        photo_id = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-") or stem
        category = meta.get("category") or (parts[-2] if len(parts) >= 2 else "general")
        url = ""
        try:
            safe_path = rel_path.replace("'", "''")
            url_row = session.sql(
                f"SELECT GET_PRESIGNED_URL(@{PHOTO_STAGE}, '{safe_path}', 3600) AS U"
            ).collect()
            url = (url_row[0]["U"] if url_row else "") or ""
        except Exception as e:
            presign_errors += 1
            if presign_errors <= 2:
                _record_photo_error(
                    f"GET_PRESIGNED_URL failed for '{rel_path}': {e}"
                )
        if not url:
            continue
        out.append(Photo(
            id=photo_id,
            label=meta.get("label") or stem.replace("_", " ").replace("-", " ").title(),
            category=category,
            url=str(url),
            description=meta.get("description", ""),
            tags=meta.get("tags", ()),
        ))
    if not out and rows:
        _record_photo_error(
            f"LIST returned {len(rows)} files but every GET_PRESIGNED_URL "
            "failed — check role grants on the stage (USAGE + READ) and "
            "that DIRECTORY = TRUE is enabled."
        )
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def list_photos() -> list[Photo]:
    """All photos available to the picker — library + uploaded."""
    session = _try_snowpark_session()
    library: list[Photo]
    if session is not None:
        try:
            library = _list_stage(session)
            if not library:
                # Stage exists but is empty — fall back to local mock so
                # the demo isn't blank during initial setup.
                library = _list_local()
        except Exception:
            library = _list_local()
    else:
        library = _list_local()
    return list(_UPLOAD_CACHE.values()) + library


def get_photo(photo_id: str) -> Optional[Photo]:
    """Look up a single Photo by its id. Returns None if not found."""
    if not photo_id:
        return None
    if photo_id in _UPLOAD_CACHE:
        return _UPLOAD_CACHE[photo_id]
    for p in list_photos():
        if p.id == photo_id:
            return p
    return None


def add_uploaded_photo(filename: str, raw: bytes, mime: str) -> Photo:
    """Register a user-uploaded image and return its Photo.

    Local mode: stash in the per-process cache as a base64 data: URI so
    the renderer can show it immediately. Snowflake mode: PUT the bytes
    into the photo stage and re-list to pick up the pre-signed URL.
    """
    if not raw:
        raise ValueError("uploaded photo is empty")
    digest = hashlib.sha1(raw).hexdigest()[:10]
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", filename or "uploaded")
    photo_id = f"uploaded:{digest}"
    label = Path(filename or "Uploaded photo").stem.replace("_", " ")
    enc = base64.b64encode(raw).decode("ascii")
    url = f"data:{mime or 'image/png'};base64,{enc}"
    photo = Photo(id=photo_id, label=label or "Uploaded photo",
                  category="uploaded", url=url)
    _UPLOAD_CACHE[photo_id] = photo
    return photo


# Topic → category keywords (for auto-pick by topic name).
_TOPIC_KEYWORDS: list[tuple[str, list[str]]] = [
    ("airway",      ["airway", "intubation", "ventilation", "extubation"]),
    ("cardiac",     ["cardiac", "myocardial", "acs", "stemi", "chest pain", "heart"]),
    ("surgical",    ["surg", "procedural", "operating", "implant"]),
    ("medication",  ["medication", "drug", "pharmac", "dosing"]),
    ("documentation", ["documentation", "record", "chart", "note"]),
    ("communication", ["communication", "handoff", "consult", "transfer"]),
    ("monitoring", ["monitor", "monitoring"]),
    ("imaging",    ["imag", "radiolog", "ct", "mri", "x-ray", "ultrasound"]),
    ("triage",     ["triage", "assessment", "exam", "history", "physical"]),
    ("ed",         ["emergency", "ed", "trauma"]),
    ("ob",         ["obstetric", "delivery", "labor", "gynec", "pregnan"]),
    ("pediatric",  ["pediatric", "children", "infant", "neonat"]),
]


_STOP_WORDS = {
    "a", "an", "the", "of", "for", "to", "and", "or", "in", "on",
    "with", "by", "at", "from", "is", "as", "be",
}


def _tokenize(s: str) -> set[str]:
    """Lower-case, word-split, drop short stop-words. Used by search."""
    if not s:
        return set()
    toks = re.findall(r"[a-z0-9]{3,}", s.lower())
    return {t for t in toks if t not in _STOP_WORDS}


def _photo_score(photo: Photo, query_tokens: set[str]) -> int:
    """Score a photo against query tokens. Higher = better match.

    Tag hits weighted 4x (curated), title hits 3x (specific),
    category 2x (broad), description 1x (loose). Tags are tokenized
    word-by-word so multi-word tags like 'chest pain' match queries
    for 'chest' OR 'pain' OR 'chest pain'.
    """
    if not query_tokens:
        return 0
    title_toks = _tokenize(photo.label)
    cat_toks = _tokenize(photo.category)
    desc_toks = _tokenize(photo.description)
    # Tokenize tags: each tag string contributes its words AND itself as
    # a whole (so single-word and multi-word tags both match correctly).
    tag_toks: set[str] = set()
    for raw in (photo.tags or ()):
        s = str(raw).lower().strip()
        if not s:
            continue
        tag_toks.add(s)
        tag_toks |= _tokenize(s)
    score = 0
    score += 4 * len(query_tokens & tag_toks)
    score += 3 * len(query_tokens & title_toks)
    score += 2 * len(query_tokens & cat_toks)
    score += 1 * len(query_tokens & desc_toks)
    return score


def search_photos(query: str, limit: int = 24) -> list[Photo]:
    """Return library photos matching `query`, ranked by score.

    Tokenizes the query, scores each photo against tags / title /
    category / description, and returns the top `limit`. Photos with
    score = 0 are dropped. With an empty query, returns the full
    library in original order (capped at `limit`).
    """
    photos = [p for p in list_photos() if not p.id.startswith("uploaded:")]
    if not query.strip():
        return photos[:limit]
    qtoks = _tokenize(query)
    if not qtoks:
        return photos[:limit]
    scored = [(p, _photo_score(p, qtoks)) for p in photos]
    scored = [(p, s) for p, s in scored if s > 0]
    scored.sort(key=lambda x: -x[1])
    return [p for p, _ in scored[:limit]]


def auto_pick_for_topic(topic: str, used_ids: Iterable[str] = (),
                         driver_context: str = "",
                         specialty_context: str = "") -> Optional[Photo]:
    """Pick a library photo for `topic`.

    Current behavior: round-robin pick the first photo not already used in
    this course. The `topic` / `driver_context` / `specialty_context` args
    are accepted for API compatibility but ignored — content-aware matching
    will be reintroduced once the COURSE_PHOTOS stage has tag metadata in
    COURSE_PHOTOS_METADATA. For now we just need *a* photo per slot.
    """
    used = set(used_ids)
    photos = [p for p in list_photos() if not p.id.startswith("uploaded:")]
    if not photos:
        return None
    for p in photos:
        if p.id not in used:
            return p
    # Every photo already used in this course — allow reuse.
    return photos[0]


# ---------------------------------------------------------------------------
# Snowflake setup helpers (for the data engineer)
# ---------------------------------------------------------------------------
SNOWFLAKE_SETUP_SQL = f"""
-- Run once in your Snowflake account to create the photo stage:
CREATE STAGE IF NOT EXISTS {PHOTO_STAGE}
  DIRECTORY = (ENABLE = TRUE)
  ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE');

-- Then upload your photos via SnowSQL or the Snowsight UI:
--   PUT file:///path/to/airway/*.jpg @{PHOTO_STAGE}/airway/ AUTO_COMPRESS=FALSE;
--   PUT file:///path/to/cardiac/*.jpg @{PHOTO_STAGE}/cardiac/ AUTO_COMPRESS=FALSE;
-- Photos are grouped by the directory path under the stage; that
-- directory name is read as the photo's category.
""".strip()
