---
name: django-testing-quality
description: Add or review BC_ladder Django tests, factories, concurrency cases, query-count assertions, migration checks, and repository quality gates.
---

# Django Testing and Quality Workflow

Use the testing framework and conventions already present in the repository.
Do not introduce pytest, factory_boy, coverage tools, or plugins merely because
they are familiar.

## Test strategy

Prefer the smallest test that proves the rule, then add integration coverage for
critical workflows.

### Unit-level targets

- score validators;
- availability intersection;
- lineup combinations;
- candidate ranking;
- state transition guards;
- point calculations; and
- permission predicates.

### Database/service targets

- team membership constraints;
- transaction rollback;
- stale suggestion detection;
- match confirmation;
- reservation uniqueness;
- idempotent score submission; and
- point ledger updates.

### Request/UI targets

- authentication;
- object-level authorization;
- CSRF and HTTP method behavior;
- form or serializer errors;
- redirects and messages;
- rendered state; and
- JavaScript behavior when the project has a frontend test setup.

## Required domain matrix

Test at least:

- zero, one, two, and three team members;
- attempt to add a fourth member;
- user already on another active team;
- same-team and cross-ladder opponent exclusion;
- overlapping and non-overlapping availability;
- exact boundary overlap;
- stale and consumed slots;
- both-team acceptance;
- competing confirmation requests;
- straight-set and split-set scores;
- deciding tie-break win-by-two;
- unauthorized and duplicate writes; and
- ladder update exactly once.

## Concurrency testing

For race-sensitive booking and scoring paths:

- use the database engine and transaction test class appropriate to the
  repository;
- create separate database connections or workers where needed;
- synchronize requests so they contend on the same rows;
- assert one successful durable outcome;
- assert the loser receives a controlled conflict;
- assert no duplicate match, reservation, score, or point update exists.

SQLite may not reproduce production row-lock behavior. If production uses
PostgreSQL, clearly distinguish portable unit tests from PostgreSQL-specific
concurrency verification.

## Query performance

Use query-count assertions on important pages and services where stable.

Check:

- ladder list;
- team detail;
- availability calendar;
- suggestion list;
- match history; and
- admin list views touched by the change.

Avoid brittle exact counts when framework differences make them unstable; a
reasonable upper bound can still catch N+1 regressions.

## Fixtures and factories

- Use existing factories or builders.
- Keep test data minimal and explicit.
- Use timezone-aware datetimes.
- Avoid depending on insertion order unless ordering is part of the behavior.
- Use clear helper names for teams, lineups, slots, suggestions, and matches.
- Do not reuse mutable global test state.

## Quality sequence

Discover commands from the repository, then run applicable checks in this
order:

1. focused tests;
2. Django system check;
3. migration consistency;
4. formatter and linter;
5. type checker;
6. frontend tests/build;
7. full backend suite;
8. security/dependency checks.

Capture exact commands and results. Do not report success for a command that
was not run.

## Review output

Report:

- new or changed tests;
- important untested paths;
- concurrency database limitations;
- query-count findings;
- commands and results;
- flaky or environment-dependent behavior; and
- recommended next test if coverage is incomplete.
