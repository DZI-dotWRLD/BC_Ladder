# BC_ladder Product Requirements

## Product goal

Provide a reliable tennis-club web application that helps registered doubles
players find compatible opponents, schedule matches, report results, and
participate in gender-specific ranking ladders.

## Primary actors

- **Player:** manages personal availability, views team and ladder data, reviews
  suggestions, participates in acceptance, and submits scores for authorized
  matches.
- **Team member:** one of up to three active members associated with one team.
- **Administrator:** manages team membership requests, resolves exceptional
  cases, and performs authorized operational actions.
- **Optional captain:** only use this role if it already exists or is explicitly
  requested. Its permissions must be defined rather than assumed.

## Functional requirements

### Ladders

- Support a Men's Doubles ladder.
- Support a Women's Doubles ladder.
- Keep teams and matches isolated by ladder.
- Track rank and points using the project's configured point policy.
- Preserve an auditable history of completed matches and point changes.

### Team membership

- One user may have no more than one active team.
- One team may have no more than three active members.
- A match lineup contains exactly two members.
- A user may request a team change.
- An administrator completes removal before the user joins a new team.
- Historical matches remain connected to the participants who played them.

### Availability

- A user sets weekly availability.
- The system identifies overlapping availability among members of the same team.
- For every shared slot, the system creates every possible unique two-member
  lineup.
- The system finds eligible two-member lineups from another team in the same
  ladder with compatible availability.
- Confirmed match participants become unavailable for that slot.

### Suggestions and acceptance

- Suggestions must contain two teams, two players per team, and one compatible
  slot.
- Both teams must accept before the match is confirmed.
- Suggestions become invalid if membership, availability, or conflicts change.
- Confirmation must be atomic and safe under concurrent requests.
- Duplicate acceptance must not duplicate a match.

### Score submission

- Match format is best of three.
- A team wins by winning two sets.
- Split regular sets require a deciding match tie-break.
- The deciding tie-break is first to at least 10, win by two.
- At 9-9, continue until a two-point lead.
- Invalid or unauthorized scores must be rejected.
- Valid completion and ladder updates occur atomically and only once.

## Non-functional requirements

### Correctness

- Enforce critical invariants in services and, where practical, database
  constraints.
- Keep state transitions explicit and auditable.
- Make side-effecting operations idempotent.

### Performance

- Avoid N+1 queries.
- Index common lookup fields.
- Do not generate opponent candidates using an unnecessary global Cartesian
  product.
- Use deterministic, complexity-conscious algorithms.
- Paginate unbounded result sets.

### Security

- Require authentication for player features.
- Enforce object-level authorization.
- Protect state changes with CSRF and appropriate HTTP methods.
- Do not expose secrets or sensitive player information.
- Validate all client input on the server.

### Maintainability

- Follow Django conventions.
- Keep business rules in explicit domain services.
- Add migrations for schema changes.
- Add automated tests for every behavior change.
- Preserve backwards compatibility unless the task explicitly authorizes a
  breaking change.

## Product decisions still requiring explicit configuration

These items must be read from the current implementation or decided by the
project owner rather than guessed:

- exact ladder point formula;
- regular-set rules, including any 6-6 tie-break policy;
- club timezone and availability slot granularity;
- suggestion expiration duration;
- whether one player, both teams, or an administrator must confirm a submitted
  result;
- tie-breaking rules when teams have equal ladder points;
- notification channels; and
- whether captains have special acceptance powers.
