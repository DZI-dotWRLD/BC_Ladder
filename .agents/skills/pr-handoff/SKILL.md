---
name: pr-handoff
description: Prepare a concise review-ready handoff for a completed BC_ladder change, including behavior, design, migrations, tests, commands, complexity, security, concurrency, deployment, and remaining risks.
---

# Pull Request Handoff

Use this skill after implementation and verification are substantially complete.

## Inspect before writing

- Read `AGENTS.md`.
- Review the complete branch diff.
- Review new migrations.
- Review test results and command output.
- Confirm no secrets, debug files, local database files, or generated junk were
  added.
- Confirm the summary matches what was actually implemented.

## Required handoff

### Summary

Explain the user problem and the behavior now provided.

### Design

Describe important model, service, API, UI, and state-transition decisions.
Explain how the design preserves BC_ladder invariants.

### Files

Group meaningful changed files by concern rather than listing every trivial
line.

### Database

List migrations, constraints, indexes, data backfills, deployment order, and
rollback concerns.

### Verification

List exact commands run with pass/fail results. Separate commands not run and
explain why.

### Test coverage

Describe success, validation, authorization, concurrency, idempotency, and
performance cases.

### Performance

State time and additional space complexity for non-trivial algorithms. Note
query-count changes and indexes.

### Security and concurrency

Describe object permissions, server-side validation, transaction boundaries,
locks, constraints, and retry behavior.

### Deployment

Mention environment variables, static assets, scheduled jobs, data migrations,
or operational steps.

### Risks

List assumptions, product decisions still needed, and follow-up work.

## Style

Be factual and concise. Do not say "fully tested," "secure," or "production
ready" without evidence. Do not claim commands were run when they were not.
