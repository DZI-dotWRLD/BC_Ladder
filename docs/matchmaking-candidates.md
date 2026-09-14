# Matchmaking candidate snapshot and command

Suggestions use arbitrary timezone-aware availability intervals. The search
loads four querysets: active same-ladder teams with standings, active memberships
with players/users, active overlapping source windows, and active overlapping
reservations. Team count, source-window count, and eligible pair count do not add
queries. No legacy profile team FK substitutes for active membership eligibility.

Each team contributes at most three unordered two-player lineups. Sorted source
windows are intersected with two pointers. A temporal sweep partitions lineups
into own-team/opponent active buckets; only intervals overlapping across those
buckets are inspected. End events precede start events, so touching boundaries
never overlap. Reservation starts and prefix-maximum ends provide binary-search
conflict detection, including overlapping historical reservation rows.

Let T be teams, P active players, A loaded availability rows, R reservation rows,
L lineup intervals, K overlapping own/opponent pairs, and E emitted options.
Python time is O(T + P + A + R + L log L + K log(R + 1) + E log E), with
O(T + P + A + R + L + E) space. Database sorting/index access is additional.
With three members per team, lineup intersection work is O(A). A densely shared
window can still yield output-sized pair growth; no opponent/opponent pairs or
nonoverlapping lineup pairs are compared. The existing top-ten page slice is
retained after the existing points-distance, time, team, and player ranking;
end and source-window IDs only break previously unresolved ties.

GET renders a Django signed, 30-minute candidate command bound to the requesting
user. It contains exact team IDs, ordered selected player IDs, UTC interval,
source availability IDs, and source bounds. POST never chooses by its URL list
index (retained only for route compatibility). A form limits input length;
the service verifies the signature, expiry, actor, and own team, locks source
windows in ID order, rechecks active actor membership, and compares the exact
identity with freshly eligible candidates before creating the request in the
same transaction. Ranking changes cannot silently select another candidate.
Missing/tampered/expired/stale commands create no suggestion or workflow event.

This slice changes no schema, historic records, standings policy, or acceptance
policy. The subsequent booking-concurrency slice must unify membership/team,
source-window, and confirmation lock order; this slice does not claim to prove
membership-removal or PostgreSQL confirmation contention behavior. Existing
service callers of `create_match_suggestion(option)` remain compatible.
