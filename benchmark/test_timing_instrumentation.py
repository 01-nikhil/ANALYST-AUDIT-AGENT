"""
Fixture test for TimingCallbackHandler + summarize_timing() in run_benchmark.py.

No real API calls - uses FakeMessagesListChatModel (built into langchain_core,
zero-cost, deterministic) and a synthetic LangGraph shaped like app/main.py's
real graph (chatbot -> tools -> extract_claims -> auditor -> feedback), so the
node-attribution logic is exercised against the same call shapes the real
system uses:
  - chatbot: a plain chat model .invoke() call, AND (via a scripted tool_call)
    a real ToolNode invocation of a "web_search"-named tool - this validates
    the ToolNode invocation path, which is different from a direct .invoke()
    and was not covered by the direct-tool-call check below.
  - extract_claims / feedback: plain chat model .invoke() calls (a simpler
    subcase of the with_structured_output wrapping already validated
    separately against the real Gemini API - with_structured_output adds an
    intermediate RunnableSequence layer, and the node-attribution walk-up
    logic was confirmed to resolve through that layer using a real model
    before this benchmark change was written).
  - auditor: two DIRECT tool.invoke() calls (not through ToolNode, exactly
    like auditor() calling fetch_page.invoke({"url": ...}) in app/main.py) -
    one named "fetch_page" that succeeds, one named "will_raise" that raises,
    to verify both the success and on_tool_error paths are attributed and
    flagged correctly.

Run with:
    python benchmark/test_timing_instrumentation.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from typing import Annotated
from typing_extensions import TypedDict
from langchain_core.messages import AIMessage, HumanMessage, ToolCall
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.tools import tool
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from run_benchmark import TimingCallbackHandler, summarize_timing

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)
        print(f"FAIL: {message}")
    else:
        print(f"OK:   {message}")


# --- Build a synthetic graph shaped like app/main.py's real graph ---

@tool
def web_search(query: str) -> str:
    """Dummy web_search tool - routed through a real ToolNode, like the Analyst's searches."""
    return f"search results for {query}"


@tool
def fetch_page(url: str) -> str:
    """Dummy fetch_page tool - invoked DIRECTLY (not via ToolNode), like auditor()'s independent fetch."""
    return f"[FETCH STATUS: SUCCESS]\nURL: {url}\nCONTENT: ok"


@tool
def will_raise(x: str) -> str:
    """Dummy tool that raises, to test the on_tool_error path."""
    raise RuntimeError("simulated fetch failure")


# chatbot's model: first call returns a tool_call (routes to ToolNode), second
# call (after the loop back) returns a plain final message.
chatbot_llm = FakeMessagesListChatModel(responses=[
    AIMessage(content="", tool_calls=[ToolCall(name="web_search", args={"query": "q"}, id="call_1")]),
    AIMessage(content="final chatbot answer"),
])

extract_claims_llm = FakeMessagesListChatModel(responses=[AIMessage(content="extracted claims")])
feedback_llm = FakeMessagesListChatModel(responses=[AIMessage(content="feedback lessons")])


class State(TypedDict):
    messages: Annotated[list, add_messages]


def chatbot(state: State):
    result = chatbot_llm.invoke(state["messages"])
    return {"messages": [result]}


def extract_claims(state: State):
    result = extract_claims_llm.invoke([HumanMessage(content="extract")])
    return {"messages": [result]}


def auditor(state: State):
    ok = fetch_page.invoke({"url": "https://example.com/ok"})
    try:
        will_raise.invoke({"x": "test"})
    except Exception:
        pass
    return {"messages": [HumanMessage(content=str(ok))]}


def feedback(state: State):
    result = feedback_llm.invoke([HumanMessage(content="feedback")])
    return {"messages": [result]}


def route_after_chatbot(state: State):
    route = tools_condition(state)
    if route == END:
        return "extract_claims"
    return route


graph_builder = StateGraph(State)
graph_builder.add_node("chatbot", chatbot)
graph_builder.add_node("tools", ToolNode(tools=[web_search]))
graph_builder.add_node("extract_claims", extract_claims)
graph_builder.add_node("auditor", auditor)
graph_builder.add_node("feedback", feedback)

graph_builder.add_edge(START, "chatbot")
graph_builder.add_conditional_edges("chatbot", route_after_chatbot, {"tools": "tools", "extract_claims": "extract_claims"})
graph_builder.add_edge("tools", "chatbot")
graph_builder.add_edge("extract_claims", "auditor")
graph_builder.add_edge("auditor", "feedback")
graph_builder.add_edge("feedback", END)

graph = graph_builder.compile()

handler = TimingCallbackHandler(question_id=99, pass_id=1)
graph.invoke({"messages": [HumanMessage(content="start")]}, config={"callbacks": [handler]})

records = handler.records
print(f"\nCaptured {len(records)} timing records:")
for r in records:
    print(f"  {r['operation_type']:<18} node={r['node']:<15} detail={r['detail']:<15} "
          f"elapsed={r['elapsed_seconds']}s success={r['success']} error={r['error']}")

# --- Attribution checks ---
def find(operation_type, node=None):
    return [r for r in records if r["operation_type"] == operation_type and (node is None or r["node"] == node)]

check(len(find("llm_chatbot")) == 2, "two llm_chatbot records (initial tool-call turn + final answer turn)")
check(len(find("llm_extract_claims")) == 1, "one llm_extract_claims record")
check(len(find("llm_feedback")) == 1, "one llm_feedback record")
check(len(find("search", node="tools")) == 1, "web_search tool call attributed to node='tools' (via real ToolNode)")
check(len(find("fetch", node="auditor")) == 1, "fetch_page direct .invoke() attributed to node='auditor'")
check(len(find("tool_will_raise", node="auditor")) == 1, "will_raise direct .invoke() attributed to node='auditor'")

# --- question_id / pass_id stamped on every record ---
check(all(r["question_id"] == 99 for r in records), "every record carries question_id=99")
check(all(r["pass_id"] == 1 for r in records), "every record carries pass_id=1")

# --- success/failure flagging ---
fetch_record = find("fetch", node="auditor")[0]
check(fetch_record["success"] is True, "successful fetch_page call flagged success=True")
check(fetch_record["error"] is None, "successful fetch_page call has error=None")

raise_record = find("tool_will_raise", node="auditor")[0]
check(raise_record["success"] is False, "will_raise call flagged success=False")
check(raise_record["error"] is not None and "simulated fetch failure" in raise_record["error"],
      "will_raise call captures the error message")

# --- node_total records present for every real graph node ---
node_totals = {r["node"]: r for r in find("node_total")}
for expected_node in ["chatbot", "tools", "extract_claims", "auditor", "feedback"]:
    check(expected_node in node_totals, f"node_total record present for '{expected_node}'")

# --- elapsed_seconds is non-negative and start_offset_seconds is monotonic-ish ---
check(all(r["elapsed_seconds"] >= 0 for r in records), "all elapsed_seconds are non-negative")
check(all(r["start_offset_seconds"] >= 0 for r in records), "all start_offset_seconds are non-negative")

# --- summarize_timing aggregation ---
summary = summarize_timing(records)
print(f"\nsummarize_timing() output: {summary}")

check(summary["num_llm_calls"] == 4, "summary counts 4 total LLM calls (2 chatbot + 1 extract_claims + 1 feedback)")
check(summary["num_search_calls"] == 1, "summary counts 1 search call")
check(summary["num_fetch_calls"] == 1, "summary counts 1 fetch call")
check(summary["num_failed_calls"] == 1, "summary counts exactly 1 failed call (will_raise)")

expected_llm_total = round(
    summary["llm_chatbot_seconds"] + summary["llm_extract_claims_seconds"]
    + summary["llm_auditor_seconds"] + summary["llm_feedback_seconds"] + summary["llm_unknown_seconds"], 3
)
check(summary["llm_total_seconds"] == expected_llm_total, "llm_total_seconds matches sum of llm_* buckets")

expected_tool_total = round(summary["search_seconds"] + summary["fetch_seconds"] + summary["other_tool_seconds"], 3)
check(summary["tool_total_seconds"] == expected_tool_total, "tool_total_seconds matches sum of search/fetch/other")

check(summary["timed_total_seconds"] == round(summary["llm_total_seconds"] + summary["tool_total_seconds"], 3),
      "timed_total_seconds == llm_total_seconds + tool_total_seconds")

check(set(summary["node_total_seconds"].keys()) == {"chatbot", "tools", "extract_claims", "auditor", "feedback"},
      "node_total_seconds has an entry for every real graph node")
check(summary["node_sum_seconds"] == round(sum(summary["node_total_seconds"].values()), 3),
      "node_sum_seconds matches sum of node_total_seconds values")

# --- an unknown/untracked node should not be attributed to a known node ---
untracked = TimingCallbackHandler(question_id=1, pass_id=1)
untracked._parent_of["orphan"] = None  # no matching node anywhere in the chain
check(untracked._find_node("orphan") == "unknown", "a run with no traceable node parent resolves to 'unknown'")

print("\n" + "=" * 60)
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
else:
    print("ALL CHECKS PASSED")
