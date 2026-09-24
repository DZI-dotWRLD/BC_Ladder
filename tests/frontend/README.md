# Frontend CI checks

The `frontend` job in `.github/workflows/django.yml` runs a pinned Playwright
Chromium dependency against a disposable SQLite database. Existing Django jobs
continue to test SQLite and PostgreSQL, deployment checks and dependency quality.
No production credentials or GitHub secrets are needed.

The suite checks:
- **Pages:** login, registration and reset, Home, the three Play tabs,
  opponent discovery, the scorecard and both ladders.
- **Layout at 320, 390, 768, 1024 and 1440px:** no page overflow, exactly one
  `h1` per page, and every `aria-describedby` target exists.
- **Design rules:** Home has no setup checklist, the suggestions page does not
  repeat the availability form, and the VS mark is centered.
- **Interactions:** field help appears on focus, the account menu opens and
  closes with Escape, and help closes on an outside click.
- **Availability picker:** a day chip and the Morning preset fill the real
  fields; finish options always follow the start; saving adds a window.
- **Without JavaScript:** navigation and forms still work, and a real invalid
  availability POST through the native fields shows the server's error.
- **Keyboard:** the skip link reaches the main content.
- **Scoring:** matching scores are submitted as both selected teams, to verify
  the waiting, opponent-action and official-result states.

It mutates only demo matches in the disposable database; reseed a fresh
database before repeating it. Set `PLAYWRIGHT_CHANNEL=msedge` (or `chrome`)
to use an installed browser instead of Playwright's bundled Chromium.
Screenshots and server logs are uploaded for seven days, including on failure.
Screenshots are evidence, not approved visual-diff baselines. Firefox/Safari,
every button/state and visual taste are not covered by this smoke suite.

For local execution, run `npm ci` from `tests/frontend`, then from the repo root
`tests/frontend/node_modules/.bin/playwright install chromium` (on Windows use
the `.cmd` executable). Set `DJANGO_DEBUG=true` in the shell. Without it,
`migrate`, `seed_demo` and `runserver` refuse to start. Start Django on
`127.0.0.1:8001` with an explicitly isolated database, migrate it, and run the
existing `seed_demo` command before `node tests/frontend/check.mjs`. Set
`FRONTEND_BASE_URL` to target a different server. Never seed or run this against club/production
data. The test uses only the existing demo account and invalid availability
submission, and never sends real email.

After pushing these files, GitHub runs the workflow automatically on PRs and
pushes to main, agent/** and codex/**. To block merging on failures, configure a
GitHub branch rule/ruleset requiring quality, sqlite, postgres and frontend.
That repository-owner setting is separate from this local configuration.
