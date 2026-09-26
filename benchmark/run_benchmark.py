"""
PS3 8-Question Benchmark harness.

Invokes the existing, unmodified `agent` from app/main.py against live web
evidence and records cost/latency/quality metrics per question. Does not
change Analyst/Auditor/Feedback/compose_final_answer behavior in any way -
all instrumentation here is external (callback + post-hoc state inspection).

Usage:
    python -m benchmark.run_benchmark --dry-run          # structural test, no API calls
    python -m benchmark.run_benchmark --question 1        # run exactly one question live
    python -m benchmark.run_benchmark --all                # full sequential 8-question run

Questions must run in order for --all, since persistent memory written by
question N's feedback() step is meant to be available to question N+1's
chatbot() step (app/research_memory.json).
"""
import argparse
import datetime
import json
import os
import sys
import time

from langchain_core.callbacks import BaseCallbackHandler

BENCHMARK_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(BENCHMARK_DIR)
sys.path.insert(0, REPO_ROOT)

QUESTIONS_PATH = os.path.join(BENCHMARK_DIR, "questions.json")
PRICING_PATH = os.path.join(BENCHMARK_DIR, "pricing_config.json")
RESULTS_DIR = os.path.join(BENCHMARK_DIR, "results")


def load_questions():
    with open(QUESTIONS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["questions"]


def load_pricing():
    with open(PRICING_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def count_tool_usage(messages):
    """Counts searches/fetches and their success/failure from final graph state messages.
    Uses the same [FETCH STATUS: ...] convention app/main.py already relies on."""
    num_searches = 0
    num_page_fetches = 0
    successful_fetches = 0
    failed_fetches = 0

    for msg in messages:
        if getattr(msg, "type", None) != "tool":
            continue
        tool_name = getattr(msg, "name", "") or ""
        content = str(msg.content)

        if tool_name == "web_search":
            num_searches += 1
        elif tool_name == "fetch_page":
            num_page_fetches += 1
            if "[FETCH STATUS: SUCCESS]" in content:
                successful_fetches += 1
            elif "[FETCH STATUS: FAILED]" in content:
                failed_fetches += 1

    return {
        "num_searches": num_searches,
        "num_page_fetches": num_page_fetches,
        "successful_fetches": successful_fetches,
        "failed_fetches": failed_fetches,
    }


def count_claim_verdicts(claims):
    supported = 0
    contradicted = 0
    unsupported = 0
    for claim in claims:
        status = claim.get("verification_status", "unknown")
        if status == "audited_supported":
            supported += 1
        elif status == "audited_contradicted":
            contradicted += 1
        else:
            # failed, unverified, fetched-but-not-audited, or unknown all
            # count as unsupported for benchmark purposes - none of these
            # are a positive verification outcome.
            unsupported += 1
    return {
        "num_claims": len(claims),
        "supported_claims": supported,
        "contradicted_claims": contradicted,
        "unsupported_claims": unsupported,
    }


def count_auditor_fetches(audit_results):
    """Derives Auditor-side fetch success/failure from audit_results, separately
    from the Analyst's own tool-call fetches (see count_tool_usage above).

    app/main.py's auditor() always attempts exactly one independent fetch_page
    call per claim (or synthesizes a FAILED marker if there's no source_url),
    so len(audit_results) == number of Auditor fetch attempts.

    On fetch failure, auditor() sets supporting_evidence to the raw fetched_data
    string, which starts with the literal "[FETCH STATUS: FAILED]" marker - so
    that marker's presence reliably identifies an Auditor fetch failure.

    On fetch success, supporting_evidence instead holds an LLM-extracted quote
    (no fetch-status marker at all) - this is true whether the resulting verdict
    is supported, contradicted, OR unsupported-due-to-insufficient-evidence, and
    also true if the post-fetch audit judgement itself errored/returned empty.
    verification_status alone is NOT a reliable signal here, since "failed" is
    also used for those succeeded-fetch-but-inconclusive-judgement cases - only
    the literal marker in supporting_evidence distinguishes a real fetch failure.

    These counts describe the ORIGINAL cited-source fetch only. As of the Auditor
    fallback upgrade, records also carry an explicit 'original_fetch_status'
    ('success'/'failed') - when present it is the authoritative signal and is
    preferred, because on a fallback-SUCCESS record the final supporting_evidence
    holds the fallback page's quote (no failure marker) even though the original
    fetch failed. For older records lacking that field, the supporting_evidence
    marker heuristic is used, preserving backward compatibility. Fallback fetches
    are counted separately by count_auditor_fallback_fetches().
    """
    auditor_fetches = len(audit_results)

    def _original_failed(a):
        if "original_fetch_status" in a:
            return a.get("original_fetch_status") == "failed"
        return str(a.get("supporting_evidence", "")).startswith("[FETCH STATUS: FAILED]")

    auditor_failed_fetches = sum(1 for a in audit_results if _original_failed(a))
    auditor_successful_fetches = auditor_fetches - auditor_failed_fetches
    return {
        "auditor_fetches": auditor_fetches,
        "auditor_successful_fetches": auditor_successful_fetches,
        "auditor_failed_fetches": auditor_failed_fetches,
    }


def count_auditor_fallback_fetches(audit_results):
    """Counts the Auditor's FALLBACK fetches, tracked separately from the original
    cited-source fetches (count_auditor_fetches above). A fallback fetch is attempted
    only when the original cited-source fetch failed AND the fallback search returned a
    usable candidate URL. Success/failure is read from the explicit provenance field
    'fallback_fetch_status' ('success'/'failed'/'no_results'/None) added by auditor().

    Records that predate the fallback upgrade have no fallback fields; they contribute 0
    to every count (via .get defaults), so this is safe on old-shape audit_results too.
    """
    attempted = 0
    successful = 0
    failed = 0
    for a in audit_results:
        status = a.get("fallback_fetch_status")
        if status == "success":
            attempted += 1
            successful += 1
        elif status == "failed":
            attempted += 1
            failed += 1
        # "no_results" (search returned nothing to fetch) or None (no fallback / old
        # record) are not counted as fallback fetch attempts.
    return {
        "auditor_fallback_fetches": attempted,
        "auditor_fallback_successful_fetches": successful,
        "auditor_fallback_failed_fetches": failed,
    }


def sum_usage_metadata(usage_metadata: dict):
    """UsageMetadataCallbackHandler.usage_metadata is keyed by model name.
    Sum across all models present (normally just one)."""
    input_tokens = 0
    output_tokens = 0
    total_tokens = 0
    for _model_name, usage in (usage_metadata or {}).items():
        input_tokens += usage.get("input_tokens", 0) or 0
        output_tokens += usage.get("output_tokens", 0) or 0
        total_tokens += usage.get("total_tokens", 0) or 0
    return input_tokens, output_tokens, total_tokens


def compute_cost(input_tokens, output_tokens, num_searches, pricing):
    gemini_in = pricing.get("GEMINI_INPUT_COST_PER_1K_TOKENS_INR")
    gemini_out = pricing.get("GEMINI_OUTPUT_COST_PER_1K_TOKENS_INR")
    tavily_cost = pricing.get("TAVILY_COST_PER_SEARCH_INR")

    if gemini_in is None or gemini_out is None or tavily_cost is None:
        return {
            "llm_cost_inr": None,
            "search_cost_inr": None,
            "total_cost_inr": None,
            "cost_status": "unavailable - pricing_config.json has no pricing values yet",
        }

    llm_cost = (input_tokens / 1000.0) * gemini_in + (output_tokens / 1000.0) * gemini_out
    search_cost = num_searches * tavily_cost
    return {
        "llm_cost_inr": round(llm_cost, 4),
        "search_cost_inr": round(search_cost, 4),
        "total_cost_inr": round(llm_cost + search_cost, 4),
        "cost_status": "computed",
    }


# Node names as they appear in app/main.py's StateGraph (graph_builder.add_node(...)).
# Used to recognize which on_chain_start events correspond to a real graph node
# (as opposed to internal LangChain plumbing like RunnableSequence/PydanticOutputParser).
KNOWN_GRAPH_NODES = {"chatbot", "tools", "extract_claims", "auditor", "feedback", "compose_final_answer"}

# Maps a graph node name to the LLM-call timing bucket requested for the
# "Question total -> LLM calls -> ..." breakdown.
NODE_TO_LLM_BUCKET = {
    "chatbot": "llm_chatbot",
    "extract_claims": "llm_extract_claims",
    "auditor": "llm_auditor",
    "feedback": "llm_feedback",
}


class TimingCallbackHandler(BaseCallbackHandler):
    """Benchmark-only instrumentation. Attributes every LLM call and every tool
    call (web_search, fetch_page - whether routed through the Analyst's ToolNode
    or called directly inside auditor()) to the graph node it happened in, using
    only the standard LangChain callback events - no app/main.py changes.

    How node attribution works (validated empirically before writing this):
      - Each LangGraph node execution fires on_chain_start with kwargs["name"]
        set to the node's name (e.g. "chatbot", "auditor") and a fresh run_id.
      - Every nested Runnable invocation inside that node (a chat model call,
        llm.with_structured_output(...) - which wraps the chat model in an
        intermediate RunnableSequence - or a direct tool.invoke() call) carries
        a parent_run_id chain that, when walked upward, eventually reaches that
        node's own run_id. So a call's node is found by walking parent_run_id
        links until a run_id matching a KNOWN_GRAPH_NODES chain_start is found -
        this works regardless of how many wrapper layers with_structured_output
        or similar helpers introduce.
      - This holds even for auditor()'s direct fetch_page.invoke() call, which
        is not routed through the ToolNode - it still fires on_tool_start with
        parent_run_id pointing (after possibly zero levels) at auditor's run_id.
    """

    def __init__(self, question_id: int, pass_id: int = 1):
        self.question_id = question_id
        self.pass_id = pass_id
        self.records = []
        self._t0 = time.perf_counter()
        self._parent_of = {}      # run_id -> parent_run_id (or None)
        self._node_of = {}        # run_id -> node name, only for tracked node chain runs
        self._start_time = {}     # run_id -> perf_counter() at start, for any tracked run
        self._tool_detail = {}    # run_id -> tool name, for tool runs

    def _find_node(self, run_id):
        depth = 0
        current = self._parent_of.get(run_id)
        while current is not None and depth < 25:
            if current in self._node_of:
                return self._node_of[current]
            current = self._parent_of.get(current)
            depth += 1
        return "unknown"

    def _record(self, operation_type, node, detail, run_id, success, error=None):
        start = self._start_time.pop(run_id, None)
        if start is None:
            return
        now = time.perf_counter()
        self.records.append({
            "question_id": self.question_id,
            "pass_id": self.pass_id,
            "operation_type": operation_type,
            "node": node,
            "detail": detail,
            "start_offset_seconds": round(start - self._t0, 3),
            "elapsed_seconds": round(now - start, 3),
            "success": success,
            "error": (str(error)[:300] if error is not None else None),
        })

    # --- chain (graph node) events ---
    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, tags=None, metadata=None, **kwargs):
        run_id = str(run_id)
        self._parent_of[run_id] = str(parent_run_id) if parent_run_id else None
        node_name = kwargs.get("name")
        if node_name in KNOWN_GRAPH_NODES:
            self._node_of[run_id] = node_name
            self._start_time[run_id] = time.perf_counter()

    def on_chain_end(self, outputs, *, run_id, parent_run_id=None, **kwargs):
        run_id = str(run_id)
        if run_id in self._node_of:
            self._record("node_total", self._node_of[run_id], self._node_of[run_id], run_id, success=True)

    def on_chain_error(self, error, *, run_id, parent_run_id=None, **kwargs):
        run_id = str(run_id)
        if run_id in self._node_of:
            self._record("node_total", self._node_of[run_id], self._node_of[run_id], run_id, success=False, error=error)

    # --- LLM / chat model events ---
    def on_llm_start(self, serialized, prompts, *, run_id, parent_run_id=None, **kwargs):
        run_id = str(run_id)
        self._parent_of[run_id] = str(parent_run_id) if parent_run_id else None
        self._start_time[run_id] = time.perf_counter()

    def on_chat_model_start(self, serialized, messages, *, run_id, parent_run_id=None, **kwargs):
        run_id = str(run_id)
        self._parent_of[run_id] = str(parent_run_id) if parent_run_id else None
        self._start_time[run_id] = time.perf_counter()

    def _finish_llm(self, run_id, success, error=None):
        run_id = str(run_id)
        if run_id not in self._start_time:
            return
        node = self._find_node(run_id)
        bucket = NODE_TO_LLM_BUCKET.get(node, "llm_unknown")
        self._record(bucket, node, "chat_model_call", run_id, success, error)

    def on_llm_end(self, response, *, run_id, parent_run_id=None, **kwargs):
        self._finish_llm(run_id, success=True)

    def on_llm_error(self, error, *, run_id, parent_run_id=None, **kwargs):
        self._finish_llm(run_id, success=False, error=error)

    # --- tool events (web_search, fetch_page, calculator - Analyst AND Auditor) ---
    def on_tool_start(self, serialized, input_str, *, run_id, parent_run_id=None, tags=None, **kwargs):
        run_id = str(run_id)
        self._parent_of[run_id] = str(parent_run_id) if parent_run_id else None
        self._start_time[run_id] = time.perf_counter()
        self._tool_detail[run_id] = (serialized or {}).get("name", "unknown_tool")

    def _finish_tool(self, run_id, success, error=None):
        run_id = str(run_id)
        if run_id not in self._start_time:
            return
        node = self._find_node(run_id)
        tool_name = self._tool_detail.pop(run_id, "unknown_tool")
        if tool_name == "web_search":
            operation_type = "search"
        elif tool_name == "fetch_page":
            operation_type = "fetch"
        else:
            operation_type = f"tool_{tool_name}"
        self._record(operation_type, node, tool_name, run_id, success, error)

    def on_tool_end(self, output, *, run_id, parent_run_id=None, **kwargs):
        self._finish_tool(run_id, success=True)

    def on_tool_error(self, error, *, run_id, parent_run_id=None, **kwargs):
        self._finish_tool(run_id, success=False, error=error)


def summarize_timing(records: list) -> dict:
    """Aggregates raw TimingCallbackHandler records into the requested
    Question total -> {LLM calls -> chatbot/extract_claims/auditor/feedback,
    Tavily/web search time, page fetch time} breakdown, plus a residual
    'unaccounted_seconds' (question wall-clock minus summed node_total spans)
    to reveal time not attributable to any captured LLM/tool call at all -
    e.g. hidden SDK-level retries/network waits, or LangGraph orchestration
    overhead. This is the key diagnostic for outliers like the Q2/Q6 spikes."""
    buckets = {
        "llm_chatbot_seconds": 0.0,
        "llm_extract_claims_seconds": 0.0,
        "llm_auditor_seconds": 0.0,
        "llm_feedback_seconds": 0.0,
        "llm_unknown_seconds": 0.0,
        "search_seconds": 0.0,
        "fetch_seconds": 0.0,
        "other_tool_seconds": 0.0,
    }
    counts = {
        "num_llm_calls": 0,
        "num_search_calls": 0,
        "num_fetch_calls": 0,
        "num_failed_calls": 0,
    }
    node_total_seconds = {}

    for r in records:
        op = r["operation_type"]
        elapsed = r["elapsed_seconds"]
        if not r.get("success", True):
            counts["num_failed_calls"] += 1

        if op == "node_total":
            node_total_seconds[r["node"]] = node_total_seconds.get(r["node"], 0.0) + elapsed
            continue

        if op in ("llm_chatbot", "llm_extract_claims", "llm_auditor", "llm_feedback", "llm_unknown"):
            buckets[f"{op}_seconds"] += elapsed
            counts["num_llm_calls"] += 1
        elif op == "search":
            buckets["search_seconds"] += elapsed
            counts["num_search_calls"] += 1
        elif op == "fetch":
            buckets["fetch_seconds"] += elapsed
            counts["num_fetch_calls"] += 1
        else:
            buckets["other_tool_seconds"] += elapsed

    llm_total = sum(v for k, v in buckets.items() if k.startswith("llm_"))
    tool_total = buckets["search_seconds"] + buckets["fetch_seconds"] + buckets["other_tool_seconds"]

    summary = {k: round(v, 3) for k, v in buckets.items()}
    summary.update(counts)
    summary["llm_total_seconds"] = round(llm_total, 3)
    summary["tool_total_seconds"] = round(tool_total, 3)
    summary["timed_total_seconds"] = round(llm_total + tool_total, 3)
    summary["node_total_seconds"] = {k: round(v, 3) for k, v in node_total_seconds.items()}
    summary["node_sum_seconds"] = round(sum(node_total_seconds.values()), 3)
    return summary


def run_question(question: dict, pricing: dict, pass_id: int = 1) -> dict:
    """Runs exactly one question through the real, unmodified agent and
    returns its metric record. Makes real API calls (Gemini + Tavily).

    pass_id is a benchmark-only label (e.g. 1 = baseline, 2 = same 8 questions
    re-run against accumulated persistent memory) - it does not affect how the
    question is run, only how the result record is tagged."""
    # Import lazily so --dry-run never touches app.main at module scope in a
    # way that could surprise the caller before argument parsing completes.
    from langchain_core.callbacks import UsageMetadataCallbackHandler
    from langchain_core.messages import HumanMessage
    from app.main import agent, load_memory

    memory_before = load_memory()
    memory_before_count = len(memory_before)

    handler = UsageMetadataCallbackHandler()
    timing_handler = TimingCallbackHandler(question_id=question["question_id"], pass_id=pass_id)
    start = time.time()

    final_state = agent.invoke(
        {"messages": [HumanMessage(content=question["question"])]},
        config={"callbacks": [handler, timing_handler]},
    )

    elapsed_seconds = time.time() - start
    timing_summary = summarize_timing(timing_handler.records)
    timing_summary["unaccounted_seconds"] = round(elapsed_seconds - timing_summary["node_sum_seconds"], 3)

    input_tokens, output_tokens, total_tokens = sum_usage_metadata(handler.usage_metadata)

    tool_stats = count_tool_usage(final_state.get("messages", []))
    claim_stats = count_claim_verdicts(final_state.get("claims", []))
    auditor_stats = count_auditor_fetches(final_state.get("audit_results", []))
    auditor_fallback_stats = count_auditor_fallback_fetches(final_state.get("audit_results", []))

    feedback_lessons = final_state.get("feedback_lessons", [])
    memory_after = load_memory()
    new_lessons_added = len(memory_after) - memory_before_count

    final_message = final_state["messages"][-1] if final_state.get("messages") else None
    final_answer_excerpt = ""
    if final_message is not None and getattr(final_message, "type", None) == "ai":
        final_answer_excerpt = str(final_message.content)[:500]

    cost = compute_cost(input_tokens, output_tokens, tool_stats["num_searches"], pricing)

    result = {
        "pass_id": pass_id,
        "question_id": question["question_id"],
        "question": question["question"],
        "difficulty": question["difficulty"],
        "status": "success",
        "error": None,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "elapsed_seconds": round(elapsed_seconds, 3),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        **cost,
        **tool_stats,
        **auditor_stats,
        **auditor_fallback_stats,
        **claim_stats,
        "lessons_generated": len(feedback_lessons),
        "new_lessons_added_to_memory": new_lessons_added,
        "memory_lessons_available_at_start": memory_before_count,
        "final_answer_excerpt": final_answer_excerpt,
        "timing_summary": timing_summary,
        "timing_log": timing_handler.records,
    }

    print(f"\n[BENCHMARK] Q{question['question_id']} complete in {elapsed_seconds:.2f}s "
          f"| tokens={total_tokens} | claims={claim_stats['num_claims']} "
          f"(supported={claim_stats['supported_claims']}, "
          f"contradicted={claim_stats['contradicted_claims']}, "
          f"unsupported={claim_stats['unsupported_claims']}) "
          f"| auditor_fetches={auditor_stats['auditor_fetches']} "
          f"(success={auditor_stats['auditor_successful_fetches']}, "
          f"failed={auditor_stats['auditor_failed_fetches']}) "
          f"| fallback_fetches={auditor_fallback_stats['auditor_fallback_fetches']} "
          f"(success={auditor_fallback_stats['auditor_fallback_successful_fetches']}, "
          f"failed={auditor_fallback_stats['auditor_fallback_failed_fetches']}) "
          f"| new_lessons={new_lessons_added} "
          f"| llm(chatbot={timing_summary['llm_chatbot_seconds']}s, "
          f"extract_claims={timing_summary['llm_extract_claims_seconds']}s, "
          f"auditor={timing_summary['llm_auditor_seconds']}s, "
          f"feedback={timing_summary['llm_feedback_seconds']}s) "
          f"search={timing_summary['search_seconds']}s "
          f"fetch={timing_summary['fetch_seconds']}s "
          f"unaccounted={timing_summary['unaccounted_seconds']}s")

    return result


def run_question_safe(question: dict, pricing: dict, pass_id: int = 1) -> dict:
    """Wraps run_question so a single question's failure does not crash the
    whole --all run and does not fabricate metrics it doesn't have. On
    failure, only fields we can honestly measure (memory size before the
    attempt, elapsed time until failure) are populated; every other metric
    is explicitly null, never a fabricated 0."""
    from app.main import load_memory

    memory_before_count = len(load_memory())
    start = time.time()
    try:
        return run_question(question, pricing, pass_id=pass_id)
    except Exception as e:
        elapsed_seconds = time.time() - start
        print(f"\n[BENCHMARK] Q{question['question_id']} FAILED after {elapsed_seconds:.2f}s: {e}")
        return {
            "pass_id": pass_id,
            "question_id": question["question_id"],
            "question": question["question"],
            "difficulty": question["difficulty"],
            "status": "failed",
            "error": str(e),
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "elapsed_seconds": round(elapsed_seconds, 3),
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
            "llm_cost_inr": None,
            "search_cost_inr": None,
            "total_cost_inr": None,
            "cost_status": "unavailable - question failed before completion",
            "num_searches": None,
            "num_page_fetches": None,
            "successful_fetches": None,
            "failed_fetches": None,
            "auditor_fetches": None,
            "auditor_successful_fetches": None,
            "auditor_failed_fetches": None,
            "auditor_fallback_fetches": None,
            "auditor_fallback_successful_fetches": None,
            "auditor_fallback_failed_fetches": None,
            "num_claims": None,
            "supported_claims": None,
            "contradicted_claims": None,
            "unsupported_claims": None,
            "lessons_generated": None,
            "new_lessons_added_to_memory": None,
            "memory_lessons_available_at_start": memory_before_count,
            "final_answer_excerpt": None,
            "timing_summary": None,
            "timing_log": [],
        }


def compute_summary(results: list) -> dict:
    successes = [r for r in results if r.get("status") == "success"]
    failures = [r for r in results if r.get("status") == "failed"]

    def total(field):
        return sum(r[field] for r in successes if r.get(field) is not None)

    # Cost aggregation. Per-question costs are None when pricing_config.json has no
    # values; aggregate only over questions that produced a numeric cost, and report
    # None (not a misleading 0) when no priced questions exist.
    priced = [r for r in successes if r.get("total_cost_inr") is not None]

    def cost_total(field):
        return round(sum(r[field] for r in priced), 4) if priced else None

    total_cost = cost_total("total_cost_inr")
    average_cost = round(total_cost / len(priced), 4) if priced else None

    return {
        "total_questions": len(results),
        "successful_questions": len(successes),
        "failed_questions": len(failures),
        "failed_question_ids": [r["question_id"] for r in failures],
        "total_elapsed_seconds": round(sum(r["elapsed_seconds"] for r in results if r.get("elapsed_seconds") is not None), 3),
        "total_input_tokens": total("input_tokens"),
        "total_output_tokens": total("output_tokens"),
        "total_tokens": total("total_tokens"),
        "total_searches": total("num_searches"),
        "total_page_fetches": total("num_page_fetches"),
        "total_successful_fetches": total("successful_fetches"),
        "total_failed_fetches": total("failed_fetches"),
        "total_auditor_fetches": total("auditor_fetches"),
        "total_auditor_successful_fetches": total("auditor_successful_fetches"),
        "total_auditor_failed_fetches": total("auditor_failed_fetches"),
        "total_auditor_fallback_fetches": total("auditor_fallback_fetches"),
        "total_auditor_fallback_successful_fetches": total("auditor_fallback_successful_fetches"),
        "total_auditor_fallback_failed_fetches": total("auditor_fallback_failed_fetches"),
        "total_claims": total("num_claims"),
        "total_supported_claims": total("supported_claims"),
        "total_contradicted_claims": total("contradicted_claims"),
        "total_unsupported_claims": total("unsupported_claims"),
        "total_lessons_generated": total("lessons_generated"),
        "total_new_lessons_added_to_memory": total("new_lessons_added_to_memory"),
        "total_llm_cost_inr": cost_total("llm_cost_inr"),
        "total_search_cost_inr": cost_total("search_cost_inr"),
        "total_cost_inr": total_cost,
        "average_cost_per_question_inr": average_cost,
        "priced_questions": len(priced),
    }


def dry_run() -> bool:
    """Structural validation only. No API calls, no agent invocation."""
    ok = True

    print("[DRY RUN] Loading questions.json ...")
    try:
        questions = load_questions()
    except Exception as e:
        print(f"[DRY RUN] FAIL: could not load questions.json: {e}")
        return False

    required_fields = {
        "question_id", "question", "difficulty", "category",
        "research_objective", "entities_introduced", "entities_reused",
        "expected_research_complexity", "harder_than_previous",
    }
    if len(questions) != 8:
        print(f"[DRY RUN] FAIL: expected 8 questions, found {len(questions)}")
        ok = False
    for q in questions:
        missing = required_fields - set(q.keys())
        if missing:
            print(f"[DRY RUN] FAIL: question {q.get('question_id')} missing fields: {missing}")
            ok = False
    if ok:
        print(f"[DRY RUN] OK: {len(questions)} questions loaded with all required fields.")

    print("[DRY RUN] Loading pricing_config.json ...")
    try:
        pricing = load_pricing()
        print(f"[DRY RUN] OK: pricing config loaded (values null until supplied): "
              f"gemini_in={pricing.get('GEMINI_INPUT_COST_PER_1K_TOKENS_INR')}, "
              f"gemini_out={pricing.get('GEMINI_OUTPUT_COST_PER_1K_TOKENS_INR')}, "
              f"tavily={pricing.get('TAVILY_COST_PER_SEARCH_INR')}")
    except Exception as e:
        print(f"[DRY RUN] FAIL: could not load pricing_config.json: {e}")
        ok = False

    print("[DRY RUN] Importing app.main (agent, load_memory) without invoking it ...")
    try:
        from app.main import agent, load_memory, MEMORY_FILE_PATH
        print(f"[DRY RUN] OK: app.main imported. Memory file path: {MEMORY_FILE_PATH}")
    except Exception as e:
        print(f"[DRY RUN] FAIL: could not import app.main: {e}")
        return False

    print("[DRY RUN] Checking compiled graph structure ...")
    try:
        graph = agent.get_graph()
        nodes = set(graph.nodes.keys())
        expected_nodes = {
            "__start__", "chatbot", "tools", "extract_claims",
            "auditor", "feedback", "compose_final_answer", "__end__",
        }
        missing_nodes = expected_nodes - nodes
        if missing_nodes:
            print(f"[DRY RUN] FAIL: graph missing expected nodes: {missing_nodes}")
            ok = False
        else:
            print(f"[DRY RUN] OK: graph contains all expected nodes: {sorted(nodes)}")

        edge_pairs = {(e.source, e.target) for e in graph.edges}
        expected_chain = [
            ("auditor", "feedback"),
            ("feedback", "compose_final_answer"),
            ("compose_final_answer", "__end__"),
            ("extract_claims", "auditor"),
        ]
        for pair in expected_chain:
            if pair not in edge_pairs:
                print(f"[DRY RUN] FAIL: missing expected edge {pair}")
                ok = False
        if ok:
            print("[DRY RUN] OK: post-audit chain confirmed: "
                  "extract_claims -> auditor -> feedback -> compose_final_answer -> END")
    except Exception as e:
        print(f"[DRY RUN] FAIL: could not introspect compiled graph: {e}")
        ok = False

    print("[DRY RUN] Checking memory file is readable ...")
    try:
        mem = load_memory()
        print(f"[DRY RUN] OK: research_memory.json readable, {len(mem)} lesson(s) currently present "
              f"(pre-existing from prior sessions, not from this benchmark).")
    except Exception as e:
        print(f"[DRY RUN] FAIL: could not read memory file: {e}")
        ok = False

    print("[DRY RUN] Checking results directory is writable ...")
    try:
        os.makedirs(RESULTS_DIR, exist_ok=True)
        probe_path = os.path.join(RESULTS_DIR, ".dry_run_probe")
        with open(probe_path, "w", encoding="utf-8") as f:
            f.write("probe")
        os.remove(probe_path)
        print("[DRY RUN] OK: results directory is writable.")
    except Exception as e:
        print(f"[DRY RUN] FAIL: results directory not writable: {e}")
        ok = False

    print("\n[DRY RUN] Checking UsageMetadataCallbackHandler import (no invocation) ...")
    try:
        from langchain_core.callbacks import UsageMetadataCallbackHandler  # noqa: F401
        print("[DRY RUN] OK: UsageMetadataCallbackHandler importable.")
    except Exception as e:
        print(f"[DRY RUN] FAIL: could not import UsageMetadataCallbackHandler: {e}")
        ok = False

    print(f"\n[DRY RUN] {'PASS' if ok else 'FAIL'} - structural checks complete. No API calls were made.")
    return ok


def new_results_path() -> str:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return os.path.join(RESULTS_DIR, f"run_{stamp}.json"), stamp


def write_results(out_path: str, stamp: str, results: list, summary: dict = None, pass_id: int = 1) -> None:
    """Overwrites out_path with current results. Called after every question
    (not just at the end) so progress is never lost if the process dies."""
    payload = {"run_timestamp_utc": stamp, "pass_id": pass_id, "results": results}
    if summary is not None:
        payload["summary"] = summary
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser(description="PS3 8-Question Benchmark harness")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Structural test only, no API calls.")
    group.add_argument("--question", type=int, metavar="N", help="Run exactly one question (1-8) live.")
    group.add_argument("--all", action="store_true", help="Run all 8 questions sequentially, live.")
    parser.add_argument("--pass-id", type=int, default=1,
                         help="Benchmark-only label for this run (e.g. 2 for a repeat pass over "
                              "accumulated memory). Does not affect how questions are run.")
    args = parser.parse_args()

    if args.dry_run:
        ok = dry_run()
        sys.exit(0 if ok else 1)

    questions = load_questions()
    pricing = load_pricing()
    out_path, stamp = new_results_path()

    if args.question is not None:
        matches = [q for q in questions if q["question_id"] == args.question]
        if not matches:
            print(f"No question with question_id={args.question} found.")
            sys.exit(1)
        results = [run_question_safe(matches[0], pricing, pass_id=args.pass_id)]
        write_results(out_path, stamp, results, compute_summary(results), pass_id=args.pass_id)
    else:  # --all
        results = []
        for q in questions:
            results.append(run_question_safe(q, pricing, pass_id=args.pass_id))
            # Write after every question, not just at the end, so a crash or
            # interrupt never loses already-completed questions' metrics.
            write_results(out_path, stamp, results, compute_summary(results), pass_id=args.pass_id)

    print(f"\n[BENCHMARK] Results written to: {out_path}")
    summary = compute_summary(results)
    print(f"[BENCHMARK] Summary: pass_id={args.pass_id} "
          f"{summary['successful_questions']}/{summary['total_questions']} succeeded, "
          f"failed question_ids={summary['failed_question_ids']}")


if __name__ == "__main__":
    main()
