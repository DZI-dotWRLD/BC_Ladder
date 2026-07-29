---
name: frontend-design-engineering
description: Design and implement polished, modern, responsive, accessible BC_ladder user interfaces using the repository's Django frontend or a justified React architecture. Use for visual redesigns, design systems, navigation and page layouts, interactive UI, frontend architecture decisions, responsive behavior, accessibility, frontend performance, and translating UX specifications into production code.
---

# Frontend Design Engineering

Create an exceptional tennis product, not a generic dashboard. Combine product design judgment with production frontend engineering while preserving Django's server-authoritative domain rules.

## Start with evidence

1. Read `AGENTS.md`, `REQUIREMENTS.md`, relevant views, forms, URLs, templates, static assets, and tests.
2. Map the user journey and all meaningful states: loading, empty, success, invalid, stale, conflict, unauthorized, and server error.
3. Identify the current frontend toolchain. Do not introduce React, a package manager, or a build system merely to restyle pages.
4. Read [references/architecture.md](references/architecture.md) when selecting or changing the frontend architecture.
5. Confirm the backend contract, authorization boundaries, CSRF behavior, and server validation before implementation.

## Design direction

- Build a distinctive club-sport identity with strong hierarchy, purposeful typography, restrained color, and match-focused information design.
- Establish reusable design tokens for color, spacing, type, radii, elevation, motion, and breakpoints.
- Create reusable primitives and components before repeating page-specific styles.
- Make ranking, team, availability, suggestion status, acceptance state, and match outcome scannable.
- Prefer progressive disclosure over dense forms and tables on small screens.
- Design mobile-first and verify common mobile, tablet, and desktop widths.
- Include deliberate empty, error, confirmation, destructive, and success states.
- Use motion sparingly, respect `prefers-reduced-motion`, and never make animation necessary to understand state.

## Implementation rules

- Prefer semantic HTML and native controls. Preserve keyboard navigation, visible focus, labels, error associations, and logical heading order.
- Target WCAG 2.2 AA contrast and interaction behavior.
- Keep server-rendered Django forms and CSRF protection intact unless an approved API contract replaces them.
- Treat client validation as assistance; server validation remains authoritative.
- Do not put ladder, eligibility, booking, or scoring rules in presentation code as the source of truth.
- Extract inline styles into organized static assets as the visual system grows.
- Avoid unnecessary dependencies, global event handlers, layout shifts, oversized bundles, and blocking assets.
- Prefer resilient enhancement: core actions must remain understandable when JavaScript fails unless the approved architecture explicitly requires JavaScript.
- Test user-visible behavior and accessibility with the repository's supported tools. Add a frontend toolchain only with an explicit maintenance and testing plan.

## React quality bar

When React is justified:

- Use TypeScript and a maintained build tool approved for the repository.
- Keep components small, composable, and driven by explicit typed contracts.
- Separate server state, local UI state, and form state; do not mirror derived state.
- Use stable keys, deterministic rendering, route-level error handling, accessible async status, and stale-response protection.
- Keep authentication and authorization on Django. Use secure same-origin requests and CSRF tokens for mutations.
- Define loading, optimistic, retry, conflict, and empty behavior before coding.
- Measure bundle size and interaction performance; avoid a state library until complexity demonstrates the need.
- Add component and interaction tests plus at least one end-to-end path for critical flows.

## Handoff

Report design rationale, architecture choice, components and tokens, changed files, responsive and accessibility behavior, checks run, performance implications, backend assumptions, and remaining risks.
