# BC_ladder Agentic Development Workflow

This workflow can be followed by one Codex session using different review
phases, or by multiple agents working on separate branches. Every phase remains
subject to `AGENTS.md`.

## Phase 1: Repository analyst

### Goal

Build a fact-based view of the existing project before changing code.

### Actions

- Map Django apps and important files.
- Identify models, constraints, migrations, services, selectors, views,
  forms/serializers, templates, JavaScript, tests, and settings.
- Find current commands from `pyproject.toml`, requirements files,
  `package.json`, Makefiles, task runners, and CI.
- Compare current behavior with `REQUIREMENTS.md`.
- Record assumptions and product decisions that are not represented in code.

### Deliverable

A concise implementation plan with affected files, data migration needs, test
cases, security risks, concurrency risks, and expected complexity.

## Phase 2: Domain designer

### Goal

Choose a design that enforces invariants in one clear place.

### Actions

- Define state transitions.
- Decide which protections belong in database constraints, models, services,
  forms/serializers, and permissions.
- Specify transaction boundaries.
- Specify idempotency keys or uniqueness protections for repeated writes.
- Specify query strategy and indexes.
- Confirm historical data behavior.

### Gate

Do not implement until the design explains how it prevents invalid membership,
double booking, unauthorized writes, and duplicate point updates.

For user-facing work, pair this phase with the `frontend-design-engineering`
skill. Freeze the visual direction, component inventory, responsive behavior,
accessibility criteria, frontend architecture, and backend contract before
implementation. React requires an explicit justification and maintenance plan;
it is not the default merely because a redesign is modern.

## Phase 3: Implementer

### Goal

Build the smallest complete vertical slice.

### Actions

- Add failing tests first when practical.
- Implement domain service behavior.
- Add model and migration changes.
- Connect forms/serializers and views.
- Update templates or frontend code.
- Keep endpoints thin.
- Add user-visible errors for expected conflicts.
- Do not refactor unrelated code.

### Gate

The focused tests for the new behavior pass.

## Phase 4: Adversarial reviewer

### Goal

Try to break the implementation.

### Review scenarios

- Two requests accept the same slot simultaneously.
- A user is removed from a team after a suggestion is generated.
- A player changes availability before the second team accepts.
- A user attempts to accept for another team.
- A user attempts to submit a score for another match.
- A duplicate request is retried after a network timeout.
- A three-person team creates duplicate or reversed lineups.
- Men's and Women's ladders are accidentally mixed.
- A deciding tie-break is submitted as `10-9`.
- Ladder points update twice.
- List pages introduce N+1 queries.
- Naive datetimes cross daylight-saving changes.

### Deliverable

Findings ordered by severity with fixes or explicit justification.

## Phase 5: Test and quality agent

### Goal

Verify behavior and repository health.

### Actions

- Run focused unit and integration tests.
- Run concurrency tests where supported.
- Run the full suite when feasible.
- Run Django checks and migration consistency checks.
- Run formatting, linting, type checking, frontend checks, and build commands
  that are configured by the repository.
- Measure query counts on important views.
- Report skipped or unavailable checks honestly.

### Gate

No known high-severity correctness, permission, transaction, or migration issue
remains.

## Phase 6: Handoff agent

### Goal

Create a review-ready change summary.

### Deliverable

- Problem solved.
- User-visible behavior.
- Main implementation decisions.
- Files changed.
- Migrations.
- Tests and commands run.
- Complexity.
- Security and concurrency treatment.
- Rollback or deployment notes.
- Remaining assumptions and risks.

## Parallel-work guidance

Parallelize only work that has a stable interface:

- tests for a specified service;
- UI work against an agreed form or API contract;
- documentation;
- independent read-only review.

Do not have multiple agents independently edit the same models, migrations, or
booking transaction. Assign one owner for schema and core domain services.

## Suggested branch discipline

- Use one focused branch per feature.
- Keep migrations in dependency order.
- Rebase or merge before generating the final migration.
- Review the complete diff, not only the latest commit.
- Never resolve a migration conflict by deleting another valid migration
  without understanding deployed history.
