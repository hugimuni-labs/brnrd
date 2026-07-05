# Plan: brnrd dashboard MVP

**Status: accepted 2026-05-26** (locked in PR #40 MR review,
**explicitly fluid** — user plans to adjust a lot of the
design during implementation as the UX shape becomes
concrete; treat the view inventory + slice plan as a
working frame, not a contract; expect re-grooming each
time a slice lands).

Implementation plan for the brnrd dashboard — the user-facing
web layer on top of the brnrd backend that gives users a view
of their accounts, projects, daemons, bindings, live activity,
AI credentials, failover policy, audit log, and cost ledger.

Companion to [`subject-managed-mode.md`](subject-managed-mode.md)
(the surfaces the dashboard renders),
[`design-brnrd-protocol.md`](design-brnrd-protocol.md) (the
REST endpoints the dashboard consumes — same surface the
daemon-side cloud-gate adapter consumes; no separate API to
maintain), and
[`decision-monorepo-structure.md`](decision-monorepo-structure.md)
(`src/brnrd_web/` lives in the monorepo).

## Current implementation state

The dashboard MVP plan is accepted, but the full dashboard is not
implemented. The prototype web shell in `src/brnrd_web/` currently
covers GitHub login plus the device-flow approve page on the intended
server-rendered substrate: Jinja templates plus packaged static CSS
mounted from the brnrd FastAPI app. The projects, tasks, credentials,
activity, failover, audit, allowance, and billing views still depend on the
remaining managed-gates / failover / billing backend slices.

Ship order within this plan: bootstrap (slice 1) → core views
(slice 2) → cost / audit (slice 3) → polish (slice 4). Each slice
is shippable in isolation; slice 1 alone is "you can log in and
see your projects exist," already useful for self-hosters.

## Goals

- A user can sign in with GitHub, pair a daemon, install the GitHub App, send
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

Nine views, each one renderable end-to-end against real backend
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
9. **Activity view (NEW)** — running and scheduled work across
   a project: active runs from the daemon presence / run
   registry, queued or parked respawns, and future scheduled
   wakes. Each row shows kind (`running`, `queued`,
   `scheduled`, `respawn`), source thread, summary, Shell/Core,
   phase / status, branch / PR when known, started / updated
   timestamp, and next fire time or `defer_until` when
   applicable. This is the UI owner for `plan-repo-gardening`
   Task 2E; the runner/schedule data contract still lives with
   the daemon / brnrd protocol slices, not in dashboard-local
   polling code.

Plus:

- Login flow through GitHub OAuth (`/auth/github/start` →
  `/auth/github/callback`); no email/password signup.
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
- **Ergonomics views** (per-project + fleet) — specced in
  [`design-agent-ergonomics.md`](design-agent-ergonomics.md) as a
  follow-up slice that lands after the back-channel sink + brnrd
  endpoint do; not in the MVP eight.

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

> **Canonical policy lives in
> [`decision-pricing-shape.md`](decision-pricing-shape.md) §
> "Dashboard nudges + transparency"** — that page owns the
> trigger / copy / anti-patterns table. This section spells out
> only what the dashboard has to *build* to implement that
> policy: gauge placements, banner-component shape, dismissal
> persistence, and the gate-side footer wiring. If you want to
> change *when* or *what* the dashboard nudges say, change the
> pricing-shape page; if you want to change *how* the dashboard
> renders the nudge, change here.

The nudge is conceptually the **resolution to a real
user-facing situation** (slow reply, paused compute, blocked
action), not a generic upsell. The dashboard's job is to make
that resolution visible + actionable without ever blocking
the user's flow.

### Inline gauges (always visible, never blocking)

- **Top nav**: a compact `usage` button shows a single-letter
  status: green dot (under 75% of all caps), yellow (≥75% of
  any cap), orange (≥90%), red (≥100%, throttle active).
  Click → allowance view.
- **Project list view**: project-count gauge in the header
  (`8 / 25 projects` or `2 / 3 projects` etc., tier-aware).
- **Failover view**: monthly events + monthly credits gauges
  alongside the existing usage line.
- **Allowance view**: full gauges for events, credits (with
  bucket breakdown), and projects (with unlock progress for
  subscribers).

One Jinja partial renders all four placements; differs only
by which gauges are visible and at what size.

### Banner-nudge component

The component the dashboard renders for each banner trigger
defined in
[`decision-pricing-shape.md`](decision-pricing-shape.md) §
"Banner nudges (per session, dismissible)":

- Top of the page content area (not the nav).
- One banner max at a time; if multiple thresholds are
  crossed, prioritise the most severe (throttle active →
  near-cap → expiry-soon → upgrade-prompt).
- Dismissal stored in the session cookie as a per-trigger key;
  next session the banner reappears if the condition still
  holds.
- Dismiss affordance is the same visual weight as the CTA
  affordance (per the canonical anti-pattern list).

The banner copy + CTA strings live in a single Python module
(e.g. `src/brnrd_web/nudges.py`) that mirrors the pricing-shape
trigger table. When pricing-shape's table changes, this module
gets a one-line update; nothing else moves.

### Gate-side nudge footer

The gate adapter (TG / GH / Slack) reads from the same
`nudges.py` module to construct the one-line footer it appends
to soft-throttled / out-of-credit / cap-blocked replies. The
reply itself is always delivered — events keep flowing during
soft-throttle, per
[`decision-pricing-shape.md`](decision-pricing-shape.md) §
"Event-cap overage — soft throttle, not a hard wall"; the
footer is the resolution to the "why is this slow?" / "why is
this paused?" question, not a "subscribe to get a reply"
gate.

The dashboard surfaces the same footer-state as a session
banner if the user opens the dashboard while in a throttled
state, so the dashboard and the gate stay aligned.

## Slices

### Slice 1 — Bootstrap + login + accounts/projects view

Get a user logged in and seeing their project list. Self-hosters
can pick this up to confirm their backend is wired.

Steps:

1. `src/brnrd_web/` package layout — **login / approve substrate shipped
   2026-06-10**:
   - `templates/` Jinja2 templates (one per view + a base layout)
   - `static/` small CSS today; add the HTMX asset when partial-update
     views land; no JS-build pipeline
   - `__init__.py` registers the routes onto the brnrd FastAPI
     app (no separate web server).
2. Auth flow: `/login` GET renders "Sign in with GitHub";
   `/auth/github/start` redirects to GitHub with state + PKCE;
   `/auth/github/callback` resolves the GitHub identity, creates or
   updates the brnrd account, sets the session cookie, and redirects.
   No `/signup` and no email/password fallback.
3. Session middleware for protected routes; redirect to
   `/login` on miss.
4. View 1: `GET /` → projects list. Renders against
   `GET /v1/accounts/projects`. Includes "+ New project" inline
   form (HTMX POST → `/v1/accounts/projects`, swap in the new
   row).
5. Base layout: top nav (logo, GitHub login/email, "+ New project"
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

### Slice 3 — Audit log + cost chart + event detail + conversation view + allowance view + activity view

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
9. **View 9: `GET /activity` → activity view** (NEW). Renders
   running runs from the daemon presence/run registry, scheduled
   wakes from the resident schedule, and parked respawn requests
   from the respawn/defer queue. The backend contract now
   expose a uniform activity record:
   `id`, `kind`, `repo_id`, `source`, `conversation_key`,
   `summary`, `runner` (Shell/Core metadata), `status`,
   `phase`, `branch`, `pr_number`, `started_at`,
   `updated_at`, `scheduled_for`, `defer_until`, and
   `links`. Dashboard behaviour is read-only in MVP: filter by
   kind/status, link to event/run detail when available, and
   show empty states for "nothing running" and "nothing
   scheduled." Mutation actions (cancel / reschedule / approve
   respawn) belong to later protocol slices.

**Estimate.** ~1300-1750 LOC templates + ~1000 LOC routes +
inline SVG chart helper (~200 LOC) + gauge + banner partials
(~300 LOC) + ~800 LOC tests.

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
| Auth views (`/login`, `/auth/github/start`, `/auth/github/callback`, `/logout`) | `src/brnrd_web/routes.py` now; split under `src/brnrd_web/routes/*.py` when the MVP grows |
| Project / binding / credential / activity / failover / audit views | `src/brnrd_web/routes/*.py` |
| Tests | `tests/brnrd_web/` |
| Build | None — Python-only, no JS bundler at MVP |
| Deploy | Bundled with brnrd backend; served from same Upsun app per `design-brnrd-protocol.md` → "Upsun deployment notes" |

## Gap: Current Planned State view (2026-07-04, evt-…v7me thread)

The maintainer asked (telegram, unprompted) for a durable, account-scoped,
web-visible surface showing "workstreams and plans for them and the state of
things according to plan — the forks, the blocks, the decisions" — distinct
from a single run's status. Naming it here so the ask has a handle: **Current
Planned State (CPS)**.

**This is not new architecture — it already shipped as CS5 + CS7**
(`plan-control-surface.md`): `plans/<repo-slug>/active.md` +
`plans/_cross-repo/active.md` (tactical, "what we're doing now") and
`ledger/decisions.md` (strategic, "what we've decided and why"), both in the
account dominion repo, both injected into every wake already. What's
genuinely missing is **this dashboard's view of them** — the view inventory
above (Slices 1-4) never scoped a route for the two files. Adding one is
small: read two markdown files per account (repo-scoped + cross-repo) out of
the account dominion repo the same way CS2's `run_state_blob_url` already
resolves a per-run doc, render as-is (they're already prose the resident
writes for a human). No new backend shape.

**Resolved (maintainer, GH #53 comment, 2026-07-04T18:07:59Z):**
- **Archival convention** — agreed, with one addition: an archived file opens
  with a short header summarizing *what* was parked and *why*, so a later
  lookup doesn't have to re-read the whole thing to know if it's relevant.
  Convention is now: `plans/<repo-slug>/archive/<date>-<slug>.md`, first
  block a `parked: <date> · why: <one line>` header, body unchanged from
  what `active.md` held. Resident discipline (archive + trim `active.md`),
  no new mechanism.
- **Aesthetic fork** — resolved: ship the CPS view plain through Slice 1-3,
  skin once it exists to skin ("Persona 5 thing can wait"). But the
  maintainer separated this from a second, *not* aesthetic-specific ask:
  take a close look at the current frontend stack's general quality —
  his read is it's "quite stripped down and likely outdated right now,"
  independent of whether it ever gets a Persona-5 skin. That's a distinct
  open item, not closed by the skin-later call:
  - **Frontend stack quality audit — superseded 2026-07-05, replace not
    audit.** The maintainer moved past an audit: "current frontend stack
    is likely shit, it is a good time to replace it with something
    modern, extensible, thoroughly built, responsive, and little-code,
    easy-to-maintain," explicitly deferring the actual framework/stack
    choice ("I don't know this field well enough") and setting one bar
    instead: **"it should survive a fireship review (or alike)."** Not
    stack-picked here — real implementation work for the slice that
    rebuilds it. Detail:
    [`design-dashboard-live-surface.md`](design-dashboard-live-surface.md)
    §"quota multi-axis, PR/issue extensibility, frontend replaced".
- **The "vehicles" framing** — confirmed already data: the runner catalog's
  `cost_rank`/`class` spectrum (`design-runner-cores.md`) *is* the resource
  economy — CPS renders spend against it, doesn't invent a second one.

**Shipped 2026-07-04 ("let's implement the dashboard"):** the CPS view
itself, mirroring the shipped Activity view's pattern (local daemon
publishes a snapshot; hosted DB stores it; dashboard reads the DB
directly — no browser access to the account dominion).

- `Repo.plan_md` / `Repo.plan_updated_at` (repo-scoped CS5 active plan) and
  `Account.cross_repo_plan_md` / `Account.decision_ledger_md` /
  `Account.plans_updated_at` (account-wide CS5 cross-repo plan + CS7
  ledger) — new columns, Postgres startup migration in `migrations.py`.
- `PUT /v1/daemons/plans` (`schemas.PlansReport`/`PlansOut`) — daemon-facing,
  same last-write-wins shape as `/v1/daemons/activity`.
- `src/brr/gates/cloud.py`: `_plans_snapshot`/`_publish_plans`, called each
  loop iteration alongside `_publish_activity`. Reads
  `account.active_plan_path`, `cross_repo_plans_path`, `decisions_ledger_path`
  via a read-only `resolve_context(create=False)`; returns `None` (skip,
  don't publish) rather than raising when no account context resolves — a
  plain repo-local `.brr/` with no account is a normal shape, not an error.
- `GET /plans` (`src/brnrd_web/plans_dashboard.py` + `templates/plans.html`)
  — renders the three fields plain (`<pre>`, no markdown-to-HTML pass; ship
  plain, skin later per the aesthetic-fork resolution above), one panel per
  populated field, empty state when nothing's mirrored yet. Nav link added
  to `dashboard.html` and `activity.html`.
- Tests: `tests/test_brnrd_plans.py` (endpoint + dashboard render + empty
  state + login-required), `tests/test_cloud_gate.py::
  test_loop_publishes_plans_snapshot` (local files on disk → published →
  landed in the right DB rows, end to end).
- Not done: CSV/markdown export, live polling (Activity's HTMX poll
  pattern would fit if this gets busy), and no attempt to render the
  archive convention (`plans/<repo>/archive/*.md`) — only `active.md` and
  the ledger are mirrored; archived plans stay a local/git-blob-link
  concern, not a dashboard one, unless that's asked for separately.

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
- **Built-in payments UI for top-ups / subscription
  management** — Stripe Checkout + Customer Portal handle this
  per [`design-billing.md`](design-billing.md); the dashboard
  just links out to Stripe for card / invoice / cancellation
  flows. The audit log still carries enough for CSV export
  for users who want to dig into their cost history outside
  the dashboard.

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
6. [`plan-repo-gardening.md`](plan-repo-gardening.md) Task 2E for the
   Activity view handoff from runner/schedule work.
7. [`decision-connectors-layering.md`](decision-connectors-layering.md)
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
- 2026-05-26 (locking pass III — grooming). **"Allowance
  gauges + honest-nudge UX" section trimmed** to remove the
  duplicated trigger / copy / anti-patterns table (canonical
  home: `decision-pricing-shape.md` § "Dashboard nudges +
  transparency"). This page now spells out only the *build*
  side of the nudge UX — gauge component, banner component,
  dismissal-persistence shape, gate-footer wiring — and
  delegates the *policy* side (when to nudge, what to say,
  what NOT to do) to pricing-shape. The two-place
  duplication was the highest drift risk in the locking-
  pass-II shape; the canonical-pointer shape removes it
  without losing the implementation detail the slice-3
  work needs. Gate-side footer also reworded to match the
  pricing-shape soft-throttle reframe — events still flow
  during soft-throttle, the footer is the resolution to a
  throttled-flow situation rather than a paywall. Banner
  copy + gate footer strings now live in a single
  `src/brnrd_web/nudges.py` module that the gate adapter
  AND the dashboard both read from, so the two surfaces
  stay aligned. Driven by the user's "I think there's a
  lot of data duplication ... maybe we still could prune
  and groom it" MR-review feedback.
- 2026-06-29 (runner/schedule activity handoff). **Activity
  view added as the 9th first-class view**: running runs from
  daemon presence/run state, scheduled wakes from resident
  schedule, and parked respawn requests from the respawn/defer
  queue. This resolves `plan-repo-gardening.md` Task 2E at the
  dashboard-planning layer: the dashboard owns the read-only UI
  surface; the daemon / brnrd protocol slices still own the
  uniform activity-record endpoint and any later cancel /
  reschedule / approve actions.
- 2026-06-29 (Activity implementation). The read-only Activity
  slice shipped: daemons publish snapshots with
  `PUT /v1/daemons/activity`, account clients read
  `GET /v1/accounts/activity`, and `GET /activity` renders the
  dashboard view. The activity record follows the accepted
  repo-first decision (`repo_id`, not `project_id`); later cancel,
  reschedule, and approve-respawn mutations remain outside this MVP
  slice.
- 2026-07-04 (CPS implementation). The Current Planned State view
  shipped, mirroring the Activity slice's publish/store/render shape:
  `PUT /v1/daemons/plans` + `Repo.plan_md` / `Account.cross_repo_plan_md`
  / `Account.decision_ledger_md` + `GET /plans`. See "Gap: Current
  Planned State view" above for the full shape and what's still out
  (export, live polling, archived-plan rendering).
