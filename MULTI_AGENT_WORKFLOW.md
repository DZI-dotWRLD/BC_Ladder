# BC_ladder Multi-Agent Workflow

## How it works

Codex reads the project-scoped custom agents from `.codex/agents/`. The main
Codex thread acts as the supervisor and can spawn the named agents.

The recommended sequence is:

```text
Manager
  |
  +-- Designer (read-only)
  +-- Backend developer: discovery only
  +-- Frontend developer: discovery only
  |
  v
Manager freezes contracts and file ownership
  |
  +-- Backend developer (write)
  +-- Frontend developer (write only if files are disjoint)
  |
  v
Tester (write tests, report production defects)
  |
  v
QA reviewer (read-only)
  |
  v
Manager integrates, verifies, and reports
```

For substantial redesigns, the designer owns read-only user-flow and visual
direction work; the frontend developer owns implementation and uses the
`frontend-design-engineering` skill. The manager may ask the frontend developer
for an architecture spike before freezing the contract. Use React only after
that spike shows that progressive Django UI or isolated enhancement is
insufficient.

Do not run frontend and backend writes in parallel when they need to change the
same files. Never generate migrations from multiple agents concurrently.

## First command to try

Open the BC_ladder repository in Codex and submit:

```text
Use the manager custom agent to coordinate this task:

<describe the feature>

Follow the gated workflow. First delegate read-only discovery and design. Wait
for those reports and freeze the implementation contract and file ownership.
Then delegate backend and frontend implementation, in parallel only if their
files are disjoint. After integration, delegate testing and then read-only QA.
Resolve blocking findings and return the AGENTS.md completion report.
```

## Repository audit prompt

```text
Use manager to audit the existing BC_ladder project. Spawn designer,
backend_developer, and frontend_developer for read-only inspection in parallel.
Wait for all three. Then ask tester to inspect current test coverage and
qa_reviewer to identify domain, security, concurrency, migration, and
performance risks. Do not edit code. Return one prioritized implementation
roadmap with file references and dependencies.
```

## Feature prompt example

```text
Use manager to implement weekly availability and opponent suggestions.

Required flow:
- users publish weekly availability;
- find availability shared by two members of the same team;
- generate every unique two-player lineup;
- find a compatible lineup from another team in the same ladder;
- require both teams to accept;
- reserve the slot atomically for exactly the four selected users.

Use availability-matchmaking, django-feature-workflow,
django-testing-quality, and performance-security-review. Follow all workflow
gates. Do not allow concurrent edits to models or migrations.
```

## Review-only prompt

```text
Review the current branch against main with parallel read-only subagents.
Use backend_developer for backend correctness analysis only, frontend_developer
for UI analysis only, tester for test-gap analysis only, and qa_reviewer for
final risk classification. Do not modify files. Wait for every agent and have
manager produce a deduplicated report ordered by severity.
```

## Monitoring agents

In Codex CLI, use:

```text
/agent
```

to inspect or switch among agent threads.

In the Codex app or IDE extension, open the subagent activity panel to inspect
active and completed threads.

## True parallel code changes

Subagents are best for independent analysis and bounded work. For large frontend
and backend implementations, use separate Codex app worktree chats:

1. Start a backend worktree from the same base branch.
2. Start a frontend worktree from the same base branch.
3. Give both agents the frozen contract and strict file ownership.
4. Create a branch in each worktree.
5. Review and integrate the branches.
6. Run tester and QA against the integrated branch.

This provides isolation and avoids agents overwriting each other's files.
