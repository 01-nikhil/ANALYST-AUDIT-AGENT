"""
Headless test of the real Streamlit page (ui/streamlit_app.py) using Streamlit's
AppTest. The page is started and driven exactly as a user would (click "Run
Research"); only the bridge to the research backend (ui.runner) is replaced with a
fake that streams realistic node updates, so no API calls are made.

Verifies: the UI starts, a request reaches the backend bridge with the typed
question, verdicts render, memory renders, and failures render (never as success).

Run with:
    python ui/test_ui_app.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from streamlit.testing.v1 import AppTest

from ui import render as rd
from ui import runner
from ui import sample_updates as su

APP = os.path.join(ROOT, "ui", "streamlit_app.py")
failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
        print("FAIL: " + msg)
    else:
        print("OK:   " + msg)


MEM = {"lessons": ["Lesson %d about verifying sources" % i for i in range(1, 9)], "count": 8, "cap": 8, "injected": 8}
ORIG = {k: getattr(runner, k) for k in ("stream_research", "missing_api_keys", "get_memory_view", "get_evidence_label")}
calls = []


def install(updates=None, exc_after=None, keys=None):
    """Replace the research bridge with a fake. `exc_after` raises after N updates."""
    def fake_stream(question):
        calls.append(question)
        for i, item in enumerate(updates or []):
            if exc_after is not None and i == exc_after:
                raise RuntimeError("backend exploded")
            yield item
        if exc_after is not None and exc_after >= len(updates or []):
            raise RuntimeError("backend exploded")
    runner.stream_research = fake_stream
    runner.missing_api_keys = lambda: list(keys or [])
    runner.get_memory_view = lambda: dict(MEM)
    runner.get_evidence_label = lambda s: "LABEL[%s]" % s


def restore():
    for k, v in ORIG.items():
        setattr(runner, k, v)


def page_text(at):
    return "\n".join(m.value for m in at.markdown)


def click_run(at):
    [b for b in at.button if b.label == "Run Research"][0].click()
    return at.run()


try:
    # 1. The UI starts and shows the initial state.
    install()
    at = AppTest.from_file(APP, default_timeout=30).run()
    check(not at.exception, "UI starts without an exception")
    txt = page_text(at)
    check("Analyst" in txt and "Evidence-driven research with independent verification" in txt, "header: name and subtitle")
    check("System Ready" in txt, "header: 'System Ready' status")
    check(at.text_area[0].value == rd.DEMO_QUESTION, "input: default demo question is pre-filled")
    check(at.text_area[0].placeholder == rd.PLACEHOLDER, "input: placeholder example shown")
    check(any(b.label == "Run Research" for b in at.button), "input: 'Run Research' button present")
    check("Analyst agent" in txt and "Auditor agent" in txt and "Feedback loop" in txt, "workflow: two agents and feedback loop visible before any run")
    check("8 lessons retained" in txt and "Lessons injected into Analyst: 8" in txt, "memory panel renders on load")
    check("Lesson 1 about verifying sources" in txt, "memory panel lists the stored lessons")
    check("Questions completed" in txt and "Memory size" in txt, "metrics section renders")

    # 2. A request reaches the backend bridge and a CONTRADICTED verdict renders.
    calls.clear()
    install(su.contradicted_run())
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.text_area[0].set_value("Was OpenAI fully acquired by Microsoft in 2019?")
    at = click_run(at)
    check(not at.exception, "run completes without an exception")
    check(calls == ["Was OpenAI fully acquired by Microsoft in 2019?"], "the typed question reached the backend bridge exactly once")
    txt = page_text(at)
    check("CONTRADICTED" in txt, "verdict: CONTRADICTED renders")
    check("LABEL[audited_contradicted]" in txt, "final answer uses the system's evidence label")
    check("https://example.com/microsoft-openai-2019" in txt, "final answer shows the source link")
    check("Tavily search" in txt and "Query: Microsoft OpenAI 2019" in txt, "live trace shows the search and its query")
    check("Opened cited source" in txt, "live trace shows the Auditor opening the cited source")
    check("1 lesson(s) added to memory" in txt, "live trace shows feedback adding a lesson")
    check("Final answer composed" in txt, "live trace ends with the final answer")
    check("Full composed answer" in [e.label for e in at.expander], "composed answer available in an expander")
    check("Lessons injected into Analyst: 8" in txt, "memory panel still shows injected count after a run")
    check("Questions completed" in txt and 'data-k="done"><div class="v">1<' in txt, "metrics: 1 question completed")
    check('data-k="contradicted"><div class="v">1<' in txt, "metrics: 1 contradicted from the real audit result")
    check('data-status="completed"' in txt and 'data-status="running"' not in txt, "workflow: all stages completed, none left running")

    # 3. UNSUPPORTED + unverifiable source.
    install(su.unsupported_fetch_failed_run())
    at = AppTest.from_file(APP, default_timeout=30).run()
    at = click_run(at)
    txt = page_text(at)
    check("UNSUPPORTED" in txt, "verdict: UNSUPPORTED renders")
    check("Source could not be independently verified" in txt, "failure: 'Source could not be independently verified' renders")
    check("Independent fallback search" in txt, "trace: Auditor fallback search shown")
    check("[FETCH STATUS: FAILED]" not in txt, "failure marker is not presented as evidence")
    check('data-k="unsupported"><div class="v">1<' in txt, "metrics: 1 unsupported")

    # 4. Search failure.
    install(su.search_failed_run())
    at = AppTest.from_file(APP, default_timeout=30).run()
    at = click_run(at)
    txt = page_text(at)
    check("Search failed" in txt, "failure: 'Search failed' renders")
    check('data-status="failed"' in txt, "workflow: a stage is shown as failed")

    # 5. Backend exception is shown as a failed run.
    install(su.contradicted_run(), exc_after=2)
    at = AppTest.from_file(APP, default_timeout=30).run()
    at = click_run(at)
    txt = page_text(at)
    check(not at.exception, "a backend exception does not crash the page")
    check("Research failed" in txt and "backend exploded" in txt, "failure: backend error is surfaced")
    check("Run failed" in txt, "header: shows 'Run failed'")
    check('data-k="done"><div class="v">0<' in txt, "metrics: a failed run is not counted as completed")

    # 6. Missing API keys are reported, and the backend is not called.
    calls.clear()
    install(su.contradicted_run(), keys=["TAVILY_API_KEY"])
    at = AppTest.from_file(APP, default_timeout=30).run()
    check(any("missing TAVILY_API_KEY" in w.value for w in at.warning), "setup warning names the missing key")
    at = click_run(at)
    check(any("Cannot run" in e.value for e in at.error) and calls == [], "run is refused and the backend is never called")

    # 7. Empty question.
    install(su.contradicted_run())
    calls.clear()
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.text_area[0].set_value("   ")
    at = click_run(at)
    check(any("Enter a research question" in w.value for w in at.warning) and calls == [], "empty question is rejected without calling the backend")

    # 8. The UI never writes benchmark result files.
    results_dir = os.path.join(ROOT, "benchmark", "results")
    before = sorted(os.listdir(results_dir))
    install(su.contradicted_run())
    at = AppTest.from_file(APP, default_timeout=30).run()
    click_run(at)
    check(sorted(os.listdir(results_dir)) == before, "running the UI does not create or change benchmark result files")
finally:
    restore()

print("\n" + "=" * 60)
if failures:
    print("%d FAILURE(S):" % len(failures))
    for f in failures:
        print("  - " + f)
    sys.exit(1)
print("ALL CHECKS PASSED")
