# HANDOFF.md

## Project
Self-improving Research & Verification System
PS3 Analyst and Auditor

## Current Status
Phase 6.1 COMPLETE.

Implemented:
1. LangGraph analyst
2. Web search
3. Page fetching
4. Structured claims
5. Independent auditor
6. Auditor verdicts
7. Feedback generation
8. Persistent research memory
9. Deterministic final-answer composition (compose_final_answer)

## Critical Evidence Rule

SEARCH FOUND IT != FETCHED IT != AUDITOR VERIFIED IT

Search snippets are not fetched evidence.
Failed fetches are never supporting evidence.
This rule now also holds for the final user-facing answer, not just
internal state: evidence labels in the final answer are derived
strictly from state["claims"] / state["audit_results"] via
get_evidence_label(), never inferred from LLM wording, domain name,
or source title.

## Current Graph

START
 ↓
chatbot
 ↕
tools
 ↓
extract_claims
 ↓
auditor
 ↓
feedback
 ↓
compose_final_answer
 ↓
END

## Phase 6.1 Resolution

The final answer previously shown to the user was the chatbot's
pre-audit draft, generated before extract_claims/auditor/feedback ran
- so it could never truthfully reflect Auditor verdicts. This is fixed:
compose_final_answer now runs after auditor and feedback, and builds
the final answer deterministically from structured state
(get_evidence_label(verification_status)). The CLI no longer prints
the chatbot's draft; it prints only compose_final_answer's output.

Verified via 21/21 synthetic regression checks (no live API calls),
covering the 403 fetch-failure case, a supported claim, a contradicted
claim, and independent per-URL status handling. See STATUS.md for
details.

## Next Task

Build the 8-question benchmark to validate the frozen architecture
end-to-end (live search + fetch + audit + feedback + composed final
answer).

## Do NOT

- redesign the graph
- replace the Auditor
- replace Feedback
- add unnecessary tools
- rewrite working components
- change the evidence model without reason
- change the current architecture while building the benchmark