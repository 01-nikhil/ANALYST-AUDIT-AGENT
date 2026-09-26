# PS3 — Analyst + Auditor: A Self-Improving Research & Verification System

## Problem

LLM research agents routinely conflate three very different things: a search
engine *finding* a claim, a page being *fetched*, and a claim being *verified*.
A snippet that mentions a fact is not proof of that fact, an inaccessible URL is
not evidence, and a plausible-sounding answer can still be wrong. This project
enforces one rule end-to-end:

> **SEARCH FOUND IT ≠ FETCHED IT ≠ AUDITOR VERIFIED IT**

The system is a LangGraph state graph with two cooperating roles — an **Analyst**
that researches and an independent **Auditor** that verifies — plus a persistent
feedback memory that carries lessons forward. It is a working research harness,
not a finished or perfect product; its value is that it never overstates how well
a claim was verified.

## Analyst

The Analyst (`chatbot` node) answers a research question using two tools:
`web_search` (Tavily) and `fetch_page`. It preserves exact source URLs and
labels evidence by how it was obtained (search snippet vs. fetched page). It is
free to answer from snippets, but those are never treated as verified.

## Auditor

The Auditor runs **after** the Analyst and **independently re-fetches the cited
source** for every extracted claim — it does not trust the Analyst's own fetch.
It issues a verdict of `supported`, `contradicted`, or `unsupported` based only
on the page content it fetched itself.

**Auditor fallback:** if the cited source cannot be fetched, the Auditor performs
**one bounded, neutral fallback web search** about the claim's subject (not about
the Analyst's conclusion), fetches the top accessible alternative, and verifies
against that. If the fallback fetch also fails, the verdict stays `unsupported`.
An unrelated or insufficient fallback page is **not** turned into a contradiction.

## Evidence verification

Evidence status is structured, never inferred from prose or domain name:

- `unverified` → *Information from Search Snippets*
- `fetched` → *Information from Fetched Page Content*
- `audited_supported` → *Auditor-verified evidence (SUPPORTED)*
- `audited_contradicted` → *Auditor-verified evidence (CONTRADICTED)*
- `failed` → *Unverified — source could not be independently verified*

A failed fetch is never supporting evidence, and the user's original claim is
preserved verbatim (never rewritten to match the evidence). **Unsupported claims
remain unsupported when the evidence is insufficient** — the system declines to
manufacture a verdict rather than guessing.

## Persistent feedback memory

After auditing, a Feedback engine turns audit outcomes into durable research
lessons (`Auditor → Feedback → Analyst`). Lessons persist across runs and are
injected into future Analyst prompts, so the system can adjust its research
behavior over time.

The raw memory grew to 34 lessons across four benchmark passes, mostly
paraphrase duplicates. It was **compressed to 6 durable, entity-agnostic
principles** with an **8-lesson runtime cap** and lesson-text-only injection.
This change reduced injected memory by roughly 90% (≈2,800 → ≈275 tokens per
Analyst call) and bounds future growth. **It was a cost/robustness change; we do
not claim it improved answer accuracy** — accuracy across passes is dominated by
live-search nondeterminism and was not attributable to memory changes.

## Benchmark methodology

A self-written 8-question benchmark (`benchmark/questions.json`) increases in
difficulty and reuses entities across questions (basic fact → multi-source →
entity reuse → comparison → conflicting sources → multi-hop → hard verification →
adversarial). Questions run **sequentially** so later questions can benefit from
memory written earlier. The harness (`benchmark/run_benchmark.py`) invokes the
unmodified agent and records, per question: elapsed time, input/output/total
tokens, searches, Analyst and Auditor fetches (including fallback), verdict
counts, lessons generated/added, memory size, and a per-node timing breakdown.
Token usage and timing are captured via LangChain callbacks; no application code
is modified for measurement. Deterministic fixture tests cover the branches that
live runs can't reliably trigger.

## Cost tracking

Costs are computed from `benchmark/pricing_config.json` using official list
prices (verified 2026-09-26): Gemini 3.5 Flash-Lite ($0.30 / 1M input, $2.50 / 1M
output) and Tavily basic search ($0.008 / credit), converted at USD→INR ≈ 96.
The harness reports per-question LLM/search/total cost in INR and the average
across the run; assumptions (paid tier, basic search depth, indicative FX rate,
Analyst-search-only cost basis) are documented in the config.

## Key benchmark result (latest completed run — Pass 4)

| Metric | Value |
|---|---|
| Questions completed | 8 / 8 |
| Total time | 156.33 s |
| Total tokens | 157,988 |
| Analyst searches | 9 |
| Auditor original fetches | 8 |
| Auditor fallback searches | 2 |
| Auditor fallback fetches | 2 (both succeeded) |
| Supported / Contradicted / Unsupported | 6 / 1 / 1 |
| Feedback lessons generated (before memory compression) | 6 |
| Estimated cost | ≈ ₹13.00 total (≈ ₹1.63 / question) |

In this run the fallback rescued one claim whose cited source was unreachable
(fetching an independent alternative and verifying it), and correctly left
another claim `unsupported` when the fallback evidence did not actually address
the comparison. These results reflect one live run with nondeterministic web
search; they demonstrate the mechanism working, not a guarantee of accuracy.

## Running

```
python -m benchmark.run_benchmark --dry-run          # structural check, no API calls
python -m benchmark.run_benchmark --question 1        # one question, live
python -m benchmark.run_benchmark --all --pass-id N   # full 8-question run
python app/main.py                                     # interactive agent
```

Requires `GOOGLE_API_KEY` and `TAVILY_API_KEY` in `.env`. See `DECISIONS.md`,
`STATUS.md`, and `HANDOFF.md` for architecture and history.
