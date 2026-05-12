"""Streamlit-in-Snowflake entry point.

Snowflake's Streamlit runtime expects the main file to be named
`streamlit_app.py` by default. This file is a one-liner shim that
loads the unified app — which has the splash screen + both builders
behind it.

For Streamlit Community Cloud or local `streamlit run`, you can use
either entry — `streamlit run app.py` or `streamlit run streamlit_app.py`
both work.
"""
from __future__ import annotations

# Just execute app.py in the current Streamlit context. Using runpy keeps
# the splash + router behaviour identical between SiS and `streamlit run`.
import runpy
runpy.run_path("app.py", run_name="__main__")
