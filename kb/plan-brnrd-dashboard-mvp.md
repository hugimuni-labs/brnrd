# Plan: brnrd dashboard MVP

Implementation plan for the brnrd dashboard — the user-facing
web layer on top of the brnrd backend that gives users a view
of their accounts, projects, daemons, bindings, AI credentials,
failover policy, audit log, and cost ledger.

Companion to [`subject-managed-mode.md`](subject-managed-mode.md)
(the surfaces the dashboard renders),
[`design-brnrd-protocol.md`](design-brnrd-protocol.md) (the
REST endpoints the dashboard consumes — same surface the
daemon-side cloud-gate adapter consumes; no separate API to
maintain), and
[`decision-monorepo-structure.md`](decision-monorepo-structure.md)
(`src/brnrd_web/` lives in the monorepo).

## Status

**Not started.** Blocked on:

- `design-brnrd-protocol.md` acceptance — the REST endpoints
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

Eight views, each one renderable end-to-end against real backend
endpoints:

1. **Accounts / projects view** — list projects, create new
   project, delete project, per-project daemon-status badge,
   per-project last-activity timestamp. Includes the
   tier-aware **project-cap gauge** (`8 / 25 projects
   (unlocked unlimited at $10 of cumulative top-ups — $X.XX
   to go)` for subscribers pre-unlock; `8 projects
   (unlimited)` post-unlock; `2 / 3 projects` for Free).
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
8. **Allowance + usage view (NEW)** — first-class read of the
   account's standing against tier limits: events bar
   (consumed / cap, this month), credits bar with bucket
   breakdown (signup bonus / subscriber monthly grant /
   purchased / promotional, each with its own expiry note),
   projects bar (used / effective cap, with the "to-unlock"
   delta if applicable), throttle-state banner when active,
   spend chart (per-day credits consumed for the last 6 months
   on Subscribed, current month only on Free). This is the
   anchor surface for the nudge UX (see "Allowance gauges +
   honest-nudge UX" section below). Linked from the top nav
   directly, not buried under a settings page.

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
  [`design-brnrd-protocol.md`](design-brnrd-protocol.md) →
  "Upsun deployment notes."
- No `node_modules` in the brnrd backend deploy pipeline.

If the cost / spawn chart or the conversation view grows real
interactivity demands (live tail, drag-to-reorder, etc.), we add
a small SPA carve-out for that view only. The MVP doesn't need
it.

## Allowance gauges + honest-nudge UX

The dashboard surfaces allowance consumption as a first-class
view AND inline gauges across every other view where they're
relevant. Nudges toward subscribe / top-up appear when the
user crosses an allowance threshold. **Honest nudges, not dark
patterns** — see
[`decision-pricing-shape.md`](decision-pricing-shape.md) §
"Dashboard nudges + transparency" for the full policy +
anti-pattern list.

### Inline gauges (always visible, never blocking)

- **Top nav**: a compact `usage` button shows a single-letter
  status: green dot (under 75% of all caps), yellow (≥75% of
  any cap), orange (≥90%), red (≥100%, throttle active).
  Click → allowance view.
- **Project list view**: project-count gauge in the header
  (`8 / 25 projects`).
- **Failover view**: monthly events + monthly credits gauges
  alongside the existing usage line.
- **Allowance view**: full gauges for events, credits (with
  bucket breakdown), and projects (with unlock progress for
  subscribers).

### Banner nudges (per session, dismissible)

Banners appear at the top of the page content area (not the
nav). One banner max at a time; if multiple thresholds are
crossed, prioritise the most severe (throttling active → near-
cap → expiry-soon → upgrade-prompt). Dismissed banners stay
hidden for the session; next session they reappear if the
condition still holds.

| Trigger | Banner copy | CTA(s) |
|---------|-------------|--------|
| Free user ≥80 events this month | "You're at 80% of your free event allowance this month." | "Subscribe for 10K/mo →" |
| Free user at 100 events this month (throttling) | "Events throttled — you've hit the Free cap. Throttle clears \<next month boundary\>." | "Subscribe to lift now →" / "Self-host instead" |
| Free user's signup bonus consumed | "Free signup bonus consumed. Top up or subscribe for ongoing failover compute." | "Top up at $0.01/credit" / "Subscribe →" |
| Free user's signup bonus expires within 5 days unused | "Your signup bonus expires \<date\> — \<N\> credits unused." | "Try failover now" / "Subscribe for ongoing credits →" |
| Subscriber ≥80% of credit grant this month | "You've used 80% of this month's 300 included credits." | "Top up at $0.01/credit (~33 spawns per $1)" |
| Subscriber at 25-project cap, not unlocked | (form-side error on 26th project creation) "Subscriber accounts support up to 25 projects by default — unlock unlimited after $10 of cumulative top-ups (\$X.XX to go)." | "Top up now →" |
| Subscriber ≥80% of event cap | "You're at 80% of your monthly event cap." | "Email us — we'll raise it" |
| Throttling active for ANY user | (red banner, not dismissible until cleared) "Your events are being throttled. \<reason + clear-time\>." | (contextual: subscribe / wait / contact) |

### Gate-side nudge footer

Beyond the dashboard, gate replies (TG / GH / Slack) include a
one-line subscribe footer **only** when the user just hit a
throttle / cap / out-of-credit event. Never on successful
responses. Format:

```
[ this task was queued — Free event cap reached.
  subscribe at brnrd.dev/subscribe → ]
```

Single line, plain text (rendered as such on each platform's
formatting layer).

### Anti-patterns (explicitly avoided)

- **No modal blockers.** Banners are inline; the user is never
  forced to click anything to continue.
- **No cancellation friction.** Cancel button on the
  subscription settings goes straight to Stripe Customer
  Portal — no "are you sure?" + "what could we do better?"
  + retention offers.
- **No dismissal asymmetry.** The "dismiss" affordance is the
  same visual weight as the "subscribe" affordance.
- **No countdown timers / "limited-time" pressure.** The
  early-supporter $5 price is mentioned matter-of-factly on
  the subscribe page, not as a pressure tactic on the
  dashboard.
- **No silent throttling.** Every throttle is signposted; the
  user always knows why a request was slowed / queued. This
  is the load-bearing version of "honest" — the user is in
  control of the situation because they understand it.
- **No nudge spam.** At most one banner per page-load; at most
  one gate footer per throttle event (not per queued event);
  dismissed banners stay dismissed for the session.

## Slices

### Slice 1 — Bootstrap + login + accounts/projects view

Get a user logged in and seeing their project list. Self-hosters
can pick this up to confirm their backend is wired.

Steps:

1. `src/brnrd_web/` package layout:
   - `templates/` Jinja2 templates (one per view + a base layout)
   - `static/` HTMX asset, a small CSS, no JS-build pipeline
   - `__init__.py` registers the routes onto the brnrd FastAPI
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
   `brr brnrd connect` or install the GitHub App
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

### Slice 3 — Audit log + cost chart + event detail + conversation view + allowance view

The observability surfaces. After this slice, a user can see
what happened, what it cost, and where they stand against
their tier limits.

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
6. **View 8: `GET /usage` → allowance view** (NEW). Renders
   against `GET /v1/accounts/wallet` (credit buckets +
   `cumulative_purchased_usd_lifetime` + `project_cap_unlocked`),
   `GET /v1/accounts/usage/events` (current-month event count
   + cap), and the projects-list endpoint (count + effective
   cap). Three gauges + bucket breakdown table + spend chart
   (reuse the inline SVG helper from step 4). Empty-state
   sub-views for "no credits yet — your signup bonus expires
   in N days" etc.
7. **Inline gauge component**: small Jinja partial that
   renders an `events / cap` or `credits / grant` or
   `projects / cap` bar. Used in the top nav (status dot),
   projects view header, failover view, and the allowance
   view. One component, four placements.
8. **Banner-nudge component**: dismissible banner template
   driven by a server-computed nudge-priority list per page-
   render. Dismissal state stored in the session cookie
   (per-page-key, not per-banner; one cookie kv pair per
   threshold-crossing event). Banner triggers + copy match
   the policy table in "Allowance gauges + honest-nudge UX"
   above; the table also lives in
   [`decision-pricing-shape.md`](decision-pricing-shape.md)
   so backend + dashboard can stay in sync.

**Estimate.** ~1200-1600 LOC templates + ~900 LOC routes +
inline SVG chart helper (~200 LOC) + gauge + banner partials
(~300 LOC) + ~700 LOC tests.

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
| Templates, static assets, view routes | `src/brnrd_web/` |
| FastAPI app composition (mounts web routes on the existing API app) | `src/brnrd/app.py` (or wherever it lives) |
| Session middleware | `src/brnrd/middleware/session.py` |
| Auth views (`/login`, `/signup`, `/logout`) | `src/brnrd_web/routes/auth.py` |
| Project / binding / credential / failover / audit views | `src/brnrd_web/routes/*.py` |
| Tests | `tests/brnrd_web/` |
| Build | None — Python-only, no JS bundler at MVP |
| Deploy | Bundled with brnrd backend; served from same Upsun app per `design-brnrd-protocol.md` → "Upsun deployment notes" |

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
  Covered in `design-brnrd-protocol.md` → "Upsun deployment
  notes" already.
- **Slice 4 (polish) is a tar pit.** It's easy to spend a month
  polishing. Mitigation: time-box to one focused week; accept
  "good enough for launch" rather than "perfect."

## Read next

1. [`subject-managed-mode.md`](subject-managed-mode.md) for the
   surfaces the dashboard renders.
2. [`design-brnrd-protocol.md`](design-brnrd-protocol.md)
   for the REST endpoints the dashboard consumes.
3. [`decision-monorepo-structure.md`](decision-monorepo-structure.md)
   for where `src/brnrd_web/` fits.
4. [`decision-pricing-shape.md`](decision-pricing-shape.md) for
   what the cost chart needs to show.
5. [`plan-managed-gates-launch.md`](plan-managed-gates-launch.md)
   and [`plan-failover-compute.md`](plan-failover-compute.md)
   for the backend endpoints the dashboard reads.
6. [`decision-connectors-layering.md`](decision-connectors-layering.md)
   for why no connectors view ships at MVP.

## Lineage

- 2026-05-25 — drafted as part of the brnrd reshape that
  collapsed brnrd as a separate name into brnrd. The
  dashboard absorbs what would have been `plan-brnrd-mvp.md`
  in an earlier draft of the reshape.
- 2026-05-26 (locking pass II — allowance view + honest-nudge
  UX). **Allowance + usage view added as the 8th first-class
  view** (linked from top nav, not buried under settings):
  events bar, credits bar with bucket breakdown
  (`free_signup_bonus` / `subscriber_monthly` / `purchased` /
  `promotional`), projects bar (with unlock-progress delta
  for subscribers), throttle banner when active, spend chart
  (6 months for Subscribed; current month for Free). **New
  "Allowance gauges + honest-nudge UX" section** captures
  the inline-gauge placements (top nav status dot, project
  list header, failover view) + the banner-nudge trigger /
  copy / CTA table + the explicit anti-patterns list (no
  modals, no cancellation friction, no countdown timers, no
  silent throttling, no nudge spam) + the gate-side one-line
  subscribe footer triggered ONLY on throttle / cap / out-of-
  credit events. Slice 3 extended to deliver the allowance
  view + inline gauge component + banner-nudge component; LOC
  estimate raised accordingly. Projects-view item-1 grew the
  tier-aware project-cap gauge for the new 3 / 25 / unlimited
  tiering. Driven by the user's "a dashboard to show the
  allowance consumption in events and credits, and a nudge to
  go subscribe if anything got above the allowance — that's
  not too mean, right?" + "throttling is a good idea, like
  it." Cross-references
  [`decision-pricing-shape.md`](decision-pricing-shape.md) §
  "Dashboard nudges + transparency" as the canonical policy
  source.
