"""
HTML/CSS builders for the demo UI. Pure functions (no Streamlit import), so the
rendering can be tested offline. Every piece of dynamic text (questions, URLs, web
content, lessons) is HTML-escaped before it is placed in markup.

HTML is emitted as compact single-line strings on purpose: Streamlit's markdown
renderer ends an HTML block at a blank line and treats 4-space indentation as code.
"""
from __future__ import annotations

import html as _html

from ui.events import RUNNING_TEXT, STAGE_LABELS

DEMO_QUESTION = ("Confirm: In 2019, Microsoft fully acquired OpenAI for $1 billion, "
                 "making OpenAI a wholly-owned Microsoft subsidiary.")
PLACEHOLDER = "Was OpenAI fully acquired by Microsoft for $1 billion in 2019?"

AGENT_NAMES = {"analyst": "Analyst", "auditor": "Auditor", "feedback": "Feedback",
               "user": "You", "system": "System"}
STATUS_ICON = {"idle": "○", "running": "◔", "completed": "✓", "failed": "✕",
               "ok": "✓", "info": "•"}
STATUS_TEXT = {"idle": "idle", "running": "running", "completed": "completed", "failed": "failed"}
VERDICT_ICON = {"supported": "✓", "unsupported": "?", "contradicted": "✕", "unaudited": "–", "pending": "◔"}


def esc(value) -> str:
    return _html.escape("" if value is None else str(value), quote=True)


def safe_link(url: str) -> str:
    """Render a URL as a link only for http(s); otherwise plain escaped text."""
    u = (url or "").strip()
    if u.lower().startswith(("http://", "https://")):
        return f'<a href="{esc(u)}" target="_blank" rel="noopener noreferrer">{esc(u)}</a>'
    return esc(u) if u else "—"


CSS = """
<style>
:root{--bg:#0b0f14;--surface:#111823;--surface2:#0e141d;--border:#1f2b3b;--text:#e6edf3;--muted:#8b98a9;
--analyst:#38bdf8;--auditor:#a78bfa;--feedback:#f472b6;--system:#94a3b8;--user:#94a3b8;
--supported:#3fb950;--unsupported:#d29922;--contradicted:#f85149;}
.block-container{padding-top:2.2rem;max-width:1240px;}
.aa-header{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap;margin:0 0 20px}
.aa-title{font-size:2.1rem;font-weight:700;letter-spacing:-.02em;line-height:1.1;color:var(--text)}
.aa-title .x{color:var(--muted);font-weight:400;margin:0 .2em}
.aa-sub{color:var(--muted);margin-top:6px;font-size:1rem}
.aa-status{display:inline-flex;align-items:center;gap:8px;font-size:.8rem;color:var(--muted);border:1px solid var(--border);border-radius:999px;padding:6px 14px;background:var(--surface)}
.aa-dot{width:8px;height:8px;border-radius:50%;background:var(--supported);box-shadow:0 0 0 3px rgba(63,185,80,.16)}
.aa-dot.busy{background:var(--analyst);box-shadow:0 0 0 3px rgba(56,189,248,.18);animation:aa-pulse 1.2s ease-in-out infinite}
.aa-dot.bad{background:var(--contradicted);box-shadow:0 0 0 3px rgba(248,81,73,.18)}
.aa-section{font-size:.72rem;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);margin:22px 0 10px;font-weight:600}
.aa-card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:16px 18px;margin-bottom:12px}
.aa-flow{display:flex;align-items:stretch;gap:10px;flex-wrap:wrap}
.aa-arrow{align-self:center;color:var(--muted);font-size:1.1rem}
.aa-solo,.aa-group{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:10px 12px}
.aa-solo{display:flex;flex-direction:column;justify-content:center;min-width:92px}
.aa-group{border-top:3px solid var(--system)}
.aa-group[data-agent=analyst]{border-top-color:var(--analyst)}
.aa-group[data-agent=auditor]{border-top-color:var(--auditor)}
.aa-group[data-agent=feedback]{border-top-color:var(--feedback)}
.aa-group-h{font-size:.68rem;letter-spacing:.14em;text-transform:uppercase;font-weight:700;margin-bottom:8px}
.aa-group[data-agent=analyst] .aa-group-h{color:var(--analyst)}
.aa-group[data-agent=auditor] .aa-group-h{color:var(--auditor)}
.aa-group[data-agent=feedback] .aa-group-h{color:var(--feedback)}
.aa-group-f{font-size:.7rem;color:var(--muted);margin-top:8px}
.aa-chips{display:flex;gap:8px;flex-wrap:wrap}
.aa-chip{display:flex;flex-direction:column;gap:2px;min-width:84px;padding:8px 10px;border-radius:8px;background:var(--surface2);border:1px solid var(--border);transition:border-color .25s,background .25s}
.aa-lab{font-weight:600;font-size:.86rem;color:var(--text)}
.aa-st{font-size:.7rem;color:var(--muted)}
.aa-ic{font-size:.9rem;margin-right:6px;color:var(--muted)}
.aa-chip[data-status=running],.aa-solo[data-status=running]{border-color:var(--analyst);background:rgba(56,189,248,.07)}
.aa-chip[data-status=running] .aa-ic,.aa-solo[data-status=running] .aa-ic{color:var(--analyst);display:inline-block;animation:aa-spin 1.1s linear infinite}
.aa-chip[data-status=running] .aa-st,.aa-solo[data-status=running] .aa-st{color:var(--analyst)}
.aa-chip[data-status=completed] .aa-ic,.aa-chip[data-status=completed] .aa-st,.aa-solo[data-status=completed] .aa-ic,.aa-solo[data-status=completed] .aa-st{color:var(--supported)}
.aa-chip[data-status=failed],.aa-solo[data-status=failed]{border-color:rgba(248,81,73,.55)}
.aa-chip[data-status=failed] .aa-ic,.aa-chip[data-status=failed] .aa-st,.aa-solo[data-status=failed] .aa-ic,.aa-solo[data-status=failed] .aa-st{color:var(--contradicted)}
.aa-ev{display:flex;gap:12px;padding:10px 0;border-bottom:1px solid var(--border);animation:aa-in .3s ease-out}
.aa-ev:last-child{border-bottom:none}
.aa-ev-ic{width:22px;height:22px;flex:0 0 22px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:.75rem;font-weight:700;background:var(--surface2);border:1px solid var(--border);color:var(--muted)}
.aa-ev[data-status=ok] .aa-ev-ic{color:var(--supported);border-color:rgba(63,185,80,.5)}
.aa-ev[data-status=failed] .aa-ev-ic{color:var(--contradicted);border-color:rgba(248,81,73,.55)}
.aa-ev-body{min-width:0;flex:1}
.aa-ev-top{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.aa-tag{font-size:.64rem;letter-spacing:.12em;text-transform:uppercase;font-weight:700;padding:2px 7px;border-radius:5px;border:1px solid var(--border);color:var(--muted)}
.aa-tag[data-agent=analyst]{color:var(--analyst);border-color:rgba(56,189,248,.4)}
.aa-tag[data-agent=auditor]{color:var(--auditor);border-color:rgba(167,139,250,.4)}
.aa-tag[data-agent=feedback]{color:var(--feedback);border-color:rgba(244,114,182,.4)}
.aa-ev-title{font-weight:600;font-size:.95rem;color:var(--text)}
.aa-ev-detail{color:var(--muted);font-size:.85rem;margin-top:3px;word-break:break-word}
.aa-running{display:flex;align-items:center;gap:10px;color:var(--analyst);font-size:.88rem;padding-top:10px}
.aa-running .aa-ic{animation:aa-spin 1.1s linear infinite;display:inline-block;color:var(--analyst)}
.aa-empty{color:var(--muted);font-size:.92rem;padding:8px 0}
.aa-badge{display:inline-flex;align-items:center;gap:6px;font-weight:700;font-size:.78rem;letter-spacing:.06em;padding:3px 10px;border-radius:999px;border:1px solid var(--border)}
.aa-badge[data-verdict=supported],.aa-vb[data-verdict=supported]{color:var(--supported);border-color:rgba(63,185,80,.55);background:rgba(63,185,80,.1)}
.aa-badge[data-verdict=unsupported],.aa-vb[data-verdict=unsupported]{color:var(--unsupported);border-color:rgba(210,153,34,.55);background:rgba(210,153,34,.1)}
.aa-badge[data-verdict=contradicted],.aa-vb[data-verdict=contradicted]{color:var(--contradicted);border-color:rgba(248,81,73,.55);background:rgba(248,81,73,.1)}
.aa-vb{display:inline-block;font-weight:700;letter-spacing:.06em;font-size:.8rem;padding:2px 10px;border-radius:999px;border:1px solid var(--border)}
.aa-badge[data-verdict=pending]{color:var(--analyst);border-color:rgba(56,189,248,.45);background:rgba(56,189,248,.08)}
.aa-claim[data-verdict=pending]{border-left:3px solid var(--analyst)}
.aa-claim[data-verdict=supported]{border-left:3px solid var(--supported)}
.aa-claim[data-verdict=unsupported]{border-left:3px solid var(--unsupported)}
.aa-claim[data-verdict=contradicted]{border-left:3px solid var(--contradicted)}
.aa-claim-h{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:8px}
.aa-evlabel{color:var(--muted);font-size:.8rem}
.aa-claim-text{font-size:1.02rem;font-weight:600;margin-bottom:10px;color:var(--text)}
.aa-kv{display:flex;gap:10px;font-size:.85rem;margin-top:6px;color:var(--text)}
.aa-kv>span:first-child{flex:0 0 108px;color:var(--muted)}
.aa-kv a{color:var(--analyst);word-break:break-all}
.aa-kv>span:last-child,.aa-kv>a{min-width:0;word-break:break-word}
.aa-err{border-left:3px solid var(--contradicted)}
.aa-mem-h{display:flex;align-items:baseline;justify-content:space-between;gap:10px;flex-wrap:wrap}
.aa-mem-t{font-size:1.05rem;font-weight:700}
.aa-mem-c{color:var(--muted);font-size:.82rem}
.aa-lessons{max-height:300px;overflow-y:auto;padding-right:6px;margin-top:8px}
.aa-lesson{display:flex;gap:10px;font-size:.84rem;padding:8px 0;border-top:1px solid var(--border);color:var(--text)}
.aa-lesson .n{flex:0 0 18px;color:var(--muted)}
.aa-new{font-size:.6rem;font-weight:700;letter-spacing:.1em;color:var(--feedback);border:1px solid rgba(244,114,182,.45);border-radius:4px;padding:1px 5px;margin-left:6px;vertical-align:middle}
.aa-inject{margin-top:10px;font-size:.82rem;color:var(--auditor);border:1px solid rgba(167,139,250,.35);border-radius:8px;padding:6px 10px;display:inline-block}
.aa-metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px}
.aa-metric{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:12px 14px}
.aa-metric .v{font-size:1.5rem;font-weight:700;line-height:1.1}
.aa-metric .l{font-size:.72rem;color:var(--muted);margin-top:4px;text-transform:uppercase;letter-spacing:.08em}
.aa-metric[data-k=supported] .v{color:var(--supported)}
.aa-metric[data-k=unsupported] .v{color:var(--unsupported)}
.aa-metric[data-k=contradicted] .v{color:var(--contradicted)}
.aa-note{color:var(--muted);font-size:.75rem;margin-top:8px}
@keyframes aa-spin{to{transform:rotate(360deg)}}
@keyframes aa-pulse{0%,100%{opacity:1}50%{opacity:.35}}
@keyframes aa-in{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
@media (max-width:760px){.aa-arrow{display:none}.aa-title{font-size:1.6rem}}
@media (prefers-reduced-motion:reduce){*{animation:none!important}}
</style>
"""


def header_html(state: str = "ready") -> str:
    text, cls = {"ready": ("System Ready", ""), "running": ("Research running", " busy"),
                 "error": ("Run failed", " bad")}.get(state, ("System Ready", ""))
    return (
        '<div class="aa-header"><div>'
        '<div class="aa-title">Analyst<span class="x">×</span>Auditor</div>'
        '<div class="aa-sub">Evidence-driven research with independent verification</div></div>'
        f'<div class="aa-status"><span class="aa-dot{cls}"></span>{esc(text)}</div></div>'
    )


def _chip(stage: str, status: str, agent: str) -> str:
    return (f'<div class="aa-chip" data-status="{esc(status)}" data-agent="{esc(agent)}">'
            f'<div><span class="aa-ic">{STATUS_ICON.get(status, "○")}</span>'
            f'<span class="aa-lab">{esc(STAGE_LABELS[stage])}</span></div>'
            f'<span class="aa-st">{esc(STATUS_TEXT.get(status, status))}</span></div>')


def flow_html(statuses: dict) -> str:
    """Two agents and a feedback loop: Question -> Analyst agent -> Auditor agent ->
    Feedback loop -> Final answer, each stage with its own status."""
    q = statuses.get("question", "idle")
    solo_q = (f'<div class="aa-solo" data-status="{esc(q)}"><div><span class="aa-ic">{STATUS_ICON.get(q, "○")}</span>'
              f'<span class="aa-lab">Question</span></div><span class="aa-st">{esc(STATUS_TEXT.get(q, q))}</span></div>')
    fin = statuses.get("final", "idle")
    solo_f = (f'<div class="aa-solo" data-status="{esc(fin)}"><div><span class="aa-ic">{STATUS_ICON.get(fin, "○")}</span>'
              f'<span class="aa-lab">Final answer</span></div><span class="aa-st">{esc(STATUS_TEXT.get(fin, fin))}</span></div>')
    analyst = "".join(_chip(s, statuses.get(s, "idle"), "analyst") for s in ("analyst", "search", "evidence"))
    auditor = _chip("auditor", statuses.get("auditor", "idle"), "auditor")
    feedback = _chip("feedback", statuses.get("feedback", "idle"), "feedback")
    return (
        '<div class="aa-flow">' + solo_q + '<div class="aa-arrow">→</div>'
        '<div class="aa-group" data-agent="analyst"><div class="aa-group-h">Analyst agent</div>'
        f'<div class="aa-chips">{analyst}</div><div class="aa-group-f">plans · searches · fetches · extracts claims</div></div>'
        '<div class="aa-arrow">→</div>'
        '<div class="aa-group" data-agent="auditor"><div class="aa-group-h">Auditor agent</div>'
        f'<div class="aa-chips">{auditor}</div><div class="aa-group-f">re-opens each cited source independently</div></div>'
        '<div class="aa-arrow">→</div>'
        '<div class="aa-group" data-agent="feedback"><div class="aa-group-h">Feedback loop</div>'
        f'<div class="aa-chips">{feedback}</div><div class="aa-group-f">↺ lessons feed the next question</div></div>'
        '<div class="aa-arrow">→</div>' + solo_f + '</div>'
    )


def trace_html(events: list, running: str | None = None, error: str | None = None) -> str:
    if not events and not running:
        return '<div class="aa-card"><div class="aa-empty">Run a research question to watch the two agents work.</div></div>'
    items = []
    for e in events:
        status = e.get("status", "info")
        title = esc(e.get("title", ""))
        if e.get("kind") == "verdict":
            v = e.get("meta", {}).get("verdict", "unsupported")
            title = f'<span class="aa-vb" data-verdict="{esc(v)}">{VERDICT_ICON.get(v, "?")} {esc(v.upper())}</span>'
        detail = f'<div class="aa-ev-detail">{esc(e["detail"])}</div>' if e.get("detail") else ""
        items.append(
            f'<div class="aa-ev" data-status="{esc(status)}"><div class="aa-ev-ic">{STATUS_ICON.get(status, "•")}</div>'
            f'<div class="aa-ev-body"><div class="aa-ev-top"><span class="aa-tag" data-agent="{esc(e.get("agent", "system"))}">'
            f'{esc(AGENT_NAMES.get(e.get("agent", "system"), "System"))}</span><span class="aa-ev-title">{title}</span></div>'
            f'{detail}</div></div>')
    if running and not error:
        items.append(f'<div class="aa-running"><span class="aa-ic">◔</span>{esc(RUNNING_TEXT.get(running, "Working..."))}</div>')
    return '<div class="aa-card">' + "".join(items) + '</div>'


def _claim_cards(run, label_fn) -> str:
    audits = {(a.get("original_claim", ""), a.get("source_url", "")): a for a in run.audit_results}
    cards = []
    for c in run.claims:
        a = audits.get((c.get("original_claim", ""), c.get("source_url", "")), {})
        verdict = a.get("verdict") or ("unaudited" if (run.finished or run.error) else "pending")
        label = label_fn(c.get("verification_status", "unverified")) if label_fn else ""
        rows = [f'<div class="aa-kv"><span>Source</span>{safe_link(c.get("source_url", ""))}</div>']
        if a.get("citation_status") == "missing":
            rows.append('<div class="aa-kv"><span>Citation</span><span>NO CITATION provided by the Analyst</span></div>')
        if a.get("reasoning"):
            rows.append(f'<div class="aa-kv"><span>Auditor reasoning</span><span>{esc(a["reasoning"])}</span></div>')
        ev = a.get("supporting_evidence")
        if ev and not str(ev).startswith("[FETCH STATUS: FAILED]") and ev != "None":
            rows.append(f'<div class="aa-kv"><span>Evidence</span><span>{esc(ev)}</span></div>')
        if a.get("original_fetch_status") == "failed" and a.get("final_evidence_source") == "none":
            rows.append('<div class="aa-kv"><span>Fetch</span><span>Source could not be independently verified</span></div>')
        if a.get("final_evidence_source") == "fallback_source":
            rows.append(f'<div class="aa-kv"><span>Fallback source</span>{safe_link(a.get("fallback_source_url", ""))}</div>')
        cards.append(
            f'<div class="aa-card aa-claim" data-verdict="{esc(verdict)}"><div class="aa-claim-h">'
            f'<span class="aa-badge" data-verdict="{esc(verdict)}">{VERDICT_ICON.get(verdict, "–")} {esc("AWAITING AUDIT" if verdict == "pending" else verdict.upper())}</span>'
            f'<span class="aa-evlabel">{esc(label)}</span></div>'
            f'<div class="aa-claim-text">{esc(c.get("original_claim", ""))}</div>{"".join(rows)}</div>')
    return "".join(cards)


def final_html(run, label_fn=None) -> str:
    if run is None:
        return '<div class="aa-card"><div class="aa-empty">The audited answer appears here once the Auditor has verified each claim.</div></div>'
    parts = []
    if run.error:
        parts.append(f'<div class="aa-card aa-err"><div class="aa-claim-h"><span class="aa-badge" data-verdict="contradicted">✕ RESEARCH FAILED</span></div>'
                     f'<div class="aa-ev-detail">{esc(run.error)}</div></div>')
    if run.claims:
        parts.append(_claim_cards(run, label_fn))
    elif run.finished:
        parts.append('<div class="aa-card"><div class="aa-empty">No claims were extracted, so there was nothing for the Auditor to verify.</div></div>')
    if not parts:
        parts.append('<div class="aa-card"><div class="aa-empty">Waiting for the Auditor...</div></div>')
    return "".join(parts)


def memory_html(view: dict, new_lessons: list | None = None) -> str:
    n, cap, injected = view["count"], view["cap"], view["injected"]
    new_set = {(t or "").strip() for t in (new_lessons or [])}
    plural = "lesson" if n == 1 else "lessons"
    rows = []
    for i, text in enumerate(view["lessons"], 1):
        tag = '<span class="aa-new">NEW</span>' if text.strip() in new_set else ""
        rows.append(f'<div class="aa-lesson"><span class="n">{i}</span><span>{esc(text)}{tag}</span></div>')
    body = "".join(rows) if rows else '<div class="aa-empty">No lessons stored yet.</div>'
    return (
        '<div class="aa-card"><div class="aa-mem-h"><span class="aa-mem-t">Research Memory</span>'
        f'<span class="aa-mem-c">{n} {plural} retained · cap {cap}</span></div>'
        f'<div class="aa-lessons">{body}</div>'
        f'<div class="aa-inject">Lessons injected into Analyst: {injected}</div></div>'
    )


def metrics_html(history: list, memory_size: int) -> str:
    """Only real values from completed runs in this session plus the live memory size."""
    done = sum(1 for h in history if h.get("finished"))
    claims = sum(h.get("claims", 0) for h in history)
    sup = sum(h.get("supported", 0) for h in history)
    uns = sum(h.get("unsupported", 0) for h in history)
    con = sum(h.get("contradicted", 0) for h in history)
    tiles = [("done", "Questions completed", done), ("claims", "Claims audited", claims),
             ("supported", "Supported", sup), ("unsupported", "Unsupported", uns),
             ("contradicted", "Contradicted", con), ("mem", "Memory size", memory_size)]
    body = "".join(f'<div class="aa-metric" data-k="{k}"><div class="v">{v}</div><div class="l">{esc(l)}</div></div>'
                   for k, l, v in tiles)
    return f'<div class="aa-metrics">{body}</div><div class="aa-note">Counts cover runs completed in this browser session; memory size is read live.</div>'


def run_summary(run) -> dict:
    """Session-history record built only from the actual run state."""
    c = run.verdict_counts()
    return {"finished": bool(run.finished and not run.error), "claims": len(run.audit_results),
            "supported": c["supported"], "unsupported": c["unsupported"], "contradicted": c["contradicted"]}
