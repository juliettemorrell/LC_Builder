"""Catalog of one-click chat actions surfaced as chips above the chat input.

Each action turns into a short instruction passed to the section-edit prompt.
"""
from __future__ import annotations

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
