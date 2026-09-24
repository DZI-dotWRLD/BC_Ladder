# Matchmaking candidate snapshot and command

Suggestions use arbitrary timezone-aware availability intervals. The search
loads four querysets: active same-ladder teams with standings, active memberships
with players/users, active overlapping source windows, and active overlapping
reservations. Team count, source-window count, and eligible pair count do not add
queries. Eligibility comes only from active memberships. The search runs only
when a player opens `/suggestions/?discover=1`. It covers now to now + 30
days, and the page shows the top ten options.

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
source availability IDs, and source bounds. The command is posted as a hidden
`candidate` field to `suggestions/create/`. There is no list-index route. A form
limits input length to 8192 characters;
the service verifies the signature, expiry, actor, and own team, locks source
windows in ID order, rechecks active actor membership, and compares the exact
identity with freshly eligible candidates before creating the request in the
same transaction. Ranking changes cannot silently select another candidate.
Missing/tampered/expired/stale commands create no suggestion or workflow event.

Creation, removal and confirmation share one lock order. See
[booking-concurrency.md](booking-concurrency.md), which also covers the
PostgreSQL contention tests. Trusted service callers, such as `seed_demo`,
may still call `create_match_suggestion(option)` directly.
