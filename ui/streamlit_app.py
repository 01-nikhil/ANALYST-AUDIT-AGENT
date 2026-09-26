"""
Analyst x Auditor - demo UI.

A presentation layer over the EXISTING Analyst + Auditor LangGraph workflow. It
calls the compiled agent through ui.runner (stream_mode="updates") and renders each
real node result as it completes. It duplicates no research logic and writes no
benchmark result files.

Run from the repo root:
    streamlit run ui/streamlit_app.py
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import streamlit as st

from ui import events as ev
from ui import render as rd
from ui import runner

st.set_page_config(page_title="Analyst × Auditor", page_icon="◈", layout="wide")
st.markdown(rd.CSS, unsafe_allow_html=True)

ss = st.session_state
ss.setdefault("history", [])
ss.setdefault("last_run", None)
ss.setdefault("question", rd.DEMO_QUESTION)


def _use_demo():
    ss["question"] = rd.DEMO_QUESTION


def safe_memory_view() -> dict:
    """Live memory snapshot; degrades to an empty view instead of crashing the page."""
    try:
        return runner.get_memory_view()
    except Exception:
        return {"lessons": [], "count": 0, "cap": 0, "injected": 0}


def safe_label(status: str) -> str:
    try:
        return runner.get_evidence_label(status)
    except Exception:
        return ""


missing_keys = runner.missing_api_keys()

# --- Header ---------------------------------------------------------------
header_ph = st.empty()
header_ph.markdown(rd.header_html("ready"), unsafe_allow_html=True)
if missing_keys:
    st.warning("Setup needed: missing " + ", ".join(missing_keys) + " in .env - research cannot run yet.")

# --- Research input -------------------------------------------------------
st.markdown('<div class="aa-section">Research question</div>', unsafe_allow_html=True)
question = st.text_area("Research question", key="question", height=96,
                        placeholder=rd.PLACEHOLDER, label_visibility="collapsed")
b1, b2, _ = st.columns([1.1, 1.4, 6])
run_clicked = b1.button("Run Research", type="primary")
b2.button("Use demo question", on_click=_use_demo)

# --- Workflow visualisation ----------------------------------------------
st.markdown('<div class="aa-section">Workflow</div>', unsafe_allow_html=True)
flow_ph = st.empty()

# --- Trace | Answer + memory ---------------------------------------------
left, right = st.columns([3, 2], gap="large")
with left:
    st.markdown('<div class="aa-section">Live trace</div>', unsafe_allow_html=True)
    trace_ph = st.empty()
with right:
    st.markdown('<div class="aa-section">Final answer</div>', unsafe_allow_html=True)
    final_ph = st.empty()
    composed_ph = st.empty()
    st.markdown('<div class="aa-section">Persistent memory</div>', unsafe_allow_html=True)
    memory_ph = st.empty()

st.markdown('<div class="aa-section">Metrics</div>', unsafe_allow_html=True)
metrics_ph = st.empty()


def paint(run, live=False):
    """Render every panel from the current run state (real data only)."""
    statuses = run.stage_statuses() if run else {k: "idle" for k in ev.STAGES}
    flow_ph.markdown(rd.flow_html(statuses), unsafe_allow_html=True)
    trace_ph.markdown(
        rd.trace_html(run.events if run else [], run.running if (run and live) else None,
                      run.error if run else None),
        unsafe_allow_html=True)
    final_ph.markdown(rd.final_html(run, safe_label), unsafe_allow_html=True)
    with composed_ph.container():
        if run and run.final_answer:
            with st.expander("Full composed answer"):
                st.markdown(run.final_answer)
    view = safe_memory_view()
    new = [l.get("lesson", "") for l in run.feedback_lessons] if run else []
    memory_ph.markdown(rd.memory_html(view, new), unsafe_allow_html=True)
    metrics_ph.markdown(rd.metrics_html(ss["history"], view["count"]), unsafe_allow_html=True)


if run_clicked:
    q = (question or "").strip()
    if not q:
        st.warning("Enter a research question first.")
        paint(ss["last_run"])
    elif missing_keys:
        st.error("Cannot run: missing " + ", ".join(missing_keys) + ".")
        paint(ss["last_run"])
    else:
        run = ev.RunState(q)
        ss["last_run"] = run
        header_ph.markdown(rd.header_html("running"), unsafe_allow_html=True)
        paint(run, live=True)
        try:
            for node, update in runner.stream_research(q):
                run.apply(node, update)
                paint(run, live=True)
        except Exception as exc:  # surfaced as a failed run, never as a success
            run.fail(f"{type(exc).__name__}: {exc}")
        ss["history"].append(rd.run_summary(run))
        header_ph.markdown(rd.header_html("error" if run.error else "ready"), unsafe_allow_html=True)
        paint(run)
else:
    last = ss["last_run"]
    if last is not None and not last.finished and not last.error:
        last.fail("The run was interrupted before it finished.")
    paint(last)
