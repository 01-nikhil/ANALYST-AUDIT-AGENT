"""
Verification test for cost calculation in run_benchmark.py.

No API calls. Checks:
- compute_cost() input/output token and Tavily search math against known inputs,
  using the REAL configured pricing_config.json values.
- compute_cost() returns None fields when pricing is unavailable.
- compute_summary() aggregates total cost and average-cost-per-question correctly,
  and reports None (not 0) when no question was priced.

Run with:
    python benchmark/test_cost_calculation.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from run_benchmark import compute_cost, compute_summary, load_pricing

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
        print(f"FAIL: {msg}")
    else:
        print(f"OK:   {msg}")


def approx(a, b, tol=1e-6):
    return a is not None and b is not None and abs(a - b) <= tol


# --- 1. compute_cost math with the REAL configured pricing ---
pricing = load_pricing()
gi = pricing["GEMINI_INPUT_COST_PER_1K_TOKENS_INR"]
go = pricing["GEMINI_OUTPUT_COST_PER_1K_TOKENS_INR"]
tv = pricing["TAVILY_COST_PER_SEARCH_INR"]
check(gi is not None and go is not None and tv is not None, "pricing_config.json has numeric values configured")

c = compute_cost(1000, 1000, 1, pricing)
check(approx(c["llm_cost_inr"], gi + go), f"LLM cost for 1K in + 1K out = gi+go ({gi}+{go})")
check(approx(c["search_cost_inr"], tv), f"search cost for 1 search = TAVILY rate ({tv})")
check(approx(c["total_cost_inr"], gi + go + tv), "total cost = llm + search")
check(c["cost_status"] == "computed", "cost_status = computed when pricing present")

# Larger, realistic-ish numbers
c2 = compute_cost(150000, 7000, 9, pricing)
expected_llm = (150000 / 1000.0) * gi + (7000 / 1000.0) * go
expected_search = 9 * tv
check(approx(c2["llm_cost_inr"], round(expected_llm, 4)), "LLM cost scales linearly with tokens")
check(approx(c2["search_cost_inr"], round(expected_search, 4)), "search cost scales linearly with searches")
check(approx(c2["total_cost_inr"], round(expected_llm + expected_search, 4)), "total = llm + search (large inputs)")

# --- 2. compute_cost with pricing unavailable ---
cnull = compute_cost(1000, 1000, 1, {"GEMINI_INPUT_COST_PER_1K_TOKENS_INR": None,
                                     "GEMINI_OUTPUT_COST_PER_1K_TOKENS_INR": None,
                                     "TAVILY_COST_PER_SEARCH_INR": None})
check(cnull["llm_cost_inr"] is None and cnull["total_cost_inr"] is None, "null pricing -> None cost fields")
check("unavailable" in cnull["cost_status"], "null pricing -> cost_status unavailable")


# --- 3. compute_summary cost aggregation (priced) ---
def result(qid, in_tok, out_tok, searches):
    cc = compute_cost(in_tok, out_tok, searches, pricing)
    r = {"question_id": qid, "status": "success", "elapsed_seconds": 1.0,
         "input_tokens": in_tok, "output_tokens": out_tok, "total_tokens": in_tok + out_tok,
         "num_searches": searches, "num_page_fetches": 0, "successful_fetches": 0, "failed_fetches": 0,
         "auditor_fetches": 1, "auditor_successful_fetches": 1, "auditor_failed_fetches": 0,
         "auditor_fallback_fetches": 0, "auditor_fallback_successful_fetches": 0, "auditor_fallback_failed_fetches": 0,
         "num_claims": 1, "supported_claims": 1, "contradicted_claims": 0, "unsupported_claims": 0,
         "lessons_generated": 0, "new_lessons_added_to_memory": 0}
    r.update(cc)
    return r

r1 = result(1, 10000, 1000, 1)
r2 = result(2, 20000, 2000, 2)
s = compute_summary([r1, r2])
check(s["priced_questions"] == 2, "summary: priced_questions counts questions with numeric cost")
check(approx(s["total_llm_cost_inr"], round(r1["llm_cost_inr"] + r2["llm_cost_inr"], 4)), "summary: total LLM cost = sum of per-question LLM cost")
check(approx(s["total_search_cost_inr"], round(r1["search_cost_inr"] + r2["search_cost_inr"], 4)), "summary: total search cost = sum")
check(approx(s["total_cost_inr"], round(r1["total_cost_inr"] + r2["total_cost_inr"], 4)), "summary: total cost = sum of per-question total")
check(approx(s["average_cost_per_question_inr"], round(s["total_cost_inr"] / 2, 4)), "summary: average = total / priced_questions")

# --- 4. compute_summary when costs are unavailable ---
null_pricing = {"GEMINI_INPUT_COST_PER_1K_TOKENS_INR": None, "GEMINI_OUTPUT_COST_PER_1K_TOKENS_INR": None, "TAVILY_COST_PER_SEARCH_INR": None}
def result_unpriced(qid):
    cc = compute_cost(1000, 100, 1, null_pricing)
    r = result(qid, 1000, 100, 1)
    r.update(cc)  # overwrite cost fields with None
    return r
su = compute_summary([result_unpriced(1)])
check(su["total_cost_inr"] is None and su["average_cost_per_question_inr"] is None, "summary: no priced questions -> cost totals None (not 0)")
check(su["priced_questions"] == 0, "summary: priced_questions = 0 when unpriced")

print("\n" + "=" * 60)
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
else:
    print("ALL CHECKS PASSED")
