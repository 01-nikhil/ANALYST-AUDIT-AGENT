# DECISIONS.md

Architectural decisions for the PS3 Analyst + Auditor system, in the order they
were made. Kept intentionally short (≤ 2 pages).

## 1. LangGraph state graph
The system is a single LangGraph `StateGraph`, not an ad-hoc loop. Flow:
`chatbot → (tools ↔ chatbot) → extract_claims → auditor → feedback →
compose_final_answer → END`. A typed `State` carries messages, claims,
audit_results, and feedback_lessons. Chosen for explicit, inspectable stages and
because measurement (tokens/timing) attaches cleanly to named nodes.

## 2. Search snippets are not evidence
A `web_search` result is a lead, never verified evidence. Only a successfully
`fetch_page`-d page counts as fetched content, and only an Auditor-fetched page
counts as verified. This rule (`SEARCH FOUND IT ≠ FETCHED IT ≠ AUDITOR VERIFIED
IT`) is enforced structurally, not by prose.

## 3. Analyst fetching vs. Auditor independent verification
The Analyst may answer from snippets or its own fetches, but the Auditor does
**not** trust the Analyst's evidence. The Auditor independently re-fetches each
cited source and judges only from what it fetched itself. Separating "researcher"
from "verifier" is the core zero-trust design.

## 4. Original claim preservation
The user's original factual claim is stored verbatim and never rewritten to match
what the evidence says (the "Satya Nadella 2010" bug). Verdicts describe the
original claim; they do not silently correct it.

## 5. Persistent Auditor → Feedback → Analyst memory
Audit outcomes feed a Feedback engine that writes durable research lessons to a
JSON store, which is injected into future Analyst prompts. This is the
self-improvement loop: verification failures become future guidance.

## 6. Memory compression + runtime cap
Unbounded append + exact-string dedup let the memory grow to 34 mostly-duplicate
lessons (~2,800 injected tokens/call). We compressed it to **6 durable,
entity-agnostic principles**, inject **lesson text only** (not the long audit
reason), and enforced an **8-lesson hard cap** so it cannot grow without bound
(~275 injected tokens/call). Prior lessons are archived for provenance. This is a
cost/hygiene decision; we do **not** claim it improved accuracy.

## 7. Auditor fallback search
If the cited source cannot be fetched, the Auditor runs **one bounded, neutral**
fallback web search about the claim's *subject* (never "claim is false"), fetches
the top accessible alternative, and verifies against it. If the fallback fetch
also fails, the verdict stays `unsupported`. An unrelated/insufficient fallback
page must yield `unsupported`, not a false `contradicted`. This removes the
single-point-of-failure where one blocked URL killed verification.

## 8. Deterministic final-answer composition after auditing
The user-facing answer is built by `compose_final_answer` **after** the Auditor
runs, mapping structured `verification_status` to fixed evidence labels in code —
not by asking the LLM to describe its own confidence. This guarantees the final
answer's evidence labels match the verified state.

## 9. Token / cost / timing instrumentation
The benchmark harness measures the unmodified agent externally via LangChain
callbacks: token usage, per-node timing (with an "unaccounted" residual), and
Auditor original vs. fallback fetch counts. Cost is computed from a documented
`pricing_config.json` (official Gemini + Tavily list prices, USD→INR ≈ 96),
reporting per-question and average INR. No application code is changed for
measurement.

## 10. Why we stopped benchmarking after Pass 4
Passes 1–3 established a baseline and exposed two problems: a fragile single-fetch
Auditor and an unrelated-source false contradiction on the adversarial question.
Pass 4 validated the Auditor fallback (it rescued one blocked-source claim and
correctly withheld a verdict when evidence was insufficient). With the fallback
validated and memory compressed, further live passes would mostly re-measure
nondeterministic web-search variance at real API cost without testing anything
new; deterministic fixture tests already cover the branch logic. So we froze the
architecture at Pass 4 and moved to finalization.
