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
DATABASE_URL=<postgres-url>
DJANGO_SECURE_SSL_REDIRECT=true
DJANGO_SESSION_COOKIE_SECURE=true
DJANGO_CSRF_COOKIE_SECURE=true
DJANGO_LOG_LEVEL=INFO
DJANGO_EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
DJANGO_EMAIL_HOST=smtp.gmail.com
DJANGO_EMAIL_PORT=587
DJANGO_EMAIL_HOST_USER=<gmail-sender-address>
DJANGO_EMAIL_HOST_PASSWORD=<gmail-app-password>
DJANGO_EMAIL_USE_TLS=true
DJANGO_DEFAULT_FROM_EMAIL=BC Tennis Ladder <gmail-sender-address>
DJANGO_BOOTSTRAP_ADMIN_USERNAME=<initial-admin-username>
DJANGO_BOOTSTRAP_ADMIN_EMAIL=<initial-admin-email>
DJANGO_NOTIFICATION_DELIVERY_MODE=scheduled
```

`DATABASE_URL` is preferred for Render and other platforms that provide one
managed database URL. Manual deployments may instead set
`DJANGO_DB_ENGINE=django.db.backends.postgresql`, `DJANGO_DB_NAME`,
`DJANGO_DB_USER`, `DJANGO_DB_PASSWORD`, `DJANGO_DB_HOST`, and `DJANGO_DB_PORT`.
`DJANGO_DB_PORT` may be omitted when the database URL or host configuration uses
PostgreSQL's default port.

`DJANGO_CSRF_TRUSTED_ORIGINS` may be omitted for a same-origin deployment. If
set, every entry must be an explicit HTTPS origin. Do not use local development
origins or wildcards in production.

Password recovery requires a working SMTP account in every deployed
environment. Keep its credentials in the hosting provider's secret store, not
in source control. The example uses STARTTLS on port 587; for implicit TLS on
port 465, set `DJANGO_EMAIL_USE_TLS=false` and `DJANGO_EMAIL_USE_SSL=true`.
Verify the sender in your email provider before launch. Accounts created before
email capture was added need an administrator to set a unique email address.

`DJANGO_BOOTSTRAP_ADMIN_USERNAME` and `DJANGO_BOOTSTRAP_ADMIN_EMAIL` provision
the first superuser during the release build. The account starts with an
unusable password and receives a one-time password-reset link over the configured
email backend. `bootstrap_admin` is idempotent: after any superuser exists it
exits successfully with `Administrator already provisioned.` and sends no email.
Keep both values in the hosting provider's secret environment settings. A failed
initial email rolls back account creation so the next release can retry.

For Gmail SMTP, enable 2-Step Verification on the sender account and create an
app password. Store it only in the hosting provider's secret environment
settings. Do not commit it or use the account's normal password. Gmail API was
considered, but SMTP through Django is the initial provider seam because it
does not require OAuth token storage or extra Google client dependencies.

Notification rows are committed before delivery starts. A transient provider
failure does not roll back a match request or score conflict. Retry pending and
failed rows with:

```powershell
.\venv\Scripts\python.exe manage.py send_notification_emails --retry-failed
```

In production `scheduled` mode, run that command every minute; the request
process intentionally does not send notification mail. Treat a missing
one-minute schedule as a release blocker. The command also recovers expired `sending`
claims, even without retry flags; no separate worker dependency is required.
Claims last at least five minutes (or three times `EMAIL_TIMEOUT`, whichever is
longer; configure via `DJANGO_EMAIL_TIMEOUT`), and only one message is claimed
at a time. SMTP runs without an open
database transaction or row lock; token-checked completion cannot overwrite a
newer worker's claim. Attempts count claims, including interrupted attempts.

Schedule `python manage.py purge_rate_limit_events` daily. It removes only
throttle events older than 24 hours; current login lockouts remain managed by
django-axes and its one-hour cool-off.

Delivery is at-least-once, not exactly-once: a crash after SMTP acceptance but
before recording success, or a send exceeding its lease, can produce duplicates
on recovery. Set a finite SMTP timeout comfortably below the lease and monitor
failed/skipped deliveries and expired claims in read-only Django admin. `sent`
means the backend reported acceptance, not guaranteed inbox arrival. Queue rows
and recipient-user snapshots remain historical; retries read the user's current
email address so administrator corrections take effect. Never place provider
credentials or recipient addresses in logs or workflow metadata.

Deploy this schema migration before the new worker code. Do not roll back to
the old transaction-held SMTP sender while new workers have live claims; pause
delivery and resolve claims first. Rows skipped because a user has no valid email address
require correcting the user's email, then running:

```powershell
.\venv\Scripts\python.exe manage.py send_notification_emails --retry-skipped
```

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

## Render deployment trial

The repository includes `render.yaml` for a first Render Blueprint deployment:

- web service: `bc-ladder`
- database: `bc-ladder-db`
- runtime: Python
- plan: Render Free for trial only
- start command: `python -m gunicorn config.wsgi:application --workers 2 --threads 2 --timeout 30 --max-requests 500 --max-requests-jitter 50`
- build command: `bash build.sh`
- database configuration: `DATABASE_URL` from Render PostgreSQL

Render Free is appropriate for a first deployment trial, not production. Free
Render PostgreSQL databases expire after 30 days, have a 1 GB limit, and do not
include backups. Upgrade the database before storing real club data.

Because Render Shell and pre-deploy commands are not available on Free web
services, `build.sh` runs `migrate --noinput` and `audit_data_integrity` during
the free trial build. Remove that trial-only migration step before production
and move migrations to a controlled release/pre-deploy process.

Recommended first deploy sequence:

1. Push the branch to GitHub.
2. In Render, create a new Blueprint from this repository.
3. Let Render create the web service and PostgreSQL service from `render.yaml`.
4. Wait for the first build/deploy to finish.
5. Confirm the build logs show:

   ```bash
   python manage.py migrate
   python manage.py bootstrap_admin
   python manage.py audit_data_integrity
   ```

6. Visit `https://<render-host>/health/` and confirm `{"status": "ok"}`, then
   visit `/health/ready/` and confirm `{"status": "ready"}`.
7. Register a temporary player account through `/accounts/register/`.

`DJANGO_ALLOWED_HOSTS` is optional for the first `.onrender.com` trial because
the app accepts Render's `RENDER_EXTERNAL_HOSTNAME` as the default host. Set
`DJANGO_ALLOWED_HOSTS` explicitly when adding a custom domain.

Do not run migrations inside the start command. That can create startup races
and makes rollback harder.

## Smoke checks

After deploy:

```text
GET /health/ -> 200 {"status": "ok"}
GET /health/ready/ -> 200 {"status": "ready"}
GET /accounts/login/ -> 200
GET /accounts/password-reset/ -> 200
GET /ladders/mens/ as an authenticated user -> 200
GET /ladders/womens/ as an authenticated user -> 200
```

Both health endpoints are intentionally public and return `Cache-Control:
no-store`. `/health/` does not touch the database and verifies that the Django
process can route requests. `/health/ready/` runs `SELECT 1`; it returns HTTP 503
with `{"status": "degraded"}` when the database is unavailable.

Run `python manage.py expire_suggestions` to durably mark overdue proposed and
partially accepted suggestions as expired. The UI also treats overdue rows as
closed before the sweep runs, so an overdue request never presents an Accept
action.

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
