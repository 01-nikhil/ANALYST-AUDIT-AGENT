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

### Cost per question and trend (Pass 4, tokens × configured pricing)

| Q | Difficulty | Tokens | Searches | Cost (INR) |
|---|---|---|---|---|
| 1 | 1 | 15,710 | 1 | 1.37 |
| 2 | 2 | 18,975 | 1 | 1.45 |
| 3 | 3 | 15,484 | 1 | 1.44 |
| 4 | 4 | 45,276 | 2 | **3.13** |
| 5 | 5 | 15,223 | 1 | 1.36 |
| 6 | 6 | 16,286 | 1 | 1.38 |
| 7 | 7 | 15,649 | 1 | 1.47 |
| 8 | 8 | 15,385 | 1 | 1.39 |
| **Total** | | **157,988** | **9** | **≈ 13.00** |
| **Average / question** | | | | **≈ 1.63** |

**Trend:** cost is roughly flat at ~₹1.4/question and does **not** rise with
difficulty — it tracks tokens and searches, not question hardness. The one
outlier is **Q4 (₹3.13)**, driven by a larger token count (~45k, from a longer
Analyst turn plus an extra search) and 2 searches, not by its difficulty rank.
Between passes, totals shift mainly with live-search variance (number of searches
and page sizes) rather than any change in the system; these figures are computed
from Pass 4's committed token/search counts and are indicative (FX + list-price
based), not billed amounts. Cost fields are `null` in Pass 1–4 result JSON because
pricing was configured after those runs; a fresh run now records them populated.

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

## Quickstart (from a clean checkout, < 5 minutes)

```bash
# 1. Clone and enter the repo
git clone https://github.com/01-nikhil/ANALYST-AUDIT-AGENT.git
cd ANALYST-AUDIT-AGENT

# 2. Create a virtual environment and install dependencies
python -m venv .venv
# Windows:        .venv\Scripts\activate
# macOS / Linux:  source .venv/bin/activate
pip install -r requirements.txt

# 3. Provide API keys (create a .env file in the repo root)
#    Get keys from https://aistudio.google.com/apikey and https://app.tavily.com
printf 'GOOGLE_API_KEY=your-google-key\nTAVILY_API_KEY=your-tavily-key\n' > .env

# 4. Verify the install with NO API calls (structural check)
python -m benchmark.run_benchmark --dry-run
```

### Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `GOOGLE_API_KEY` | yes | Gemini model access (`langchain-google-genai`) |
| `TAVILY_API_KEY` | yes | `web_search` tool (Tavily) |

Both are read from `.env` at startup via `python-dotenv`. Keys are never printed
or committed (`.env` is gitignored).

## Running

```bash
python app/main.py                                     # interactive agent (asks questions live)
python -m benchmark.run_benchmark --dry-run            # structural check, no API calls
python -m benchmark.run_benchmark --question 1         # run one benchmark question, live
python -m benchmark.run_benchmark --all --pass-id N    # full 8-question benchmark, live
```

Benchmark results are written to `benchmark/results/run_<timestamp>.json`.

## Running the tests

All tests are offline fixtures (no API calls, no cost). Run them directly:

```bash
python benchmark/test_count_auditor_fetches.py
python benchmark/test_count_auditor_fallback_fetches.py
python benchmark/test_auditor_fallback.py
python benchmark/test_auditor_no_citation.py
python benchmark/test_memory_cap.py
python benchmark/test_cost_calculation.py
python benchmark/test_timing_instrumentation.py
```

Each prints `ALL CHECKS PASSED` and exits non-zero on failure.

## Demo UI

A small Streamlit page that makes the orchestration visible. It is a
**presentation layer over the existing Analyst/Auditor system, not part of the
research logic**. It runs the **real Analyst → Auditor → Feedback workflow, not
mocks**: it calls the same compiled LangGraph agent (`app.main.agent`) and renders
each real node result as it completes. It duplicates no research logic, fakes no
results, and never writes benchmark result files.

**Requirements and cost.** The UI needs the same `GOOGLE_API_KEY` (Gemini) and
`TAVILY_API_KEY` (Tavily search) in `.env` as the rest of the project (see
Quickstart). **Every "Run Research" click makes real Gemini and Tavily API calls
and incurs real cost** (roughly ₹1–3 per question at the configured list prices).
If a key is missing, the page says so and does not run.

```bash
# install the UI dependency (kept separate from the core requirements)
pip install -r ui/requirements-ui.txt

# start it from the repo root, with .env in place
streamlit run ui/streamlit_app.py
```

Then open the printed local URL (default http://localhost:8501). The default
question is the adversarial benchmark claim ("Confirm: In 2019, Microsoft fully
acquired OpenAI for $1 billion…"); any question can be typed instead.

**What it demonstrates**

- **Two agents and a feedback loop:** the Analyst agent (plan, search, fetch,
  claim extraction), the Auditor agent (re-opens each cited source independently
  and returns SUPPORTED / UNSUPPORTED / CONTRADICTED), and the Feedback loop that
  writes lessons to persistent memory.
- **A live trace** of observable events only: tool names, queries, result counts,
  fetch outcomes, claims, Auditor fallback steps, verdicts and feedback. It shows
  no hidden reasoning. Failures are shown as failures ("Search failed",
  "Source could not be independently verified", UNSUPPORTED, CONTRADICTED).
- **Persistent memory:** the lessons currently stored and how many are injected
  into the Analyst (capped at 8).
- **Metrics** counted from the runs completed in the current browser session
  (questions, claims audited, verdict counts) plus the live memory size.

**Memory is real state.** A UI run executes the real Feedback node, so it **may
update `app/research_memory.json`** (adding a lesson) whenever memory is below its
8-lesson cap; at the cap it reports "No new lesson added to memory" and the file is
left unchanged. That file is tracked in git, so a real run can show up as a local
modification. UI tests (offline, no API calls, no memory writes):

```bash
python ui/test_ui_events.py        # event/stage logic and HTML rendering
python ui/test_ui_app.py           # starts the real page headlessly and drives it
python ui/test_ui_integration.py   # request -> real compiled graph (LLM/network faked)
```

## Benchmark questions

`benchmark/questions.json` contains **8 self-written questions of strictly
increasing difficulty (1 → 8)**: basic factual → multi-source → entity reuse →
comparison → conflicting sources → multi-hop → hard verification → adversarial.
**Six of the eight reuse entities** introduced in earlier questions (e.g. Q2–Q6
and Q8 reuse *Microsoft*; Q6 reuses *Satya Nadella*; Q7–Q8 reuse *OpenAI*),
exceeding the "at least 2 reuse" requirement. Each question records its
`difficulty`, `entities_introduced`, and `entities_reused`; see
`benchmark/BENCHMARK_SPEC.md` for the full matrix.

See `DECISIONS.md`, `STATUS.md`, `HANDOFF.md`, and `benchmark/BENCHMARK_SPEC.md`
for architecture, trade-offs, and history.
