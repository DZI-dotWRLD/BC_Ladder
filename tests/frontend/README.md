# Frontend CI checks

The `frontend` job in `.github/workflows/django.yml` runs a pinned Playwright
Chromium dependency against a disposable SQLite database. Existing Django jobs
continue to test SQLite and PostgreSQL, deployment checks and dependency quality.
No production credentials or GitHub secrets are needed.

Coverage: login/registration/reset, Dashboard/Team composition, three Play entry
routes, scorecard links, centred VS, ladder division switching, navigation,
field-description targets, page overflow at 320/390/768/1024/1440px, a real invalid
availability POST, failed-fragment fallback and no-JavaScript fallback.
The suite also scrolls deferred sections into view, follows the keyboard skip
link, and submits matching scores as both selected teams to verify waiting,
opponent-action and official-result presentation. It mutates only demo matches
in the disposable database; reseed a fresh database before repeating it.
Screenshots and server logs are uploaded for seven days, including on failure.
Screenshots are evidence, not approved visual-diff baselines. Firefox/Safari,
every button/state and visual taste are not covered by this smoke suite.

For local execution, run `npm ci` from `tests/frontend`, then from the repo root
`tests/frontend/node_modules/.bin/playwright install chromium` (on Windows use
the `.cmd` executable). Start Django on `127.0.0.1:8001` with an explicitly
isolated database, migrate it, and run the existing `seed_demo` command before
`node tests/frontend/check.mjs`. Never seed or run this against club/production
data. The test uses only the existing demo account and invalid availability
submission, and never sends real email.

After pushing these files, GitHub runs the workflow automatically on PRs and
pushes to main, agent/** and codex/**. To block merging on failures, configure a
GitHub branch rule/ruleset requiring quality, sqlite, postgres and frontend.
That repository-owner setting is separate from this local configuration.
