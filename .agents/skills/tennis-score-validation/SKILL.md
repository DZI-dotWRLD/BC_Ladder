---
name: tennis-score-validation
description: Implement, debug, or review BC_ladder tennis score parsing, best-of-three validation, deciding match tie-break rules, match completion, and atomic ladder point updates.
---

# Tennis Score Validation Workflow

Use this skill for score forms, APIs, validators, match completion, result
confirmation, or ladder point updates.

## Required format

- The match is best of three.
- A team wins by winning two sets.
- If teams split the first two regular sets, the third entry is a deciding
  match tie-break.
- The deciding tie-break is played to at least 10 points and won by two.
- At 9-9, continue until one team leads by two.
- A deciding tie-break is not played if one team won both regular sets.

## The implemented policy (do not change without owner approval)

All of this is in `ladder/services.py`:
- **`validate_regular_set`:** valid only as 6-0 to 6-4, 7-5 or 7-6 (7-6
  implies a set tie-break). 6-5, 6-6 and anything above 7 are invalid. There
  are no shortened sets.
- **`validate_match_tiebreak`:** the winner has at least 10, wins by at least
  2, and each score is capped at 99.
- **`validate_match_score`:** two or three entries. A straight-set win
  rejects a third entry. Split sets require the third entry to be a match
  tie-break. It derives the winner and returns normalized sets.
- **Form bounds** (`ScoreSubmissionForm`): regular sets 0-7, tie-break 0-99.
- **Not modelled:** retirement, walkover, default and no-show. A match
  missing a submission stays `scheduled`; this is an open owner decision.
- **Points:** `WIN_POINTS = 3` for a win and 0 for a loss. Standings order by
  points, wins, fewer losses, case-insensitive name, then team ID.

## Submission and confirmation flow

Each team submits once (`MatchResultSubmission` is unique per match and
team). Resubmitting the identical score is a no-op. Only the immutable
selected `MatchParticipant`s may submit. When the two submissions match,
`finalize_match_result` writes the result, ledger, standings and positions
once. When they differ, `create_admin_notification_for_conflict` creates the
conflict notification, and an administrator resolves it with
`resolve_score_conflict` (admin action "Use selected submission as official
score"), which writes a `ScoreCorrectionAudit`. Standings writers take the
per-division advisory lock first (see `docs/booking-concurrency.md`).

Keep regular-set validation in a separate function from deciding
match-tie-break validation.

## Recommended domain API

Prefer pure, reusable functions or value objects such as:

```python
validate_regular_set(team_a_games, team_b_games, rules)
validate_match_tiebreak(team_a_points, team_b_points)
validate_match_score(sets, rules)
determine_winner(sets, rules)
```

Return structured validation errors suitable for forms or serializers. Do not
trust a client-supplied winner; derive it from the validated score.

## Deciding match tie-break validation

A deciding match tie-break is valid only when:

- both values are non-negative integers;
- the larger value is at least 10;
- the absolute difference is at least 2; and
- the score represents a terminal state.

Examples:

- valid: `10-0`, `10-8`, `11-9`, `12-10`, `15-13`;
- invalid: `9-7`, `10-9`, `11-10`, equal scores, negative values, or decimals.

For a terminal win-by-two tie-break, checking `max(points) >= 10` and
`abs(a - b) >= 2` is sufficient unless the product records intermediate point
history.

## Whole-match validation

1. Parse input without silently coercing malformed values.
2. Validate each regular set using the configured regular-set rules.
3. Count set winners.
4. Stop once one team has won two sets.
5. Reject extra sets after the match is already decided.
6. Require a deciding match tie-break only when the first two sets are split.
7. Validate the deciding tie-break.
8. Derive exactly one match winner.
9. Reject draws and contradictory winner fields.

Valid shapes include:

- Team A wins two regular sets.
- Team B wins two regular sets.
- Teams split the first two regular sets and one wins a valid match tie-break.

## Persistence and point updates

Use one transaction to:

1. lock the match;
2. verify the requester is authorized;
3. verify the match may receive a result;
4. validate the score;
5. persist normalized set records;
6. mark the result submitted or confirmed according to existing workflow;
7. update ladder points only when confirmation requirements are met;
8. create an auditable point-change record when the schema supports it; and
9. mark the match completed.

Protect against repeated submissions with state checks and uniqueness or
idempotency rules. A retry must not add points twice.

Do not let users directly post calculated ladder points.

## Tests

Include:

- straight-set win for each team;
- split sets plus `10-8`;
- split sets plus `11-9`;
- split sets plus `12-10`;
- invalid `10-9`;
- invalid deciding tie-break below 10;
- deciding tie-break when one team already won both sets;
- missing deciding tie-break after split sets;
- extra fourth set;
- malformed or negative score;
- unauthorized submitter;
- wrong match state;
- duplicate submission;
- concurrent submissions;
- point update occurs once;
- transaction rollback when the point update fails; and
- historical score remains stable after later team membership changes.

## Output

Report score assumptions, normalization, validation rules, transaction
behavior, point-update idempotency, and tests run.
