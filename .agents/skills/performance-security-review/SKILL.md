---
name: performance-security-review
description: Review BC_ladder changes for Django query efficiency, algorithmic complexity, authorization, data validation, transaction safety, race conditions, privacy, and secure deployment practices.
---

# Performance and Security Review

Use this skill before completing a substantive feature or when reviewing a
branch or pull request.

## Start with the changed behavior

Read `AGENTS.md`, inspect the diff, and trace each user-controlled input to every
read and write it can trigger.

## Authorization review

For each endpoint or action, verify:

- authentication is required where appropriate;
- the queryset is scoped to objects the user may access;
- team membership or administrator status is checked;
- one user cannot act for an unrelated team;
- one user cannot edit another user's availability;
- match participants are verified;
- ladder points are not client-controlled;
- identifiers from URLs and payloads are not trusted before authorization; and
- templates do not expose data the backend would deny.

## Input and web security review

Verify:

- state changes do not occur via GET;
- CSRF protection remains enabled;
- forms or serializers whitelist accepted fields;
- file uploads, if any, validate type and size;
- output is escaped;
- redirects are not open;
- secrets are absent from code and logs;
- rate limiting or abuse controls are considered for high-impact endpoints;
- errors do not reveal sensitive internals; and
- duplicate form submissions are safe.

## Transaction and race review

Trace the exact transaction boundary for:

- joining or leaving a team;
- accepting a suggestion;
- consuming availability;
- submitting a score; and
- updating ladder points.

Look for check-then-write races. Critical eligibility checks must run inside the
same transaction as the write. Use row locks and database constraints where
appropriate.

Verify retries cannot create:

- two active memberships;
- a fourth team member;
- duplicate confirmed matches;
- overlapping user reservations;
- duplicate score sets; or
- duplicate point changes.

## Query review

Inspect evaluated querysets and templates for N+1 behavior.

Consider:

- `select_related()` for single-valued relations;
- `prefetch_related()` for collections;
- filtered prefetches;
- database annotations and aggregation;
- `exists()` rather than loading rows;
- pagination;
- indexes matching filters and ordering;
- avoiding repeated `.count()` or property queries in loops; and
- avoiding loading full model objects when IDs or values suffice.

Do not optimize blindly. Explain the expected query and data-volume impact.

## Algorithm review

For each non-trivial Python loop:

1. Define input sizes.
2. State time complexity.
3. State additional space complexity.
4. Identify repeated scans or avoidable sorting.
5. Prefer hash lookup, indexed buckets, or sorted two-pointer scans where they
   simplify the solution.
6. Keep deterministic ordering.
7. Check worst-case memory growth.

For matchmaking, reject an unnecessary global all-pairs comparison. Bucket by
ladder and compatible time slot before ranking opponents.

## Data integrity review

Verify:

- constraints match domain invariants;
- migrations are safe for existing rows;
- data migrations are deterministic and reversible when practical;
- status transitions cannot skip required states;
- historical matches survive membership changes;
- timezone handling is aware and consistent; and
- deletion policies do not erase required audit history.

## Severity

Classify findings:

- **Critical:** privilege bypass, data corruption, secret exposure, or reliable
  duplicate financial/ladder effects.
- **High:** race condition, cross-team write, invalid booking, unsafe migration,
  or major denial of service.
- **Medium:** N+1 query, incomplete validation, confusing state transition, or
  privacy overexposure.
- **Low:** maintainability or small efficiency concern without immediate user
  harm.

Fix critical and high-confidence high-severity findings before completion.

## Output

Provide findings with file and symbol references, severity, exploit or failure
scenario, proposed fix, and verification. Then summarize query impact,
algorithmic complexity, transaction coverage, and remaining risk.
