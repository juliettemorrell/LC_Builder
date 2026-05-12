"""Snowsight Main-file compatibility shim.

The real entry point is `streamlit_app.py`. Snowsight defaults the Main
File to either `streamlit_app.py` or `app.py` depending on how the app
was created — this shim makes both spellings work. If your app boots
fine via `streamlit_app.py`, this file is just sitting unused.
"""
from __future__ import annotations

import runpy as _runpy
import os as _os

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_runpy.run_path(_os.path.join(_HERE, "streamlit_app.py"), run_name="__main__")
