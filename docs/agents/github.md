# GitHub Planning and Triage

Issues and specifications live in `DZI-dotWRLD/BC_Ladder` GitHub Issues. Infer
the active repository from `git remote -v`; prefer the connected GitHub tooling
when available and use `gh` for operations it does not cover.

Use one roadmap issue as the current-stage map. Keep its checklist aligned with
`docs/agents/current-stage.md`. Create child issues only when work needs an
independent decision, specification, or implementation handoff. Use GitHub
dependencies when available; otherwise start the child body with
`Blocked by: #<number>`.

## Triage labels

| State | Repository label |
| --- | --- |
| Maintainer evaluation required | `needs-triage` |
| Reporter information required | `needs-info` |
| Specification complete | `ready-for-agent` |
| Human action or decision required | `ready-for-human` |
| Intentionally declined | `wontfix` |

Matt Pocock wayfinding work may additionally use `wayfinder:map`,
`wayfinder:research`, `wayfinder:prototype`, `wayfinder:grilling`, and
`wayfinder:task` when those labels exist.
