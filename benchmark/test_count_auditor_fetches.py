"""
Synthetic/fixture test for count_auditor_fetches() in run_benchmark.py.

No API calls, no agent invocation - pure parser logic against hand-built
audit_results records shaped exactly like what app/main.py's auditor()
actually produces (see auditor() in app/main.py for the source shapes).

Run with:
    python benchmark/test_count_auditor_fetches.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from run_benchmark import count_auditor_fetches

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)
        print(f"FAIL: {message}")
    else:
        print(f"OK:   {message}")


# --- Case 1: empty audit_results (no claims extracted) ---
result = count_auditor_fetches([])
check(result == {"auditor_fetches": 0, "auditor_successful_fetches": 0, "auditor_failed_fetches": 0},
      "empty audit_results -> all zero")

# --- Case 2: Auditor fetch failed (real shape from app/main.py's failure branch) ---
# supporting_evidence == fetched_data, which starts with the literal marker.
failed_fetch_record = {
    "original_claim": "claim",
    "source_url": "https://example.com/blocked",
    "evidence_type": "search_snippet",
    "verification_status": "failed",
    "verdict": "unsupported",
    "reasoning": "Independent page fetch failed for URL 'https://example.com/blocked'. The original claim cannot be verified without accessible source page content.",
    "supporting_evidence": "[FETCH STATUS: FAILED]\nURL: https://example.com/blocked\nREASON: HTTP Request failed (403 Client Error)",
}
result = count_auditor_fetches([failed_fetch_record])
check(result["auditor_fetches"] == 1, "single failed-fetch record -> auditor_fetches == 1")
check(result["auditor_failed_fetches"] == 1, "single failed-fetch record -> auditor_failed_fetches == 1")
check(result["auditor_successful_fetches"] == 0, "single failed-fetch record -> auditor_successful_fetches == 0")

# --- Case 3: Auditor fetch succeeded, verdict supported ---
supported_record = {
    "original_claim": "claim",
    "source_url": "https://example.com/ok",
    "evidence_type": "fetched_page",
    "verification_status": "audited_supported",
    "verdict": "supported",
    "reasoning": "The page confirms the claim.",
    "supporting_evidence": "\"Direct quote from the fetched page confirming the claim.\"",
}
result = count_auditor_fetches([supported_record])
check(result["auditor_fetches"] == 1, "successful supported record -> auditor_fetches == 1")
check(result["auditor_successful_fetches"] == 1, "successful supported record -> auditor_successful_fetches == 1")
check(result["auditor_failed_fetches"] == 0, "successful supported record -> auditor_failed_fetches == 0")

# --- Case 4: Auditor fetch succeeded, verdict contradicted ---
contradicted_record = dict(supported_record)
contradicted_record["verdict"] = "contradicted"
contradicted_record["verification_status"] = "audited_contradicted"
result = count_auditor_fetches([contradicted_record])
check(result["auditor_successful_fetches"] == 1, "successful contradicted record -> counted as successful fetch")
check(result["auditor_failed_fetches"] == 0, "successful contradicted record -> not counted as failed fetch")

# --- Case 5: TRICKY - Auditor fetch succeeded, but verdict is 'unsupported'
# because the fetched page lacked sufficient evidence (not because the fetch failed).
# verification_status is "failed" here too, which is why verification_status alone
# is NOT a reliable signal - only the missing FETCH STATUS marker distinguishes this
# from a real fetch failure.
inconclusive_record = {
    "original_claim": "claim",
    "source_url": "https://example.com/ok-but-vague",
    "evidence_type": "fetched_page",
    "verification_status": "failed",
    "verdict": "unsupported",
    "reasoning": "The fetched page content does not contain sufficient facts to verify or disprove the original claim.",
    "supporting_evidence": "The page discusses related topics but does not directly address the claim.",
}
result = count_auditor_fetches([inconclusive_record])
check(result["auditor_successful_fetches"] == 1,
      "fetch succeeded but verdict unsupported (insufficient evidence) -> still counted as a SUCCESSFUL auditor fetch")
check(result["auditor_failed_fetches"] == 0,
      "fetch succeeded but verdict unsupported (insufficient evidence) -> NOT counted as a failed fetch")

# --- Case 6: TRICKY - Auditor fetch succeeded, but the post-fetch LLM judgement
# call itself errored (app/main.py's `except Exception as e` branch inside the
# success path). verification_status is "failed" again, but the fetch succeeded.
judgement_error_record = {
    "original_claim": "claim",
    "source_url": "https://example.com/ok-but-judge-errored",
    "evidence_type": "fetched_page",
    "verification_status": "failed",
    "verdict": "unsupported",
    "reasoning": "Auditor evaluation error: some LLM error",
    "supporting_evidence": "None",
}
result = count_auditor_fetches([judgement_error_record])
check(result["auditor_successful_fetches"] == 1,
      "fetch succeeded but audit judgement errored -> still counted as a SUCCESSFUL auditor fetch")
check(result["auditor_failed_fetches"] == 0,
      "fetch succeeded but audit judgement errored -> NOT counted as a failed fetch")

# --- Case 7: mixed batch across multiple claims in one question ---
mixed = [failed_fetch_record, supported_record, contradicted_record, inconclusive_record, judgement_error_record]
result = count_auditor_fetches(mixed)
check(result["auditor_fetches"] == 5, "mixed batch of 5 records -> auditor_fetches == 5")
check(result["auditor_failed_fetches"] == 1, "mixed batch -> exactly 1 real fetch failure")
check(result["auditor_successful_fetches"] == 4, "mixed batch -> 4 successful fetches (regardless of verdict)")

print("\n" + "=" * 60)
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
else:
    print("ALL CHECKS PASSED")
