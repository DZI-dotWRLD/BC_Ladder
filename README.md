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
git diff --check
```

GitHub Actions runs tests, Django checks, and migration consistency checks on
pushes and pull requests.

## Environment Variables

Development defaults are local only. Deployed environments should set:

```text
DJANGO_SECRET_KEY
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS
DJANGO_DB_ENGINE
DJANGO_DB_NAME
DJANGO_DB_USER
DJANGO_DB_PASSWORD
DJANGO_DB_HOST
DJANGO_DB_PORT
DJANGO_SECURE_SSL_REDIRECT=true
DJANGO_SESSION_COOKIE_SECURE=true
DJANGO_CSRF_COOKIE_SECURE=true
```

Enable `DJANGO_SECURE_HSTS_SECONDS` only after HTTPS is verified end to end.

## Current Limitations

- SQLite is supported for local development, but PostgreSQL is required before
  production concurrency verification.
- Signup policy, captain role, notification channels, score corrections,
  suggestion expiry policy, and equal-points ordering still need product
  decisions.
- The UI is server-rendered and intentionally lightweight. It is ready for club
  review, not final brand polish.

## Phase B Checklist

- PostgreSQL configuration and CI service database.
- PostgreSQL-specific transaction/concurrency tests.
- Deployment secret management and production host configuration.
- Static-file hosting.
- HTTPS, secure cookies, trusted proxy settings, and staged HSTS.
- Backups, restore testing, runbooks, monitoring, and structured logging.
- Dependency/security scanning.
