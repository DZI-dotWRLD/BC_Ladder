# Production integration QA — 2026-09-14

## Outcome and scope

Local integration of slices 1, 2, 3, 5, 6 and 7 is verified on SQLite and
Chromium. This is not production approval: slice 4 (booking/membership lock
coordination), slice 8 (deployment/recovery rehearsal), and PostgreSQL
verification remain open.

Branch: `codex/production-integration-qa`.
Base: `2dc33de615693aacdf2942e6e23d4aba95cf7f7d`.
Reviewed code: `18374d33ddc5ed295915c58b0ff31984370290c7`.

Six source commits were cherry-picked locally with their intents preserved:
`7f3e738`, `4fa4f62`, `37ee7da`, `8913748`, `ac69bca`, and `60dd40b`.
No push, main merge, live deployment, real SMTP, club-data mutation, or
dependency installation was performed. The original checkout and its unrelated
changes were preserved.

## Behavior and integration corrections

- Workflow-owned admin records prohibit direct writes; explicit operational
  actions retain the underlying model-change permission check. Request action
  identifiers and acceptance versions are validated and authorization-scoped.
- Registration commits user/profile atomically and handles identity races.
  A stock-auth expression unique index protects nonblank normalized email.
- Matchmaking uses four querysets and temporal buckets. Signed commands bind
  the requesting actor to exact teams, players, source windows and interval.
  Discovery is explicit (`?discover=1`), not triggered by every composed page.
- Authorized open-suggestion expiry persists; availability uses configured
  timezone and rejects ambiguous/nonexistent naive DST inputs.
- Email claims are leased in short transactions; SMTP runs outside those
  transactions, with token-aware completion and expired-claim recovery.
- Match actions distinguish participants, submissions, conflict and official
  result. Lists are paginated, composition is lazy, and error pages are defined.

Independent QA exposed three defects that were fixed with regressions:

1. Confirmed acceptance retries after expiry now return the existing match
   after authorization/version checks, without changing source history.
2. Reservation reads cover full loaded source-window bounds, not merely the
   search horizon, while retaining four matcher queries.
3. Imported query-only pagination links resolve against their source route,
   not the containing dashboard/availability page.

Existing request tests were updated to use explicit discovery. Conflicting
form/import changes preserve both candidate and acceptance validation.

## Database and deployment treatment

Both original `0016` migrations are unchanged. New, operation-free
`0017_merge_production_slices` depends on both leaves. A fresh disposable SQLite
database applied the complete graph successfully.

Before deploying these migrations, take a tested backup and run
`python manage.py audit_user_emails` before `migrate`. Duplicate nonblank
normalized emails require owner/administrator remediation; migrations fail
safely without merging or deleting accounts. Blank legacy addresses remain
allowed. Index creation may block auth-table writes, so use controlled release
timing. Future stock-auth table rebuilds must preserve the ladder-owned index;
SQLite and PostgreSQL casing behavior differs for non-ASCII addresses.

Configure the existing `send_notification_emails` recovery command through the
deployment platform. Delivery is at-least-once, not exactly-once: provider
acceptance followed by a crash or lease overrun can cause a duplicate message.
Rollback must use schema-compatible code and a rehearsed recovery procedure;
no real backup/restore or rollback was performed here.

## Verification evidence

All Python commands used the existing root `venv/Scripts/python.exe`, with the
integration worktree as the working directory. Test databases were explicitly
isolated SQLite; email used the locmem backend.

| Check | Result |
| --- | --- |
| Combined focused admin/registration/expiry/frontend tests | 34 discovered, OK, 1 PostgreSQL skip |
| Confirmed-retry and timezone regressions | 15 tests, OK |
| Final candidate/discovery/query-bound regressions | 11 tests, OK |
| Full combined suite | 204 discovered, OK, 18 PostgreSQL skips, 68.178 seconds |
| Registration and dual-acceptance request checks with normal hashers | 11 discovered, OK, 1 PostgreSQL skip |
| `manage.py check` | Pass |
| `manage.py makemigrations --check --dry-run` | Pass, no changes |
| `manage.py migrate --noinput` on fresh QA database | Pass, both leaves and merge applied |
| `audit_user_emails` and `audit_data_integrity` | Pass before browser run and after official scoring |
| `ruff check .` and `ruff format --check .` | Pass, 52 Python files formatted |
| `git diff --check` and committed-patch whitespace check | Pass |
| `node --check` for app.js and browser script | Pass |
| `node tests/frontend/check.mjs` | Chromium pass, run by frontend agent on root-prepared QA server |
| `pip_audit -r requirements.txt` | No known vulnerabilities found |
| `manage.py check --deploy` using synthetic CI-like configuration | No errors; HSTS W005/W021 warnings |
| `manage.py collectstatic --noinput` with strict production manifest | 137 copied, 405 post-processed |

The full suite used a process-only fast fixture hasher, not a production
settings change. Its actual invocation, after setting the isolated environment,
was:

```powershell
python -c 'import os; os.environ["DJANGO_SETTINGS_MODULE"]="config.settings"; import django; django.setup(); from django.conf import settings; settings.PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"]; from django.core.management import call_command; call_command("test", interactive=False, verbosity=1)'
```

Normal-hasher verification ran:

```powershell
python manage.py test ladder.tests.PhaseARequestTests.test_suggestion_create_and_dual_acceptance_flow ladder.tests_registration_integrity --noinput
```

The first combined run failed one older request test that omitted `discover=1`;
the test was corrected and the final full suite rerun successfully.

Browser coverage includes five viewport sizes, keyboard skip navigation,
invalid availability POST, failed composition/no-JavaScript fallbacks, and
dual-team score submission through the official result. Composed pagination
uses injected links in real Django responses to verify real source-route GET
navigation; database match-page contents are covered separately by request
tests. Firefox/Safari, complete assistive-technology/manual state review, and
approved visual baselines are not established by this pass. Screenshots and
the disposable database remain untracked under the worktree's `output/`.
The root-owned QA server was stopped after auditing.

## Independent review and remaining gates

### Standards

No remaining hard documented violations or high/critical findings identified
in the reviewed integration. One low-priority judgement concern remains:
email delivery reloads related event content and rerenders it per recipient.
Consider one content load/render outside claim transactions without changing
individual claims or recipient privacy.

### Spec

No remaining new actionable spec/security findings identified. The confirmed
history and reservation-horizon regressions were rechecked after correction.
Both reviewers inspected source/diffs independently; they did not independently
rerun root/implementation-agent test commands.

Summary: Standards 0 hard violations, 1 low-priority heuristic concern; Spec 0
remaining new findings. This does not remove the following release gates:

- PostgreSQL execution: no local PostgreSQL installation was found. Installed
  Docker had no running engine; an attempted background start did not expose a
  usable engine. The 18 production-engine tests remain skipped, including new
  email/registration contention cases. New integration code has not run in
  GitHub CI because nothing was pushed.
- Slice 4: shared lock order and active team/membership checks during candidate
  creation, removal and confirmation need implementation and PostgreSQL proof.
- Slice 8: first-admin provisioning, production hosting, controlled release
  migrations, monitoring, scheduled retries, tested restore/rollback and hosted
  five-user rehearsal remain unimplemented/unverified.
- HSTS subdomain/preload warnings are deliberate conservative configuration;
  enable those flags only after the deployment owner verifies their domains.

Next: use this reviewed integration as the local prerequisite base for slice 4,
obtain a usable isolated PostgreSQL environment, rerun the combined gates, and
then proceed to deployment rehearsal. Do not release real club data yet.
