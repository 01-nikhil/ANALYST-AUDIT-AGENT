# HANDOFF.md

## Project
Self-improving Research & Verification System
PS3 Analyst and Auditor

## Current Status
Phase 6.1 completed.

Implemented:
1. LangGraph analyst
2. Web search
3. Page fetching
4. Structured claims
5. Independent auditor
6. Auditor verdicts
7. Feedback generation
8. Persistent research memory

## Critical Evidence Rule

SEARCH FOUND IT != FETCHED IT != AUDITOR VERIFIED IT

Search snippets are not fetched evidence.
Failed fetches are never supporting evidence.

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
END

## Current Issue

Final answer generation must correctly reflect:
- search_snippet
- fetched_page
- audited_supported
- audited_contradicted
- failed/unsupported

Do not allow the LLM to invent evidence labels.

## Next Task

Fix final answer composition so it derives evidence labels
strictly from structured state.

After that:
FREEZE ARCHITECTURE
Run 8-question benchmark.

## Do NOT

- redesign the graph
- replace the Auditor
- replace Feedback
- add unnecessary tools
- rewrite working components
- change the evidence model without reason