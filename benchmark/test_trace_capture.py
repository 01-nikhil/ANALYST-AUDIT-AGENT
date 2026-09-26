"""
Offline test for full per-question trace capture (serialize_message / build_trace)
in run_benchmark.py. No API calls: uses a synthetic final_state with real
LangChain message objects and dict-shaped audit_results / claims / feedback.

Verifies the trace preserves: the Analyst plan text, every tool call (name+args),
every tool result, the Auditor verdicts + reasoning + citation_status + fallback
provenance, the feedback lessons, and the full (untruncated) final answer.

Run with:
    python benchmark/test_trace_capture.py
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from run_benchmark import serialize_message, build_trace

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
        print("FAIL: " + msg)
    else:
        print("OK:   " + msg)


# --- Synthetic message history: user -> analyst plan+tool call -> tool result -> final ---
plan_ai = AIMessage(
    content="Plan: I will first search for the CEO appointment date, then fetch the source.",
    tool_calls=[{"name": "web_search", "args": {"query": "Satya Nadella CEO date"}, "id": "c1"}],
)
tool_msg = ToolMessage(content="Title: X\nURL: https://example.com\nContent: Feb 2014", tool_call_id="c1", name="web_search")
final_ai = AIMessage(content="## Final Verified Answer\n\n**Claim 1:** ...\n- Source URL: https://example.com\n" + ("x" * 800))

final_state = {
    "messages": [HumanMessage(content="When did Nadella become CEO?"), plan_ai, tool_msg, final_ai],
    "claims": [{"original_claim": "When did Nadella become CEO?", "source_url": "https://example.com",
                "evidence_type": "search_snippet", "verification_status": "audited_supported"}],
    "audit_results": [{
        "original_claim": "When did Nadella become CEO?", "source_url": "https://example.com",
        "verdict": "supported", "verification_status": "audited_supported",
        "reasoning": "Fetched page confirms Feb 2014.", "supporting_evidence": "\"Feb 2014\"",
        "citation_status": "present", "original_fetch_status": "success",
        "fallback_search_performed": False, "fallback_search_query": None,
        "fallback_source_url": None, "fallback_fetch_status": None, "final_evidence_source": "original_cited_source",
    }],
    "feedback_lessons": [{"lesson": "Fetch the source page.", "reason": "r", "source_of_feedback": "auditor"}],
}

trace = build_trace(final_state, str(final_ai.content))

# JSON-serializable?
try:
    json.dumps(trace)
    check(True, "trace is JSON-serializable")
except Exception as e:
    check(False, "trace is JSON-serializable (%s)" % e)

# Plan text present
msgs = trace["analyst_messages"]
check(any("Plan:" in m.get("content", "") for m in msgs), "Analyst PLAN text preserved in trace")

# Tool call (name + args) present
tool_call_msgs = [m for m in msgs if m.get("tool_calls")]
check(tool_call_msgs and tool_call_msgs[0]["tool_calls"][0]["name"] == "web_search",
      "tool CALL preserved with name")
check(tool_call_msgs[0]["tool_calls"][0]["args"].get("query") == "Satya Nadella CEO date",
      "tool CALL preserved with args (search query)")

# Tool result present (role=tool, name set, content preserved)
tool_results = [m for m in msgs if m.get("role") == "tool"]
check(tool_results and "Feb 2014" in tool_results[0]["content"], "tool RESULT content preserved")
check(tool_results[0].get("name") == "web_search", "tool RESULT linked to originating tool name")

# Auditor verdict + reasoning + citation_status preserved
ar = trace["audit_results"][0]
check(ar["verdict"] == "supported", "Auditor VERDICT preserved")
check("confirms Feb 2014" in ar["reasoning"], "Auditor REASONING preserved")
check(ar["citation_status"] == "present", "Auditor citation_status preserved")
check("fallback_search_query" in ar and "fallback_fetch_status" in ar,
      "fallback provenance fields present in trace (fallback-after-failure capturable)")

# Feedback lessons preserved
check(trace["feedback_lessons"] and trace["feedback_lessons"][0]["lesson"] == "Fetch the source page.",
      "FEEDBACK lessons preserved")

# Full (untruncated) final answer preserved
check(len(trace["final_answer_full"]) > 500, "FULL final answer preserved (not truncated to 500 chars)")

# serialize_message never emits str(msg) wrapper artifacts
check("additional_kwargs" not in json.dumps(msgs), "messages serialized structurally (no str(msg) dump)")

print("\n" + "=" * 60)
if failures:
    print("%d FAILURE(S):" % len(failures))
    for f in failures:
        print("  - " + f)
    sys.exit(1)
else:
    print("ALL CHECKS PASSED")
