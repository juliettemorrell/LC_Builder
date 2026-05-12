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
# Falls back to a plain st.container — NOT st.expander, because callers
# routinely place st.expander blocks inside popovers (Tools menu) and
# Streamlit forbids expander nesting. The container renders the label as
# a markdown subheading; nested expanders inside it work normally.
# ---------------------------------------------------------------------------
def _popover_fallback(label, **kwargs):
    import re as _re
    clean = _re.sub(r":material/[a-z0-9_]+:", "", label).strip(" :")
    if clean:
        st.markdown(f"##### {clean}")
    return st.container()


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
# st.container(height=..., border=...) — `height` was added in Streamlit
# 1.31 (Jan 2024), `border` in 1.31 too. SiS warehouse-runtime Streamlit
# is older than that, so passing either kwarg raises:
#   TypeError: container() got an unexpected keyword argument 'height'
#
# Wrap st.container with a version that detects support via inspection
# and silently strips any kwargs the underlying st.container can't
# accept. Layout degrades gracefully — the container just doesn't get a
# scroll-area-with-fixed-height, content flows inline instead.
# ---------------------------------------------------------------------------
import inspect as _inspect


def _wrap_strip_unknown_kwargs(name: str) -> None:
    """Replace `st.<name>` with a wrapper that drops kwargs the native
    function doesn't accept. Lets call sites pass newer kwargs (height=,
    gap=, use_container_width=, column_config=, etc.) without raising
    TypeError on older Streamlit. The wrapper preserves the original
    function's behaviour when all kwargs are supported.

    If the signature uses **kwargs (so anything is accepted) or
    introspection fails, the function is left untouched.
    """
    native = getattr(st, name, None)
    if native is None:
        return
    try:
        sig = _inspect.signature(native)
    except (TypeError, ValueError):
        return
    has_var_keyword = any(
        p.kind == _inspect.Parameter.VAR_KEYWORD
        for p in sig.parameters.values()
    )
    if has_var_keyword:
        # Already accepts arbitrary kwargs — wrapping isn't needed.
        return
    known = set(sig.parameters.keys())

    def _wrapped(*args, **kwargs):
        safe = {k: v for k, v in kwargs.items() if k in known}
        return native(*args, **safe)

    _wrapped.__name__ = getattr(native, "__name__", name)
    _wrapped.__doc__ = getattr(native, "__doc__", None)
    setattr(st, name, _wrapped)


# Widgets that we know we pass newer kwargs to. The wrapper is a no-op
# on modern Streamlit (kwargs already accepted) and silently strips
# unknown kwargs on older runtimes — so a single call like
# `st.columns([1, 2], gap="small")` works whether `gap` exists or not.
for _widget in (
    "container",     # height, border  (1.31+)
    "columns",       # gap, vertical_alignment, border  (gap 1.13+, others 1.36+)
    "button",        # use_container_width  (1.13), icon (1.36)
    "dataframe",     # column_config  (1.23), use_container_width  (1.13)
    "data_editor",   # column_config  (1.23)
    "selectbox",     # placeholder, label_visibility
    "text_area",     # height was there a while, but be defensive
    "text_input",    # label_visibility
    "file_uploader", # label_visibility
    "checkbox",      # label_visibility
    "radio",         # horizontal, captions, label_visibility
    "download_button", # icon, type
    "spinner",       # show_time
    "progress",      # text was added in 1.27
    "markdown",      # help (1.37+)
    "caption",       # help
    "code",          # line_numbers, wrap_lines
):
    _wrap_strip_unknown_kwargs(_widget)


# ---------------------------------------------------------------------------
# st.image — special wrap because the stretch-to-width kwarg got RENAMED
# (use_column_width → use_container_width) in Streamlit 1.36. Naïvely
# stripping the new name on old runtimes would leave the image at native
# width. Instead, when only use_column_width is supported, translate.
# ---------------------------------------------------------------------------
_native_image = st.image
try:
    _image_params = set(_inspect.signature(_native_image).parameters.keys())
except (TypeError, ValueError):
    _image_params = set()


def _safe_image(*args, **kwargs):
    # Translate use_container_width ↔ use_column_width when the call site's
    # kwarg name isn't supported by this Streamlit version. If signature
    # introspection failed (no _image_params), we don't know what's
    # supported — strip BOTH width kwargs to be safe (image just renders
    # at its natural size, no crash).
    if not _image_params:
        kwargs.pop("use_container_width", None)
        kwargs.pop("use_column_width", None)
        try:
            return _native_image(*args, **kwargs)
        except TypeError:
            # Some unknown kwarg in the residue — strip everything and retry
            return _native_image(*args)
    if "use_container_width" in kwargs and "use_container_width" not in _image_params:
        if "use_column_width" in _image_params:
            kwargs["use_column_width"] = kwargs.pop("use_container_width")
        else:
            kwargs.pop("use_container_width", None)
    if "use_column_width" in kwargs and "use_column_width" not in _image_params:
        if "use_container_width" in _image_params:
            kwargs["use_container_width"] = kwargs.pop("use_column_width")
        else:
            kwargs.pop("use_column_width", None)
    safe = {k: v for k, v in kwargs.items() if k in _image_params}
    return _native_image(*args, **safe)


# Install UNCONDITIONALLY — even when signature introspection failed,
# the fallback path above guarantees we won't crash on unknown kwargs.
st.image = _safe_image


# ---------------------------------------------------------------------------
# st.status  (added in Streamlit 1.27, September 2023)
# Falls back to a container so nested content (logs, progress, etc.) can
# include its own expanders without hitting the nesting error.
# ---------------------------------------------------------------------------
def _status_fallback(label, *, expanded: bool = False, state: str = "running",
                     **kwargs):
    if label:
        st.markdown(f"**{label}**")
    return st.container()


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
