# BC_ladder Agent Instructions

BC_ladder is a server-rendered Django application for a private tennis club's
Men's and Women's Doubles ladders. Preserve the repository's established
architecture and business history while making the smallest coherent change.

Players form teams of two or three, post availability, and get suggested
lineups against same-ladder opponents. A match is booked when one selected
player from each side accepts. Both teams then submit a score, and matching
scores award 3 points to the winner. Administrators approve team joins and
removals and resolve score conflicts. Read `README.md` for the full journey.

## Commands

The stack is Python 3.14 with the repo `venv/` and Django 6. **Set
`DJANGO_DEBUG=true` in the shell first.** Nothing loads `.env`, and without
that variable every `manage.py` command fails production validation.

```powershell
$env:DJANGO_DEBUG = "true"
.\venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
.\venv\Scripts\python.exe manage.py test ladder.tests_scoring --noinput          # one module
.\venv\Scripts\python.exe manage.py test ladder.tests_scoring.ClassName.test_x   # one test
.\venv\Scripts\python.exe manage.py test --noinput                               # full suite
.\venv\Scripts\python.exe manage.py check
.\venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\venv\Scripts\python.exe -m ruff format --check . ; .\venv\Scripts\python.exe -m ruff check .
powershell -ExecutionPolicy Bypass -File .\scripts\quality.ps1   # all gates; does NOT stop on failure
```

Tests live in `ladder/tests_*.py` and use Django's `TestCase` (no pytest).
PostgreSQL-only classes are skipped on SQLite and run in the CI `postgres`
job. `node tests/frontend/check.mjs` is the Playwright smoke test; see
`tests/frontend/README.md`.

## Route Before Editing

- **Roadmap work:** read `docs/agents/current-stage.md` before selecting or
  starting a project slice; it fixes the active branch order, scope, and gates.
- **Domain work:** read `REQUIREMENTS.md` completely before changing models,
  migrations, services, permissions, availability, suggestions, matches,
  scoring, standings, or workflow events. Its invariants are non-negotiable
  unless the project owner explicitly changes them.
- **Architecture:** `docs/architecture.md` maps models, state machines,
  services, configuration and commands. `docs/booking-concurrency.md` defines
  the mandatory lock order.
- **Deployment work:** read `DEPLOYMENT.md` before changing settings, secrets,
  hosting, migrations, static assets, health checks, backups, or rollback.
  `docs/production-readiness.md` lists open blockers. Update it when one
  closes.
- **GitHub planning:** read `docs/agents/github.md` before creating or changing
  roadmap issues, labels, dependencies, or triage state.
- **Frontend work:** use `frontend-design-engineering` and
  `docs/frontend-pilot-spec.md`; keep Django templates and progressive
  enhancement unless an approved architecture decision says otherwise.
- **Specialist work:** use the matching repo skill in `.agents/skills/`:
  `availability-matchmaking`, `tennis-score-validation`,
  `django-testing-quality`, `performance-security-review`,
  `django-feature-workflow`, or `pr-handoff`. Tools that don't auto-load that
  folder should read the `SKILL.md` directly.

## Working Method

1. Inspect the relevant implementation and tests before proposing changes.
2. State the requested behavior, affected invariants, authorization boundary,
   transaction boundary, and historical-data effect.
3. Plan one coherent vertical slice. Record unrelated ideas in the backlog.
4. Add or update the lowest useful tests, then implement through explicit
   services and thin views.
5. Run focused checks, review edge cases and compatibility, then run applicable
   repository quality gates.
6. Review the complete diff and report what changed, migrations, tests,
   commands, performance, security/concurrency treatment, and remaining risks.

Start new roadmap branches from updated `main`. Keep one active roadmap branch
and pull request at a time. Preserve unrelated user changes, including git
stashes and the untracked `output/` directory, which holds owner worktrees and
QA evidence. Change `.agents/skills/` and `.codex/` only when the owner asks.

## Architecture Boundaries

- Models own persisted state, constraints, and small state-local behavior.
- Services own membership, availability, matchmaking, booking, cancellation,
  scoring, standings, and workflow transitions.
- Query helpers own reusable optimized reads.
- Forms own input shape and user-facing validation.
- Views own authentication, object authorization, orchestration, and responses.
- Templates and progressive JavaScript own presentation only.

Keep core workflows explicit. Prefer `transaction.atomic()`, row locks, database
constraints, and idempotent services where writes can race or retry. Avoid core
business behavior in signals, model `save()`, templates, or duplicated view
logic.

In this repo the layers live in these files:
- **Services:** `ladder/services.py`, plus `registration.py`,
  `workflow_events.py` and `email_notifications.py`.
- **Views:** `ladder/views.py`.
- **Admin:** `ladder/admin.py`. Workflow-owned models are read-only there, and
  admin actions call services.

Services raise `DomainError` subclasses, which views turn into messages.
Workflow events are created only through `record_workflow_event` with a
unique `dedupe_key`.

## Universal Guardrails

- Require authentication and object-level authorization for player data and
  actions; use Django CSRF protection and non-GET mutations.
- Preserve historical memberships, participants, matches, results, standings,
  ledgers, reservations, and workflow events according to `REQUIREMENTS.md`.
- Store timezone-aware datetimes, use `timezone.now()`, and display the club or
  configured user timezone.
- Use named URLs, Django forms, server validation, explicit state transitions,
  and migrations for schema changes. Never rewrite an applied migration.
- Validate identifiers through authorized querysets and protect against IDOR,
  mass assignment, duplicate writes, stale state, and race conditions.
- Keep secrets, credentials, tokens, private keys, database dumps, and
  unnecessary personal data out of source control and logs.
- Prevent N+1 queries on important lists; use indexes and deterministic,
  complexity-conscious algorithms for high-volume paths.

## Verification

Every behavior change needs focused coverage plus request/integration coverage
for critical workflows. Add PostgreSQL-specific tests for row-lock or exclusion-
constraint behavior that SQLite cannot prove. Use existing test conventions and
avoid introducing test frameworks or dependencies without need.

Before completion, run the applicable commands discovered from the repository:

1. focused tests;
2. Django system and migration-consistency checks;
3. formatter, linter, and `git diff --check`;
4. frontend checks or builds when configured;
5. the full suite when feasible;
6. security/dependency checks when configured.

Report any skipped or unavailable check and its reason. Never claim a command
passed unless it was run successfully.
