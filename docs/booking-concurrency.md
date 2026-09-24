# Booking and membership serialization

The application retains arbitrary aware intervals, existing ladder/scoring
policies, signed exact candidate commands, and historical match participants.
This protocol is implemented in `ladder/services.py` (merged in PR #12).
PostgreSQL exclusions (migration `0012`) remain the final protection against
overlapping active availability/reservations.

## Stable parent locks

Every mutation that can change a player's booking eligibility starts by locking
persisted `PlayerProfile` rows in ascending primary-key order. Booking takes all
four selected profiles and the creating/accepting actor profile (a non-selected
third teammate may create but cannot accept). Membership changes lock their
player first. Availability save/cancel lock their player first. Cancellation
locks immutable match participant profiles first.

When team eligibility/capacity matters, acquire `Team` rows next, ascending PK,
then the suggestion or membership row, then related availability rows ascending
PK, and then reservation rows ascending PK. Joined locking querysets use
`of=("self",)` so PostgreSQL does not implicitly acquire player/team locks in a
different order. Child rows may be omitted when irrelevant: cancellation uses
profiles then match without requiring current team membership or team status.
Team parents use PostgreSQL `FOR NO KEY UPDATE`: IDs remain immutable, so
foreign-key `KEY SHARE` references from scored history can proceed without
waiting on eligibility/capacity transactions. Status and capacity mutations
still conflict on the same team parent. This avoids an FK-reference lock cycle
between standings writers and team/membership mutations.

Membership request resolution no longer starts with the membership row before
its player; removal and confirmation therefore share a serialization point.
Join requests lock the player's current/target teams before memberships. Team
creation and join cancellation already start at the player and do not later
acquire any additional existing player locks.

Profile parents exist before a reservation or suggestion exists. This protects
the check-empty-then-insert path that locking only child rows cannot protect.
Duplicate trusted/signed creation locks the same selected profiles, compares
equivalence by team-to-unordered-player pairs and exact interval in either team
perspective, and returns the first open suggestion. Its persisted orientation,
participants, event, and email lineage are not rewritten. New lineups are stored
in stable player-ID order. `actor=None` remains the trusted seed/service path;
when provided, the actor must be an authenticated active member of requesting
team A. Both paths revalidate current teams, persisted memberships, all four
exact source IDs/bounds, active availability, and reservation conflicts.

## Acceptance and historical outcomes

Inside one transaction, acceptance acquires stable parents and the suggestion,
checks selected-player authorization, current active teams, exact side-to-team
mapping, two players per side/four distinct players, persisted active membership,
and existing player/ladder eligibility. Suggestions have no participant or
interval mutation service, so the unused optimistic version and client version
input were removed; participant identity is re-read under the locked suggestion
transaction instead. Second-team acceptance locks compatible
availability and reservation rows, rechecks eligibility/conflicts, and creates
one match, four immutable participants, four reservations, consumes only those
four source windows, and appends one event. Conflict or stale failures roll back
the second acceptance and all booking side effects.

Authorized expiry is still committed before its controlled stale error is
raised. Confirmed retries return the original match before current membership
or expiry checks, including after a selected player is removed. If removal
wins first, new booking rejects; if confirmation wins first, later removal is
allowed and does not erase/invalidate the historical match or reservations.
Cancellation uses historical participant authorization, releases reservations,
and restores safe consumed availability. It locks active player windows plus
referenced source windows, not every historical availability row.

Scoring/finalization/standings joined locks are scoped to their own rows to avoid
implicit unordered team locks; point values and equal-points ordering are
unchanged. Score correction retains the notification/match lock while scoping
its submission lock to the submission, without implicitly locking its team.
SQLite proves portable state guards/rollback/history, not production row locks.
Dedicated PostgreSQL tests use separate connections and observe a
contender's attempted shared profile `FOR UPDATE` while a winning transaction
owns that parent; they assert exact durable outcomes and no hung worker.

Booking lock-set sorting is O(p log p + t log t + a log a + r log r), with p at
most five profiles, t two teams, a compatible source rows, and r intersecting
reservations; additional space O(p+t+a+r). Cancellation loads active windows and
the match's referenced sources rather than total historical windows. Relevant
database lookups use existing PK, membership, availability, and reservation
indexes. Matchmaking read queries/ordering and configured interval policy remain
unchanged. Only service-mediated mutations participate in this protocol;
privileged direct SQL/admin domain edits still require operational discipline.

## Division-wide standings serialization

Sorting only a match's pair of standing rows is insufficient: an A/B update can
hold A while waiting for B, while a B/C update holds B and reaches A during the
subsequent division-wide position recalculation. Every service standings writer
therefore acquires a PostgreSQL transaction-scoped advisory lock before reading,
creating, or locking its first standing row. The shared lock exists even when a
division has no standing rows, so concurrent first-row creation is covered.

The advisory namespace is `0x42434C44` (`BCLD`), with key 1 for Men's Doubles and
key 2 for Women's Doubles. Key 3 serializes any legacy unrecognized division.
Operations affecting multiple divisions acquire their keys in numeric order;
unfiltered reconciliation takes all three before reading teams or results.
PostgreSQL releases these locks automatically when the outer transaction ends,
including rollback. Re-entering a lock in a nested helper retains that same
transaction boundary. SQLite uses its existing transaction behavior and does
not execute the PostgreSQL advisory statement.

The scoring order is the match (and applicable notification/submission), then
all affected division keys, then standing rows in team-ID order, then result,
ledger, and position writes. Corrections acquire every old/new affected division
before decrementing the previous result. Recalculation and the single/pair
standing helpers also acquire the shared gate, so standalone recalculation and
team creation participate. Team creation holds its player/new team before the
gate; standings operations do not acquire player locks or existing team update
locks after the gate. Reconciliation takes the gate before its confirmed-result
snapshot and updates standings in team-ID order. This prevents both lock cycles
and rebuilding from a snapshot taken before a concurrent result committed.

The protocol adds a constant number of advisory-lock queries per result and
serializes standings writes within a division. Position calculation retains its
existing O(n log n) time and O(n) space for n standings. It changes neither points,
equal-points ordering, result history, nor ledger idempotency. Direct SQL and
disposable demo-data reset commands require exclusive operational use and do
not provide the live-service concurrency contract.

## Verification

Run the focused service/request and contention coverage with the repository's
Python 3.14 environment:

```powershell
python manage.py test ladder.tests_booking_concurrency ladder.tests_phase_b --noinput
python manage.py check
python manage.py makemigrations --check --dry-run
python -m ruff check .
python -m ruff format --check .
git diff --check
```

Run the same focused test command against PostgreSQL to exercise the separate
connections; SQLite intentionally skips that class. Tests cover creation from
both signed and trusted candidates, both confirmation/removal orderings,
competing suggestions with initially absent reservations, availability
cancellation winning a race, stale membership and malformed roster rejection,
historical access/retries, and transaction rollback after the second reservation
write fails. PostgreSQL coverage also overlaps finalization of A/B and B/C
matches and checks both results, all four ledger rows, shared-team totals, and
final positions. Contention harnesses must synchronize the first serialization
attempt (the profile lock for booking or division advisory lock for standings),
before either transaction owns that gate; waiting for both transactions at a
later standing-row lock would itself block a correctly serialized implementation.
