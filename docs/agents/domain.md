# Domain Docs

This repository uses a single-context domain-doc layout.

## Before exploring, read these when they exist

- `CONTEXT.md` at the repo root
- `docs/adr/` ADRs that touch the area being changed
- `REQUIREMENTS.md`
- `AGENTS.md`

If `CONTEXT.md` or `docs/adr/` do not exist yet, proceed silently. They are created lazily when the project resolves terminology or records a hard-to-reverse decision.

## File structure

```text
/
├── CONTEXT.md
├── docs/
│   ├── adr/
│   └── agents/
├── config/
└── ladder/
```

## Current bounded contexts

BC_ladder is currently one Django application, not a monorepo. Treat the domain as one context with these main modules:

- player registration and profile setup;
- team membership and admin approval;
- availability publishing;
- lineup generation and opponent suggestions;
- dual acceptance and match booking;
- score submission and conflict resolution;
- ladder standings and point ledger; and
- deployment and operations.

## Vocabulary discipline

Use the domain names already present in `AGENTS.md`, `REQUIREMENTS.md`, models, and services:

- `TeamMembership`, not informal "team user link";
- `AvailabilitySlot`, not generic "calendar event";
- `MatchSuggestion`, not legacy "challenge" for the new booking flow;
- `SuggestionAcceptance`, not generic "approval";
- `MatchReservation`, not generic "hold";
- `ConfirmedMatchResult`, not generic "final score";
- `PointLedger`, not direct point mutation.

If future work introduces new domain terms, either add them to `CONTEXT.md` or record the decision in an ADR.
