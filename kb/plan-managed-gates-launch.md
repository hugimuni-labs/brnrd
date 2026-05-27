# Plan: managed gates — launch sequencing

**Status: accepted 2026-05-26** (locked in PR #40 MR review;
implementation feedback may reshape — treat the slice
breakdown as a working spine, not a contract).

Implementation plan for **Surface A** (managed dispatcher: hosted
gates + multi-project routing + permission prompts) of
[managed mode](subject-managed-mode.md), specified in
[`design-brnrd-protocol.md`](design-brnrd-protocol.md). Three
slices ship at launch, in this order: **GitHub App adapter first**
(largest BYO-setup pain relief), **Telegram bot adapter as
fast-follow** on the same backend, **multi-project routing +
permission-prompt API** on top of both.

Sister plan
[`plan-failover-compute.md`](plan-failover-compute.md) covers the
managed-compute failover spawn on top of the same backend
skeleton this plan stands up. Sister plan
[`plan-brnrd-dashboard-mvp.md`](plan-brnrd-dashboard-mvp.md)
covers the dashboard view of all of this.

## Status

**Not started.** Blocked on:

- A small brnrd backend prototype (~3 days work) demonstrating
  the inbox-as-service protocol end-to-end (Telegram update →
  inbox → daemon poll → response → Telegram message). FastAPI
  app + postgres on Upsun per
  [`design-brnrd-protocol.md`](design-brnrd-protocol.md) →
  "Upsun deployment notes." Lives at `src/brnrd/` per
  [`decision-monorepo-structure.md`](decision-monorepo-structure.md).
- [`design-brnrd-protocol.md`](design-brnrd-protocol.md)
  acceptance — the wire format (gates, routing, prompts) needs
  to be locked before both sides start building in parallel.
- [`decision-pricing-shape.md`](decision-pricing-shape.md)
  acceptance — the launch needs free-tier rate caps and the
  100-spawns-per-month cap decided before the gate adapters
  wire up enforcement.

## Goals

- Both adapters reachable via `brr brnrd pair {telegram,github}`
  on launch day.
- A single managed bot per platform serves multiple of a user's
  projects via the multi-project routing protocol (no per-project
  bot setup).
- Permission prompts via the gate (TG inline buttons, GH issue
  comment commands) gate any failover spawn that needs explicit
  approval per the user's policy.
- OSS paths for Telegram and GitHub gates remain 1:1 untouched —
  existing `[gate.telegram]` and `[gate.github]` config sections
  keep working exactly as today.
- Launch announcement headlines both equally (TG + GH bot as
  the managed dispatcher's two front doors — Free for up to 3
  projects, Subscribed for up to 10 + the rest of the
  platform bundle).

## Done definition

- The `cloud` gate adapter ships in `src/brr/gates/cloud.py`,
  registered alongside `telegram` / `slack` / `github` in the
  gate registry.
- `brr brnrd pair {telegram,github}` CLI verbs work
  end-to-end: signup, API key issuance, pairing for both
  platforms.
- Multi-project routing protocol implemented end-to-end:
  - GH App webhook resolves `(installation_id, repo_full_name)
    → project_id` via the `repo_project_bindings` table; auto-
    bound on install, re-bindable from CLI / dashboard.
  - Telegram bot resolves `(account_id, chat_id) → project_id`
    via `chat_project_bindings`; `/connect <project>` binds the
    current chat; `/project <name> <task>` overrides for one
    message; `@<name> <task>` terse form; `/projects` and
    `/status` for introspection.
- Permission-prompt API live end-to-end:
  - `POST /v1/internal/prompts` posts a prompt via the
    originating gate.
  - Gate-side: TG inline buttons (Approve / Queue), GH issue
    comment commands (`@brr-bot approve` / `@brr-bot queue`).
  - `POST /v1/webhooks/prompts/{platform}/{prompt_id}/{outcome}`
    handles user response; resolves the prompt; either fires the
    spawn (via the failover-compute pathway) or queues the event.
- One docs page at `src/brr/docs/managed-mode.md` covering the
  pairing flow, multi-project routing UX, and permission-prompt
  UX.
- Tests cover: long-poll happy path, long-poll timeout, response
  post, 401 on revoked key, restart-resume from persisted
  cursor, multi-project routing resolution per platform, prompt
  posting + callback round trip on both platforms.

## Slices

### Slice 1 — Backend skeleton + GitHub App adapter

The bigger pain-relief slice. Ship first.

**Steps:**

1. `src/brnrd/` skeleton: FastAPI app, postgres schema
   (accounts, projects, daemons, bindings, events, audit_log),
   alembic migrations, Upsun deployment template at
   `deploy/upsun/`.
2. `src/brr/gates/cloud.py` — the cloud gate adapter
   (lifecycle, long-poll loop, response-post loop, cursor
   persistence). Common to GH and TG; the webhook side is
   brnrd's concern, not the daemon's.
3. CLI plumbing: `brr brnrd pair github`, `brr brnrd
   list-projects`, `brr brnrd bind-repo`. The pair verb
   opens the GH App install URL in the user's browser and
   waits for brnrd to confirm the install webhook landed.
4. brnrd-side webhook receiver for `installation`,
   `installation_repositories`, `issue_comment`,
   `pull_request_review_comment` events; normalisation to the
   event shape from the design.
5. brnrd-side response forwarder: post comment / review reply
   on the originating PR / issue.
6. Repo-project binding: auto-bind on `installation` and
   `installation_repositories` events (one repo → one project,
   defaulting to a project named after the repo); re-bindable
   via `brr brnrd bind-repo <installation_id> <repo>
   <project>`.
7. End-to-end smoke test: install brnrd GitHub App on a test
   repo → comment `@brr <task>` → event resolves to project →
   task lands in daemon inbox → daemon completes task →
   response posts back as a PR comment.

**Estimate.** ~700-900 LOC daemon-side (cloud gate adapter +
CLI verbs); ~1000-1400 LOC brnrd-side (FastAPI skeleton +
postgres schema + webhook handler + GH App JWT exchange +
comment-post logic + binding management).

### Slice 2 — Telegram bot adapter (fast-follow)

One to two weeks after slice 1 ships. Reuses the brnrd backend
entirely; adds one webhook endpoint, one platform-specific
response formatter, and the chat-binding flow.

**Steps:**

1. brnrd-side webhook receiver for Telegram Bot API updates;
   normalisation to the same event shape used for GH.
2. brnrd-side response forwarder: post to `chat_id` via
   Telegram `sendMessage` API.
3. `brr brnrd pair telegram` CLI flow (pairing-code path
   from the design).
4. Telegram-specific command grammar: `/start <code>` for
   pairing, `/connect <project>` for chat-to-project binding,
   `/project <name> <task>` per-message override, `@<name>
   <task>` terse form, `/projects` and `/status` for
   introspection.
5. Daemon-side: no new code — the cloud gate adapter handles TG
   events the same as GH events; the event shape is uniform.
6. Smoke test mirroring slice 1, plus routing-specific cases:
   one bot serving two of the user's projects; per-message
   override; sticky binding survives bot restart.

**Estimate.** Daemon-side ~0 new code (reuse from slice 1).
brnrd-side ~500-700 LOC (webhook + sendMessage + command
parser + binding management).

### Slice 3 — Permission-prompt API + gate-side integration

Wires the failover-compute permission gate (from
[`plan-failover-compute.md`](plan-failover-compute.md) Slice 2)
to actually surface via the gate. This slice depends on the
failover-compute Slice 2 landing first.

**Steps:**

1. `POST /v1/internal/prompts` endpoint that takes a prompt
   payload (event_id, est_cost, est_runtime, current-month
   usage) and selects the appropriate gate to post via.
2. Telegram-side prompt formatter: message text + two inline
   buttons (`Approve` / `Queue`); optional "Never ask under
   $X" inline button on first prompt.
3. GitHub-side prompt formatter: issue comment text describing
   the spawn + command syntax (`@brr-bot approve` /
   `@brr-bot queue`); follows the same comment thread.
4. `POST /v1/webhooks/prompts/{platform}/{prompt_id}/{outcome}`
   endpoints — both signed by the originating platform; on
   resolution, `PATCH /v1/internal/prompts/{prompt_id}` and
   trigger the spawn or queue per outcome.
5. Prompt timeout handling: 6h TTL; on expiry, auto-queue
   with a "permission timed out, event queued" notification
   via the gate.
6. Cost transparency in the prompt payload: include
   est_runtime, est_cost, current-month usage ("23/100 spawns
   used") prominently.

**Estimate.** ~400-600 LOC backend (prompt endpoints +
gate-specific formatters + callback handlers).

## What ships where

| Component | Lives at |
|-----------|----------|
| `src/brr/gates/cloud.py` — cloud gate adapter | `src/brr/` |
| `brr brnrd pair {telegram,github}` CLI verbs | `src/brr/cli/accounts.py` |
| `brr brnrd {list-projects,bind-chat,bind-repo}` CLI verbs | `src/brr/cli/accounts.py` |
| `src/brr/docs/managed-mode.md` (pairing + routing + prompt UX) | `src/brr/docs/` |
| brnrd backend (FastAPI + postgres + workers) | `src/brnrd/` |
| Multi-project routing tables + binding endpoints | `src/brnrd/` |
| Permission-prompt API + gate-side formatters | `src/brnrd/` |
| Upsun deployment template for brnrd backend | `deploy/upsun/` |
| Hosted bot operations (running `@brr_bot`, the brnrd GitHub App) | brnrd operator — not a code artifact |

Monorepo layout per
[`decision-monorepo-structure.md`](decision-monorepo-structure.md):
backend lives at `src/brnrd/` alongside the daemon at
`src/brr/`, sharing the kb and `pyproject.toml`.

## Out of scope

- Slack / Discord / GitLab adapters (same protocol, separate
  rollout — likely one to two months after launch each).
- The `fanout` multi-daemon routing policy (deferred per the
  design page).
- Web dashboard for managing daemons / bindings / pairings —
  lives in
  [`plan-brnrd-dashboard-mvp.md`](plan-brnrd-dashboard-mvp.md);
  CLI-first for this plan.
- Payment / billing automation (manual invoicing for launch
  tier per
  [`decision-pricing-shape.md`](decision-pricing-shape.md)).
- Failover spawn invocation (separated into
  [`plan-failover-compute.md`](plan-failover-compute.md); this
  plan only ships the prompt API surface).
- BYO compute path itself (subscriber-opt-in, parallel-shipped
  with managed Fly per
  [`decision-pricing-shape.md`](decision-pricing-shape.md) +
  [`plan-failover-compute.md`](plan-failover-compute.md);
  Surface A is gates-only, BYO lives on Surface B).

## Risks

- **Wire-format churn.** If
  [`design-brnrd-protocol.md`](design-brnrd-protocol.md)
  changes during the build, both sides need coordinated
  releases. Mitigation: lock the design with a `Status:
  accepted` banner before starting slice 1.
- **GitHub App approval delays.** Public GitHub Apps need a
  manual approval step for verified-creator badge; not blocking
  for launch but worth filing early.
- **Per-tenant blast radius.** A bug in brnrd's account
  scoping could leak events across accounts. Mitigation:
  query-level account context, integration tests per endpoint,
  audit logging from day one, data-minimization principle from
  `design-brnrd-protocol.md` baked into every endpoint.
- **Multi-project routing confusion.** Users with multiple
  projects could mis-bind chats and have events go to the
  wrong project. Mitigation: clear `/status` command to show
  the current binding; clear errors on mis-bind ("this chat is
  bound to <project-X>; switch with `/connect <project-Y>`");
  prefix override (`/project <name>`) as a safety valve.
- **Permission-prompt fatigue.** If the default mode is `ask`
  and users get prompted constantly, they'll either disable
  failover or jump to `auto-approve-always` without reading the
  cost. Mitigation: prompt copy frames usage clearly
  ("23/100 free spawns this month"); first-prompt "Never ask
  under $X" shortcut nudges toward `auto-approve-under-usd`.
- **Telegram message-ordering on /project prefix.** If a user
  sends `/project foo <task>` then a follow-up reply without
  prefix, the reply may go to the original sticky-bound
  project, not `foo`. Mitigation: document this behaviour
  explicitly; revisit if user feedback shows it's confusing.

## Read next

1. [`design-brnrd-protocol.md`](design-brnrd-protocol.md) —
   the contract this plan implements (Gates + Multi-project
   routing + Permission-prompt endpoints sections).
2. [`design-github-gate-vs-brnrd-app.md`](design-github-gate-vs-brnrd-app.md)
   — the OSS-vs-managed split for the GitHub side specifically:
   what code lives on which side, what's structurally reused
   (`paths`/`cache`/`parse` in `brr.gates.github`), and why both
   integrations survive launch instead of one obsoleting the other.
3. [`plan-failover-compute.md`](plan-failover-compute.md) — the
   sister plan covering managed compute on top of the same
   backend skeleton this plan stands up.
4. [`plan-brnrd-dashboard-mvp.md`](plan-brnrd-dashboard-mvp.md)
   — the sister plan for the dashboard view of all of this.
5. [`subject-managed-mode.md`](subject-managed-mode.md) — the
   strategic context (managed dispatcher + managed compute
   surfaces, Free + Subscribed tier shape, work-continuity frame).
6. [`decision-pricing-shape.md`](decision-pricing-shape.md) —
   the pricing model (platform subscription + metered credits
   for compute) that drives the per-tier caps the prompt API
   references.
7. [`decision-monorepo-structure.md`](decision-monorepo-structure.md)
   — where `src/brnrd/` lives.

## Lineage

- 2026-05-22 — drafted as part of the managed-mode KB shape
  rollout.
- 2026-05-22 — repointed at the protocol design (then
  `design-brr-run-protocol.md`, since renamed to
  `design-brnrd-protocol.md` on 2026-05-25 with the brnrd
  naming flip; both were renames from the original
  `design-managed-gates.md`) and cross-linked to the new
  `plan-failover-compute.md` sister plan after the
  work-continuity reframe expanded the design's scope.
- 2026-05-25 — added multi-project routing UX (chat / repo
  binding mechanics, `/connect`, `/project`, `@<name>` command
  grammar) and permission-prompt API + gate-side integration as
  Slice 3. Repointed at the reshaped protocol + pricing +
  monorepo decisions. brnrd backend repo replaced with
  `src/brnrd/` in the monorepo. Third reframe breadcrumb in
  [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §1.
- 2026-05-25 (pass 3) — repointed all references to
  `design-brnrd-protocol.md` after the brnrd naming was
  retained as the canonical hosted-product name (was briefly
  going to be `brr.run`; reverted on cost + brand-asset
  grounds). Fourth reframe breadcrumb in
  [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §1.
