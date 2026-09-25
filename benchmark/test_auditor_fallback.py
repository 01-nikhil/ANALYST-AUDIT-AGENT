"""
Fixture test for the Auditor fallback verification flow in app/main.py.

No live API calls: the I/O leaves (fetch_page, web_search) and the two
LLM-touching helpers (_build_fallback_query, _audit_fetched_evidence) are
monkeypatched on the app.main module, and auditor() is driven directly with
synthetic state. This exercises the real control-flow, provenance fields,
verification_status mapping, original-claim preservation, and record assembly.

A separate block calls the REAL _audit_fetched_evidence and _build_fallback_query
with only app.main.llm.with_structured_output patched, to assert the prompt-level
guards (requirement #6 "unrelated -> not contradicted", and the neutral fallback
query independence rule) without hitting the network.

Run with:
    python benchmark/test_auditor_fallback.py
"""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import app.main as m
from langchain_core.messages import HumanMessage

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)
        print(f"FAIL: {message}")
    else:
        print(f"OK:   {message}")


# --- Fakes ---------------------------------------------------------------

class FakeTool:
    """Stands in for a @tool object: exposes .invoke(dict) -> str."""
    def __init__(self, fn, calls):
        self._fn = fn
        self._calls = calls

    def invoke(self, arg):
        self._calls.append(arg)
        return self._fn(arg)


def success_page(url):
    return f"[FETCH STATUS: SUCCESS]\nURL: {url}\nTITLE: t\nCONTENT:\nrelevant page content for {url}"


def failed_page(url):
    return f"[FETCH STATUS: FAILED]\nURL: {url}\nREASON: HTTP 403"


class Patcher:
    """Monkeypatch app.main globals with restore-on-exit."""
    def __init__(self, **overrides):
        self.overrides = overrides
        self.saved = {}

    def __enter__(self):
        for k, v in self.overrides.items():
            self.saved[k] = getattr(m, k)
            setattr(m, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            setattr(m, k, v)


def run_auditor_with(claims, fetch_map, search_output, audit_fn, query="NEUTRAL QUERY about claim subject"):
    """Runs m.auditor() with patched leaves. Returns (audit_results, updated_claims,
    fetch_calls, search_calls, audit_calls)."""
    fetch_calls, search_calls, audit_calls = [], [], []

    fake_fetch = FakeTool(lambda a: fetch_map.get(a["url"], failed_page(a["url"])), fetch_calls)
    fake_search = FakeTool(lambda a: search_output, search_calls)

    def fake_audit(original_claim, evidence_url, fetched_data, is_fallback):
        audit_calls.append({"original_claim": original_claim, "evidence_url": evidence_url,
                            "fetched_data": fetched_data, "is_fallback": is_fallback})
        return audit_fn(original_claim, evidence_url, fetched_data, is_fallback)

    with Patcher(fetch_page=fake_fetch, web_search=fake_search,
                 _build_fallback_query=lambda oc: query, _audit_fetched_evidence=fake_audit):
        out = m.auditor({"claims": claims})
    return out["audit_results"], out["claims"], fetch_calls, search_calls, audit_calls


ORIGINAL_CLAIM = "In 2019, Microsoft fully acquired OpenAI for $1 billion, making OpenAI a wholly-owned Microsoft subsidiary."


def base_claim(source_url):
    return {"original_claim": ORIGINAL_CLAIM, "evidence": "snippet", "source_url": source_url,
            "source_title": "T", "evidence_type": "search_snippet", "verification_status": "unverified"}


# --- Scenario 1: original fetch succeeds -> NO fallback, existing behavior ---
audit_results, claims, fetches, searches, audits = run_auditor_with(
    claims=[base_claim("https://cited-ok.com")],
    fetch_map={"https://cited-ok.com": success_page("https://cited-ok.com")},
    search_output="URL: https://should-not-be-used.com\n",
    audit_fn=lambda oc, url, data, fb: ("supported", "audited_supported", "cited page verifies", "\"quote\""),
)
r = audit_results[0]
check(len(searches) == 0, "S1 original-success: fallback web_search was NOT called")
check(r["verification_status"] == "audited_supported" and r["verdict"] == "supported", "S1: verdict supported / audited_supported")
check(r["fallback_search_performed"] is False, "S1: fallback_search_performed = False")
check(r["original_fetch_status"] == "success", "S1: original_fetch_status = success")
check(r["final_evidence_source"] == "original_cited_source", "S1: final_evidence_source = original_cited_source")
check(r["fallback_source_url"] is None, "S1: fallback_source_url is None")
check(audits and audits[0]["is_fallback"] is False, "S1: audit ran with is_fallback=False")
check(claims[0]["verification_status"] == "audited_supported", "S1: claim verification_status updated")


# --- Scenario 2: original fails + fallback search/fetch succeeds -> fallback used ---
audit_results, claims, fetches, searches, audits = run_auditor_with(
    claims=[base_claim("https://blocked.com")],
    fetch_map={"https://blocked.com": failed_page("https://blocked.com"),
               "https://alt-good.com": success_page("https://alt-good.com")},
    search_output="Title: X\nURL: https://alt-good.com\nContent: c\n",
    audit_fn=lambda oc, url, data, fb: ("supported", "audited_supported", "fallback page verifies", "\"fallback quote\""),
)
r = audit_results[0]
check(len(searches) == 1, "S2 fallback: exactly ONE fallback search performed")
check(r["fallback_search_performed"] is True, "S2: fallback_search_performed = True")
check(r["original_fetch_status"] == "failed", "S2: original_fetch_status = failed")
check(r["fallback_source_url"] == "https://alt-good.com", "S2: fallback_source_url = the alternative source")
check(r["fallback_fetch_status"] == "success", "S2: fallback_fetch_status = success")
check(r["final_evidence_source"] == "fallback_source", "S2: final_evidence_source = fallback_source")
check(r["verification_status"] == "audited_supported" and r["verdict"] == "supported", "S2: verdict from fallback evidence")
check(audits and audits[-1]["is_fallback"] is True, "S2: audit ran with is_fallback=True")
check(audits[-1]["evidence_url"] == "https://alt-good.com", "S2: audit evaluated the fallback URL")
check(audits[-1]["fetched_data"].startswith("[FETCH STATUS: SUCCESS]"), "S2: audit used the fetched fallback PAGE (not a snippet)")
check(claims[0]["verification_status"] == "audited_supported", "S2: claim upgraded to audited_supported via fallback")


# --- Scenario 3: original fails + fallback fetch also fails -> UNSUPPORTED ---
audit_results, claims, fetches, searches, audits = run_auditor_with(
    claims=[base_claim("https://blocked.com")],
    fetch_map={"https://blocked.com": failed_page("https://blocked.com"),
               "https://alt-bad.com": failed_page("https://alt-bad.com")},
    search_output="URL: https://alt-bad.com\n",
    audit_fn=lambda oc, url, data, fb: ("supported", "audited_supported", "should not be called", "x"),
)
r = audit_results[0]
check(r["verdict"] == "unsupported" and r["verification_status"] == "failed", "S3: original+fallback fail -> UNSUPPORTED/failed")
check(r["fallback_fetch_status"] == "failed", "S3: fallback_fetch_status = failed")
check(r["final_evidence_source"] == "none", "S3: final_evidence_source = none")
check(len(audits) == 0, "S3: audit LLM NOT called when no page was successfully fetched")
check(str(r["supporting_evidence"]).startswith("[FETCH STATUS: FAILED]"), "S3: supporting_evidence keeps the FAILED marker (a failed fetch is never supporting evidence)")


# --- Scenario 4: fallback source UNRELATED -> must NOT be CONTRADICTED merely for being unrelated ---
audit_results, claims, fetches, searches, audits = run_auditor_with(
    claims=[base_claim("https://blocked.com")],
    fetch_map={"https://blocked.com": failed_page("https://blocked.com"),
               "https://unrelated.com": success_page("https://unrelated.com")},
    search_output="URL: https://unrelated.com\n",
    # A faithful audit of an unrelated page returns 'unsupported', not 'contradicted'.
    audit_fn=lambda oc, url, data, fb: ("unsupported", "failed", "page is unrelated to the claim", "n/a"),
)
r = audit_results[0]
check(r["verdict"] == "unsupported", "S4: unrelated fallback page -> verdict unsupported (NOT contradicted)")
check(r["verdict"] != "contradicted", "S4: unrelated fallback page is NOT marked contradicted")
check(r["fallback_fetch_status"] == "success" and r["final_evidence_source"] == "fallback_source",
      "S4: fallback fetch still recorded as a successful fetch even though verdict is unsupported")


# --- Scenario 5: fallback yields only a snippet (candidate fetch fails) -> never 'verified' ---
# Same shape as S3 but framed as the requirement-5 check: a search snippet alone,
# without a successful page fetch, cannot produce supported/contradicted.
audit_results, claims, fetches, searches, audits = run_auditor_with(
    claims=[base_claim("https://blocked.com")],
    fetch_map={"https://blocked.com": failed_page("https://blocked.com"),
               "https://snippet-only.com": failed_page("https://snippet-only.com")},
    search_output="Title: has a promising snippet\nURL: https://snippet-only.com\nContent: MS acquired OpenAI\n",
    audit_fn=lambda oc, url, data, fb: ("supported", "audited_supported", "should not be called", "x"),
)
r = audit_results[0]
check(r["verification_status"] not in ("audited_supported", "audited_contradicted"),
      "S5: fallback snippet without a successful page fetch is NEVER treated as verified evidence")
check(len(audits) == 0, "S5: audit LLM not invoked on an unfetched snippet")


# --- Scenario 6: original claim preserved verbatim across every path ---
for label, ar in [("S1", "https://cited-ok.com")]:
    pass
# Check across S1-S5 results collected above is implicit; do an explicit multi-claim run:
audit_results, claims, _, _, _ = run_auditor_with(
    claims=[base_claim("https://cited-ok.com"), base_claim("https://blocked.com")],
    fetch_map={"https://cited-ok.com": success_page("https://cited-ok.com"),
               "https://blocked.com": failed_page("https://blocked.com"),
               "https://alt-good.com": success_page("https://alt-good.com")},
    search_output="URL: https://alt-good.com\n",
    audit_fn=lambda oc, url, data, fb: ("supported", "audited_supported", "ok", "\"q\""),
)
check(all(r["original_claim"] == ORIGINAL_CLAIM for r in audit_results),
      "S6: original_claim preserved verbatim in every audit record")
check(all(c["original_claim"] == ORIGINAL_CLAIM for c in claims),
      "S6: original_claim preserved verbatim in every updated claim")


# --- Unit: _extract_urls_from_search ---
so = "Title: A\nURL: https://a.com\nContent: c\n\nTitle: B\nURL: https://b.com\nContent: c\n\nURL: https://a.com\n"
urls = m._extract_urls_from_search(so, exclude_url="https://a.com")
check(urls == ["https://b.com"], "extract_urls: excludes the failed cited URL and dedups")
urls2 = m._extract_urls_from_search(so, exclude_url=None)
check(urls2 == ["https://a.com", "https://b.com"], "extract_urls: preserves order, dedups repeats")


# --- Prompt-level guards (real helpers, only llm.with_structured_output patched) ---

class CapturingStructured:
    """Captures the prompt passed to .invoke and returns a canned object."""
    def __init__(self, canned):
        self.canned = canned
        self.captured_prompt = None

    def invoke(self, messages):
        self.captured_prompt = messages[0].content
        return self.canned


class _AR:  # minimal stand-in for an AuditRecord result
    verdict = "unsupported"
    reasoning = "r"
    supporting_evidence = "e"


class _Q:
    def __init__(self, query):
        self.query = query


class FakeLLM:
    """Stands in for m.llm; .with_structured_output(model) returns the given capturer."""
    def __init__(self, structured):
        self._structured = structured

    def with_structured_output(self, model):
        return self._structured


# Fallback audit prompt must contain the "unrelated -> not contradicted" guard.
cap = CapturingStructured(_AR())
with Patcher(llm=FakeLLM(cap)):
    m._audit_fetched_evidence(ORIGINAL_CLAIM, "https://alt.com", success_page("https://alt.com"), is_fallback=True)
p = cap.captured_prompt or ""
check("Do NOT assign 'contradicted' merely because the page is unrelated" in p,
      "prompt-guard: fallback audit prompt forbids 'contradicted' for an unrelated page (req #6)")
check("FALLBACK" in p.upper(), "prompt-guard: fallback prompt is explicitly framed as fallback evidence")
check(ORIGINAL_CLAIM in p, "prompt-guard: fallback prompt contains the original claim verbatim")

# Original-source (non-fallback) audit prompt must NOT carry the fallback framing.
cap_orig = CapturingStructured(_AR())
with Patcher(llm=FakeLLM(cap_orig)):
    m._audit_fetched_evidence(ORIGINAL_CLAIM, "https://cited.com", success_page("https://cited.com"), is_fallback=False)
po = cap_orig.captured_prompt or ""
check("CITED SOURCE URL" in po and "FALLBACK" not in po.upper(),
      "prompt-guard: original-source audit prompt is the existing cited-source prompt (no fallback framing)")

# Fallback query generation must be neutral; and the banned-word guard falls back to the raw claim.
cap_ok = CapturingStructured(_Q("Microsoft OpenAI 2019 $1 billion investment"))
with Patcher(llm=FakeLLM(cap_ok)):
    q = m._build_fallback_query(ORIGINAL_CLAIM)
gen_prompt = cap_ok.captured_prompt or ""
check("neutral" in gen_prompt.lower(), "query-independence: generation prompt demands a NEUTRAL query")
check(("true" in gen_prompt.lower()) and ("false" in gen_prompt.lower()) and ("debunk" in gen_prompt.lower()),
      "query-independence: prompt explicitly bans judgemental terms (true/false/debunked...)")
check(q == "Microsoft OpenAI 2019 $1 billion investment", "query-independence: a clean neutral query is used as-is")

# If the model leaks a judgemental term, _build_fallback_query must NOT use it.
with Patcher(llm=FakeLLM(CapturingStructured(_Q("Microsoft acquired OpenAI false")))):
    q_bad = m._build_fallback_query(ORIGINAL_CLAIM)
check(q_bad == ORIGINAL_CLAIM, "query-independence: a query containing a banned term is rejected -> falls back to raw claim subject")


print("\n" + "=" * 60)
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
else:
    print("ALL CHECKS PASSED")
