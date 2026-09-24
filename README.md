# BC Tennis Ladder

A web app that runs a private tennis club's **Men's and Women's Doubles
ladders**.

## The problem it solves

Organizing ladder doubles by hand means chasing four people's schedules,
settling who plays whom, and trusting that scores and standings are recorded
fairly. BC Ladder automates that loop:

1. **Register** with a unique email, then verify it through a one-time link.
2. **Form a team** of two or three players. You can create a team, or ask to
   join one, in which case an administrator approves.
3. **Post availability** as time windows in the club timezone.
4. **Find opponents.** The app computes every two-player lineup from your team
   that shares a free window with a lineup from another team in the same
   ladder. It ranks them by ladder-points proximity.
5. **Request a match.** Only the four selected players are involved: once one
   player on *each* side accepts, the match is booked atomically and those
   four players' time is reserved.
6. **Play and report.** Each team submits the best-of-three score. Matching
   submissions complete the match and award 3 points to the winner. If the
   submissions differ, an administrator resolves the conflict with an audited
   correction.
7. **Standings** update exactly once per result. Ties are ordered by points,
   then wins, then fewer losses, then team name.

History is never rewritten. Past memberships, matches, scores, point ledgers
and workflow events survive later team or account changes.

## Stack

Django 6.0 on Python 3.14. The pages are server-rendered templates with a
small amount of progressive JavaScript. PostgreSQL 17 is the production
database and SQLite is used for local development. Static files are served by
WhiteNoise, and gunicorn runs the app on Render. Email goes over SMTP (Gmail)
through a transactional outbox. Sentry is optional.

## Local setup (PowerShell)

```powershell
py -3.14 -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements-dev.txt
$env:DJANGO_DEBUG = "true"
.\venv\Scripts\python.exe manage.py migrate
.\venv\Scripts\python.exe manage.py seed_demo
.\venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

**`DJANGO_DEBUG=true` must be set in the shell.** Settings read only real
environment variables, and nothing loads a `.env` file. Without that variable
the app assumes production and refuses to start on SQLite. `.env.example`
lists the local defaults for reference. In development, email is printed to
the console, including verification links.

Demo accounts (local only): `demo-admin` / `DemoPass123!` and
`demo-mens-1` / `DemoPass123!`. `seed_demo` is idempotent. It refuses to run
when DEBUG is off, or against a PostgreSQL database whose name doesn't contain
demo, dev, test or ci. `--allow-non-debug` overrides both guards, so never use
it against club data.

## Main routes

| Route | Purpose |
| --- | --- |
| `/` | Dashboard with the next action |
| `/accounts/register/`, `/accounts/login/`, `/accounts/password-reset/` | Account access |
| `/team/` | Create, join or leave a team |
| `/availability/` | Post and cancel availability windows |
| `/suggestions/` | Match requests; `?discover=1` searches for opponents |
| `/matches/`, `/matches/<id>/` | Match history, detail, score entry, cancellation |
| `/ladders/mens/`, `/ladders/womens/` | Standings |
| `/admin/` | Membership approvals, score-conflict resolution, read-only history |
| `/health/`, `/health/ready/` | Liveness and database readiness |

The navigation groups availability, suggestions and matches under **Play**.

## Quality checks

```powershell
$env:DJANGO_DEBUG = "true"
powershell -ExecutionPolicy Bypass -File .\scripts\quality.ps1                           # full
powershell -ExecutionPolicy Bypass -File .\scripts\quality.ps1 -SkipFullTests -SkipAudit # fast
```

The script runs `check`, `makemigrations --check`, Ruff format and lint,
`git diff --check`, the test suite and pip-audit. It does **not** stop when
a step fails, so read every step's output. `scripts/quality.sh` is the POSIX
equivalent and does stop on failure.

GitHub Actions runs four jobs on pull requests and on pushes to `main`,
`agent/**` and `codex/**`:
- **quality:** lint, format, pip-audit, `check --deploy`, strict `collectstatic`.
- **sqlite:** the full suite on SQLite.
- **postgres:** the full suite on PostgreSQL 17, including the
  PostgreSQL-only concurrency tests that are skipped on SQLite.
- **frontend:** the Playwright Chromium smoke test (see
  [tests/frontend/README.md](tests/frontend/README.md)).

To run the suite on a local PostgreSQL database, also set `DJANGO_DB_ENGINE`
and `DJANGO_DB_NAME`, `USER`, `PASSWORD`, `HOST` and `PORT`.

## Documentation map

| Document | Read it for |
| --- | --- |
| [REQUIREMENTS.md](REQUIREMENTS.md) | The authoritative business rules and invariants |
| [docs/architecture.md](docs/architecture.md) | Models, state machines, services, configuration, commands |
| [docs/booking-concurrency.md](docs/booking-concurrency.md) | Lock order and standings serialization |
| [docs/matchmaking-candidates.md](docs/matchmaking-candidates.md) | The opponent-search algorithm and signed commands |
| [docs/frontend-pilot-spec.md](docs/frontend-pilot-spec.md) | UI contract, states and accessibility rules |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Environment, Render, cron jobs, backups, rollback, incidents |
| [docs/production-readiness.md](docs/production-readiness.md) | What is ready, and the blockers before real club data |
| [docs/agents/current-stage.md](docs/agents/current-stage.md) | The active roadmap |
| [AGENTS.md](AGENTS.md) | Rules for AI coding agents working in this repo |
