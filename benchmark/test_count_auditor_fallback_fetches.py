"""
Fixture test for count_auditor_fallback_fetches() and the fallback-aware behavior
of count_auditor_fetches() in run_benchmark.py.

No API calls - hand-built audit_results records shaped exactly like what
app/main.py's upgraded auditor() now produces (with provenance fields), plus
old-shape records (no provenance) to prove backward compatibility.

Run with:
    python benchmark/test_count_auditor_fallback_fetches.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from run_benchmark import count_auditor_fetches, count_auditor_fallback_fetches

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)
        print(f"FAIL: {message}")
    else:
        print(f"OK:   {message}")


# --- Record shapes matching the new auditor() provenance ---

# A) Original cited-source fetch succeeded (no fallback performed).
rec_original_success = {
    "original_claim": "c", "source_url": "https://a.com", "evidence_type": "search_snippet",
    "verification_status": "audited_supported", "verdict": "supported",
    "reasoning": "r", "supporting_evidence": "\"quote\"",
    "original_source_url": "https://a.com", "original_fetch_status": "success",
    "fallback_search_performed": False, "fallback_search_query": None,
    "fallback_source_url": None, "fallback_fetch_status": None,
    "final_evidence_source": "original_cited_source", "final_verdict": "supported",
}

# B) Original fetch failed, fallback fetch SUCCEEDED -> supported via fallback.
rec_fallback_success = {
    "original_claim": "c", "source_url": "https://blocked.com", "evidence_type": "search_snippet",
    "verification_status": "audited_supported", "verdict": "supported",
    "reasoning": "r", "supporting_evidence": "\"fallback quote\"",  # NOTE: no failure marker
    "original_source_url": "https://blocked.com", "original_fetch_status": "failed",
    "fallback_search_performed": True, "fallback_search_query": "neutral query",
    "fallback_source_url": "https://alt.com", "fallback_fetch_status": "success",
    "final_evidence_source": "fallback_source", "final_verdict": "supported",
}

# C) Original fetch failed, fallback fetch ALSO failed -> unsupported.
rec_fallback_failed = {
    "original_claim": "c", "source_url": "https://blocked.com", "evidence_type": "search_snippet",
    "verification_status": "failed", "verdict": "unsupported",
    "reasoning": "r", "supporting_evidence": "[FETCH STATUS: FAILED]\nURL: https://blocked.com\nREASON: 403",
    "original_source_url": "https://blocked.com", "original_fetch_status": "failed",
    "fallback_search_performed": True, "fallback_search_query": "neutral query",
    "fallback_source_url": "https://alt.com", "fallback_fetch_status": "failed",
    "final_evidence_source": "none", "final_verdict": "unsupported",
}

# D) Original fetch failed, fallback search returned NO usable candidate -> unsupported.
rec_fallback_no_results = {
    "original_claim": "c", "source_url": "https://blocked.com", "evidence_type": "search_snippet",
    "verification_status": "failed", "verdict": "unsupported",
    "reasoning": "r", "supporting_evidence": "[FETCH STATUS: FAILED]\nURL: https://blocked.com\nREASON: 404",
    "original_source_url": "https://blocked.com", "original_fetch_status": "failed",
    "fallback_search_performed": True, "fallback_search_query": "neutral query",
    "fallback_source_url": None, "fallback_fetch_status": "no_results",
    "final_evidence_source": "none", "final_verdict": "unsupported",
}

# E) Old-shape record (no provenance fields at all) - backward compat.
rec_old_success = {
    "original_claim": "c", "source_url": "https://a.com", "evidence_type": "search_snippet",
    "verification_status": "audited_supported", "verdict": "supported",
    "reasoning": "r", "supporting_evidence": "\"quote\"",
}
rec_old_failed = {
    "original_claim": "c", "source_url": "https://b.com", "evidence_type": "search_snippet",
    "verification_status": "failed", "verdict": "unsupported",
    "reasoning": "r", "supporting_evidence": "[FETCH STATUS: FAILED]\nURL: https://b.com\nREASON: 403",
}


# --- count_auditor_fallback_fetches ---
r = count_auditor_fallback_fetches([rec_original_success])
check(r == {"auditor_fallback_fetches": 0, "auditor_fallback_successful_fetches": 0, "auditor_fallback_failed_fetches": 0},
      "original-success record -> no fallback fetch counted")

r = count_auditor_fallback_fetches([rec_fallback_success])
check(r["auditor_fallback_fetches"] == 1 and r["auditor_fallback_successful_fetches"] == 1 and r["auditor_fallback_failed_fetches"] == 0,
      "fallback-success record -> 1 fallback fetch, 1 successful")

r = count_auditor_fallback_fetches([rec_fallback_failed])
check(r["auditor_fallback_fetches"] == 1 and r["auditor_fallback_successful_fetches"] == 0 and r["auditor_fallback_failed_fetches"] == 1,
      "fallback-failed record -> 1 fallback fetch, 1 failed")

r = count_auditor_fallback_fetches([rec_fallback_no_results])
check(r["auditor_fallback_fetches"] == 0 and r["auditor_fallback_failed_fetches"] == 0,
      "fallback-no_results record -> NOT counted as a fallback fetch attempt (nothing was fetched)")

r = count_auditor_fallback_fetches([rec_old_success, rec_old_failed])
check(r == {"auditor_fallback_fetches": 0, "auditor_fallback_successful_fetches": 0, "auditor_fallback_failed_fetches": 0},
      "old-shape records (no provenance) -> 0 fallback fetches (backward compatible)")

# Mixed batch
mixed = [rec_original_success, rec_fallback_success, rec_fallback_failed, rec_fallback_no_results]
r = count_auditor_fallback_fetches(mixed)
check(r["auditor_fallback_fetches"] == 2, "mixed batch -> 2 fallback fetch attempts (success + failed only)")
check(r["auditor_fallback_successful_fetches"] == 1, "mixed batch -> 1 fallback success")
check(r["auditor_fallback_failed_fetches"] == 1, "mixed batch -> 1 fallback failure")


# --- count_auditor_fetches: ORIGINAL fetch attribution must use original_fetch_status when present ---
r = count_auditor_fetches([rec_fallback_success])
check(r["auditor_fetches"] == 1 and r["auditor_failed_fetches"] == 1 and r["auditor_successful_fetches"] == 0,
      "fallback-success record: ORIGINAL fetch correctly counted as FAILED (despite no failure marker in supporting_evidence)")

r = count_auditor_fetches([rec_original_success])
check(r["auditor_successful_fetches"] == 1 and r["auditor_failed_fetches"] == 0,
      "original-success record: ORIGINAL fetch counted as successful via original_fetch_status")

# Backward compat: old-shape records fall back to the supporting_evidence marker heuristic.
r = count_auditor_fetches([rec_old_success, rec_old_failed])
check(r["auditor_fetches"] == 2 and r["auditor_successful_fetches"] == 1 and r["auditor_failed_fetches"] == 1,
      "old-shape records: original fetch success/failure still derived from the [FETCH STATUS: FAILED] marker")

# Return dict of count_auditor_fetches still has EXACTLY the original 3 keys (no schema break).
r = count_auditor_fetches([rec_fallback_success])
check(set(r.keys()) == {"auditor_fetches", "auditor_successful_fetches", "auditor_failed_fetches"},
      "count_auditor_fetches still returns exactly its original 3 keys")

print("\n" + "=" * 60)
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
else:
    print("ALL CHECKS PASSED")
