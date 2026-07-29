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

## Do not guess regular-set policy

Inspect existing code or configured rules for:

- whether a regular set is first to six by two;
- whether a tie-break occurs at 6-6;
- allowed shortened-set formats;
- retirement, walkover, default, or incomplete-match handling.

Keep regular-set validation in a separate function or strategy from deciding
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
