# BC Tennis Ladder

BC_ladder is a Django application for a private tennis club doubles ladder. It
supports player profiles, men's and women's teams, availability, opponent
suggestions, dual-team match acceptance, score submission, standings, and admin
operations.

## Local Setup

From PowerShell:

```powershell
py -3.14 -m venv venv
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\venv\Scripts\python.exe manage.py migrate
.\venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

Open:

```text
http://127.0.0.1:8000/
```

## Demo Data

Create deterministic local demo data:

```powershell
.\venv\Scripts\python.exe manage.py seed_demo
```

Local-only demo credentials:

```text
Admin: demo-admin / DemoPass123!
Player: demo-mens-1 / DemoPass123!
```

The command is idempotent. Running it again updates/reuses the same users,
profiles, teams, memberships, standings, availability, suggestions, and matches
instead of creating duplicates.

## Useful Routes

```text
/accounts/login/
/accounts/register/
/profile/setup/
/
/team/
/availability/
/suggestions/
/matches/
/ladders/mens/
/ladders/womens/
/admin/
```

## Quality Commands

```powershell
.\venv\Scripts\python.exe manage.py test
.\venv\Scripts\python.exe manage.py check
.\venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\venv\Scripts\python.exe manage.py reconcile_standings
.\venv\Scripts\python.exe manage.py audit_data_integrity
git diff --check
```

Optional local quality tooling:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\venv\Scripts\python.exe -m ruff check .
.\venv\Scripts\python.exe -m ruff format --check .
.\venv\Scripts\python.exe -m pip_audit -r requirements.txt
```

Run the standard local quality harness:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\quality.ps1
```

For a faster local pre-commit pass:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\quality.ps1 -SkipFullTests -SkipAudit
```

GitHub Actions runs tests, Django checks, migration consistency checks, Ruff,
dependency audit, and deployment checks on pushes and pull requests. Tests run
against both SQLite and PostgreSQL.

Deployment steps live in [DEPLOYMENT.md](DEPLOYMENT.md).

## PostgreSQL Local Verification

SQLite remains the default for local development. To run against PostgreSQL,
create a database and set:

```powershell
$env:DJANGO_DB_ENGINE = "django.db.backends.postgresql"
$env:DJANGO_DB_NAME = "bc_ladder"
$env:DJANGO_DB_USER = "bc_ladder"
$env:DJANGO_DB_PASSWORD = "<password>"
$env:DJANGO_DB_HOST = "localhost"
$env:DJANGO_DB_PORT = "5432"
.\venv\Scripts\python.exe manage.py migrate
.\venv\Scripts\python.exe manage.py test
```

PostgreSQL-only concurrency tests are skipped under SQLite and run when
`DJANGO_DB_ENGINE` points at PostgreSQL. PostgreSQL migrations also add database
exclusion constraints that reject overlapping active availability and active
match reservations for the same player. SQLite keeps service-level validation
for local development, but PostgreSQL is the production correctness target for
those race-sensitive rules.

## Environment Variables

Development defaults are local only. Deployed environments should set:

```text
DJANGO_SECRET_KEY
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS
DJANGO_CSRF_TRUSTED_ORIGINS
DATABASE_URL
DJANGO_SECURE_SSL_REDIRECT=true
DJANGO_SESSION_COOKIE_SECURE=true
DJANGO_CSRF_COOKIE_SECURE=true
```

Render deployments can use `render.yaml`; Render supplies `DATABASE_URL` from
the managed PostgreSQL service and `RENDER_EXTERNAL_HOSTNAME` for the default
`.onrender.com` host. For manual non-Render deployments, the older
`DJANGO_DB_ENGINE`, `DJANGO_DB_NAME`, `DJANGO_DB_USER`, `DJANGO_DB_PASSWORD`,
`DJANGO_DB_HOST`, and `DJANGO_DB_PORT` variables are still supported.
For the Render Free trial, `build.sh` runs migrations during the build because
Free web services do not provide Shell/pre-deploy access. Move migrations to a
controlled release step before production.

Enable `DJANGO_SECURE_HSTS_SECONDS` only after HTTPS is verified end to end.
When `DJANGO_DEBUG=false`, startup fails if a real secret key, non-local
allowed hosts, PostgreSQL database settings, secure cookies, and HTTPS redirect
are not configured.
If `DJANGO_CSRF_TRUSTED_ORIGINS` is set in production, use explicit `https://`
origins only; local development origins and wildcards are rejected.

## Current Limitations

- SQLite is supported for local development. PostgreSQL is configured in CI for
  production-style transaction, range-overlap constraints, and concurrency
  verification.
- Players may self-register. Selected lineup players, not unrelated teammates,
  accept suggestions and submit scores.
- Suggestions expire at the proposed match start time.
- Equal-points ladder ordering is points, wins, fewer losses, then team name/id.
- Score-conflict correction is admin-only through an audited official-submission
  workflow.
- Captain role, notification channels, and richer score-correction policy still
  need product decisions before production.
- The UI is server-rendered and intentionally lightweight. It is ready for club
  review, not final brand polish.

## Phase B Checklist

- Render web service and Render PostgreSQL deployment trial.
- Broader PostgreSQL-specific transaction/concurrency coverage.
- Data audit before production migration to confirm no existing overlapping
  active availability or active reservation rows.
- Deployment secret management and production host configuration.
- Static-file hosting.
- HTTPS, secure cookies, trusted proxy settings, and staged HSTS.
- Backups, restore testing, monitoring, and structured logging.
- Dependency/security scanning.
