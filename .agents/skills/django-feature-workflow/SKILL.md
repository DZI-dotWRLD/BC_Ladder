---
name: django-feature-workflow
description: Implement or modify a full-stack Django feature in BC_ladder while preserving repository conventions, domain invariants, authorization, migrations, tests, and UI behavior.
---

# Django Feature Workflow

Use this skill for general feature work that touches one or more Django layers.

## Inputs to gather

- Requested user behavior.
- Existing app, model, URL, view, form or serializer, template, JavaScript, and
  test locations.
- Current database and Django version.
- Current permission model.
- Existing quality commands.
- Applicable invariants from `AGENTS.md`.

## Procedure

1. Read `AGENTS.md` and `REQUIREMENTS.md`.
2. Inspect the relevant implementation end to end before editing.
3. Search for existing patterns that solve similar problems.
4. State the affected domain invariants and likely files.
5. Design the write path:
   - authorization;
   - validation;
   - transaction boundary;
   - database constraints;
   - idempotency;
   - state transitions; and
   - user-visible errors.
6. Design the read path:
   - queryset ownership;
   - related-object loading;
   - ordering;
   - pagination; and
   - caching only when safe.
7. Add or update tests that fail for the missing behavior.
8. Implement the smallest complete vertical slice:
   - model and migration;
   - domain service or selector;
   - form or serializer;
   - view and URL;
   - template or frontend behavior;
   - admin changes when required.
9. Run focused tests.
10. Review for object-level permissions, CSRF, race conditions, duplicate
    requests, N+1 queries, timezone handling, and backwards compatibility.
11. Run broader repository checks.
12. Produce the completion report required by `AGENTS.md`.

## Design preferences

- Use explicit domain services for multi-model workflows.
- Keep views and endpoints thin.
- Keep validation close to the boundary and repeat critical checks inside the
  transaction.
- Use named URLs.
- Use timezone-aware values.
- Prefer explicit state enums.
- Preserve historical records.
- Avoid core business logic in signals.
- Do not add a dependency when Django or an existing project dependency already
  solves the problem clearly.

## Completion checks

- The feature works through the actual user-facing path.
- Unauthorized users cannot perform the action.
- Invalid state is rejected on the server.
- Database migrations are complete and reversible when practical.
- Tests cover success, invalid input, authorization, and important edge cases.
- Queries are bounded and related data is loaded efficiently.
- Documentation is updated when behavior or setup changed.
