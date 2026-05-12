"""Lightweight chat-message router.

Exposes:
- `apply_chat_edit()`: free-text user instruction → revised section text
- `apply_quick_action()`: one of the canned QUICK_ACTIONS → revised section text

Both return a small dict with the new text and metadata the UI can show
(latency, mocked, the instruction that was applied), and they audit
every call to `shared.chat_log` so the team can review what users edit.
"""
from __future__ import annotations

from typing import Optional

from cortex import complete, model_for, temp_for
from prompts import build_edit_section, PROMPT_VERSION
from quick_actions import by_id
from chat_log import log_edit


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
