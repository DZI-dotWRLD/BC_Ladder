# Production Readiness

Assessment of `main` at `7c35fbb` (2026-09-24). It replaces the dated
`production-integration-qa.md` evidence record, which lives on in git history.
Update this file when a blocker closes or a new one is found. Keep it
evidence-based: cite code, and never mark an item done without proof.

## Verdict

The domain core is production-grade. The hosting and operations setup is
still trial-grade. Do not put real club data on the current deployment until
the three blockers below are closed.

## What is production-ready

| Area | Evidence |
| --- | --- |
| Fail-closed settings | With `DJANGO_DEBUG` unset, startup refuses a weak secret, local or wildcard hosts, a non-PostgreSQL database, insecure cookies, no HTTPS redirect, or a non-strict static manifest (`config/settings.py:297-390`). CI runs `check --deploy`. |
| Authorization | Every player view requires login. Mutations are POST-only with CSRF. Object scoping happens in both the view and the service. Suggestion creation uses a signed, user-bound, 30-minute command (`ladder/services.py:1016-1046`). |
| Transactional domain core | Ordered row locks, PostgreSQL exclusion constraints (migration `0012`), a per-division advisory lock for standings, unique dedupe keys, and immutable, audited score correction. See [booking-concurrency.md](booking-concurrency.md). |
| History preservation | `PROTECT` foreign keys. Workflow-owned admin models are read-only, and deletes are disabled (`ladder/admin.py:28-77`). Workflow events are append-only. |
| Email outbox | Rows are queued in the domain transaction, and delivery runs after commit on leased claims. SMTP is never called inside a transaction. Delivery is at-least-once. |
| Abuse protection | django-axes locks out after 5 login failures for 1 hour. The DB-backed limiter caps password-reset and verification-resend at 10/IP/hour and 3/email/hour, with a uniform response either way. |
| Privacy | Sentry is optional and scrubs PII keys (`config/sentry.py`). Logs carry IDs, not addresses. `anonymize_user` deactivates accounts without deleting history. |
| Operations tooling | `bootstrap_admin`, `audit_data_integrity`, `reconcile_standings`, `expire_suggestions`, `purge_rate_limit_events`, `send_notification_emails`, `/health/` and `/health/ready/`. |
| CI | Ruff, pip-audit, deploy check and strict `collectstatic`. The full suite (247 tests) runs on SQLite and on PostgreSQL 17, the latter including about 25 PostgreSQL-only contention tests. There is also a Playwright Chromium smoke test at five viewports. |

## Gate status

| Gate | Status | Evidence |
| --- | --- | --- |
| Booking/membership lock coordination (old "slice 4") | Closed | PR #12 (`315b43a`), `tests_booking_concurrency.py` |
| PostgreSQL verification | Closed in CI | `postgres` job in `.github/workflows/django.yml`. Confirm the latest `main` run is green on GitHub. |
| Production hardening tasks 1–15 | Merged | PRs #13, #14, #15 |
| First-admin provisioning | Done | `bootstrap_admin` (`379d9f5`) |
| Scheduled jobs defined | Done | Four cron services in `render.yaml` |
| Frontend pilot | Partly done | The frontend rebuild merged (PR #11). A further redesign is uncommitted WIP on `agent/phase-b1-frontend-pilot`. |
| Hosted rehearsal (registration to match result, 5 users) | **Open** | No evidence recorded |
| Paid hosting, backups, restore rehearsal | **Open** | `render.yaml` uses free plans; nothing automates backups |

## Blockers (must close before real club data)

1. **Free hosting.** The web service and PostgreSQL use `plan: free`
   (`render.yaml:5,172`). The free database expires after 30 days, has no
   backups and a 1 GB limit, and the web service sleeps when idle.
2. **No backup or restore.** [DEPLOYMENT.md](../DEPLOYMENT.md#backups) requires
   PITR, a daily off-provider `pg_dump`, and a restore rehearsal. None of these
   is automated or recorded.
3. **No hosted rehearsal.** The staging run from registration to match result
   with one administrator and four players has not been done.

## High risks (fix or consciously accept before launch)

4. **Likely HTTPS redirect loop on Render.** `render.yaml` sets
   `DJANGO_SECURE_SSL_REDIRECT=true` but not
   `DJANGO_SECURE_PROXY_SSL_HEADER_NAME/VALUE`. Render terminates TLS at its
   proxy, so Django probably sees HTTP and redirects forever, `/health/`
   included. Verify on the first deploy and add
   `HTTP_X_FORWARDED_PROTO` / `https` if needed. Also add a `healthCheckPath`.
5. **Registration is open to anyone.** PR #16 removed invite codes. Any
   verified account can create a team without admin approval
   (`create_team_for_player` makes an active membership directly), appear on
   the ladder, and request matches. `register` has no rate limit or CAPTCHA, so
   it can be used to spray verification email and burn the Gmail quota. This
   conflicts with "private club". Decide on approval, invites or an allowlist.
6. **Client IP behind the proxy.** The app's rate limits key on `REMOTE_ADDR`
   (`ladder/views.py:69,352`), which is probably the proxy address, so every
   visitor shares one bucket. Axes has no ipware proxy configuration. Username
   lockout also lets anyone lock a known player out for an hour.
7. **Migrations run in the build.** `build.sh` runs `migrate`,
   `bootstrap_admin` and `audit_data_integrity` during the build. A data finding
   blocks even a hotfix deploy, and the first build fails unless the
   bootstrap-admin variables and working SMTP are configured. Move these to a
   pre-deploy step on a paid plan.

## Medium risks

8. **Unbounded email retries.** `--retry-failed` runs every minute with no cap
   on attempts and no backoff (`attempts` is counted but never checked). One
   bad address can be retried about 1,440 times a day against Gmail SMTP
   limits.
9. **Some matches can never finish.** If only one team submits, or nobody
   plays, the match stays `scheduled` forever. The admin correction tool only
   handles score *conflicts*.
10. **Team names are not unique in the database.** Names are checked
    case-insensitively in the form and service only, so concurrent creation can
    produce duplicates.
11. **Anonymization is partial.** `anonymize_user_account` cancels
    availability and join requests only. Active memberships, open suggestions
    and scheduled matches remain.
12. **Personal data retention.** Axes `AccessAttempt`/`AccessLog` rows (IP,
    username, user agent) are kept indefinitely.
13. **Account activation happens on GET.** Email link scanners can activate
    an account, which is harmless but surprising.
14. **No monitoring.** Alerts for cron failures, uptime checks and Sentry
    all need manual setup.

## Low / cleanup

- The ladder page orders by `team__name` in the database, but stored positions
  use a case-insensitive name tie-break. These can differ only for teams tied
  on points, wins and losses.
- Unknown ladder divisions return 403 instead of 404.
- Transitive dependencies are unpinned, and there is no Dependabot.
- `scripts/quality.ps1` does not stop when a native command fails. Read each
  step's output.
- The Ladder nav link always opens the Men's ladder.

## Open product decisions (owner)

The open product decisions, such as point values, captain powers,
notification channels, score-correction policy and hosting/retention, are
listed in [REQUIREMENTS.md](../REQUIREMENTS.md#decisions-still-open). Blockers
5 and 9 above add two more: **who may join the club ladder**, and **how
unfinished matches close**.
