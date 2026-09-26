"""
Integration test: a research request from the UI bridge (ui.runner.stream_research)
reaches the REAL, unmodified compiled graph in app.main and streams real node updates
back. Only the leaves that would cost money or need the network are faked (the LLMs,
Tavily, and page fetching); every graph node, the ToolNode, edges, the auditor's
control flow, the feedback/memory writer and compose_final_answer all run for real.

Also proves the run does not touch the real research_memory.json or create benchmark
result files (memory writes go to a temp file).

Run with:
    python ui/test_ui_integration.py
"""
import hashlib
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolCall

import app.main as m
from ui import events as ev
from ui import render as rd
from ui import runner

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
        print("FAIL: " + msg)
    else:
        print("OK:   " + msg)


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


REAL_MEMORY = m.MEMORY_FILE_PATH
RESULTS_DIR = os.path.join(ROOT, "benchmark", "results")
mem_hash_before = sha(REAL_MEMORY)
results_before = sorted(os.listdir(RESULTS_DIR))

QUESTION = "Was OpenAI fully acquired by Microsoft for $1 billion in 2019?"
SRC = "https://example.com/openai-microsoft"


class FakeTavily:
    fail = False

    def __init__(self, api_key=None):
        pass

    def search(self, query=None):
        if FakeTavily.fail:
            raise RuntimeError("tavily down")
        return {"results": [{"title": "Microsoft invests $1B in OpenAI", "url": SRC, "content": "partnership"}]}


class FakeFetch:
    def invoke(self, arg):
        return f"[FETCH STATUS: SUCCESS]\nURL: {arg['url']}\nTITLE: Example\nCONTENT:\nMicrosoft invested $1 billion in OpenAI as a partner."


class Structured:
    def __init__(self, obj):
        self.obj = obj

    def invoke(self, messages):
        return self.obj


class FakeLLM:
    """Stands in for app.main.llm: with_structured_output(model) returns a canned, real pydantic object."""
    def __init__(self, claims):
        self.claims = claims

    def with_structured_output(self, model):
        if model is m.ClaimsExtraction:
            return Structured(m.ClaimsExtraction(claims=self.claims))
        if model is m.FeedbackOutput:
            return Structured(m.FeedbackOutput(lessons=[m.ResearchLesson(
                lesson="Distinguish investment from acquisition.", reason="audit")]))
        if model is m.AuditRecord:
            return Structured(m.AuditRecord(
                original_claim="x", source_url=SRC, evidence_type="search_snippet", verification_status="audited_contradicted",
                verdict="contradicted", reasoning="The page describes a partnership investment, not an acquisition.",
                supporting_evidence="Microsoft invested $1 billion in OpenAI as a partner."))
        raise AssertionError("unexpected structured model " + str(model))


def run_real_graph(claims, tavily_fails=False):
    """Run the real agent through ui.runner with only network/LLM leaves faked."""
    tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump([{"lesson": "Existing principle", "reason": "r", "source_of_feedback": "curated"}], tmp)
    tmp.close()
    saved = {k: getattr(m, k) for k in ("MEMORY_FILE_PATH", "llm_with_tools", "llm", "TavilyClient", "fetch_page")}
    saved_env = os.environ.get("TAVILY_API_KEY")
    try:
        os.environ["TAVILY_API_KEY"] = "test-key"
        FakeTavily.fail = tavily_fails
        m.MEMORY_FILE_PATH = tmp.name
        m.llm_with_tools = FakeMessagesListChatModel(responses=[
            AIMessage(content="", tool_calls=[ToolCall(name="web_search", args={"query": "Microsoft OpenAI 2019 investment"}, id="c1")]),
            AIMessage(content="Draft answer before the audit."),
        ])
        m.llm = FakeLLM(claims)
        m.TavilyClient = FakeTavily
        m.fetch_page = FakeFetch()
        updates = list(runner.stream_research(QUESTION))
        memory_after = json.load(open(tmp.name, encoding="utf-8"))
        return updates, memory_after
    finally:
        for k, v in saved.items():
            setattr(m, k, v)
        if saved_env is None:
            os.environ.pop("TAVILY_API_KEY", None)
        else:
            os.environ["TAVILY_API_KEY"] = saved_env
        os.unlink(tmp.name)


claim = m.Claim(original_claim=QUESTION, evidence="snippet", source_url=SRC, source_title="Example",
                evidence_type="search_snippet", verification_status="unverified")

# --- 1. Real graph, happy path ----------------------------------------------
updates, memory_after = run_real_graph([claim])
nodes = [n for n, _ in updates]
check(nodes == ["chatbot", "tools", "chatbot", "extract_claims", "auditor", "feedback", "compose_final_answer"],
      "UI bridge streamed the real graph nodes in order: %s" % nodes)

run = ev.RunState(QUESTION)
for n, u in updates:
    run.apply(n, u)
t = [e["title"] for e in run.events]
check(run.finished and run.error is None, "run finished with no error")
check("Tavily search" in t and "1 result(s) returned" in t, "real ToolNode/web_search output produced the search + results events")
check(any("Query: Microsoft OpenAI 2019 investment" in e["detail"] for e in run.events), "real tool-call query appears in the trace")
check("Opened cited source" in t, "real auditor opened the cited source")
verdict = [e for e in run.events if e["kind"] == "verdict"]
check(len(verdict) == 1 and verdict[0]["title"] == "CONTRADICTED", "real auditor verdict flows through as CONTRADICTED")
check(run.audit_results[0]["citation_status"] == "present" and run.audit_results[0]["original_fetch_status"] == "success",
      "real audit record carries citation_status and fetch provenance")
check("1 lesson(s) added to memory" in t, "real feedback node reported an added lesson")
check(any(l["lesson"] == "Distinguish investment from acquisition." for l in memory_after), "lesson was persisted by the real memory writer (to the temp file)")
check("Final Verified Answer" in run.final_answer and "CONTRADICTED" in run.final_answer, "real compose_final_answer output is used as the final answer")
check(all(s == "completed" for s in run.stage_statuses().values()), "all workflow stages completed from real events")
html = rd.final_html(run, runner.get_evidence_label)
check("CONTRADICTED" in html and "Auditor-verified evidence (CONTRADICTED" in html, "rendered card uses the system's own deterministic evidence label")

# --- 2. Real graph, search failure ------------------------------------------
updates, _ = run_real_graph([], tavily_fails=True)
run = ev.RunState(QUESTION)
for n, u in updates:
    run.apply(n, u)
check("Search failed" in [e["title"] for e in run.events], "real web_search error is surfaced as 'Search failed'")
check(run.stage_statuses()["search"] == "failed", "search stage is failed, not completed")
check("No claims extracted" in [e["title"] for e in run.events], "no claims extracted is reported honestly")
check(run.finished, "graph still ran to completion")

# --- 3. Isolation guarantees --------------------------------------------------
check(sha(REAL_MEMORY) == mem_hash_before, "the real app/research_memory.json was not modified by the test")
check(sorted(os.listdir(RESULTS_DIR)) == results_before, "no benchmark result file was created or changed")
check(runner.get_memory_view()["cap"] == m.MEMORY_MAX_LESSONS and runner.get_memory_view()["injected"] <= m.MEMORY_MAX_LESSONS,
      "memory view reports the real cap and a capped injected count")

print("\n" + "=" * 60)
if failures:
    print("%d FAILURE(S):" % len(failures))
    for f in failures:
        print("  - " + f)
    sys.exit(1)
print("ALL CHECKS PASSED")
