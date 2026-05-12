"""Streamlit version-compatibility shims.

Streamlit-in-Snowflake bundles its own Streamlit, often several versions
behind the latest pip release. This module monkey-patches `streamlit`
at import time so newer APIs degrade gracefully to older equivalents
when missing, instead of raising `AttributeError`.

Import this module ONCE, BEFORE any other module that touches `st.X`:

    import streamlit as st
    from shared import _compat   # noqa: F401  (just for the side effect)
    # ... rest of imports / code

All shims are no-ops in modern Streamlit (≥ 1.36) since the real APIs
already exist — `_ensure_attr` only sets the attribute if it's missing.
"""
from __future__ import annotations

import contextlib
import streamlit as st


def _ensure_attr(name: str, fallback) -> None:
    """Set `st.<name> = fallback` only if `st` doesn't already have it."""
    if not hasattr(st, name):
        setattr(st, name, fallback)


# ---------------------------------------------------------------------------
# st.html  (added in Streamlit 1.33, March 2024)
# Older Streamlits don't strip <style> tags, so st.markdown is a clean fallback.
# ---------------------------------------------------------------------------
def _html_fallback(body):
    st.markdown(body, unsafe_allow_html=True)


_ensure_attr("html", _html_fallback)


# ---------------------------------------------------------------------------
# st.popover  (added in Streamlit 1.32, March 2024)
# Falls back to expander — same `with ...:` context-manager interface.
# Older runtimes don't accept popover-only kwargs (use_container_width, help
# can be safely ignored when degrading).
# ---------------------------------------------------------------------------
def _popover_fallback(label, **kwargs):
    return st.expander(label, expanded=False)


_ensure_attr("popover", _popover_fallback)


# ---------------------------------------------------------------------------
# st.rerun  (added in Streamlit 1.27, September 2023)
# Older Streamlits expose the same behaviour as st.experimental_rerun.
# Snapshot the native experimental_rerun BEFORE shimming, so the fallback
# doesn't end up calling our own shimmed version recursively.
# ---------------------------------------------------------------------------
_native_experimental_rerun = getattr(st, "experimental_rerun", None)


def _rerun_fallback():
    if callable(_native_experimental_rerun):
        _native_experimental_rerun()
    # else: nothing to do — Streamlit will pick up state changes on the
    # next interaction. Better silent than crashing.


_ensure_attr("rerun", _rerun_fallback)


# ---------------------------------------------------------------------------
# st.toggle  (added in Streamlit 1.26, July 2023)
# Visually a switch; semantically identical to a checkbox.
# ---------------------------------------------------------------------------
def _toggle_fallback(label, value: bool = False, *, key=None, help=None,
                     disabled: bool = False, **kwargs):
    return st.checkbox(label, value=value, key=key, help=help, disabled=disabled)


_ensure_attr("toggle", _toggle_fallback)


# ---------------------------------------------------------------------------
# st.status  (added in Streamlit 1.27, September 2023)
# Falls back to a styled expander.
# ---------------------------------------------------------------------------
def _status_fallback(label, *, expanded: bool = False, state: str = "running",
                     **kwargs):
    return st.expander(label, expanded=expanded)


_ensure_attr("status", _status_fallback)


# ---------------------------------------------------------------------------
# st.chat_message  (added in Streamlit 1.24, June 2023)
# Container with a role header. Avatar kwarg is accepted but ignored when
# falling back.
# ---------------------------------------------------------------------------
@contextlib.contextmanager
def _chat_message_fallback(role: str = "assistant", *, avatar=None):
    container = st.container()
    with container:
        st.markdown(f"**{role.capitalize()}:**")
        yield container


# Streamlit's real chat_message is itself a context manager, so the shim
# wraps a contextmanager. We attach it directly (no need for a class).
_ensure_attr("chat_message", _chat_message_fallback)


# ---------------------------------------------------------------------------
# st.chat_input  (added in Streamlit 1.24, June 2023)
# Falls back to text_input that returns the value or None when empty.
# ---------------------------------------------------------------------------
def _chat_input_fallback(placeholder: str = "", *, key=None, **kwargs):
    raw = st.text_input(placeholder, key=key)
    return raw or None


_ensure_attr("chat_input", _chat_input_fallback)


# ---------------------------------------------------------------------------
# st.connection  (added in Streamlit 1.28, October 2023)
# This is rarely missing — but if it is, cortex.py's _try_get_session()
# already catches AttributeError and falls through to env-var auth.
# We don't shim it here because doing so would mask the failure mode that
# cortex.py expects.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# st.column_config.*  (added in Streamlit 1.23, April 2023)
# If missing, st.dataframe(column_config=...) will ignore the kwarg, so
# we shim a no-op namespace whose attribute access returns a sentinel.
# ---------------------------------------------------------------------------
class _ColConfigStub:
    """Returns a do-nothing object for any attribute access. Streamlit
    versions without column_config will simply ignore unknown kwargs on
    st.dataframe, so the table still renders — just without per-column
    formatting."""
    def __getattr__(self, name):
        def _noop(*args, **kwargs):
            return None
        return _noop


if not hasattr(st, "column_config"):
    st.column_config = _ColConfigStub()


# ---------------------------------------------------------------------------
# st.experimental_rerun (deprecated alias)
# Only shim this if it's missing AND we have a native rerun to point at.
# If both are missing we leave it absent — no code in this repo uses the
# experimental name anyway.
# ---------------------------------------------------------------------------
if not hasattr(st, "experimental_rerun"):
    _native_rerun = getattr(st, "rerun", None)
    if callable(_native_rerun) and _native_rerun is not _rerun_fallback:
        st.experimental_rerun = _native_rerun
