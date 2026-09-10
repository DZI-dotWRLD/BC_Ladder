# Current Stage: Frontend Pilot and Deployment

This is the authoritative execution order until the project owner changes it.
Track it in one GitHub roadmap issue with a checklist. Keep one roadmap branch
and pull request active at a time. Start each branch from updated `main`, finish
its commits in order, and merge it with green checks before starting the next.

## Branch 1: Frontend pilot experience

Branch: `agent/phase-b1-frontend-pilot`

Build one reviewable frontend outcome through sequential coherent commits:

1. Audit every player journey and meaningful UI state; record the approved
   frontend specification.
2. Establish design tokens, shared components, responsive navigation, and the
   page shell.
3. Polish registration, login, profile setup, dashboard, and team workflows.
4. Polish availability entry, timezone communication, validation, active
   windows, and cancellation presentation.
5. Polish suggestions, acceptance progress, selected lineups, matches, scoring,
   cancellation, and ladder standings.
6. Verify responsive, keyboard, accessibility, empty, error, stale, conflict,
   and frontend regression behavior.

Preserve Django templates, forms, sessions, CSRF, redirects, and server-owned
domain rules. Use small progressive JavaScript only where it materially improves
the interaction. Defer React, a separate frontend application, a notification
inbox, a visual availability planner, and new domain features. Transactional
email delivery for match requests and score conflicts is an owner-approved
exception implemented outside the frontend pilot slice.

### Frontend gate

The journey is understandable on phone, tablet, and desktop; focused and full
quality gates pass; visual and accessibility checks are recorded; GitHub SQLite
and PostgreSQL jobs are green; and the pull request is merged.

## Branch 2: Pilot deployment

Branch: `agent/phase-b2-pilot-deployment`

Start after Branch 1 merges. Build through sequential coherent commits:

1. Provide secure first-administrator provisioning.
2. Finalize Render and PostgreSQL deployment configuration.
3. Verify migrations, static assets, health checks, production checks, logging,
   backup, restore, and rollback procedures.
4. Document and run deployment smoke checks and a five-user rehearsal using one
   administrator and four selected players.

Use disposable data until database persistence, backups, and restore testing
are approved for real club data.

### Deployment gate

The hosted staging application passes the full registration-to-match-result
workflow; operational checks are recorded; GitHub checks are green; and the pull
request is merged.

## Pilot release

Run the documented rehearsal after Branch 2. Create narrowly scoped fix branches
only for observed pilot blockers and place unrelated ideas in the roadmap
backlog. Release to a small controlled cohort after the administrator workflow,
support procedure, backup policy, and rollback plan are ready.
