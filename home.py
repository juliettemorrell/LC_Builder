"""Optional landing page.

Streamlit doesn't run two apps from one entry, but you can run this page to
remind yourself which entry to launch:

    streamlit run app_course_generator.py
    streamlit run app_claims_lesson.py
"""
import streamlit as st
from shared.carbon import inject_carbon_css, topbar

st.set_page_config(page_title="MyAdvice Builder — Buildathon", layout="wide",
                   initial_sidebar_state="collapsed")
inject_carbon_css()
topbar("Buildathon")

st.markdown("""
<div class='hero-wrap'>
  <div class='hero-eyebrow'>Buildathon</div>
  <div class='hero-title'>Two apps. One backbone.</div>
  <div class='hero-sub'>Both apps share the Cortex wrapper, prompt library, confidence scoring, and Carbon-styled UI under <code>shared/</code>.</div>
</div>
""", unsafe_allow_html=True)

c1, c2 = st.columns(2, gap="large")
with c1:
    with st.container(border=True):
        st.markdown("### :material/menu_book: Course Generator")
        st.markdown(
            "Pick a risk driver. Get a full course: body, assessment, embedded "
            "claims lesson. Each section is graded for confidence and can be "
            "regenerated, AI-edited, or hand-edited."
        )
        st.code("streamlit run app_course_generator.py", language="bash")
with c2:
    with st.container(border=True):
        st.markdown("### :material/description: Claims Lesson Generator")
        st.markdown(
            "Surfaces ranked candidate claims. Pick one and we generate the "
            "full lesson grounded in both the claim and the matching Risk "
            "Playbook section."
        )
        st.code("streamlit run app_claims_lesson.py", language="bash")
