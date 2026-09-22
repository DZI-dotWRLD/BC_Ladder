# BC Ladder Architecture

## Purpose

BC Ladder is a server-rendered Django application for a private tennis club's
Men's and Women's Doubles ladders. Registered players form teams, publish
availability, receive compatible opponent suggestions, jointly confirm matches,
submit scores, and view standings. Administrators resolve membership requests
and score conflicts. PostgreSQL is the production database; SQLite supports
local development and portable tests.

The authoritative business rules are in [`REQUIREMENTS.md`](../REQUIREMENTS.md).
This document describes the implementation, not a replacement policy.

## Repository layout

| Path | Responsibility |
| --- | --- |
| `config/settings.py` | Environment parsing, Django integration, security policy, logging, email, and production fail-closed validation. |
| `config/urls.py`, `config/views.py` | Root routing plus liveness and database-readiness endpoints. |
| `ladder/models.py` | Persisted state, indexes, constraints, and small state-local validation. |
| `ladder/services.py` | Authorized transactional mutations, matchmaking, booking, scoring, and standings. |
| `ladder/forms.py` | Request input shape and user-facing validation. |
| `ladder/views.py`, `ladder/urls.py` | Authentication, object authorization, service orchestration, and HTTP responses. |
| `ladder/registration.py` | Atomic self-registration, normalized email identity, and email-verification tokens. |
| `ladder/workflow_events.py` | Append-only workflow-event creation and recipient snapshots. |
| `ladder/email_notifications.py` | Transactional email outbox queuing, claiming, rendering, and delivery. |
| `ladder/admin.py` | Operational administration with history and domain-invariant safeguards. |
| `ladder/templates/`, `ladder/static/` | Server-rendered presentation and progressive JavaScript. |
| `ladder/management/commands/` | Provisioning, maintenance, audit, recovery, and demo commands. |
| `ladder/tests_*.py` | Feature, integration, concurrency, admin, and hardening regression tests. |
| `docs/booking-concurrency.md` | Required lock order and booking/standings serialization protocol. |
| `docs/matchmaking-candidates.md` | Candidate-search algorithm, signed command, and complexity contract. |
| `DEPLOYMENT.md`, `render.yaml`, `build.sh` | Production configuration, scheduled jobs, backup, rollback, and incident operations. |

## Domain model

Django's `User` is the login identity. An inactive user cannot authenticate.
The `ladder` app owns these models:

| Model | Purpose and important states |
| --- | --- |
| `RateLimitEvent` | Opaque DB-backed timestamped event used for recovery and verification throttles. |
| `Team` | Ladder team in `mens` or `womens`; status is `active` or `retired`. |
| `PlayerProfile` | One-to-one user extension containing the player's ladder gender. Its active team is derived only from memberships. |
| `TeamMembership` | Historical player/team relationship; status is `join_requested`, `active`, or `inactive`. `removal_requested` remains a legacy choice; current removal requests keep `active` status and set `removal_requested_at`. |
| `LadderStanding` | One row per team with position, matches, wins, losses, and points. |
| `AvailabilitySlot` | Player interval; status is `active`, `consumed`, or `cancelled`. `reserved` remains a legacy choice; confirmed bookings use `MatchReservation`. |
| `MatchSuggestion` | Proposed exact teams, interval, and expiry; status is `proposed`, `partially_accepted`, `confirmed`, `declined`, `expired`, or `cancelled`. |
| `SuggestionParticipant` | Immutable selected player, team, side, and lineup order for a suggestion. |
| `SuggestionAcceptance` | At most one acceptance per suggestion/team, recording the accepting user. |
| `Match` | Historical match produced by an optional source suggestion; status is `scheduled`, `completed`, or `cancelled`. |
| `MatchParticipant` | Immutable selected player/team/side snapshot for a confirmed match. |
| `MatchReservation` | Per-player interval reservation; status is `active` or `released`. PostgreSQL excludes overlapping active reservations. |
| `MatchResultSubmission` | One immutable submission per match/team with submitting user and set-win totals. |
| `MatchResultSet` | Ordered regular set or `match_tiebreak` score belonging to a submission. |
| `ConfirmedMatchResult` | One official winner/loser and source submission per completed match. |
| `PointLedger` | Idempotent points effect per match/team/reason. |
| `ScoreCorrectionAudit` | Protected administrator selection of an official conflicting submission, including prior and new winner/loser IDs. |
| `AdminNotification` | Unique unresolved operational notification; currently type `score_conflict`. |
| `WorkflowEvent` | Append-only domain event with protected object references, state transition, dedupe key, and privacy-limited metadata. |
| `WorkflowEventRecipient` | Snapshotted user recipient for a workflow event. |
| `EmailNotificationDelivery` | Per-event/user outbox row; type is `match_request` or `score_conflict`, and status is `pending`, `sending`, `sent`, `failed`, or `skipped`. |

Historical participants, memberships, matches, results, ledgers, corrections,
workflow events, and delivery rows are preserved. Foreign-key protection and
service rules prevent a later membership or account change from rewriting the
record of a completed workflow.

## State machines

### Membership

```text
join_requested ── approve ──> active
       │                         │
       ├─ reject/cancel ──> inactive
       │                         ▲
       └─────────────────────────┘

active ── request removal ──> active + removal_requested_at
  active + removal_requested_at ── reject ──> active
  active + removal_requested_at ── approve ──> inactive
```

Only administrators resolve requests. A player has at most one active
membership, and a team has at most three active members.

### Suggestion and booking

```text
proposed ── first team accepts ──> partially_accepted
    │                                  │
    ├─ expiry sweep/lazy expiry ──> expired
    │                                  ├─ expiry ──> expired
    │                                  └─ second team accepts ──> confirmed ──> one scheduled Match
    ├─ policy transition ──> declined
    └─ policy transition ──> cancelled
```

Acceptances are idempotent. A confirmed retry returns the existing match.
Selected participants and the interval have no mutation service and are
revalidated under the suggestion lock.

### Match and reservation

```text
scheduled ── agreed valid score ──> completed
scheduled ── selected participant/admin cancels before scoring ──> cancelled
active reservation ── match cancellation ──> released
consumed source availability ── safe cancellation restore ──> active or cancelled-as-superseded
```

Cancelled and completed matches remain historical and are never restored in
place.

### Scoring

```text
no submissions ── one team submits ──> waiting_for_submissions
waiting_for_submissions ── identical opponent submission ──> confirmed ──> completed
waiting_for_submissions ── different opponent submission ──> conflict
conflict ── administrator selects existing submission ──> resolved/completed
```

Score validation is best of three. Split regular sets require a deciding match
tie-break to at least 10, won by two. Finalization writes the confirmed result,
two idempotent ledger entries, standings, match state, and position changes in
one transaction.

### Email delivery

```text
pending ── claim ──> sending ── provider accepts ──> sent
                         ├─ provider/render failure ──> failed
                         ├─ invalid/missing recipient ──> skipped
                         └─ lease expires ──> eligible to reclaim
failed/skipped ── explicit retry flags ──> sending
```

Delivery is at-least-once because an SMTP provider can accept a message before
a worker records `sent`.

## Service API and actor rules

Domain failures are `DomainError` subclasses: `InvalidInput`, `StaleState`,
`AuthorizationFailure`, `BookingCollision`, and `ProvisioningError`.

| Service | Actor/authorization contract | Transactional effect |
| --- | --- | --- |
| `provision_first_administrator` | Trusted release command; only acts when no superuser exists. | Creates one unusable-password superuser and sends its reset link atomically. |
| `rate_limit_key`, `enforce_rate_limit` | Public recovery/verification endpoints provide normalized opaque identity keys. | Enforces a rolling DB count; PostgreSQL serializes by advisory lock. |
| `recalculate_ladder_positions` | Internal/trusted standings caller. | Reorders one division without changing configured points or equal-points policy. |
| `reconcile_ladder_standings` | Operational command or trusted repair path. | Rebuilds standing totals from confirmed results under the division gate. |
| `request_membership_change` | The player themself or staff. | Requests/creates a join or records a removal request under player/team locks. |
| `create_team_for_player` | The player themself or staff. | Creates a team, initial membership, standing, and position update. |
| `cancel_join_request` | The player themself or staff. | Cancels one pending join request and appends its event idempotently. |
| `anonymize_user_account` | Trusted `anonymize_user` command only. | Cancels open player state through services, removes identity fields, and deactivates without deleting history. |
| `resolve_membership_request` | Staff only. | Approves/rejects a join or removal request and appends the applicable join event. |
| `save_availability`, `cancel_availability` | Authenticated owner only. | Creates a non-overlapping interval or cancels an unreserved active interval. |
| `get_team_players`, `get_player_pairs`, `generate_team_lineups` | Read helpers; caller must already be allowed to view the team. | Return active roster members and deterministic two-player overlaps. |
| `find_opponent_suggestions` | Read helper for an authorized team page. | Returns deterministic, eligible candidates without persisting them. |
| `candidate_identity`, `sign_candidate` | `sign_candidate` binds the command to the authenticated requesting user. | Serializes/signs exact team, lineup, interval, and source-window identity. |
| `create_match_suggestion_from_candidate` | Authenticated active member of the requesting team. | Revalidates the signed command against fresh candidates, then creates the suggestion. |
| `create_match_suggestion` | Authenticated active requesting-team member; `actor=None` is reserved for trusted seed/service paths. | Locks parents/sources, deduplicates, persists participants/event/outbox rows. |
| `expire_open_suggestions` | Scheduled command or trusted maintenance caller. | Locks and expires overdue open suggestions without emitting workflow events. |
| `accept_suggestion` | One of the four selected active players. | Records side acceptance; the second side atomically creates the match, participants, reservations, and event. |
| `cancel_match` | An immutable selected participant or staff. | Cancels an unscored scheduled match and releases/restores booking state. |
| `validate_regular_set`, `validate_match_tiebreak`, `validate_match_score` | Pure validation; no actor. | Returns normalized winner/score data or raises `InvalidInput`. |
| `submit_match_result` | An immutable selected match participant. | Creates that team’s one submission/event and triggers confirmation or conflict handling. |
| `get_submissions`, `submissions_match`, `get_match_status`, `get_match_winner_and_loser` | Read helpers; caller must already have match access. | Loads and derives score state without mutation. |
| `create_admin_notification_for_conflict` | Internal scoring workflow. | Idempotently creates the conflict notification, event, and staff email outbox rows. |
| `finalize_match_result` | Internal scoring workflow after matching submissions. | Applies official result, ledger, standings, and completion exactly once. |
| `resolve_score_conflict` | Staff only. | Selects an existing submission and atomically corrects result, ledger, standings, audit, notification, and event. |

## Concurrency protocol

All booking-eligibility mutations lock stable `PlayerProfile` parents in
ascending primary-key order, followed by teams, the suggestion or membership,
availability, and reservations. Confirmation rechecks the exact four players,
active memberships, teams/division, source availability, expiry, and conflicts
inside the same transaction. PostgreSQL exclusion constraints are the final
guard against overlapping active availability and reservations.

Standings writers acquire a transaction-scoped PostgreSQL advisory lock per
division before standing rows. This serializes first-row creation and
division-wide position recalculation, avoiding pairwise lock cycles. SQLite
tests prove state and rollback behavior; PostgreSQL-specific tests prove row
locking and exclusion behavior. The exact lock order and complexity contract
are in [`booking-concurrency.md`](booking-concurrency.md); candidate-search
complexity is in [`matchmaking-candidates.md`](matchmaking-candidates.md).

## Accept-suggestion request flow

1. `accept_suggestion_view` requires login, loads a suggestion visible to the
   player's selected lineup, and accepts only POST with CSRF protection.
2. The view calls `accept_suggestion`; it does not perform domain writes.
3. The service resolves the actor's profile, starts a transaction, locks all
   selected profiles in stable order, locks both teams, then locks the
   suggestion.
4. It rechecks participant identity, actor selection, active memberships,
   team/division eligibility, status, and expiry.
5. The service idempotently creates the actor side's `SuggestionAcceptance`.
   With one accepted side it commits `partially_accepted`.
6. With both sides accepted it locks compatible availability and conflicting
   reservations, then rechecks the exact interval.
7. It creates one `Match`, four immutable `MatchParticipant` rows, four active
   reservations, consumes only those four source windows, confirms the
   suggestion, and appends the recipient-snapshotted workflow event.
8. Commit makes the outcome visible. A conflict rolls back the second
   acceptance and every booking side effect; an already-confirmed retry returns
   the existing match.

## Email outbox and retry operations

Match-request and score-conflict events create one unique
`EmailNotificationDelivery` per event/user inside the domain transaction.
`transaction.on_commit` then follows `DJANGO_NOTIFICATION_DELIVERY_MODE`:

- `inline`: deliver after commit in the caller; default in development/tests.
- `thread`: start a daemon thread with its own database connection.
- `scheduled`: return immediately and rely on the one-minute scheduler;
  production default.

Workers claim rows with a UUID lease, release the database lock before network
I/O, never store recipient addresses in event metadata, and record only safe
error categories. Run the production worker with:

```bash
python manage.py send_notification_emails --retry-failed
```

After correcting a missing/invalid user address, explicitly include
`--retry-skipped`. Account verification, password reset, and bootstrap-admin
messages are recovery-class email and intentionally bypass the workflow outbox.

## Management commands

| Command | Purpose |
| --- | --- |
| `anonymize_user <username>` | Deactivate/anonymize an account and cancel active availability/pending joins without deleting history. |
| `audit_data_integrity [--fail-level error|warning]` | Read-only production integrity audit with a non-zero exit at the configured severity. |
| `audit_user_emails` | Report duplicate normalized-email user IDs without logging addresses. |
| `bootstrap_admin` | Idempotently provision the first superuser from environment variables and email a reset link. |
| `expire_suggestions` | Mark overdue open suggestions expired. |
| `purge_rate_limit_events` | Delete rate-limit events older than 24 hours. |
| `reconcile_standings [--division mens|womens]` | Rebuild standings from confirmed history. |
| `seed_demo [--allow-non-debug]` | Create deterministic demo data; guarded outside debug/safe databases. |
| `send_notification_emails [--retry-failed] [--retry-skipped]` | Deliver pending/reclaimable outbox rows and requested retry classes. |

`migrate`, `collectstatic`, `check --deploy`, and the standard Django test
command remain part of release operations. Scheduling, backup, rollback, and
incident procedures are in [`DEPLOYMENT.md`](../DEPLOYMENT.md).

## Configuration variables

| Variable | Purpose/default |
| --- | --- |
| `DJANGO_DEBUG` | Debug switch; defaults false and production validation fails closed. |
| `DJANGO_SECRET_KEY` | Required rotated production signing secret. |
| `DJANGO_ALLOWED_HOSTS`, `RENDER_EXTERNAL_HOSTNAME` | Explicit deployed hosts; Render hostname can supply the default. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Optional comma-separated explicit HTTPS origins. |
| `DATABASE_URL` | Preferred production PostgreSQL URL. |
| `DJANGO_DB_ENGINE`, `DJANGO_DB_NAME`, `DJANGO_DB_USER`, `DJANGO_DB_PASSWORD`, `DJANGO_DB_HOST`, `DJANGO_DB_PORT` | Manual database configuration when `DATABASE_URL` is absent. |
| `DJANGO_TIME_ZONE` | Club timezone; defaults `America/New_York`. |
| `DJANGO_LOG_LEVEL` | Root log level; defaults `INFO`. |
| `DJANGO_EMAIL_BACKEND`, `DJANGO_EMAIL_HOST`, `DJANGO_EMAIL_PORT`, `DJANGO_EMAIL_HOST_USER`, `DJANGO_EMAIL_HOST_PASSWORD`, `DJANGO_EMAIL_USE_TLS`, `DJANGO_EMAIL_USE_SSL`, `DJANGO_EMAIL_TIMEOUT`, `DJANGO_DEFAULT_FROM_EMAIL` | Recovery and notification email transport; production uses SMTP. |
| `DJANGO_PASSWORD_RESET_TIMEOUT` | Reset/verification token lifetime; defaults 3600 seconds. |
| `DJANGO_NOTIFICATION_DELIVERY_MODE` | `inline`, `thread`, or `scheduled`; production defaults `scheduled`. |
| `DJANGO_BOOTSTRAP_ADMIN_USERNAME`, `DJANGO_BOOTSTRAP_ADMIN_EMAIL` | First-administrator identity used by `bootstrap_admin`. |
| `DJANGO_SECURE_SSL_REDIRECT` | HTTPS redirect; required true in production. |
| `DJANGO_SESSION_COOKIE_AGE` | Session lifetime in seconds; defaults 1209600 (14 days). |
| `DJANGO_SESSION_COOKIE_SECURE`, `DJANGO_CSRF_COOKIE_SECURE` | Secure-cookie flags; required true in production. SameSite is fixed to `Lax`. |
| `DJANGO_SECURE_PROXY_SSL_HEADER_NAME`, `DJANGO_SECURE_PROXY_SSL_HEADER_VALUE` | Optional trusted reverse-proxy HTTPS header pair. |
| `DJANGO_SECURE_HSTS_SECONDS`, `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS`, `DJANGO_SECURE_HSTS_PRELOAD` | HSTS rollout controls. |
| `DJANGO_SECURE_REFERRER_POLICY` | Referrer policy; defaults `same-origin`. |
| `DJANGO_WHITENOISE_MANIFEST_STRICT` | Strict static manifest; required true in production. |
| `SENTRY_DSN` | Optional Sentry activation; unset means no SDK initialization. |

The enforced CSP is same-origin for default, scripts, styles, and forms; images
also allow `data:` and `frame-ancestors` is `none`. Copy `.env.example` for
explicit local-development defaults. Never commit real secrets.

## Running tests and quality checks

From the repository root with the virtual environment installed:

```powershell
$env:DJANGO_DEBUG = "true"
.\venv\Scripts\python.exe manage.py test --noinput
.\venv\Scripts\python.exe manage.py check
.\venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\venv\Scripts\python.exe -m ruff check .
.\venv\Scripts\python.exe -m ruff format --check .
git diff --check
```

Use `scripts/quality.ps1` on Windows or `scripts/quality.sh` on POSIX systems
for the repository quality sequence. Run PostgreSQL CI/tests for exclusion
constraints, advisory locks, and row-contention guarantees that SQLite cannot
prove.
