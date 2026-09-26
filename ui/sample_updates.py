"""
Test fixtures for the demo UI: realistic (node, update) sequences shaped exactly like
what the existing graph streams with stream_mode="updates" (real LangChain message
objects and the Auditor's actual audit-record fields). Used only by the UI tests.
"""
from langchain_core.messages import AIMessage, ToolMessage

DEMO_CLAIM = ("Confirm: In 2019, Microsoft fully acquired OpenAI for $1 billion, "
              "making OpenAI a wholly-owned Microsoft subsidiary.")
SRC = "https://example.com/microsoft-openai-2019"

SEARCH_OK = ("Title: Microsoft invests $1 billion in OpenAI\nURL: https://example.com/a\nContent: partnership\n\n"
             "Title: OpenAI partnership overview\nURL: https://example.com/b\nContent: investment\n")


def audit_record(verdict="contradicted", **over):
    rec = {
        "original_claim": DEMO_CLAIM, "source_url": SRC, "evidence_type": "search_snippet",
        "verification_status": {"supported": "audited_supported", "contradicted": "audited_contradicted"}.get(verdict, "failed"),
        "verdict": verdict, "reasoning": "The fetched page describes a $1B partnership, not an acquisition.",
        "supporting_evidence": '"Microsoft invests $1 billion in OpenAI"',
        "original_source_url": SRC, "citation_status": "present", "original_fetch_status": "success",
        "fallback_search_performed": False, "fallback_search_query": None, "fallback_source_url": None,
        "fallback_fetch_status": None, "final_evidence_source": "original_cited_source", "final_verdict": verdict,
    }
    rec.update(over)
    return rec


def claim_record(status="audited_contradicted", **over):
    c = {"original_claim": DEMO_CLAIM, "evidence": "snippet", "source_url": SRC, "source_title": "Example",
         "evidence_type": "search_snippet", "verification_status": status}
    c.update(over)
    return c


def _search_turn(query="Microsoft OpenAI 2019 $1 billion investment"):
    return ("chatbot", {"messages": [AIMessage(
        content="I will search for the 2019 investment details.",
        tool_calls=[{"name": "web_search", "args": {"query": query}, "id": "c1"}])]})


def _search_result(content=SEARCH_OK):
    return ("tools", {"messages": [ToolMessage(content=content, tool_call_id="c1", name="web_search")]})


def _tail(audit, claim, lessons, final_text):
    return [
        ("chatbot", {"messages": [AIMessage(content="Draft answer before the audit.")]}),
        ("extract_claims", {"claims": [claim_record("unverified")] if claim else []}),
        ("auditor", {"audit_results": [audit] if audit else [], "claims": [claim] if claim else []}),
        ("feedback", {"feedback_lessons": lessons}),
        ("compose_final_answer", {"messages": [AIMessage(content=final_text)]}),
    ]


LESSON = {"lesson": "Verify the nature of a claim, not just its numbers.", "reason": "r", "source_of_feedback": "auditor"}


def contradicted_run():
    return [_search_turn(), _search_result()] + _tail(
        audit_record("contradicted"), claim_record("audited_contradicted"), [LESSON],
        "## Final Verified Answer\n\n**Claim 1:** demo\n- Evidence status: **Auditor-verified evidence (CONTRADICTED by the cited source)**")


def unsupported_fetch_failed_run():
    """Cited source could not be fetched; fallback search found nothing usable."""
    audit = audit_record(
        "unsupported", original_fetch_status="failed", final_evidence_source="none",
        supporting_evidence="[FETCH STATUS: FAILED]\nURL: x\nREASON: HTTP 403",
        reasoning="Independent page fetch failed for the cited URL.",
        fallback_search_performed=True, fallback_search_query="Microsoft OpenAI 2019 investment",
        fallback_source_url=None, fallback_fetch_status="no_results")
    return [_search_turn(), _search_result()] + _tail(
        audit, claim_record("failed"), [], "## Final Verified Answer\n\nunverified")


def fallback_supported_run():
    audit = audit_record(
        "supported", original_fetch_status="failed", final_evidence_source="fallback_source",
        reasoning="The fallback page confirms the claim.", supporting_evidence='"quote from fallback"',
        fallback_search_performed=True, fallback_search_query="neutral query about the claim subject",
        fallback_source_url="https://example.com/fallback", fallback_fetch_status="success")
    return [_search_turn(), _search_result()] + _tail(
        audit, claim_record("audited_supported"), [], "## Final Verified Answer\n\nsupported via fallback")


def search_failed_run():
    return [_search_turn(), _search_result("Error during web search: boom")] + _tail(
        None, None, [], "## Final Verified Answer\n\nNo claims requiring verification.")


def no_citation_run():
    audit = audit_record("unsupported", citation_status="missing", source_url="", original_source_url="",
                         original_fetch_status="failed", final_evidence_source="none",
                         supporting_evidence="[FETCH STATUS: FAILED]\nURL: None", fallback_fetch_status="no_results",
                         fallback_search_performed=True, fallback_search_query="q")
    return [_search_turn(), _search_result()] + _tail(
        audit, claim_record("failed", source_url=""), [], "## Final Verified Answer\n\nno citation")
