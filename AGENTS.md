# BC_ladder Repository Instructions

## Purpose

BC_ladder is a full-stack Django web application for a tennis club. It helps registered players:

- participate in separate Men's Doubles and Women's Doubles ranking ladders;
- belong to a doubles team;
- publish weekly availability;
- receive compatible opponent suggestions;
- accept and schedule matches;
- submit valid tennis scores; and
- update ladder points according to the project's configured point system.

Treat the business invariants in this file as non-negotiable unless the project owner explicitly changes them.

## Read Before Editing

Before making a change:

1. Inspect the repository structure and current implementation.
2. Read relevant models, migrations, services, views, forms or serializers, URLs, templates, JavaScript, tests, and settings.
3. Identify the existing package manager, test runner, formatter, linter, database, frontend approach, and CI commands.
4. Preserve established project conventions unless they conflict with correctness, security, or the rules below.
5. Make the smallest coherent change that fully solves the task.

Do not invent package versions, commands, app names, APIs, or database fields without checking the repository first.

## Core Domain Invariants

### Users and teams

- Only registered, authenticated users may use player features.
- A user may belong to zero or one active team at a time.
- A team may contain at most three active members.
- A playable doubles lineup contains exactly two active members from the same team.
- A user must never appear on both sides of the same match.
- Teams in the Men's Doubles ladder may only be matched with other Men's Doubles teams.
- Teams in the Women's Doubles ladder may only be matched with other Women's Doubles teams.
- A user may request removal from a team, but only an authorized administrator may complete the removal.
- Team membership changes must preserve historical match and score records.
- Enforce critical membership rules at both the application layer and database layer where practical.

Recommended database protections include a unique active membership per user and constraints or service-level validation that prevent more than three active members per team.

### Weekly availability

- Availability belongs to an individual user.
- Store timezone-aware values. Normalize persisted datetimes to UTC and display them in the club or user's configured timezone.
- Reject invalid windows where the end is not after the start.
- Prevent duplicate or overlapping active availability for the same user unless the existing product intentionally supports it.
- Availability used for a confirmed match must no longer be available to the participating users for that time.
- Do not make the non-participating third member of a team unavailable unless that user is part of the accepted lineup.
- Changes to availability must not silently invalidate an already confirmed match.

### Lineup generation

For a team with up to three active members:

1. Find availability shared by at least two members.
2. Generate every unique two-player lineup for each shared slot.
3. Never generate duplicate lineups with reversed member order.
4. Never generate a lineup containing inactive or removed members.
5. Keep the lineup generation deterministic so identical inputs produce identical output ordering.

With three members, there are at most three unique two-player lineups.

### Opponent suggestions

- Suggest opponents only from another active team in the same ladder.
- Both proposed lineups must share a compatible availability window.
- A suggestion contains exactly two users from each team.
- Exclude users or teams already committed to another match during the slot.
- Exclude cancelled, expired, or stale availability.
- Avoid an all-teams cross-product when indexed or bucketed lookup by ladder and time slot can be used.
- Ranking proximity may influence ordering, but it must not override eligibility rules.
- Use a deterministic final tie-breaker, such as stable database IDs, so suggestions are reproducible.
- Preserve the existing ladder point algorithm unless a task explicitly changes it.

### Match acceptance and booking

A match becomes confirmed only after both teams have accepted the same current suggestion.

Confirmation must be concurrency-safe:

1. Enter a database transaction.
2. Lock the match suggestion and all availability or reservation rows that may be consumed.
3. Re-check membership, lineup eligibility, availability, ladder, and conflict rules inside the transaction.
4. Reject the confirmation if any required slot has been consumed or changed.
5. Create or update the confirmed match.
6. Reserve or consume the slot for all four participating users.
7. Commit once all changes are valid.

Prefer `transaction.atomic()` and `select_for_update()` for the critical booking path. Add appropriate uniqueness or exclusion constraints where supported. Never rely only on a check performed before entering the transaction.

Acceptance actions should be idempotent. Repeating the same accepted request must not create duplicate matches or duplicate reservations.

### Match scoring

- Match format is best of three.
- The first team to win two sets wins the match.
- When teams split the first two sets, the third deciding set is a match tie-break.
- The match tie-break is played to at least 10 points and must be won by two points.
- At 9-9, play continues until one team leads by two.
- Valid deciding tie-break examples include `10-8`, `11-9`, and `12-10`.
- Invalid deciding tie-break examples include `10-9`, `9-7`, and `8-6`.
- A deciding match tie-break is not allowed when one team already won both regular sets.
- A match must have exactly one winner.
- Validate scores on the server even if the browser performs client-side validation.
- Do not update ladder points until the submitted result is valid and the project's result-confirmation requirements are satisfied.
- Score submission and ladder updates must be atomic and idempotent.
- Keep the precise regular-set rules configurable or consistent with the existing implementation. Do not silently invent a new 6-6 tie-break policy.

## Architecture

Use Django conventions and keep domain rules out of presentation code.

Preferred responsibility boundaries:

- **Models:** persisted state, database constraints, small state-local behavior.
- **Domain services:** team membership, availability intersection, suggestion generation, match confirmation, scoring, and ladder updates.
- **Query/selectors:** reusable optimized read queries.
- **Forms or serializers:** input shape and user-facing validation.
- **Views/API endpoints:** authentication, authorization, orchestration, and response handling.
- **Templates/components:** presentation only.
- **Tasks/jobs:** asynchronous work only when it materially improves reliability or latency.

Avoid fat views, duplicated validation, hidden side effects in model `save()`, and signals for core business workflows. Explicit service calls are easier to test and reason about.

## Django Conventions

- Follow the Django version and patterns already installed in the repository.
- Use named URLs and `reverse()` rather than hard-coded internal paths.
- Use migrations for every schema change. Never edit an already-applied migration unless the repository is demonstrably pre-release and the task explicitly requires it.
- Keep secrets and environment-specific configuration out of source control.
- Use Django's authentication, CSRF, password hashing, and permission systems.
- Check object-level authorization, not only whether a user is logged in.
- Use POST, PUT, PATCH, or DELETE for state-changing operations as appropriate. Do not change state from a GET request.
- Use `timezone.now()` and timezone-aware values.
- Prefer `TextChoices` or equivalent enums for statuses.
- Define clear state transitions for suggestions and matches, such as proposed, partially accepted, confirmed, completed, cancelled, declined, and expired.
- Preserve historical records rather than deleting data needed for ladder or match history.

## Database and Query Performance

Correctness comes first, then measured optimization.

- Prevent N+1 queries with `select_related()` and `prefetch_related()`.
- Fetch only needed fields for high-volume queries when beneficial.
- Add indexes for common filters and joins, especially ladder, team, user, status, and availability time keys.
- Keep querysets lazy until evaluation is required.
- Use `exists()` for existence checks and database aggregation instead of loading rows into Python.
- Use bulk operations only when model hooks and validations are not required.
- Paginate unbounded lists.
- Do not cache authorization-sensitive or rapidly changing booking state without a clear invalidation strategy.
- Document the expected time and space complexity of non-trivial matching algorithms.
- Prefer indexed time-slot buckets or sorted interval scans over repeated nested scans.
- Profile before introducing complicated optimizations.

## Security and Privacy

- Require authentication for all player data and actions.
- Apply least-privilege permissions for players, team members, captains if present, and administrators.
- An ordinary user must not add or remove arbitrary team members, alter another team's availability, accept for an unrelated team, submit a score for an unrelated match, or edit ladder points directly.
- Validate all identifiers against the authorized queryset.
- Protect against CSRF, mass assignment, insecure direct object references, duplicate submissions, and race conditions.
- Escape untrusted output and sanitize rich text if the application supports it.
- Never log passwords, session tokens, secrets, or unnecessary personal data.
- Do not commit `.env` files, production credentials, private keys, or database dumps.

## Testing Requirements

Every behavior change requires tests at the lowest useful level plus integration coverage for critical workflows.

At minimum, cover:

- one active team per user;
- three-member team maximum;
- admin-controlled removal;
- every unique two-player lineup from a three-member team;
- no lineup when fewer than two members share availability;
- opponent eligibility by ladder, team, and time;
- deterministic suggestion ordering;
- rejection of stale or conflicting availability;
- both-team acceptance;
- concurrent confirmation attempts producing only one booking;
- availability consumption for exactly four participating users;
- valid straight-set results;
- valid split-set results with a match tie-break;
- invalid tie-break margins such as `10-9`;
- score submission authorization;
- duplicate score submission;
- atomic ladder point updates; and
- query-count regressions on important list or suggestion views.

Use the test framework already configured in the repository. Prefer factories or builders already used by the project.

## Quality Gates

Before declaring work complete, run the applicable repository commands for:

1. focused tests for the changed behavior;
2. the full test suite when feasible;
3. Django system checks;
4. migration consistency checks;
5. formatter;
6. linter;
7. type checker, if configured;
8. frontend tests or build, if configured; and
9. security or dependency checks, if configured.

Typical commands may include `python manage.py check`, `python manage.py makemigrations --check`, and the project's test command, but inspect the repository before choosing exact commands.

Do not claim a command passed unless it was actually run. Report commands that could not be run and the reason.

## Change Workflow

For substantive work:

1. Restate the requested behavior and identify affected invariants.
2. Inspect existing code and tests.
3. Write a concise implementation plan.
4. Add or update tests that expose the missing behavior.
5. Implement a small vertical slice.
6. Add migrations if needed.
7. Run focused checks.
8. Review permissions, transactions, query count, edge cases, and backwards compatibility.
9. Run broader checks.
10. Summarize changed files, tests, migrations, performance impact, and remaining risks.

Do not refactor unrelated code in the same change unless required for correctness.

## Skill Routing

Use the repo-local skill whose description matches the task:

- `django-feature-workflow`: general full-stack Django feature work.
- `frontend-design-engineering`: visual design, design systems, responsive and accessible UI implementation, frontend architecture, progressive enhancement, and justified React work.
- `availability-matchmaking`: availability intersection, lineup generation, suggestions, acceptance, and reservation.
- `tennis-score-validation`: score parsing, validation, completion, and ladder updates.
- `django-testing-quality`: tests, fixtures, factories, and quality gates.
- `performance-security-review`: query, algorithm, transaction, permission, and security review.
- `pr-handoff`: final review summary and pull-request handoff.

Use multiple skills when a task crosses concerns.

## Agent skills

### Issue tracker

Issues and specs are tracked in this repository's GitHub Issues. See `docs/agents/issue-tracker.md`.

### Triage labels

The repo uses the default five-label triage vocabulary. See `docs/agents/triage-labels.md`.

### Domain docs

This is a single-context Django repo: use root `CONTEXT.md` and root `docs/adr/` when they exist. See `docs/agents/domain.md`.

## Completion Report

Finish substantive tasks with:

- what changed;
- why the design preserves the domain rules;
- migrations created;
- tests added or updated;
- commands run and results;
- time/space complexity for non-trivial algorithms;
- security and concurrency considerations; and
- any assumptions or follow-up risks.
