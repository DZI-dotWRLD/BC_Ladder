# Issue tracker: GitHub

Issues and specs for this repo live as GitHub issues. Use the `gh` CLI for issue operations from inside this checkout.

## Repository

Infer the repository from `git remote -v`.

Current remote:

```text
https://github.com/DZI-dotWRLD/BC_Ladder.git
```

## Conventions

- Create an issue: `gh issue create --title "..." --body "..."`
- Read an issue: `gh issue view <number> --comments`
- List issues: `gh issue list --state open`
- Comment on an issue: `gh issue comment <number> --body "..."`
- Apply or remove labels: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- Close an issue: `gh issue close <number> --comment "..."`

## Pull requests as a triage surface

PRs as a request surface: no.

If this changes later, update this file before using triage or ticket-generation skills against pull requests.

## Skill behavior

When a skill says "publish to the issue tracker", create a GitHub issue.

When a skill says "fetch the relevant ticket", run `gh issue view <number> --comments`.

## Wayfinding operations

For larger planning efforts, use one GitHub issue as the map and create child issues as decision or implementation tickets.

- Map issue label: `wayfinder:map`
- Child labels: `wayfinder:research`, `wayfinder:prototype`, `wayfinder:grilling`, or `wayfinder:task`
- Prefer GitHub native dependencies for blocking relationships when available.
- If native dependencies are unavailable, include `Blocked by: #<number>` at the top of the child issue body.
