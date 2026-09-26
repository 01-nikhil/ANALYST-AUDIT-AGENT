"""
Focused regression test for the Auditor's explicit NO-CITATION detection.

No API calls: monkeypatches app.main's I/O leaves and LLM helpers and drives
auditor() directly. Verifies the Auditor flags claims with no cited source
(citation_status="missing") WITHOUT altering supported/unsupported/contradicted
behavior, and that count_no_citation_claims() aggregates the flag correctly.

Run with:
    python benchmark/test_auditor_no_citation.py
"""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "benchmark"))

import app.main as m
from run_benchmark import count_no_citation_claims

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
        print(f"FAIL: {msg}")
    else:
        print(f"OK:   {msg}")


class FakeTool:
    def __init__(self, fn):
        self._fn = fn

    def invoke(self, arg):
        return self._fn(arg)


def success_page(url):
    return f"[FETCH STATUS: SUCCESS]\nURL: {url}\nTITLE: t\nCONTENT:\ncontent for {url}"


def failed_page(url):
    return f"[FETCH STATUS: FAILED]\nURL: {url}\nREASON: HTTP 403"


class Patcher:
    def __init__(self, **ov):
        self.ov = ov
        self.saved = {}

    def __enter__(self):
        for k, v in self.ov.items():
            self.saved[k] = getattr(m, k)
            setattr(m, k, v)
        return self

    def __exit__(self, *a):
        for k, v in self.saved.items():
            setattr(m, k, v)


def claim(source_url):
    return {"original_claim": "Some factual claim.", "evidence": "snippet",
            "source_url": source_url, "source_title": "T",
            "evidence_type": "search_snippet", "verification_status": "unverified"}


def run(claims, fetch_map, search_output, audit_fn):
    fake_fetch = FakeTool(lambda a: fetch_map.get(a["url"], failed_page(a["url"])))
    fake_search = FakeTool(lambda a: search_output)
    def fake_audit(original_claim, evidence_url, fetched_data, is_fallback):
        return audit_fn(original_claim, evidence_url, fetched_data, is_fallback)
    with Patcher(fetch_page=fake_fetch, web_search=fake_search,
                 _build_fallback_query=lambda oc: "neutral query",
                 _audit_fetched_evidence=fake_audit):
        return m.auditor({"claims": claims})["audit_results"]


# 1. Claim WITH a citation -> citation_status = "present"
ar = run([claim("https://cited-ok.com")],
         {"https://cited-ok.com": success_page("https://cited-ok.com")},
         "URL: https://x.com\n",
         lambda oc, url, data, fb: ("supported", "audited_supported", "r", "\"q\""))
check(ar[0]["citation_status"] == "present", "cited claim -> citation_status = present")
check(ar[0]["verdict"] == "supported" and ar[0]["verification_status"] == "audited_supported",
      "cited-claim verdict logic unchanged (supported -> audited_supported)")

# 2. Claim with EMPTY citation -> citation_status = "missing", flagged
ar = run([claim("")],
         {"https://alt.com": failed_page("https://alt.com")},
         "URL: https://alt.com\n",
         lambda oc, url, data, fb: ("supported", "audited_supported", "r", "q"))
check(ar[0]["citation_status"] == "missing", "empty-citation claim -> citation_status = missing")

# 3. Claim with "No URL" sentinel -> citation_status = "missing"
ar = run([claim("No URL")],
         {},
         "No results found.",
         lambda oc, url, data, fb: ("supported", "audited_supported", "r", "q"))
check(ar[0]["citation_status"] == "missing", "'No URL' sentinel -> citation_status = missing")

# 4. Missing citation does NOT change verdict semantics: if fallback finds and audits
#    evidence as contradicted, the verdict is still contradicted (flag is orthogonal).
ar = run([claim("")],
         {"https://alt.com": success_page("https://alt.com")},
         "URL: https://alt.com\n",
         lambda oc, url, data, fb: ("contradicted", "audited_contradicted", "r", "q"))
check(ar[0]["citation_status"] == "missing", "missing-citation + fallback: still flagged missing")
check(ar[0]["verdict"] == "contradicted" and ar[0]["verification_status"] == "audited_contradicted",
      "missing-citation claim keeps normal verdict mapping (contradicted) - flag is orthogonal")

# 5. Aggregation
records = [
    {"citation_status": "present"}, {"citation_status": "missing"},
    {"citation_status": "missing"}, {},  # old-shape record, no field
]
agg = count_no_citation_claims(records)
check(agg == {"auditor_no_citation_claims": 2}, "count_no_citation_claims counts only 'missing' (backward compatible)")
check(count_no_citation_claims([]) == {"auditor_no_citation_claims": 0}, "empty audit_results -> 0 no-citation claims")

print("\n" + "=" * 60)
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print("  - " + f)
    sys.exit(1)
else:
    print("ALL CHECKS PASSED")
