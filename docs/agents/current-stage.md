# Current Stage: Pilot Launch Readiness

This is the authoritative execution order until the project owner changes it.
Keep one active roadmap branch and pull request at a time. Start each branch
from updated `main`, and merge it with green `quality`, `sqlite`, `postgres`
and `frontend` checks before starting the next. Branches use the `codex/*` or
`agent/*` prefix, since CI runs on both.

## Done (on `main`)

| Stage | Where |
| --- | --- |
| Frontend pilot: design system, shell, journeys, browser CI | `agent/phase-b1-frontend-pilot`, then the rebuild in PR #11 |
| Transactional email: match requests, score conflicts, recovery | PRs #8, #10 |
| Matchmaking commands, booking/membership lock order, PostgreSQL contention tests | PR #12 |
| Production hardening tasks 1–15: fail-closed settings, rate limits, outbox leasing, `bootstrap_admin`, readiness, cron jobs, Sentry, admin invariants, headers, anonymization, architecture doc | PRs #13–#15 |
| Open registration without invite codes | PR #16 |

## In progress

- **Club redesign** on `agent/club-redesign`. It covers the new design system,
  header and tab bar, Home without repetition, Play tabs, and the sign-in
  pages; see `docs/frontend-pilot-spec.md`. It changes only templates, CSS
  and JS, with no view, service or model changes. The gate: understandable on
  phone, tablet and desktop, and all CI jobs green, including the rewritten
  Playwright smoke test.
- An older, unfinished redesign is still saved as a git stash ("frontend
  redesign WIP (phase-b1)"). The club redesign supersedes it; the owner
  decides whether to drop it.

## Next: pilot launch (proposed order, owner to confirm)

The blockers and risks are detailed in
[../production-readiness.md](../production-readiness.md).

1. **Owner decisions.** Decide who may register and form teams (open,
   invite, allowlist or approval), and how unfinished matches close.
2. **Deployment fixes.**
   - Add the proxy SSL header and `healthCheckPath` to `render.yaml`.
   - Make rate limits and axes aware of the client IP behind the proxy.
   - Cap email retry attempts.
   - Move migrations, bootstrap and audit to a pre-deploy step.
3. **Paid hosting and backups.**
   - Move to a paid web plan and paid PostgreSQL with PITR.
   - Set up a daily off-provider `pg_dump`.
   - Record a restore rehearsal ([DEPLOYMENT.md](../../DEPLOYMENT.md#backups)).
4. **Hosted rehearsal.** One administrator and four players go through the
   full journey on staging: registration, team, availability, suggestion, dual
   acceptance, both score submissions, standings. Also cover a score conflict
   and a cancellation. Record the results in `production-readiness.md`.

Use disposable data until step 3 is done and approved.

### Launch gate

Staging passes the rehearsal, operational checks and alerts are recorded,
all CI jobs are green, and the owner approves releasing to a small controlled
cohort.

## Pilot release

After the gate, release to a small cohort. Create narrowly scoped fix branches
only for observed pilot blockers. Record unrelated ideas as GitHub issues (see
[github.md](github.md)) instead of widening a branch.
