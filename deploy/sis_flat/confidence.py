"""Confidence scoring component shared by both apps."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

from cortex import complete
from prompts import build_confidence


@dataclass
class ConfidenceResult:
    grade: str  # 'A' .. 'F'
    publication_decision: str  # APPROVED | REQUIRES_REVISION | BLOCKED
    summary: str
    raw: dict
    mocked: bool


def confidence_score(generated_text: str, sources: list[str],
                     output_type: str = "course_generator") -> ConfidenceResult:
    """Grade `generated_text` against `sources`. Returns a ConfidenceResult."""
    prompt = build_confidence(generated_text, sources, output_type)
    res = complete(prompt, kind="confidence")
    parsed = _parse_json(res.text)
    return ConfidenceResult(
        grade=parsed.get("overall_grade", "C"),
        publication_decision=parsed.get("publication_decision", "REQUIRES_REVISION"),
        summary=parsed.get("summary", ""),
        raw=parsed,
        mocked=res.mocked,
    )


def _parse_json(text: str) -> dict:
    """Pull a JSON object out of a Cortex response, even if wrapped in fences/prose."""
    if not text:
        return {}
    # Try plain parse first
    try:
        return json.loads(text)
    except Exception:
        pass
    # Strip fenced code blocks
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        try:
            return json.loads(fence.group(1))
        except Exception:
            pass
    # Greedy curly-brace match as last resort
    obj = re.search(r"\{[\s\S]*\}", text)
    if obj:
        try:
            return json.loads(obj.group(0))
        except Exception:
            pass
    return {}
