# Frontend architecture selection

## Default: Django templates plus progressive enhancement

Choose this when pages are primarily forms, navigation, rankings, availability lists, match acceptance, and score submission. Use template partials, static CSS, and small focused JavaScript modules. This preserves Django forms, sessions, CSRF, messages, redirects, and low operational complexity.

## React islands

Choose isolated React roots for genuinely interaction-heavy surfaces such as a visual availability planner or complex live suggestion filtering. Django continues to render the page shell and owns authentication and mutations. Define a small JSON contract and mount only where the interaction earns the added build and testing cost.

## React single-page application

Choose a SPA only when the product needs sustained app-like client navigation, substantial shared client state, offline behavior, or a separate frontend deployment. Require an explicit API strategy, typed schemas, authentication and CSRF design, routing, error observability, end-to-end testing, deployment ownership, and a migration plan from existing templates.

## Decision test

Prefer the least complex option that satisfies the product experience. React is a rendering tool, not a visual-quality guarantee. A coherent design system, excellent content hierarchy, responsive behavior, accessibility, and careful state design matter more than framework choice.
