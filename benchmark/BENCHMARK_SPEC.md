# PS3 8-Question Benchmark Specification

Status: APPROVED. Architecture is frozen (Phase 6.1 complete) as of this benchmark's design.
See `../STATUS.md`, `../HANDOFF.md`, `../DECISIONS.md` for prior project history.

## Purpose

Exercise the existing Analyst + Auditor + Feedback system end-to-end against live web
evidence, across increasing difficulty, and record cost/latency/quality metrics so we can
observe whether the system's supported/contradicted/unsupported rates improve as persistent
memory accumulates lessons.

This benchmark does **not** modify the Analyst, Auditor, Feedback, or
`compose_final_answer` logic in `app/main.py`. It only invokes the existing compiled
`agent` and measures it from the outside.

## Questions

See `questions.json` for the machine-readable version. Summary:

| # | Category | Question |
|---|---|---|
| 1 | Basic factual research | In what month and year did Satya Nadella become CEO of Microsoft? |
| 2 | Multi-source research | What was Microsoft's total revenue for fiscal year 2024, according to its official financial filings? |
| 3 | Entity reuse | How much did Microsoft invest in OpenAI, and in which year(s) did those investments occur? |
| 4 | Comparison | How does Microsoft's fiscal year 2024 revenue compare to Alphabet's (Google's) fiscal year 2024 revenue - which company reported higher revenue? |
| 5 | Conflicting sources | Did Microsoft lay off exactly 10,000 employees in January 2023? |
| 6 | Multi-hop research | Who is the current CEO of the company that owns GitHub, and in what year did that company acquire GitHub? |
| 7 | Difficult source verification | Was Elon Musk a co-founder of OpenAI, and did he leave OpenAI's board in 2018? |
| 8 | Adversarial final question | Confirm: In 2019, Microsoft fully acquired OpenAI for $1 billion, making OpenAI a wholly-owned Microsoft subsidiary. |

Entity reuse: Microsoft (Q1 -> Q2,3,4,5,6,8), Satya Nadella (Q1 -> Q6), fiscal year 2024
(Q2 -> Q4), OpenAI (Q3 -> Q7,8). 6 of 8 questions reuse at least one earlier entity.

Questions must run **sequentially** and in order, because persistent memory
(`app/research_memory.json`) written by `feedback()` on question N is loaded by
`chatbot()` on question N+1 - later questions are expected to benefit from lessons
generated earlier in the same run.

## Metrics Recorded Per Question

```json
{
  "question_id": 1,
  "question": "string",
  "difficulty": 1,
  "timestamp": "ISO-8601, stamped when the question's run completes",
  "elapsed_seconds": 0.0,
  "input_tokens": 0,
  "output_tokens": 0,
  "total_tokens": 0,
  "llm_cost_inr": null,
  "search_cost_inr": null,
  "total_cost_inr": null,
  "cost_status": "unavailable - pricing_config.json has no pricing values yet",
  "num_searches": 0,
  "num_page_fetches": 0,
  "successful_fetches": 0,
  "failed_fetches": 0,
  "auditor_fetches": 0,
  "auditor_successful_fetches": 0,
  "auditor_failed_fetches": 0,
  "auditor_fallback_fetches": 0,
  "auditor_fallback_successful_fetches": 0,
  "auditor_fallback_failed_fetches": 0,
  "num_claims": 0,
  "supported_claims": 0,
  "contradicted_claims": 0,
  "unsupported_claims": 0,
  "lessons_generated": 0,
  "new_lessons_added_to_memory": 0,
  "memory_lessons_available_at_start": 0,
  "final_answer_excerpt": "string, truncated to ~500 chars",
  "timing_summary": {
    "llm_chatbot_seconds": 0.0,
    "llm_extract_claims_seconds": 0.0,
    "llm_auditor_seconds": 0.0,
    "llm_feedback_seconds": 0.0,
    "llm_unknown_seconds": 0.0,
    "llm_total_seconds": 0.0,
    "search_seconds": 0.0,
    "fetch_seconds": 0.0,
    "other_tool_seconds": 0.0,
    "tool_total_seconds": 0.0,
    "timed_total_seconds": 0.0,
    "num_llm_calls": 0,
    "num_search_calls": 0,
    "num_fetch_calls": 0,
    "num_failed_calls": 0,
    "node_total_seconds": {"chatbot": 0.0, "tools": 0.0, "extract_claims": 0.0, "auditor": 0.0, "feedback": 0.0, "compose_final_answer": 0.0},
    "node_sum_seconds": 0.0,
    "unaccounted_seconds": 0.0
  },
  "timing_log": [
    {"question_id": 1, "pass_id": 1, "operation_type": "llm_chatbot", "node": "chatbot", "detail": "chat_model_call", "start_offset_seconds": 0.0, "elapsed_seconds": 0.0, "success": true, "error": null}
  ]
}
```

### Derivation

| Metric | Source |
|---|---|
| `elapsed_seconds` | Harness wall-clock around `agent.invoke()` for that question. |
| `input_tokens` / `output_tokens` / `total_tokens` | `langchain_core.callbacks.UsageMetadataCallbackHandler` attached at the top-level `agent.invoke(..., config={"callbacks": [handler]})` call. Confirmed (see "Nested Token Tracking Validation" below) to capture usage from LLM calls made inside `chatbot`, `extract_claims`, `auditor`, and `feedback` even though none of those node functions accept or forward an explicit `config` parameter - LangChain/LangGraph propagate the callback via a contextvar automatically. **No `app/main.py` changes were required.** |
| `*_cost_inr` | `tokens * price` from `pricing_config.json`. Null until real pricing is supplied - see that file. |
| `num_searches` / `num_page_fetches` | Count of `tool`-type messages in the final state's `messages` list, grouped by originating tool name (`web_search` vs `fetch_page`). **These are Analyst-initiated fetches only** - see next row for the separate Auditor-initiated fetch count. |
| `successful_fetches` / `failed_fetches` | Count of `[FETCH STATUS: SUCCESS]` / `[FETCH STATUS: FAILED]` markers within `fetch_page` tool messages - same convention `analyze_tool_history()` in `app/main.py` already uses. Also Analyst-initiated only. |
| `auditor_fetches` / `auditor_successful_fetches` / `auditor_failed_fetches` | The Auditor's own independent verification fetches, tracked **separately** from the Analyst's fetches above since these are what actually drive each claim's verdict. `auditor()` calls `fetch_page.invoke()` directly (not through the `tools` node, so it never appears in `state["messages"]`) - once per claim, always - so `len(audit_results) == auditor_fetches`. Success/failure is derived from `audit_results[].supporting_evidence`: on a real fetch failure, `auditor()` sets `supporting_evidence` to the raw fetched-page string, which starts with the literal `[FETCH STATUS: FAILED]` marker; on any fetch success (regardless of whether the resulting verdict is supported, contradicted, or unsupported-due-to-insufficient-evidence, and even if the post-fetch audit judgement itself errored) `supporting_evidence` holds an LLM-extracted quote or error text with no such marker. `verification_status == "failed"` is *not* a reliable signal by itself, since it's also used for the succeeded-fetch-but-inconclusive-judgement cases - see `count_auditor_fetches()` in `run_benchmark.py` and its fixture test in `test_count_auditor_fetches.py`. |
| `auditor_fallback_fetches` / `auditor_fallback_successful_fetches` / `auditor_fallback_failed_fetches` | The Auditor's **fallback** fetches, tracked separately from the original cited-source fetches above. As of the Auditor fallback upgrade, when the original cited-source fetch fails the Auditor performs one independent neutral fallback search and fetches the top alternative result. `count_auditor_fallback_fetches()` reads the explicit provenance field `fallback_fetch_status` (`success`/`failed`/`no_results`/`None`) on each audit record: `success`/`failed` count as a fallback fetch attempt; `no_results` (search returned nothing to fetch) and `None` (no fallback / pre-upgrade record) do not. Also note: `count_auditor_fetches()` now prefers the new `original_fetch_status` provenance field to attribute the **original** fetch success/failure (falling back to the old `[FETCH STATUS: FAILED]` marker heuristic for pre-upgrade records), because on a fallback-success record the final `supporting_evidence` is the fallback page's quote and no longer carries the original failure marker. See `test_count_auditor_fallback_fetches.py` and `test_auditor_fallback.py`. |
| `num_claims` / `supported_claims` / `contradicted_claims` / `unsupported_claims` | `state["claims"]` (`verification_status`) cross-referenced with `state["audit_results"]` (`verdict`). |
| `lessons_generated` | Length of `feedback_lessons` returned in final state (already deduplicated against existing memory by `add_lessons_to_memory`). |
| `new_lessons_added_to_memory` | Diff of `app/research_memory.json` entry count, snapshotted before vs. after the question. |
| `memory_lessons_available_at_start` | Length of the pre-question memory snapshot (this is what was actually available to `chatbot()`'s system prompt for this question, since memory only grows via `feedback()` at the very end of a question's graph run). |
| `final_answer_excerpt` | First ~500 characters of `compose_final_answer`'s output message (the last message in final state). |
| `auditor_no_citation_claims` | Count of claims the Auditor flagged as having NO citation (Analyst gave no usable `source_url`), read from the explicit `citation_status` (`present`/`missing`) provenance field set by `auditor()`. This is a reporting flag only; it does not change the supported/unsupported/contradicted verdict. See `test_auditor_no_citation.py`. |
| `trace` | Full evaluator-readable per-question trace captured from graph state: `analyst_messages` (the Analyst plan + every tool call with name/args + every tool result, serialized structurally — not `str(msg)`), the structured `claims`, the `audit_results` (verdicts, reasoning, supporting evidence, `citation_status`, and fallback provenance incl. `fallback_search_query`/`fallback_source_url`/`fallback_fetch_status`), the `feedback_lessons`, and the full untruncated `final_answer_full`. Fallback-after-failure is captured in `audit_results` provenance since the Auditor's fallback search/fetch occur outside the graph message channel. Built by `build_trace()`/`serialize_message()`; see `test_trace_capture.py`. **Note:** result files from Pass 1–4 predate this field and contain metrics only; a fresh `--all` run produces trace-complete artifacts. |
| `timing_summary` / `timing_log` | Per-operation timing, added to diagnose latency outliers (e.g. Pass 2's Q2 @ 254.81s, Q6 @ 614.18s) without any `app/main.py` change. A `TimingCallbackHandler` is attached alongside the token-usage callback at the same top-level `agent.invoke(..., config={"callbacks": [...]})` call. It listens to standard LangChain callback events (`on_chain_start/end`, `on_chat_model_start`, `on_llm_end/error`, `on_tool_start`, `on_tool_end/error`) and attributes every LLM call and every tool call to the graph node it happened in by walking the `parent_run_id` chain up to the nearest `on_chain_start` whose `kwargs["name"]` matches a real node name (`chatbot`, `tools`, `extract_claims`, `auditor`, `feedback`, `compose_final_answer`) - this correctly resolves through `with_structured_output`'s extra `RunnableSequence` wrapper layer (validated empirically before implementation - see "Timing Instrumentation Validation" below), and correctly attributes `auditor()`'s direct `fetch_page.invoke()` calls (not routed through the `tools` ToolNode) to the `auditor` node. `timing_log` is the raw list of per-operation records; `timing_summary` is `summarize_timing()`'s aggregation into the requested breakdown (LLM calls per node, Tavily search time, page fetch time) plus `unaccounted_seconds` = question `elapsed_seconds` minus the summed `node_total_seconds` - a large `unaccounted_seconds` value would mean a latency spike is *not* explained by any captured LLM/tool call (e.g. hidden SDK-level retry/network wait), which is exactly the kind of thing worth knowing about the Q2/Q6 outliers before touching architecture. **Caveat:** a record's `success` field reflects whether the underlying LangChain `Runnable` call raised an exception - it is not the same as the `[FETCH STATUS: ...]` semantic outcome tracked by `auditor_fetches`/`auditor_successful_fetches`/`auditor_failed_fetches` above. `fetch_page` in `app/main.py` catches its own exceptions internally and always returns a normal string, so its timing records are always `success: true` even when the fetch semantically failed - use `auditor_failed_fetches` (not `timing_log`) to know if a fetch semantically failed. |

## Timing Instrumentation Validation

Before writing `TimingCallbackHandler`, two things were confirmed empirically against
real API calls (not assumed):
1. A LangGraph node's execution fires `on_chain_start` with `kwargs["name"]` set to the
   node's own name and a fresh `run_id` - giving a direct way to recognize "this run_id
   is the `auditor` node" etc.
2. `llm.with_structured_output(...).invoke(...)` (used by `extract_claims`, `auditor`,
   and `feedback` in `app/main.py`) wraps the base chat model in an intermediate
   `RunnableSequence`, so a chat model call's immediate `parent_run_id` is *not* the
   node's own run_id - it is two levels up. `TimingCallbackHandler._find_node()` walks
   the full `parent_run_id` chain (not just one level) to handle this correctly. A
   synthetic-graph fixture test additionally confirmed the same logic correctly
   attributes tool calls made through a real `ToolNode` (the Analyst's path) as well as
   direct `tool.invoke()` calls made inside a node function (the Auditor's path) - see
   `test_timing_instrumentation.py`.

## Nested Token Tracking Validation

Before writing `run_benchmark.py`, we validated whether `UsageMetadataCallbackHandler`
attached only at the top-level graph invocation captures token usage from nested
`.invoke()` calls made inside node functions that do not accept or forward a `config`
parameter (this is exactly how `chatbot`, `extract_claims`, `auditor`, and `feedback`
are written in `app/main.py`).

Test: a minimal two-node LangGraph graph, with a node function signature identical in
shape to `app/main.py`'s nodes (`def node(state: State):` - no `config` arg), making a
real (non-mocked) `llm.invoke(...)` call with no explicit config, run via
`graph.invoke(..., config={"callbacks": [handler]})`.

Result: **propagation works.** `handler.usage_metadata` correctly captured
`input_tokens`/`output_tokens`/`total_tokens` from the nested call. This was then
reconfirmed against the real `app/main.py` `agent` object end-to-end.

**Conclusion: no `app/main.py` changes were necessary for token tracking.** All
instrumentation lives in `run_benchmark.py`.

## Files

| File | Purpose |
|---|---|
| `benchmark/questions.json` | The 8 structured question definitions. |
| `benchmark/pricing_config.json` | Pricing config with null/TODO values - see "Do Not Invent Pricing" below. |
| `benchmark/run_benchmark.py` | Harness: loads questions, runs them sequentially against the unmodified `app.main.agent`, snapshots `research_memory.json` before/after each question, captures token usage and per-operation timing via callbacks, computes all other metrics from final graph state, writes one JSON result file per run to `benchmark/results/`. |
| `benchmark/test_count_auditor_fetches.py` | Synthetic/fixture test for the Auditor fetch success/failure parser (`count_auditor_fetches()`) - no API calls, hand-built `audit_results` records covering real/failed fetches, supported/contradicted verdicts, and the tricky succeeded-fetch-but-inconclusive-judgement cases. |
| `benchmark/test_timing_instrumentation.py` | Synthetic/fixture test for `TimingCallbackHandler` and `summarize_timing()` - no API calls, uses `FakeMessagesListChatModel` and a synthetic graph shaped like `app/main.py`'s real graph to verify node attribution (including through `ToolNode` and direct `tool.invoke()` calls), success/failure flagging, and aggregation math. |
| `benchmark/test_count_auditor_fallback_fetches.py` | Synthetic/fixture test for `count_auditor_fallback_fetches()` and the fallback-aware behavior of `count_auditor_fetches()` - no API calls, hand-built records with and without the new provenance fields (backward-compat). |
| `benchmark/test_auditor_fallback.py` | Synthetic/fixture test for the Auditor fallback flow in `app/main.py` - no API calls; monkeypatches `fetch_page`/`web_search`/`_build_fallback_query`/`_audit_fetched_evidence` to drive `auditor()` through all branches (original success, fallback success, both-fail, unrelated fallback, snippet-only), plus prompt-level guard checks for the "unrelated -> not contradicted" rule and neutral-query independence. |
| `benchmark/results/` | Output directory for benchmark run results (gitignored contents except `.gitkeep`). |

## Do Not Invent Pricing

`pricing_config.json` ships with `GEMINI_INPUT_COST_PER_1K_TOKENS_INR`,
`GEMINI_OUTPUT_COST_PER_1K_TOKENS_INR`, `TAVILY_COST_PER_SEARCH_INR`, and
`USD_TO_INR_RATE` all set to `null`. Until real values are supplied, `run_benchmark.py`
reports `llm_cost_inr` / `search_cost_inr` / `total_cost_inr` as `null` with
`cost_status: "unavailable - pricing_config.json has no pricing values yet"`. Raw token
counts and search counts are still recorded so cost can be computed retroactively once
pricing is supplied.

## Running

```
python -m benchmark.run_benchmark --dry-run          # structural test, no API calls
python -m benchmark.run_benchmark --question 1        # run exactly one question live
python -m benchmark.run_benchmark --all                # full sequential 8-question run
```

The full 8-question run consumes real API quota (Gemini + Tavily) and should only be
run deliberately, not automatically.
