"""
Pure event/state logic for the demo UI. No Streamlit and no app.main import, so it
can be unit-tested offline.

It converts the real LangGraph `stream_mode="updates"` payloads emitted by the
existing Analyst + Auditor graph into:
  - observable trace events (plan summaries, tool names, queries, results, claims,
    verdicts, feedback) - never hidden reasoning, and never invented results;
  - workflow stage statuses (idle / running / completed / failed).

Every event is derived from what a node actually returned. Failures are surfaced as
failed events and are never turned into successes.
"""
from __future__ import annotations

import re

# --- Stage model -----------------------------------------------------------

STAGES = ["question", "analyst", "search", "evidence", "auditor", "feedback", "final"]

STAGE_LABELS = {
    "question": "Question",
    "analyst": "Analyst",
    "search": "Search",
    "evidence": "Evidence",
    "auditor": "Auditor",
    "feedback": "Feedback",
    "final": "Final answer",
}

# Which agent "owns" a stage (drives colour in the UI).
STAGE_AGENT = {
    "question": "user",
    "analyst": "analyst",
    "search": "analyst",
    "evidence": "analyst",
    "auditor": "auditor",
    "feedback": "feedback",
    "final": "system",
}

RUNNING_TEXT = {
    "analyst": "Analyst is planning and researching...",
    "search": "Analyst is searching the web...",
    "evidence": "Analyst is gathering evidence and extracting claims...",
    "auditor": "Auditor is independently verifying each claim...",
    "feedback": "Feedback is extracting lessons for memory...",
    "final": "Composing the final answer...",
}

VERDICTS = ("supported", "unsupported", "contradicted")


# --- Small helpers ---------------------------------------------------------

def visible_text(content) -> str:
    """Return only the visible text of a message content. Content may be a plain
    string or a list of structured blocks; non-text blocks (e.g. thinking) are
    dropped so no hidden reasoning can reach the UI."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, str):
                parts.append(b)
            elif isinstance(b, dict) and b.get("type") == "text":
                parts.append(b.get("text", "") or "")
        return "\n".join(p for p in parts if p)
    return ""


def shorten(text: str, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", (text or "")).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def plain(text: str) -> str:
    """Strip markdown emphasis/heading markers so previews of model text read cleanly."""
    return re.sub(r"[#*`]+", "", text or "")


def make_event(stage, kind, status, title, detail="", **meta) -> dict:
    return {
        "stage": stage,
        "agent": STAGE_AGENT.get(stage, "system"),
        "kind": kind,
        "status": status,  # ok | failed | info
        "title": title,
        "detail": detail,
        "meta": meta,
    }


def _is_search_failure(content: str) -> bool:
    c = (content or "").lstrip()
    return c.startswith("Error") or c.startswith("No results found")


def _parse_search_results(content: str) -> list:
    """Parse web_search()'s formatted output ('Title:'/'URL:' blocks) into records."""
    results = []
    title = None
    for line in (content or "").split("\n"):
        line = line.strip()
        if line.startswith("Title:"):
            title = line[len("Title:"):].strip()
        elif line.startswith("URL:"):
            url = line[len("URL:"):].strip()
            if url and url != "No URL":
                results.append({"title": title or url, "url": url})
                title = None
    return results


# --- Run state -------------------------------------------------------------

class RunState:
    """Accumulates one research run from streamed graph updates."""

    def __init__(self, question: str):
        self.question = question
        self.events: list = []
        self.claims: list = []
        self.audit_results: list = []
        self.feedback_lessons: list = []
        self.final_answer: str = ""
        self.done_nodes: set = set()
        self.running: str | None = "analyst"
        self.error: str | None = None
        self.finished = False
        self._search_called = False
        self._search_failed = False
        self._search_ok = False
        self._no_claims = False

    # -- public -----------------------------------------------------------
    def apply(self, node: str, update) -> list:
        """Apply one node's update; return the trace events it produced."""
        update = update if isinstance(update, dict) else {}
        handler = {
            "chatbot": self._on_chatbot,
            "tools": self._on_tools,
            "extract_claims": self._on_extract_claims,
            "auditor": self._on_auditor,
            "feedback": self._on_feedback,
            "compose_final_answer": self._on_final,
        }.get(node)
        new_events = handler(update) if handler else []
        self.done_nodes.add(node)
        self.events.extend(new_events)
        return new_events

    def fail(self, message: str):
        """Record an unexpected run-level failure. The currently running stage is
        marked failed; nothing is reported as a success."""
        self.error = message
        self.events.append(make_event(
            self.running or "analyst", "error", "failed", "Research failed", shorten(message, 400)))

    def stage_statuses(self) -> dict:
        s = {k: "idle" for k in STAGES}
        s["question"] = "completed"
        if self.error:
            for k in ("analyst", "search", "evidence", "auditor", "feedback", "final"):
                if k in self._completed_stages():
                    s[k] = "completed"
            if self._search_failed:
                s["search"] = "failed"
            if self.running and self.running in s:
                s[self.running] = "failed"
            return s
        for k in self._completed_stages():
            s[k] = "completed"
        if self._search_failed:
            s["search"] = "failed"
        if self._no_claims:
            s["evidence"] = "failed"
        if self.running in s and not self.finished:
            s[self.running] = "running"
        return s

    def verdict_counts(self) -> dict:
        c = {v: 0 for v in VERDICTS}
        for a in self.audit_results:
            v = a.get("verdict")
            if v in c:
                c[v] += 1
        return c

    # -- internals --------------------------------------------------------
    def _completed_stages(self) -> set:
        done = set()
        d = self.done_nodes
        if "extract_claims" in d or ("chatbot" in d and self.running not in ("analyst", "search")):
            done.add("analyst")
        if self._search_ok and not self._search_failed:
            done.add("search")
        if "extract_claims" in d and not self._no_claims:
            done.add("evidence")
        if "auditor" in d:
            done.add("auditor")
        if "feedback" in d:
            done.add("feedback")
        if "compose_final_answer" in d:
            done.add("final")
        return done

    def _on_chatbot(self, update) -> list:
        events = []
        for m in update.get("messages", []) or []:
            text = visible_text(getattr(m, "content", ""))
            calls = getattr(m, "tool_calls", None) or []
            if calls:
                names = ", ".join(str(c.get("name")) for c in calls)
                detail = shorten(plain(text), 280) if text.strip() else f"Chose tool(s): {names}"
                events.append(make_event("analyst", "plan", "ok", "Planning research approach", detail))
                for c in calls:
                    name, args = c.get("name"), c.get("args") or {}
                    if name == "web_search":
                        self._search_called = True
                        events.append(make_event("search", "search", "ok", "Tavily search",
                                                 f"Query: {args.get('query', '')}", query=args.get("query", "")))
                    elif name == "fetch_page":
                        events.append(make_event("evidence", "fetch", "ok", "Fetching page",
                                                 str(args.get("url", "")), url=args.get("url", "")))
                    else:
                        events.append(make_event("analyst", "tool", "ok", f"Tool call: {name}",
                                                 shorten(str(args), 200)))
                if any(c.get("name") == "web_search" for c in calls):
                    self.running = "search"
                elif any(c.get("name") == "fetch_page" for c in calls):
                    self.running = "evidence"
                else:
                    self.running = "analyst"
            else:
                events.append(make_event("analyst", "draft", "ok",
                                         "Analyst drafted a response (pre-audit)", shorten(plain(text), 280)))
                self.running = "evidence"
        return events

    def _on_tools(self, update) -> list:
        events = []
        for m in update.get("messages", []) or []:
            name = getattr(m, "name", None)
            content = str(getattr(m, "content", "") or "")
            errored = getattr(m, "status", None) == "error"
            if name == "web_search":
                if errored or _is_search_failure(content):
                    self._search_failed = True
                    events.append(make_event("search", "search_result", "failed", "Search failed",
                                             shorten(content, 240)))
                else:
                    results = _parse_search_results(content)
                    self._search_ok = True
                    top = "; ".join(r["title"] for r in results[:3])
                    events.append(make_event("search", "search_result", "ok",
                                             f"{len(results)} result(s) returned", shorten(top, 240),
                                             results=results))
            elif name == "fetch_page":
                if "[FETCH STATUS: SUCCESS]" in content:
                    t = re.search(r"^TITLE:\s*(.*)$", content, re.M)
                    u = re.search(r"^URL:\s*(.*)$", content, re.M)
                    events.append(make_event("evidence", "fetch_result", "ok", "Source fetched",
                                             (t.group(1).strip() if t else "") or (u.group(1).strip() if u else "")))
                else:
                    r = re.search(r"^REASON:\s*(.*)$", content, re.M)
                    events.append(make_event("evidence", "fetch_result", "failed",
                                             "Source could not be independently verified",
                                             shorten(r.group(1) if r else content, 200)))
            else:
                events.append(make_event("analyst", "tool_result", "failed" if errored else "ok",
                                         f"{name or 'tool'} result", shorten(content, 200)))
        self.running = "analyst"
        return events

    def _on_extract_claims(self, update) -> list:
        self.claims = list(update.get("claims", []) or [])
        self.running = "auditor"
        if not self.claims:
            self._no_claims = True
            return [make_event("evidence", "claims", "failed", "No claims extracted",
                               "The Analyst produced nothing the Auditor could verify.")]
        first = self.claims[0].get("original_claim", "")
        return [make_event("evidence", "claims", "ok", f"{len(self.claims)} claim(s) extracted",
                           shorten(first, 220), claims=self.claims)]

    def _on_auditor(self, update) -> list:
        self.audit_results = list(update.get("audit_results", []) or [])
        if update.get("claims"):
            self.claims = list(update["claims"])
        events = []
        for a in self.audit_results:
            url = a.get("source_url") or ""
            if a.get("citation_status") == "missing":
                events.append(make_event("auditor", "no_citation", "failed", "NO CITATION",
                                         "The Analyst provided no cited source for this claim."))
            if a.get("original_fetch_status") == "success":
                events.append(make_event("auditor", "source_opened", "ok", "Opened cited source", url))
            elif a.get("original_fetch_status") == "failed":
                events.append(make_event("auditor", "source_opened", "failed",
                                         "Cited source could not be fetched", url))
            if a.get("fallback_search_performed"):
                events.append(make_event("auditor", "fallback", "info", "Independent fallback search",
                                         f"Query: {a.get('fallback_search_query') or ''}"))
                fstat = a.get("fallback_fetch_status")
                if fstat == "success":
                    events.append(make_event("auditor", "fallback", "ok", "Fallback source fetched",
                                             a.get("fallback_source_url") or ""))
                elif fstat == "failed":
                    events.append(make_event("auditor", "fallback", "failed",
                                             "Fallback source could not be fetched",
                                             a.get("fallback_source_url") or ""))
                elif fstat == "no_results":
                    events.append(make_event("auditor", "fallback", "failed",
                                             "Fallback search returned no usable source", ""))
            verdict = a.get("verdict", "unsupported")
            detail = shorten(a.get("reasoning", ""), 260)
            if a.get("final_evidence_source") == "none" and a.get("original_fetch_status") == "failed":
                detail = "Source could not be independently verified. " + detail
            events.append(make_event("auditor", "verdict",
                                     "ok" if verdict == "supported" else "failed" if verdict == "contradicted" else "info",
                                     verdict.upper(), detail, verdict=verdict,
                                     claim=a.get("original_claim", ""), source_url=url))
        self.running = "feedback"
        return events

    def _on_feedback(self, update) -> list:
        self.feedback_lessons = list(update.get("feedback_lessons", []) or [])
        self.running = "final"
        if self.feedback_lessons:
            text = " | ".join(shorten(l.get("lesson", ""), 160) for l in self.feedback_lessons)
            return [make_event("feedback", "feedback", "ok",
                               f"{len(self.feedback_lessons)} lesson(s) added to memory", text)]
        return [make_event("feedback", "feedback", "info", "No new lesson added to memory",
                           "Nothing new to add (duplicate, at capacity, or nothing to learn).")]

    def _on_final(self, update) -> list:
        msgs = update.get("messages", []) or []
        if msgs:
            self.final_answer = visible_text(getattr(msgs[-1], "content", ""))
        self.running = None
        self.finished = True
        return [make_event("final", "final", "ok", "Final answer composed",
                           "Built deterministically from the audited evidence.")]
