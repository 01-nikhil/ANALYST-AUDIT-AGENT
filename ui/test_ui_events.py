"""
Offline unit tests for the demo UI's event logic (ui.events) and HTML rendering
(ui.render). No API calls, no Streamlit.

Run with:
    python ui/test_ui_events.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from langchain_core.messages import AIMessage, ToolMessage

from ui import events as ev
from ui import render as rd
from ui import sample_updates as su

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
        print("FAIL: " + msg)
    else:
        print("OK:   " + msg)


def play(updates):
    run = ev.RunState("q")
    for node, upd in updates:
        run.apply(node, upd)
    return run


def titles(run):
    return [e["title"] for e in run.events]


# --- hidden reasoning never reaches the UI ---------------------------------
blocks = [{"type": "thinking", "thinking": "SECRET-CHAIN-OF-THOUGHT", "signature": "SIG"},
          {"type": "text", "text": "Visible plan"}]
check(ev.visible_text(blocks) == "Visible plan", "visible_text keeps text blocks and drops thinking blocks")
r = ev.RunState("q")
r.apply("chatbot", {"messages": [AIMessage(content=blocks, tool_calls=[
    {"name": "web_search", "args": {"query": "x"}, "id": "1"}])]})
check("SECRET-CHAIN-OF-THOUGHT" not in str(r.events) and "SIG" not in str(r.events),
      "thinking text/signature never appear in trace events")

# --- readable previews + honest mid-run state --------------------------------
md = ev.RunState("q")
md.apply("chatbot", {"messages": [AIMessage(content="### Confirmation Status: **False** `x`")]})
check("###" not in md.events[0]["detail"] and "**" not in md.events[0]["detail"] and "`" not in md.events[0]["detail"]
      and "Confirmation Status: False" in md.events[0]["detail"], "draft preview has markdown markers stripped but keeps the text")
mid = ev.RunState("q")
mid.apply("extract_claims", {"claims": [su.claim_record("unverified")]})
mid_html = rd.final_html(mid)
check("AWAITING AUDIT" in mid_html and "UNAUDITED" not in mid_html, "claim shows 'AWAITING AUDIT' (not UNAUDITED) while the Auditor is still working")
done_no_audit = ev.RunState("q")
done_no_audit.apply("extract_claims", {"claims": [su.claim_record("unverified")]})
done_no_audit.finished = True
check("UNAUDITED" in rd.final_html(done_no_audit) and "AWAITING AUDIT" not in rd.final_html(done_no_audit),
      "a finished run with no audit record is shown as UNAUDITED, never as awaiting")

# --- full contradicted run -------------------------------------------------
run = play(su.contradicted_run())
t = titles(run)
check("Planning research approach" in t, "trace: analyst plan event")
check("Tavily search" in t and any("Query: Microsoft OpenAI 2019" in e["detail"] for e in run.events),
      "trace: search event shows the real query")
check("2 result(s) returned" in t, "trace: search results counted from the real tool output")
check("1 claim(s) extracted" in t, "trace: claims extracted")
check("Opened cited source" in t, "trace: auditor opened cited source")
verdicts = [e for e in run.events if e["kind"] == "verdict"]
check(len(verdicts) == 1 and verdicts[0]["title"] == "CONTRADICTED", "trace: verdict CONTRADICTED")
check("1 lesson(s) added to memory" in t, "trace: feedback lesson added")
check(run.finished and run.running is None, "run finished, nothing running")
st = run.stage_statuses()
check(all(st[k] == "completed" for k in ev.STAGES), "all 7 stages completed on a successful run")
check(run.verdict_counts() == {"supported": 0, "unsupported": 0, "contradicted": 1}, "verdict counts from real audit results")

# --- stage progression ------------------------------------------------------
p = ev.RunState("q")
check(p.stage_statuses()["analyst"] == "running", "start: analyst is running")
p.apply("chatbot", su.contradicted_run()[0][1])
check(p.stage_statuses()["search"] == "running", "after tool call: search is running")
p.apply("tools", su.contradicted_run()[1][1])
check(p.stage_statuses()["search"] == "completed" and p.stage_statuses()["analyst"] == "running",
      "after tool result: search completed, analyst running again")
p.apply("chatbot", {"messages": [AIMessage(content="draft")]})
check(p.running == "evidence", "after draft: evidence is next")
p.apply("extract_claims", {"claims": [su.claim_record("unverified")]})
check(p.stage_statuses()["auditor"] == "running", "after claims: auditor running")
p.apply("auditor", {"audit_results": [su.audit_record()], "claims": [su.claim_record()]})
check(p.stage_statuses()["auditor"] == "completed" and p.stage_statuses()["feedback"] == "running", "after audit: feedback running")

# --- failures are never turned into successes -------------------------------
run = play(su.search_failed_run())
check("Search failed" in titles(run), "search failure shows 'Search failed'")
check(any(e["title"] == "Search failed" and e["status"] == "failed" for e in run.events), "search failure event has failed status")
check(run.stage_statuses()["search"] == "failed", "search stage is failed, not completed")
check("No claims extracted" in titles(run) and run.stage_statuses()["evidence"] == "failed", "no claims -> evidence stage failed")

f = ev.RunState("q")
f.apply("tools", {"messages": [ToolMessage(content="[FETCH STATUS: FAILED]\nURL: u\nREASON: HTTP 403", tool_call_id="1", name="fetch_page")]})
check(f.events[0]["title"] == "Source could not be independently verified" and f.events[0]["status"] == "failed",
      "analyst fetch failure shows 'Source could not be independently verified'")
ok = ev.RunState("q")
ok.apply("tools", {"messages": [ToolMessage(content="[FETCH STATUS: SUCCESS]\nURL: u\nTITLE: Annual Report\nCONTENT: x", tool_call_id="1", name="fetch_page")]})
check(ok.events[0]["title"] == "Source fetched" and "Annual Report" in ok.events[0]["detail"], "successful fetch shows title")

run = play(su.unsupported_fetch_failed_run())
v = [e for e in run.events if e["kind"] == "verdict"][0]
check(v["title"] == "UNSUPPORTED", "unsupported verdict shows UNSUPPORTED")
check("Source could not be independently verified" in v["detail"], "unverified source is stated in the verdict detail")
check("Cited source could not be fetched" in titles(run) and "Independent fallback search" in titles(run)
      and "Fallback search returned no usable source" in titles(run), "auditor fetch failure + fallback search + no result are all shown")

run = play(su.fallback_supported_run())
check("Fallback source fetched" in titles(run), "fallback fetch success shown")
check([e for e in run.events if e["kind"] == "verdict"][0]["title"] == "SUPPORTED", "fallback-supported verdict shown as SUPPORTED")

run = play(su.no_citation_run())
check("NO CITATION" in titles(run), "NO CITATION flagged in the trace")

# --- run-level error --------------------------------------------------------
e = ev.RunState("q")
e.apply("chatbot", su.contradicted_run()[0][1])
e.fail("RuntimeError: boom")
check(e.error and e.events[-1]["title"] == "Research failed" and e.events[-1]["status"] == "failed", "run error recorded as failed event")
check(e.stage_statuses()["search"] == "failed" and not e.finished, "error marks the running stage failed and run unfinished")

# --- rendering --------------------------------------------------------------
run = play(su.contradicted_run())
html_final = rd.final_html(run, lambda s: "LABEL:" + s)
check("CONTRADICTED" in html_final and 'data-verdict="contradicted"' in html_final, "final panel: CONTRADICTED badge")
check('href="https://example.com/microsoft-openai-2019"' in html_final and "noopener" in html_final, "final panel: source rendered as safe link")
check("LABEL:audited_contradicted" in html_final, "final panel: uses the system's evidence label")
check("$1 billion" in html_final and "partnership" in html_final, "final panel: claim, reasoning and evidence shown")
check("SUPPORTED" in rd.final_html(play(su.fallback_supported_run())), "final panel: SUPPORTED badge")
uns = rd.final_html(play(su.unsupported_fetch_failed_run()))
check("UNSUPPORTED" in uns and 'data-verdict="unsupported"' in uns, "final panel: UNSUPPORTED badge")
check("Source could not be independently verified" in uns, "final panel: unverified source stated")
check("[FETCH STATUS: FAILED]" not in uns, "final panel: failed-fetch marker is never shown as evidence")
check("NO CITATION" in rd.final_html(play(su.no_citation_run())), "final panel: NO CITATION shown")
check("RESEARCH FAILED" in rd.final_html(e), "final panel: run failure rendered")

# escaping / unsafe links
evil = ev.RunState("q")
evil.claims = [{"original_claim": "<script>alert(1)</script>", "source_url": "javascript:alert(1)", "verification_status": "failed"}]
evil.audit_results = []
h = rd.final_html(evil)
check("<script>" not in h and "&lt;script&gt;" in h, "dynamic text is HTML-escaped")
check('href="javascript' not in h, "non-http(s) URLs are never rendered as links")

# workflow strip communicates two agents and a feedback loop
flow = rd.flow_html(play(su.contradicted_run()).stage_statuses())
check("Analyst agent" in flow and "Auditor agent" in flow and "Feedback loop" in flow, "flow: two agents + feedback loop are visible")
for label in ("Question", "Search", "Evidence", "Final answer"):
    check(label in flow, "flow: stage '%s' present" % label)
check('data-status="running"' in rd.flow_html(ev.RunState("q").stage_statuses()), "flow: running stage is marked")

# memory + metrics
view = {"lessons": ["A lesson", "B lesson"] + ["x"] * 6, "count": 8, "cap": 8, "injected": 8}
m = rd.memory_html(view, new_lessons=["A lesson"])
check("8 lessons retained" in m and "Lessons injected into Analyst: 8" in m, "memory: count and injected count rendered")
check("A lesson" in m and m.count('class="aa-new"') == 1, "memory: lesson text rendered and exactly the one new lesson flagged NEW")
check("Reason" not in m, "memory: only concise lesson text is shown")
check("1 lesson retained" in rd.memory_html({"lessons": ["a"], "count": 1, "cap": 8, "injected": 1}), "memory: singular wording")
hist = [rd.run_summary(play(su.contradicted_run())), rd.run_summary(play(su.unsupported_fetch_failed_run()))]
mh = rd.metrics_html(hist, 8)
check(hist[0]["contradicted"] == 1 and hist[1]["unsupported"] == 1 and sum(h["claims"] for h in hist) == 2,
      "metrics: run summaries come from real audit results")
check('data-k="done"><div class="v">2<' in mh and 'data-k="contradicted"><div class="v">1<' in mh and 'data-k="mem"><div class="v">8<' in mh,
      "metrics: rendered values match the runs")
check("Questions completed" in mh and "Claims audited" in mh and "Memory size" in mh, "metrics: required labels present")

print("\n" + "=" * 60)
if failures:
    print("%d FAILURE(S):" % len(failures))
    for f_ in failures:
        print("  - " + f_)
    sys.exit(1)
print("ALL CHECKS PASSED")
