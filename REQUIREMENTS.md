# BC_ladder Domain Requirements

This is the authoritative domain specification. Treat these rules as
non-negotiable unless the project owner explicitly changes them. Prefer the
current implementation when this document intentionally leaves a policy
configurable; never invent a replacement policy silently.

## Product and actors

BC_ladder helps registered players form doubles teams, publish availability,
book compatible matches, submit valid results, and participate in separate
Men's and Women's Doubles ladders.

- **Player:** manages their profile and availability, views authorized team and
  ladder data, acts on selected-lineup suggestions and matches, and submits
  scores for matches they played.
- **Administrator:** resolves membership and score-conflict work and may perform
  explicitly authorized operational actions.
- **Captain:** has no special powers unless a future approved specification
  defines them.

## Users and teams

- Player features require a registered, authenticated user.
- A user belongs to zero or one active team; a team has at most three active
  members.
- A playable lineup contains exactly two active members of the same team. A user
  never appears on both sides of one suggestion or match.
- Men's teams interact only with Men's teams and Women's teams only with Women's
  teams.
- Players may request team removal or joining; only an authorized administrator
  completes removal and resolves requests that require administration.
- Membership changes preserve historical suggestions, participants, matches,
  scores, standings, ledgers, and workflow records.
- Recorded match participants retain match read access after membership changes.
  Current non-selected teammates may retain read-only team visibility but gain
  no participant action rights.
- Enforce one active membership and the three-member limit in services and with
  database constraints where practical.

## Availability and lineups

- Availability belongs to an individual player. Persist timezone-aware values
  in UTC and display the configured club or user timezone.
- An end must be after its start. Reject duplicate or overlapping active windows
  for one player.
- Availability changes never silently invalidate a confirmed match.
- A confirmed match consumes or reserves the slot for exactly its four selected
  participants, never a non-selected third teammate.
- For up to three active teammates, find windows shared by at least two players
  and generate every unique two-player lineup once. Exclude inactive or removed
  members and keep output deterministic. Three members yield at most three
  unique lineups per shared slot.
- Match cancellation releases reservations and restores consumed availability
  when safe. If overlapping active availability already covers a player, keep
  the original record inactive and record that the overlap superseded it.

## Opponent suggestions and booking

- A suggestion contains two different active teams in one ladder, exactly two
  selected players per team, and one compatible active availability window.
- Exclude stale, cancelled, consumed, or conflicting availability and users or
  teams already committed during the slot.
- Use indexed or bucketed candidate lookup rather than an unnecessary all-team
  Cartesian product. Ranking proximity may order eligible candidates but never
  override eligibility. Finish with a stable deterministic tie-breaker.
- A match becomes confirmed only after both teams accept the same current
  suggestion. Repeated acceptance is idempotent.
- Confirmation runs in one database transaction: lock the suggestion and all
  availability or reservation rows that may be consumed; re-check membership,
  lineup, ladder, availability, version, and conflicts; create one match;
  reserve exactly four players; and commit once.
- Use `transaction.atomic()`, `select_for_update()`, uniqueness, and PostgreSQL
  exclusion constraints where applicable. A pre-transaction check alone is not
  sufficient.

## Match cancellation and history

- Any of the immutable four selected participants may immediately cancel a
  scheduled match on behalf of all four. Authorized administrators retain the
  same operational path.
- Cancellation is allowed only before the first score submission. Once a score
  exists, use the score-conflict/correction workflow.
- Cancellation has no user-entered reason. It is confirmed on a dedicated page;
  only POST changes state.
- A cancelled match is historical and never restored in place. Rebooking
  requires a new suggestion. The source suggestion remains confirmed as evidence
  that it produced the match.
- Successful retries return the existing outcome without duplicate state or
  workflow events. Rejected attempts do not create workflow events.

## Match scoring and standings

- Matches are best of three. The first team to win two sets wins the match, and
  every completed match has exactly one winner.
- Split regular sets require a deciding match tie-break to at least 10 points,
  won by two. `10-8`, `11-9`, and `12-10` are valid; `10-9`, `9-7`, and `8-6`
  are invalid.
- A deciding tie-break is invalid after one team already won both regular sets.
  Preserve the implementation's regular-set and 6-6 policy unless explicitly
  changed.
- Validate scores on the server. Score submission, official result selection,
  ladder updates, and point-ledger writes are atomic and idempotent.
- Do not update points until the submitted score is valid and the configured
  result-confirmation requirements are satisfied.
- Preserve the configured point algorithm and equal-points ordering unless an
  approved task changes them.
- Score conflicts remain actionable through `AdminNotification`; resolving one
  uses the audited official-submission correction workflow.

## Workflow event history

`WorkflowEvent` and its snapshotted recipients are append-only historical facts.
The application provides no update/delete service, Django admin is read-only,
references are protected, and workflows append at most one event for the first
successful state transition. There is no backfill, read/unread state, delivery
state, notification inbox, or email provider yet.

Record these event types:

- join request created, cancelled, approved, and rejected;
- match confirmed and cancelled;
- score submitted;
- score conflict created and resolved.

Recipient snapshots are:

- join created/cancelled: requesting player and every active `is_staff=True`
  user at that time;
- join approved/rejected: requesting player and resolving administrator;
- match confirmed/cancelled and score submitted: exactly the four selected
  participants;
- score conflict created: four selected participants and every active staff user;
- score conflict resolved: four selected participants and resolving
  administrator.

Store actor, type, timestamp, protected related-object references, previous/new
state, and a small event-specific JSON snapshot such as team names, player IDs,
scheduled time, cancellation restoration outcome, or score signature. Avoid
email addresses, unnecessary usernames, and arbitrary request data.

## Cross-cutting requirements

### Security and correctness

- Enforce object authorization in views and services; ordinary users cannot
  mutate another player's membership or availability, accept for an unrelated
  lineup, submit scores for an unrelated match, or edit points.
- Use Django authentication, password hashing, CSRF, forms, escaping, and safe
  HTTP methods. Validate identifiers through authorized querysets.
- Protect against stale writes, retries, IDOR, mass assignment, race conditions,
  and duplicate side effects.
- Keep secrets and unnecessary personal data out of source control and logs.

### Architecture and performance

- Keep domain rules in explicit services, views thin, forms responsible for
  input shape, and templates responsible for presentation.
- Use migrations for schema changes and preserve applied migration history.
- Prevent N+1 reads with `select_related()` and `prefetch_related()`, index common
  filters and joins, paginate unbounded lists, and use deterministic algorithms.
- Document time and space complexity for non-trivial matching changes; optimize
  measured bottlenecks rather than adding speculative complexity.

### Required verification

Tests must cover membership limits, lineup combinations, cross-ladder and stale
candidate rejection, deterministic suggestions, dual acceptance, competing
confirmations, exactly-four-player reservation, cancellation authorization and
score cutoff, availability restoration, valid/invalid scoring, duplicate writes,
atomic ladder updates, append-only event history, recipient snapshots, and query
regressions on important lists. PostgreSQL tests must prove production row-lock
and exclusion-constraint behavior that SQLite cannot reproduce.

## Decisions still open

Read the current implementation or obtain owner approval before changing:

- exact ladder point values and regular-set 6-6 policy;
- club timezone and availability granularity;
- captain permissions;
- notification delivery channels and provider;
- password-recovery and richer score-correction policy; and
- long-term production hosting, retention, backup, and monitoring policy.
