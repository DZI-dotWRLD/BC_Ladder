# BC_ladder Local Setup

This repository is a Django 6 application. The commands below match the local
virtual environment used during remediation.

## Windows PowerShell

```powershell
py -3.14 -m venv venv
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe manage.py migrate
.\venv\Scripts\python.exe manage.py test
.\venv\Scripts\python.exe manage.py check
.\venv\Scripts\python.exe manage.py makemigrations --check --dry-run
```

## Development Settings

Development defaults are intentionally local only:

- `DJANGO_DEBUG` defaults to `true`.
- `DJANGO_ALLOWED_HOSTS` defaults to `localhost,127.0.0.1`.
- `DJANGO_SECRET_KEY` defaults to a development-only placeholder.
- SQLite is used unless `DJANGO_DB_ENGINE` and related database variables are set.

For deployed environments, set at least:

```powershell
$env:DJANGO_SECRET_KEY = "<rotated-production-secret>"
$env:DJANGO_DEBUG = "false"
$env:DJANGO_ALLOWED_HOSTS = "example.com,www.example.com"
$env:DJANGO_SECURE_SSL_REDIRECT = "true"
$env:DJANGO_SESSION_COOKIE_SECURE = "true"
$env:DJANGO_CSRF_COOKIE_SECURE = "true"
```

Enable `DJANGO_SECURE_HSTS_SECONDS` only after HTTPS is verified end to end.
Start with a short staged value before considering a long preload duration.

PostgreSQL is required before production concurrency verification. Configure it
with `DJANGO_DB_ENGINE`, `DJANGO_DB_NAME`, `DJANGO_DB_USER`,
`DJANGO_DB_PASSWORD`, `DJANGO_DB_HOST`, and `DJANGO_DB_PORT`.
