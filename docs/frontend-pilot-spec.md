# Frontend Pilot Specification

Status: approved Phase B1 implementation contract, 2026-08-22.

## Outcome

A registered club player should always understand their current ladder state,
the next safe action, and the consequence of that action on a phone, tablet, or
desktop. The interface should feel like a focused club-sport product while
keeping Django authoritative for authentication, authorization, validation,
workflow transitions, and history.

## Architecture

Keep server-rendered Django templates, Django forms, sessions, CSRF, messages,
and redirects. Use shared template partials, one organized stylesheet, and
small progressive JavaScript only where native HTML cannot provide a clear
interaction. Do not add React, a package manager, a frontend build, or a new
API contract during this phase.

## Audit baseline

The current player journey was captured at 1440 by 1000 and 390 by 844 using
the deterministic demo account. Screenshots were inspected for registration,
login, dashboard, team, availability, suggestions, match history, scheduled
match detail, cancellation confirmation, standings, and the main mobile flows.

Confirmed strengths:

- Core actions use semantic links, forms, fieldsets, labels, CSRF, and POST
  mutations.
- The dashboard provides a useful setup sequence and meaningful empty-state
  actions.
- Match cancellation has a dedicated confirmation page with clear permanence.
- Keyboard focus is visible and native controls remain usable without
  JavaScript.
- The existing black, red, ivory, and editorial-serif identity is distinctive
  enough to evolve rather than replace.

Highest-impact risks:

- Mobile navigation occupies roughly one third of the initial viewport and
  provides no current-page indication.
- Pages use generic panels and repeated paragraphs, making status, time,
  lineup, and next action hard to scan.
- Internal status values such as `waiting_for_submissions` reach players.
- Suggestions mix available options with an unbounded history of cancelled,
  confirmed, and open records without strong grouping or prioritization.
- Registration helper text runs beside fields on wide screens and errors rely
  on Django's default rendering instead of an intentional pattern.
- Match history does not separate upcoming, action-needed, completed, and
  cancelled matches.
- Destructive and routine actions need clearer visual hierarchy and consistent
  consequence text.
- There is no skip link, current-page `aria-current`, or custom treatment for
  stale, unauthorized, not-found, and server-error states.

## Journey and state inventory

Implementation and verification must cover these existing states without
changing their domain meaning.

| Surface | States |
| --- | --- |
| Authentication | register, login, invalid form, authenticated redirect |
| Profile | missing profile, completed profile |
| Dashboard | incomplete setup, pending team request, ready player, empty and populated summaries |
| Team | no team, join request pending, active team, full team, removal pending |
| Availability | empty, active windows, validation error, domain overlap error, cancelled history count |
| Suggestions | no team, too few members, no availability, no shared lineup, no opponent, no opponent overlap, available option |
| Acceptance | proposed, partially accepted, own team accepted, opponent accepted, non-selected teammate, stale version, confirmed, declined, expired, cancelled |
| Matches | empty, scheduled, score action needed, waiting for opponent score, conflict, completed, cancelled, historical participant access |
| Cancellation | allowed confirmation, no longer allowed, successful cancellation |
| Standings | empty and populated Men's or Women's ladder |
| Global feedback | information, success, validation error, domain error, unauthorized, not found, server error |

## Visual system

Evolve the current identity with reusable tokens for color, typography,
spacing, width, borders, radii, elevation, focus, motion, and breakpoints.

- Keep black and club red as the identity colors, with warm neutral surfaces.
- Add semantic success, warning, information, and danger colors that meet WCAG
  2.2 AA contrast for their actual text and background combinations.
- Use an editorial display face from the existing system stack for major
  headings and a legible system sans-serif for body copy, controls, and data.
- Use a consistent spacing scale and restrained elevation; dense data should
  rely on hierarchy and dividers rather than many independent shadows.
- Use status labels with player-facing copy, not raw model or service values.
- Do not introduce decorative images, fake icons, or animation that is needed
  to understand state.

## Shared shell and components

Build these primitives before page-specific polish:

- skip link, semantic header, responsive primary navigation, active-page state,
  account identity, and main content container;
- page header with eyebrow, title, supporting copy, and optional action;
- card, section, item row, metadata list, empty state, and data table;
- button variants for primary, secondary, quiet, and destructive actions;
- status badge variants for neutral, information, pending, success, warning,
  and danger states;
- alert variants for Django messages and form-level errors;
- form stack, field group, help text, error list, and action group;
- accessible visually-hidden utility and responsive-only utilities.

The mobile header should keep the brand and current context visible while
placing navigation in a compact native disclosure. Desktop navigation should
remain immediately available. Current location must use visual treatment and
`aria-current="page"`.

## Page priorities

1. Authentication and dashboard: reduce first-use ambiguity and make one next
   action dominant.
2. Team and availability: make membership state, timezone, active windows, and
   cancellation consequences explicit.
3. Suggestions: prioritize actionable open suggestions and compatible options;
   visually subordinate closed history without changing records.
4. Matches and scoring: separate match facts, participants, operations, score
   entry, submission progress, conflict, and result.
5. Standings: keep the table authoritative and scannable, with a resilient
   small-screen presentation.

## Responsive and accessibility contract

- Verify at 390px phone, 768px tablet, and 1440px desktop widths.
- Preserve logical document order and one clear `h1` per page.
- Provide a skip link, landmarks, active navigation semantics, visible focus,
  persistent labels, associated help and error text, and at least 44px targets
  for primary mobile interactions.
- Reflow without horizontal page scrolling at 200% zoom; allow contained table
  scrolling only when the data cannot be represented safely otherwise.
- Never communicate state by color alone. Keep native browser and server
  validation functional when JavaScript is absent.
- Respect `prefers-reduced-motion` for any nonessential transitions.

## Backend and history boundaries

Frontend work may improve queries used only to present existing state, but it
must not change membership eligibility, availability overlap rules, lineup
selection, suggestion ordering, acceptance, reservation, cancellation, score
validation, conflict resolution, standings, authorization, or WorkflowEvent
semantics. No model or migration change is planned. Existing POST endpoints and
historical records remain intact.

## Delivery sequence

1. Tokens, shared components, responsive navigation, and page shell.
2. Registration, login, profile setup, dashboard, and team.
3. Availability and its validation and cancellation states.
4. Suggestions, acceptance, matches, scoring, cancellation, and standings.
5. Responsive, keyboard, accessibility, empty, error, stale, conflict, and
   regression verification.

Each sequence item is a coherent commit. Run focused request tests and the fast
quality harness after each item; run the complete SQLite and PostgreSQL gates
before pull-request handoff.
