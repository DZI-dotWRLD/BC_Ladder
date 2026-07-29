---
name: availability-matchmaking
description: Design, implement, debug, or review BC_ladder weekly availability, two-player lineup generation, opponent suggestions, dual-team acceptance, and concurrency-safe slot reservation.
---

# Availability and Matchmaking Workflow

Use this skill whenever a task affects availability, lineup combinations,
opponent suggestions, acceptance, booking, or match conflicts.

## Required invariants

- A user has at most one active team.
- A team has at most three active members.
- A playable lineup has exactly two active members.
- Opponents come from another team in the same ladder.
- Both lineups share compatible availability.
- Both teams must accept.
- The four selected users become unavailable for the confirmed slot.
- A third team member who is not selected remains available.
- Confirmation is atomic, idempotent, and safe under concurrent requests.

## Model the time representation first

Inspect the existing implementation and determine whether availability uses:

- fixed weekly slot identifiers; or
- arbitrary datetime intervals.

Do not mix the two models accidentally.

For fixed slots, prefer a normalized slot key such as weekday plus start/end
time and timezone policy. For intervals, store timezone-aware bounds and use
sorted interval logic. When PostgreSQL range types are already used, consider
database exclusion constraints for overlap protection.

## Generate team lineups

For each active team:

1. Load active members and valid active availability efficiently.
2. Generate unique unordered member combinations using a stable ordering.
3. Intersect the two members' availability.
4. Emit one candidate per lineup and compatible slot.
5. Deduplicate by team, ordered member IDs, and normalized slot.
6. Sort deterministically.

A team has at most three members, so member combination generation is bounded
by `C(3, 2) = 3`.

### Fixed-slot implementation

Represent each user's active slots as a set or indexed queryset. Intersect the
two users' slot keys. The work is linear in the number of loaded slots, aside
from database query cost.

### Interval implementation

Sort each user's intervals once, then use a two-pointer intersection. For
interval counts `a` and `b`, intersection is `O(a + b)` time and output-sized
space. Avoid comparing every interval with every other interval unless inputs
are provably tiny and measured.

## Find opponent candidates

1. Partition candidates by ladder and normalized slot.
2. For each candidate, read only candidates in the same bucket.
3. Exclude the same team, repeated users, inactive teams, stale slots, and
   existing conflicts.
4. Apply ranking and product preferences only after eligibility filtering.
5. Use a stable final tie-breaker.

A reasonable deterministic ordering, when product rules allow it, is:

1. exact slot compatibility;
2. smallest ladder-rank or point distance;
3. fewer recent meetings;
4. oldest waiting suggestion;
5. stable team and member IDs.

Do not change the point system or ranking policy without an explicit task.

Avoid a global Cartesian product of every candidate against every other
candidate. Bucket lookup should make candidate generation close to linear in
the number of availability candidates plus the eligible matches inspected.

## Suggestion lifecycle

Use explicit statuses that match the repository, for example:

- proposed;
- partially accepted;
- confirmed;
- declined;
- expired;
- cancelled; and
- completed.

A suggestion must retain enough information to detect staleness, such as the
chosen members, slot, teams, and version or timestamps of relevant
availability.

Do not auto-confirm based on old acceptance after the lineup or slot changes.

## Confirmation transaction

Inside one `transaction.atomic()` block:

1. Lock the suggestion or match row.
2. Lock all four users' relevant availability/reservation rows.
3. Re-fetch active team memberships.
4. Verify each lineup still contains two members of its stated team.
5. Verify teams remain in the same ladder.
6. Verify both teams accepted the current suggestion.
7. Verify the slot is still compatible and unreserved.
8. Verify none of the four users has another active match in the slot.
9. Create or promote exactly one confirmed match.
10. Create exactly four user-slot reservations or consume exactly four
    availability entries.
11. Mark the suggestion confirmed.
12. Commit.

Use uniqueness constraints, conditional uniqueness, or an equivalent
reservation design to make duplicate booking impossible even under a race.

Return a clear conflict response when a competing request wins.

## Tests

Cover:

- two members with one shared slot;
- three members producing all three unordered lineups;
- no overlap;
- partial interval overlap;
- reversed member order does not duplicate a lineup;
- inactive or removed member exclusion;
- same-team exclusion;
- cross-ladder exclusion;
- third non-playing member remains available;
- stale suggestion;
- one team accepts, then second team accepts;
- both teams accept concurrently;
- two suggestions compete for one user's slot;
- retrying acceptance is idempotent;
- daylight-saving and timezone boundaries when relevant;
- deterministic ordering; and
- bounded query count.

## Output

Report the chosen time model, data constraints, transaction locks, candidate
ordering, time/space complexity, tests, and any unresolved product decision.
