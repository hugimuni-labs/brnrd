# Plan: brr.run dashboard MVP

Implementation plan for the brr.run dashboard — the user-facing
web layer on top of the brr.run backend that gives users a view
of their accounts, projects, daemons, bindings, AI credentials,
failover policy, audit log, and cost ledger.

Companion to [`subject-managed-mode.md`](subject-managed-mode.md)
(the surfaces the dashboard renders),
[`design-brr-run-protocol.md`](design-brr-run-protocol.md) (the
REST endpoints the dashboard consumes — same surface the
daemon-side cloud-gate adapter consumes; no separate API to
maintain), and
[`decision-monorepo-structure.md`](decision-monorepo-structure.md)
(`src/brr_run_web/` lives in the monorepo).

## Status

**Not started.** Blocked on:

- `design-brr-run-protocol.md` acceptance — the REST endpoints
  the dashboard reads from need to lock first.
- `plan-managed-gates-launch.md` and `plan-failover-compute.md`
  slices 1+2 each — the dashboard's data sources are the
  endpoints those plans deliver.
- `decision-monorepo-structure.md` acceptance — settles where
  the code lives.

Ship order within this plan: bootstrap (slice 1) → core views
(slice 2) → cost / audit (slice 3) → polish (slice 4). Each slice
is shippable in isolation; slice 1 alone is "you can log in and
see your projects exist," already useful for self-hosters.

## Goals

- A user can sign up, pair a daemon, install the GitHub App, send
  a `@brr` comment, and see the resulting task appear in their
  dashboard — end-to-end, no CLI required after the daemon is up.
- A user can configure failover (add AI credential, enable
  failover mode, set caps) from the dashboard.
- A user can read their audit log and current-month cost from the
  dashboard.
- Build cost stays minimal: HTMX + Jinja templates over the
  existing REST endpoints, no SPA bundler at MVP. Self-hosters
  serve it from the same backend process.
- Total MVP scope: ~1 week of focused work for the minimal shape,
  ~2-3 weeks if we polish to "I would show this to a stranger"
  level.

## Done definition

Seven views, each one renderable end-to-end against real backend
endpoints:

1. **Accounts / projects view** — list projects, create new
   project, delete project, per-project daemon-status badge,
   per-project last-activity timestamp.
2. **Project detail view** — chat bindings, repo bindings,
   per-daemon online status + last-seen + daemon name, recent
   events table (last 50).
3. **Task / event detail view** — per-event timeline (received,
   resolved to project, dispatched, executed-where, responded);
   if executed on managed compute, spawn record (cost, duration,
   exit code, sandbox link); link to resulting git branch on the
   remote.
4. **Conversation view (per-project)** — chat-style scroll of
   events and responses for one project. Live-rendered by
   proxying through to the daemon when online; falls back to
   metadata-only when daemon is offline.
5. **AI credentials view** — list credentials (id, provider,
   shape, created, last-used), add credential (modal with
   provider picker and key-or-dir-tarball shape picker), remove
   credential (with confirmation).
6. **Failover policy view** — enable / disable toggle, mode
   picker (ask / auto-under-USD / auto-under-per-day /
   auto-always / never), monthly spawn cap input, monthly cost
   cap input, current-month usage gauge, cost chart (sparkline
   per day, current month).
7. **Audit log view** — paginated, filterable by project /
   platform / outcome / spend window. Each row: timestamp, kind
   (event-dispatched, spawn-started, spawn-finished,
   credential-added, etc.), metadata, cost (if any).

Plus:

- Login / signup flow against `/v1/accounts/sessions`.
- Top nav with account switcher (for users with multiple
  accounts; v1 most users have one) and a "+ New project"
  shortcut.
- Empty states for "no projects yet," "no events yet," "no
  credentials yet" with clear next-action buttons.
- Mobile-decent layout (read-only is enough; configuration can
  be desktop-first).

Out of scope for MVP:

- Connectors view (no connectors exist; see
  [`decision-connectors-layering.md`](decision-connectors-layering.md)).
- Multi-user / team UI (per-seat team tier is post-launch).
- Notification preferences UI (defaults are baked into the
  backend; v-next).
- Themes / customisation.

## Why HTMX-first

The dashboard is mostly server-rendered tables, forms, and
charts over data the backend already exposes via REST. HTMX
covers this shape natively:

- Server-rendered templates → fast first paint, low backend
  complexity, no JS bundler.
- HTMX partial updates → enough interactivity for "add chat
  binding without full reload," "filter audit log live," "tail
  the events table."
- One static asset bundle → easy to serve from Upsun's
  read-only-app container per
  [`design-brr-run-protocol.md`](design-brr-run-protocol.md) →
  "Upsun deployment notes."
- No `node_modules` in the brr.run backend deploy pipeline.

If the cost / spawn chart or the conversation view grows real
interactivity demands (live tail, drag-to-reorder, etc.), we add
a small SPA carve-out for that view only. The MVP doesn't need
it.

## Slices

### Slice 1 — Bootstrap + login + accounts/projects view

Get a user logged in and seeing their project list. Self-hosters
can pick this up to confirm their backend is wired.

Steps:

1. `src/brr_run_web/` package layout:
   - `templates/` Jinja2 templates (one per view + a base layout)
   - `static/` HTMX asset, a small CSS, no JS-build pipeline
   - `__init__.py` registers the routes onto the brr.run FastAPI
     app (no separate web server).
2. Auth flow: `/login` GET (form), POST → POST to
   `/v1/accounts/sessions`, set session cookie, redirect.
   `/signup` GET / POST against `/v1/accounts`.
3. Session middleware for protected routes; redirect to
   `/login` on miss.
4. View 1: `GET /` → projects list. Renders against
   `GET /v1/accounts/projects`. Includes "+ New project" inline
   form (HTMX POST → `/v1/accounts/projects`, swap in the new
   row).
5. Base layout: top nav (logo, account email, "+ New project"
   shortcut, log-out), main content area.
6. Empty state: "No projects yet. Pair your first daemon with
   `brr accounts pair telegram` or install the GitHub App
   here."

**Estimate.** ~600-900 LOC templates + ~400 LOC routes + ~150
LOC CSS + base styling + ~200 LOC tests.

### Slice 2 — Project detail + AI credentials + failover policy

The configuration surfaces. After this slice, a user can fully
configure their managed-mode setup from the dashboard.

Steps:

1. View 2: `GET /projects/{project_id}` → project detail.
   Bindings (chats, repos) with add/remove inline forms.
   Daemons table with online badge + last-seen + name.
   Recent events table (last 50, links to event detail).
2. View 5: `GET /credentials` → AI credentials view. Add modal
   with provider picker (anthropic / openai / google / github)
   and shape picker (key / dir-tarball + drag-drop tarball
   upload). Remove with confirmation.
3. View 6: `GET /failover` → failover policy view. Toggle,
   mode picker, cap inputs, current-month usage gauge (just a
   number for MVP; chart in slice 3).
4. HTMX patterns: form-POST → partial-template-swap so adding
   a binding doesn't refresh the whole page.
5. Validation: client-side via HTML5 + server-side echo on the
   form swap.

**Estimate.** ~900-1200 LOC templates + ~600 LOC routes + ~400
LOC tests.

### Slice 3 — Audit log + cost chart + event detail + conversation view

The observability surfaces. After this slice, a user can see
what happened and what it cost.

Steps:

1. View 7: `GET /audit` → audit log. Pagination (50 rows per
   page), filter UI (project, platform, outcome, date range,
   spend > $X). Live-update via HTMX polling on a 10s interval
   for "new entries since cursor."
2. View 3: `GET /events/{event_id}` → event detail. Timeline
   table, spawn record (if any), branch link.
3. View 4: `GET /projects/{project_id}/chat` → conversation
   view. Proxies through to `GET <daemon>/v1/local/conversation`
   when daemon online; falls back to metadata-only timeline when
   offline. Polls for new messages on a 5s interval.
4. Cost chart on the failover view: daily sparkline for the
   current month, rendered server-side as inline SVG (no
   JS-chart library). Per-day spawn count and per-day cost on
   hover.
5. CSV export on the audit view ("Download as CSV") for users
   who want to dig into their cost history offline.

**Estimate.** ~1000-1400 LOC templates + ~700 LOC routes +
inline SVG chart helper (~200 LOC) + ~500 LOC tests.

### Slice 4 — Polish

Cashes out the value into something a stranger would call good.

Steps:

1. Empty states across all views with helpful next-action
   hints.
2. Loading states (HTMX `hx-indicator` on slow forms).
3. Error states (4xx / 5xx surfaces, with retry where
   applicable).
4. Mobile layout pass — ensure read-only flows work on a phone;
   configuration flows OK to be desktop-first.
5. Accessibility pass — keyboard navigation, screen-reader
   labels, ARIA on the live-updating regions.
6. Visual polish — consistent spacing, typography, palette.
   Lean on a simple framework (Pico CSS or similar) to avoid
   pixel-pushing.
7. Onboarding tour for first-time users — three-step modal
   overlay on first project creation explaining: "1. Pair a
   daemon. 2. Bind a chat or repo. 3. Send your first task."

**Estimate.** ~500 LOC templates / CSS + ~300 LOC tests.

## What ships where

| Component | Lives at |
|-----------|----------|
| Templates, static assets, view routes | `src/brr_run_web/` |
| FastAPI app composition (mounts web routes on the existing API app) | `src/brr_run/app.py` (or wherever it lives) |
| Session middleware | `src/brr_run/middleware/session.py` |
| Auth views (`/login`, `/signup`, `/logout`) | `src/brr_run_web/routes/auth.py` |
| Project / binding / credential / failover / audit views | `src/brr_run_web/routes/*.py` |
| Tests | `tests/brr_run_web/` |
| Build | None — Python-only, no JS bundler at MVP |
| Deploy | Bundled with brr.run backend; served from same Upsun app per `design-brr-run-protocol.md` → "Upsun deployment notes" |

## Out of scope

- **Connectors view** — no connectors exist; see
  [`decision-connectors-layering.md`](decision-connectors-layering.md).
- **Multi-user / team UI** — per-seat team tier is post-launch.
- **Notification preferences UI** — defaults are baked into the
  backend; v-next.
- **Themes / customisation** — one good default look is enough
  at MVP.
- **Real-time websockets** — HTMX polling is enough for MVP. If
  the conversation view's live experience matters, add
  websockets in v-next.
- **Mobile app** — responsive web is enough; native app is
  post-launch if there's demand.
- **Built-in payments UI** — manual invoicing at launch per
  `decision-pricing-shape.md`; the audit log carries enough for
  CSV export to email.

## Risks

- **Dashboard becomes the bottleneck for backend API design.**
  Mitigation: ship Slice 1 against the smallest possible API
  subset; iterate the rest of the API while building Slices 2-3.
  Don't let dashboard ergonomics drive premature backend
  surfaces.
- **HTMX hits its ceiling on the conversation view.** The proxy-
  to-daemon shape might want real bidirectional streaming.
  Mitigation: ship the polling version first; carve out
  websockets only if usage data shows it matters.
- **Mobile experience falls behind.** Mitigation: read-only flows
  must work on mobile from Slice 4; configuration flows can
  defer to desktop. Accept this; revisit if mobile adoption
  surprises us.
- **Upsun static-serve quirks.** The read-only application
  container needs static assets written at build time, not
  runtime. Mitigation: bundle assets into the deploy image
  during `build:` step; declare them via the routes config.
  Covered in `design-brr-run-protocol.md` → "Upsun deployment
  notes" already.
- **Slice 4 (polish) is a tar pit.** It's easy to spend a month
  polishing. Mitigation: time-box to one focused week; accept
  "good enough for launch" rather than "perfect."

## Read next

1. [`subject-managed-mode.md`](subject-managed-mode.md) for the
   surfaces the dashboard renders.
2. [`design-brr-run-protocol.md`](design-brr-run-protocol.md)
   for the REST endpoints the dashboard consumes.
3. [`decision-monorepo-structure.md`](decision-monorepo-structure.md)
   for where `src/brr_run_web/` fits.
4. [`decision-pricing-shape.md`](decision-pricing-shape.md) for
   what the cost chart needs to show.
5. [`plan-managed-gates-launch.md`](plan-managed-gates-launch.md)
   and [`plan-failover-compute.md`](plan-failover-compute.md)
   for the backend endpoints the dashboard reads.
6. [`decision-connectors-layering.md`](decision-connectors-layering.md)
   for why no connectors view ships at MVP.

## Lineage

- 2026-05-25 — drafted as part of the brr.run reshape that
  collapsed brnrd as a separate name into brr.run. The
  dashboard absorbs what would have been `plan-brnrd-mvp.md`
  in an earlier draft of the reshape.
