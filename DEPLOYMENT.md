# BC_ladder Deployment Runbook

This runbook is the production checklist for a PostgreSQL-backed deployment.
SQLite remains a local-development database only. For what still blocks a
launch with real club data, see
[docs/production-readiness.md](docs/production-readiness.md).

## Required environment

Set these variables before starting the app:

```text
DJANGO_DEBUG=false
DJANGO_SECRET_KEY=<rotated-production-secret>
DJANGO_ALLOWED_HOSTS=example.com,www.example.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://example.com,https://www.example.com
DATABASE_URL=<postgres-url>
DJANGO_SECURE_SSL_REDIRECT=true
DJANGO_SESSION_COOKIE_AGE=1209600
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

`DJANGO_SESSION_COOKIE_AGE` is the session lifetime in seconds and defaults to
14 days (`1209600`). Session and CSRF cookies use `SameSite=Lax`. The enforced
Content Security Policy permits only same-origin scripts, styles, forms, and
images (plus `data:` images), and prevents framing.

`SENTRY_DSN` is optional. When unset, the Sentry SDK is not initialized and
application behavior is unchanged. When set, Django error tracking is enabled
with `send_default_pii=False`; email, password, authorization, and cookie fields
are filtered again by the application's `before_send` hook. Store the DSN in the
hosting provider's secret settings. On an existing Render Blueprint, add it
manually to the web and cron services because newly added `sync: false`
variables are ignored during updates.

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
at a time. With `--retry-failed`, a failed row is retried on every run. There
is no attempt cap or backoff, so watch `attempts` in admin and fix or skip
addresses that fail permanently before they exhaust the SMTP quota. SMTP runs without an open
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

Rows skipped because a user has no valid email address require correcting the
user's email, then running:

```powershell
.\venv\Scripts\python.exe manage.py send_notification_emails --retry-skipped
```

If the app is behind a trusted proxy or load balancer that terminates TLS, also
set both proxy variables. Render terminates TLS at its proxy, and
`render.yaml` does **not** set them yet. With `DJANGO_SECURE_SSL_REDIRECT=true`,
expect an HTTPS redirect loop until you add them. Verify on the first deploy:

```text
DJANGO_SECURE_PROXY_SSL_HEADER_NAME=HTTP_X_FORWARDED_PROTO
DJANGO_SECURE_PROXY_SSL_HEADER_VALUE=https
```

Start HSTS conservatively after HTTPS is verified end to end. Note that
`render.yaml` already sets the 300-second value below on the web service:

```text
DJANGO_SECURE_HSTS_SECONDS=300
```

Only enable `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS=true` and
`DJANGO_SECURE_HSTS_PRELOAD=true` after confirming every subdomain is HTTPS-only.

## First deployment sequence

Run from the application checkout:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe manage.py audit_user_emails   # only when upgrading a database created before migration 0016
.\venv\Scripts\python.exe manage.py migrate
.\venv\Scripts\python.exe manage.py bootstrap_admin
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
services, `build.sh` does all of the release work during the build. In order,
it runs a pip upgrade, `pip install -r requirements.txt`,
`migrate --noinput`, `bootstrap_admin`, `audit_data_integrity`, then
`collectstatic --noinput`, and it stops on the first failure. Consequences:

- **The first build fails** unless `DJANGO_BOOTSTRAP_ADMIN_USERNAME`,
  `DJANGO_BOOTSTRAP_ADMIN_EMAIL` and working SMTP credentials are set, because
  `bootstrap_admin` must create and email the first superuser.
- **Any integrity-audit error blocks every deploy**, hotfixes included.

Before production, move migrations, bootstrap and the audit to a controlled
pre-deploy step on a paid plan.

Every management command, including the cron jobs, runs the production
settings validation. That is why each cron service in `render.yaml` sets its
own generated `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS=bc-ladder.invalid`
and `DJANGO_SECURE_SSL_REDIRECT=true`. The cron jobs create no signed links
today. If a future cron sends signed links, it must share the web service's
secret key.

Recommended first deploy sequence:

1. Push the branch to GitHub.
2. In Render, create a new Blueprint from this repository.
3. Let Render create the web service and PostgreSQL service from `render.yaml`.
4. Wait for the first build/deploy to finish.
5. Confirm the build logs show:

   ```bash
   python manage.py migrate --noinput
   python manage.py bootstrap_admin
   python manage.py audit_data_integrity
   python manage.py collectstatic --noinput
   ```

6. Visit `https://<render-host>/health/` and confirm `{"status": "ok"}`, then
   visit `/health/ready/` and confirm `{"status": "ready"}`. If the browser
   reports too many redirects, add the proxy SSL header variables described
   above.
7. Register a temporary player account through `/accounts/register/`. New
   accounts stay inactive until the emailed verification link is opened, so
   this step needs working SMTP.

`DJANGO_ALLOWED_HOSTS` is optional for the first `.onrender.com` trial because
the app accepts Render's `RENDER_EXTERNAL_HOSTNAME` as the default host. Set
`DJANGO_ALLOWED_HOSTS` explicitly when adding a custom domain.

Do not run migrations inside the start command. That can create startup races
and makes rollback harder.

## Scheduled operations

`render.yaml` defines four independent cron jobs. Render schedules use UTC.

| Job | Schedule | Command |
| --- | --- | --- |
| Notification delivery | Every minute | `python manage.py send_notification_emails --retry-failed` |
| Suggestion expiry | Every 5 minutes | `python manage.py expire_suggestions` |
| Integrity audit | Daily at 02:00 UTC | `python manage.py audit_data_integrity` |
| Rate-limit cleanup | Daily at 02:30 UTC | `python manage.py purge_rate_limit_events` |

Render cron jobs are paid services and each has a minimum monthly charge. A
hosting tier that cannot run these schedules, or an external scheduler that has
not been configured with equivalent commands and monitoring, is a hard release
blocker. On an existing Render Blueprint, add the SMTP username, password, and
default sender to the notification cron service manually because Render ignores
new `sync: false` variables during Blueprint updates. Manually trigger every job
once, confirm a zero exit status, then check its next scheduled run. Alert on
failed or missing runs; do not assume a configured schedule is executing.

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

## Backups

Production requires both continuous point-in-time recovery (PITR) and an
automated daily logical backup stored outside the database provider. Render Free
Postgres provides neither and must not hold real club data. Before launch,
upgrade to paid Postgres, confirm the Recovery page shows an active PITR window,
and configure a daily encrypted `pg_dump` upload to restricted off-provider
storage with retention and deletion alerts. Retain at least 30 daily backups;
never keep the only backup on a Render service's ephemeral filesystem.

Before production migrations:

```bash
pg_dump --format=custom --file=bc_ladder_before_migrate.dump "$DATABASE_URL"
```

For environments that do not expose `DATABASE_URL`, use equivalent `pg_dump`
host/user/database flags from the `DJANGO_DB_*` variables.

For the daily job, use the same custom format with `--no-owner --no-privileges`,
record a SHA-256 checksum, upload the dump and checksum, verify their presence,
and only then expire old backups. Treat a missed backup, failed upload, or failed
PITR checkpoint as an incident.

Perform a restore rehearsal before launch and at least quarterly:

1. Download one daily backup and verify its checksum.
2. Create a disposable, non-production PostgreSQL database.
3. Restore without pointing any production service at it:

```bash
createdb bc_ladder_restore_test
pg_restore --dbname=bc_ladder_restore_test --clean --if-exists --no-owner --no-privileges bc_ladder_before_migrate.dump
```

4. Point a staging app at the restored database and run:

```powershell
.\venv\Scripts\python.exe manage.py migrate
.\venv\Scripts\python.exe manage.py audit_data_integrity
.\venv\Scripts\python.exe manage.py check --deploy
```

5. Run the smoke checks, verify representative memberships, suggestions,
   matches, scores, workflow events, and email-delivery rows, then record the
   backup timestamp, restore duration, row checks, and operator.
6. Rehearse PITR separately by restoring to a new database, validating it in
   isolation, and deleting the rehearsal instance only after results are
   recorded. Never overwrite the primary database during a rehearsal.

## Rollback

1. Stop the release if migrations, `audit_data_integrity`, readiness, or smoke
   checks fail. Pause the four cron jobs while database state is uncertain.
2. Record the failing commit, migration state (`python manage.py showmigrations`),
   and incident time. Preserve logs without copying emails or credentials.
3. Roll code back to the previous immutable commit only when that code is
   compatible with the current schema. Redeploy, run `check --deploy`, confirm
   `/health/ready/`, and repeat the smoke checks.
4. If the schema is incompatible or data is corrupted, restore with PITR to a
   new database from before the incident. Validate it in staging, then update
   `DATABASE_URL` for the web service and every cron job in one controlled
   cutover.
5. Resume cron jobs only after the web service and integrity audit are green.
   Monitor the notification queue and suggestion sweep on their next runs.

Do not reverse production migrations after real users have written data unless
the migration has an explicitly tested lossless reverse path. A backup or PITR
restore is the recovery boundary, not an improvised SQL edit.

## Incident runbooks

### SMTP outage

1. Confirm the web app and database are healthy. Match requests and score
   conflicts remain committed even when mail fails.
2. Pause the notification cron if the provider is rejecting every request.
   Inspect delivery status, attempt count, and error category in read-only admin;
   do not log recipient addresses or credentials.
3. Verify provider status, sender verification, SMTP host/port/TLS settings, and
   secret rotation. Send a provider test message outside the application.
4. Resume the cron and run
   `python manage.py send_notification_emails --retry-failed`. Use
   `--retry-skipped` only after correcting missing or invalid user emails.
5. Confirm pending/failed counts fall and record the affected workflow-event and
   delivery IDs, outage window, and remediation.

### Duplicate notification email

1. Locate the `EmailNotificationDelivery` and related `WorkflowEvent` by ID.
   Do not delete either historical row or replay the domain action.
2. If duplicates continue, pause the notification cron. Confirm only one
   scheduler is active and inspect claim tokens, lease expiry, attempt counts,
   worker termination, and whether SMTP duration exceeded `EMAIL_TIMEOUT`.
3. Remember delivery is at-least-once: SMTP may accept a message before a worker
   crashes without recording success. Explain that outcome to affected users
   without exposing other recipients.
4. Correct the scheduler or timeout issue, resume one worker, and monitor the
   next run. Never mark a row sent unless the delivery service does so.

### Stuck score conflict

1. Find the unresolved score-conflict `AdminNotification`, match, and both
   immutable submissions. Do not edit scores, standings, ledgers, or resolution
   flags directly.
2. Confirm an authorized administrator has reviewed the players' evidence and
   selected one existing submission as official.
3. In Django admin, select that `MatchResultSubmission` and run **Use selected
   submission as official score**. This calls the transactional service that
   updates the result, standings, ledger, audit row, notification, and workflow
   event together.
4. Run `python manage.py audit_data_integrity`, verify the match is completed,
   and record only object IDs and the administrator's incident note.

### Player data-removal request

1. Authenticate the requester outside application logs and record the request
   in the club's restricted support system using the user ID, not copied match
   or email data.
2. Run the supported command using the account's current username:

```bash
python manage.py anonymize_user <username>
```

   The command deactivates the account, removes its identifying user fields,
   assigns the stable `removed-<user-id>` username, sets an unusable password,
   and cancels active availability and pending join requests through domain
   services. It never deletes profiles, memberships, matches, scores, ledgers,
   workflow history, or other protected records.
3. Do not manually rewrite protected foreign keys or edit historical rows.
4. Run `python manage.py audit_data_integrity`, verify the account cannot authenticate, and
   record completion without placing the former email or name in workflow-event
   metadata.
