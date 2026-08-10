# BC_ladder Deployment Runbook

This runbook is the production checklist for a PostgreSQL-backed deployment.
SQLite remains a local-development database only.

## Required environment

Set these variables before starting the app:

```text
DJANGO_DEBUG=false
DJANGO_SECRET_KEY=<rotated-production-secret>
DJANGO_ALLOWED_HOSTS=example.com,www.example.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://example.com,https://www.example.com
DJANGO_DB_ENGINE=django.db.backends.postgresql
DJANGO_DB_NAME=<database>
DJANGO_DB_USER=<user>
DJANGO_DB_PASSWORD=<password>
DJANGO_DB_HOST=<host>
DJANGO_DB_PORT=5432
DJANGO_SECURE_SSL_REDIRECT=true
DJANGO_SESSION_COOKIE_SECURE=true
DJANGO_CSRF_COOKIE_SECURE=true
DJANGO_LOG_LEVEL=INFO
```

`DJANGO_CSRF_TRUSTED_ORIGINS` may be omitted for a same-origin deployment. If
set, every entry must be an explicit HTTPS origin. Do not use local development
origins or wildcards in production.

If the app is behind a trusted proxy or load balancer that terminates TLS, also
set both proxy variables:

```text
DJANGO_SECURE_PROXY_SSL_HEADER_NAME=HTTP_X_FORWARDED_PROTO
DJANGO_SECURE_PROXY_SSL_HEADER_VALUE=https
```

Start HSTS conservatively after HTTPS is verified end to end:

```text
DJANGO_SECURE_HSTS_SECONDS=300
```

Only enable `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS=true` and
`DJANGO_SECURE_HSTS_PRELOAD=true` after confirming every subdomain is HTTPS-only.

## First deployment sequence

Run from the application checkout:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe manage.py migrate
.\venv\Scripts\python.exe manage.py audit_data_integrity
.\venv\Scripts\python.exe manage.py collectstatic --noinput
.\venv\Scripts\python.exe manage.py check --deploy
```

Then start the WSGI/ASGI server configured by the hosting platform.

## Smoke checks

After deploy:

```text
GET /health/ -> 200 {"status": "ok"}
GET /accounts/login/ -> 200
GET /ladders/mens/ as an authenticated user -> 200
GET /ladders/womens/ as an authenticated user -> 200
```

`/health/` is intentionally public and does not touch the database. It verifies
that the Django process can route requests. Database health is covered by
migrations, `audit_data_integrity`, and application smoke checks.

## Static files

Static files are served by WhiteNoise from `STATIC_ROOT=staticfiles/`.
`collectstatic --noinput` must run before each deployment. The staticfiles
directory is generated and must not be committed.

## Backup and restore

Before production migrations:

```bash
pg_dump --format=custom --file=bc_ladder_before_migrate.dump "$DATABASE_URL"
```

For environments that do not expose `DATABASE_URL`, use equivalent `pg_dump`
host/user/database flags from the `DJANGO_DB_*` variables.

Restore rehearsal should be performed against a non-production database:

```bash
createdb bc_ladder_restore_test
pg_restore --dbname=bc_ladder_restore_test --clean --if-exists bc_ladder_before_migrate.dump
```

After restore, point a staging app at the restored database and run:

```powershell
.\venv\Scripts\python.exe manage.py migrate
.\venv\Scripts\python.exe manage.py audit_data_integrity
.\venv\Scripts\python.exe manage.py check --deploy
```

## Rollback notes

- Code rollback is safe only to a version compatible with the migrated schema.
- Database rollback requires a tested backup restore; do not rely on reversing
  production migrations after real users have written data.
- If `audit_data_integrity` fails, stop deployment and inspect the reported IDs
  before starting the new application version.
