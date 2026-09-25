# AGENTS.md

## Project Goal

Build PS3 Analyst and Auditor:
a self-improving research and verification system.

## Before modifying code

Read:
- HANDOFF.md
- DECISIONS.md
- STATUS.md
- README.md

Inspect the existing implementation before changing it.

## Evidence Rules

SEARCH FOUND IT != FETCHED IT != AUDITOR VERIFIED IT

Never treat search snippets as fetched evidence.

Never treat a failed fetch as supporting evidence.

Never rewrite the user's original factual claim.

The Auditor must independently fetch cited sources.

## Development Rules

Prefer minimal changes.

Do not redesign working architecture without evidence.

Do not add unnecessary dependencies.

Run existing tests/regression checks after changes.

Update STATUS.md when completing a phase.

Update DECISIONS.md only when an architectural decision changes.