"""
Thin bridge between the demo UI and the EXISTING Analyst + Auditor workflow.

It does not duplicate any research logic: it imports the already-compiled
`app.main.agent` and streams it with LangGraph's `stream_mode="updates"`, yielding
one (node, update) pair as each real graph node completes. The UI reads results
only from these real updates.

This module never writes benchmark result files.
"""
from __future__ import annotations

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


def missing_api_keys() -> list:
    """Names of required API keys not present in the environment (after .env load)."""
    from dotenv import load_dotenv
    load_dotenv(os.path.join(REPO_ROOT, ".env"))
    return [k for k in ("GOOGLE_API_KEY", "TAVILY_API_KEY") if not os.environ.get(k)]


def stream_research(question: str):
    """Run the existing agent on `question`, yielding (node_name, update_dict) as each
    graph node finishes. Sequential, real, unmodified workflow."""
    from langchain_core.messages import HumanMessage
    from app.main import agent

    for chunk in agent.stream({"messages": [HumanMessage(content=question)]}, stream_mode="updates"):
        for node, update in chunk.items():
            yield node, update


def get_memory_view() -> dict:
    """Snapshot of persistent research memory as the Analyst will see it:
    the concise lesson text only, capped at MEMORY_MAX_LESSONS."""
    from app.main import load_memory, MEMORY_MAX_LESSONS

    lessons = [m.get("lesson", "") for m in load_memory() if m.get("lesson")]
    return {
        "lessons": lessons,
        "count": len(lessons),
        "cap": MEMORY_MAX_LESSONS,
        "injected": min(len(lessons), MEMORY_MAX_LESSONS),
    }


def get_evidence_label(verification_status: str) -> str:
    """The system's own deterministic evidence label for a verification_status."""
    from app.main import get_evidence_label as _label
    return _label(verification_status)
