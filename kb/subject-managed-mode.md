# Subject: managed mode — work continuity via brnrd

Hub for brr's "managed" tier: the work that lets adopters skip the
per-user bot setup, keeps their tasks moving when their laptop is
offline, and offers a coherent paid path without contradicting the
"everything is OSS self-hostable" stance. Companion to
[`subject-envs.md`](subject-envs.md) (the env protocol that the
managed-compute sandbox image is built around) and
[`subject-fleet-overlays.md`](subject-fleet-overlays.md) (the
broader fleet axes, of which managed mode is one strand).
Provenance lives in
[`notes-pondering-fleet.md`](notes-pondering-fleet.md) §1, §2, §4.

## The frame: work continuity, not laptop continuity

The brr pitch is "your laptop, accessible from anywhere." A user
buying that pitch is buying *work continuity* — they want their
ongoing work (deploys, log checks, quick code asks, light-bulb
fixes) to keep happening when they're not in front of their
laptop. The laptop is the default *home* for the work because
that's where their editor, dotfiles, conversation history, and
trust live. But "home" and "always-on" are different requirements:
when the laptop is briefly away, the work should still progress,
landing back home when home returns.

This frame matters because it eliminates a bad-shape answer
(deploy brr to an always-on third box, operate it as infra) and
clarifies a good-shape answer (brnrd is the always-on
dispatcher; ephemeral cloud sandboxes execute when home is
offline; results flow back to home via git). Earlier pondering had
the always-on box as the preferred BYO answer to laptop-down
dispatch; the 2026-05-22 reframe demoted it. See Daemon hosting
below.

## brnrd as the product

`brnrd` is the name of the hosted service / fleet manager that
sits beside brr. Two complementary angles on the same thing:

- **brnrd as a service** — hosted bots + dispatcher +
  credential vault + managed compute pool + dashboard. The
  thing a user signs up for to skip the bot-setup hassle and
  keep work flowing when the laptop sleeps.
- **brnrd as a fleet manager** — the dashboard view of a
  user's daemons, projects, bindings, AI credentials, audit
  log, cost ledger, and (eventually) cross-project agentic
  behaviours.

These were briefly considered as separate products (`brnrd` for
fleet management, `brr.run` for productizing brr as a service).
The 2026-05-25 reshape collapsed them: one product, one name.
brnrd wins as the name because (a) it's a real brand asset (the
`brr → brnrd → ⟍brr` reflection-palindrome reads as an
animated hero gif — distinctive, memorable, viral-friendly),
(b) it's a sibling to "brr" in the same naming family rather
than two unrelated brands, and (c) the domain economics are
meaningfully better (`brnrd.dev` ~$15/yr vs `brr.run` ~$120/yr
premium domain — material for a non-VC-funded project over the
long term). The dashboard is "the brnrd dashboard." Canonical
domain is **`brnrd.dev`** (HTTPS-enforced, signals the
developer audience, dev/AI-tooling registry). If a future
agentic-secretary layer earns its own brand, it can be named
then; pre-naming buys nothing.

## Current state

Managed mode is in **design**, not implementation. The dominant
constraint shaping it: the same paid tier needs to ship at launch
so early adopters see a clearly-articulated free / paid split
rather than a bait-and-switch after they've invested. Pricing
shape is captured in
[`decision-pricing-shape.md`](decision-pricing-shape.md); the
wire contract that ties everything together is in
[`design-brnrd-protocol.md`](design-brnrd-protocol.md); the
dashboard MVP is in
[`plan-brnrd-dashboard-mvp.md`](plan-brnrd-dashboard-mvp.md).

**Two surfaces at launch, one designed-deferred:**

| Surface | What it is | Pricing | Adoption pain it removes |
|---------|-----------|---------|--------------------------|
| **A. Managed dispatcher** — hosted bots + multi-project routing + permission prompts + audit | Hosted GH App + Telegram bot routing events to a per-account brnrd inbox, multi-project routing on top of one bot per platform, permission prompts before failover spawns, audit log | Free tier (rate caps, 1000 events/month, 100 failover spawns/month) | Per-user GH App / BotFather setup — currently the longest friction in adoption — AND "my laptop has to be up" — together, in one flow |
| **B. Managed compute** — failover spawn on brnrd-owned cloud | Same dispatcher; when the user's daemon is offline and the user opts in, brnrd spawns a per-task ephemeral sandbox on its own Fly Machines pool, decrypts the user's AI credentials into the sandbox, runs the task, returns the response via the gate | Usage-based, pass-through with margin (free tier covers 100 spawns/month per above) | "I want managed continuity without a credit card surprise" — paid only when failover actually fires AND the user approved |
| **C. BYO compute** (deferred) | Same failover spawn but in the user's cloud account using a user-stored cloud-platform token | Free dispatcher tier covers it | Out of scope at launch per `decision-pricing-shape.md`; wire shape preserved in the design page for clean add-back when usage justifies |

Surface A is the *entry point* (free, broad reach). Surface B is
the *paid convenience* (same dispatcher, brnrd's cloud account,
usage-billed with margin). Surface C is deferred — the protocol
supports it, but implementation surface area was disproportionate
to the ~5% of users who'd value it at launch.

Daemon-side cloud-runner adapters (a laptop daemon fans out to
*the user's* cloud via a `brr-env-*` plugin) remain independent
of managed mode entirely. Those are user-driven plugin work,
shipped per
[`research-cloud-runner-patterns.md`](research-cloud-runner-patterns.md)
on their own clock; they don't need brnrd.

## Data minimization — load-bearing for the trust story

brnrd is intentionally **thin**: a dispatcher + a credential
vault + an accounting ledger + a metadata-only conversation
graph. User content (prompts, code, responses, conversation
contents, repo state) lives on the daemon side or on the
platforms themselves; brnrd holds the table of contents, not
the chapters. Concretely:

- Event content is dropped from brnrd once dispatched. Metadata
  retained for audit (who/when/which platform/which project/
  outcome).
- Response bodies pass through the gate, are not stored.
- Conversation contents rendered live: when the daemon is
  online, the dashboard proxies to it; when offline, contents
  are fetched on demand from the platform APIs (TG / GH / Slack /
  Discord) and from git remotes. Cross-gate continuity is
  preserved via a metadata-only graph (`event_id ↔
  conversation_id ↔ branch_name`, no body, 30-day TTL) that
  brnrd holds. See "Conversation context" below.
- AI credentials encrypted at rest with per-account envelope
  keys; root key in a KMS managed separately from the database.
- Audit log is metadata-only.

The promise: "brnrd doesn't have your code." This shapes user
trust, bounds breach blast radius, and matches the
OSS-self-hostable framing (we hold less; users hold their data).
Full principle, "what we DO hold" table (with named scopes and
TTLs), and per-endpoint annotations in
[`design-brnrd-protocol.md`](design-brnrd-protocol.md) →
"Data minimization".

## Conversation context — gate-replay + git + metadata graph

When the daemon is offline and a failover spawn fires (or when
the dashboard wants to render a conversation without a live
daemon), brnrd assembles enough context for the runner /
dashboard to be useful **without persisting conversation
contents**. Three on-demand sources:

1. **Originating event payload** — already in dispatch memory
   (issue body, PR title + first comment, reply-to chain).
2. **Gate-side history fetch** — recent messages / comments
   from the platform itself, via the bot/app token already in
   hand (`conversations.history` on Slack, issue/PR comments on
   GH, channel messages on Discord). The platform IS the
   history store; we don't double-up.
3. **Git remote replay** — `git log -n 50 <branch>` + recent
   commit bodies + `kb/log.md` tail if present, fetched with
   the per-spawn GH App installation token.

For **cross-gate** continuity (the same conversation spans TG
and a GH PR), brnrd keeps a small **metadata graph** —
`event_id ↔ conversation_id ↔ branch_name`, no body, 30-day TTL.
The conversation_id is sourced from a `Brnrd-Conversation-Id`
git commit trailer the daemon writes on every commit (see
[`plan-conversation-id-propagation.md`](plan-conversation-id-propagation.md))
and from the daemon's response POST; brnrd can re-derive it
from any branch by walking git log. The metadata index is a
cache, not a source of truth.

**One named concession**: Telegram's Bot API doesn't expose
retroactive `getChatHistory`. To make failover and dashboard
rendering work on TG without forcing the user to push history
into their own infra, brnrd rolls a per-chat ring buffer
(last 50 messages × 72h TTL, encrypted at rest, per-account
audit log on every read, dropped on `/disconnect`). Slack /
Discord don't need this — their APIs expose history natively.

Full machinery and the per-endpoint annotations in
[`design-brnrd-protocol.md`](design-brnrd-protocol.md) →
"Conversation context for failover and dashboard". The
daemon-side enabler is in
[`plan-conversation-id-propagation.md`](plan-conversation-id-propagation.md).

## How the dispatcher works

brnrd is the always-on thing. Every event flows through one
dispatcher decision:

```
event arrives at brnrd (TG message / GH @brr comment / etc.)
         │
         ▼
┌──────────────────────────┐
│  resolve project_id from │
│  (chat binding or repo)  │
└──────┬───────────────────┘
       ▼
┌──────────────────────────┐
│  is a daemon for this    │
│  project online?         │
└──────┬───────────────┬───┘
       │ yes           │ no
       ▼               ▼
  enqueue        ┌────────────────────────┐
  for daemon     │  failover enabled,     │
  to drain       │  required AI credentials│
  (existing      │  present, under caps?  │
   gate path)    └─┬──────────────────┬───┘
                   │ yes              │ no
                   ▼                  ▼
            policy mode?         queue + notify
              ask: prompt via    user via gate
                   gate, await
              auto: spawn now
                   │
                   ▼
            spawn per-task sandbox
            in brnrd's Fly pool
            with AI creds decrypted
            and per-spawn GH App
            token. Sandbox runs the
            runner, pushes branch,
            POSTs response, tears
            itself down.
```

Same code path serves the spawn step whether it was triggered by
auto-approve or by an explicit "Approve" tap on a prompt. The
dispatcher's job is to walk the policy tree and emit one of four
outcomes (enqueue, spawn, prompt, error). See
[`design-brnrd-protocol.md`](design-brnrd-protocol.md) →
"Failover dispatch" for the precise decision tree and
[`research-cloud-runner-patterns.md`](research-cloud-runner-patterns.md)
for the cross-adapter patterns the server-side spawn uses.

## Surface A — managed dispatcher (gates + routing + prompts)

Today's gates are BYO: each adopter creates a Telegram bot via
@BotFather or registers a GitHub App, copies the token / app
secret into `.brr/config`, and the daemon polls or receives
webhooks directly. Setup is the longest single friction in
adoption (more so for GitHub than Telegram).

Managed gates collapse this to one CLI verb plus a bot
interaction:

1. User runs `brr accounts pair telegram` (or `... github`),
   authenticates, gets a pairing code or install URL.
2. User `/start <code>` to @brr_bot on Telegram, or installs the
   brnrd GitHub App on selected repos.
3. brnrd's hosted bot receives events and routes them to the
   user's per-account inbox-as-service, scoped by project.
4. The user's daemon long-polls brnrd and drains the inbox the
   same way it drains `.brr/inbox/` today.

### Multi-project routing

A single hosted bot per platform serves all of a user's projects.
Two resolution mechanisms:

- **GitHub** has it naturally: each repo lives under one
  installation; one repo → one project binding.
- **Telegram / Slack / Discord** need a per-chat sticky binding
  plus an in-message override:
  - `/connect <project-name>` binds the current chat to a
    project.
  - `/project <name> <task>` or `@<name> <task>` routes a single
    message elsewhere without changing the binding.
  - `/projects` lists projects + their daemon status + their
    bound chats.
  - `/status` shows the current chat's project, daemon state,
    queue depth, recent activity.

Wire protocol and command grammar in
[`design-brnrd-protocol.md`](design-brnrd-protocol.md) →
"Multi-project routing".

### Permission prompts

For users who'd rather not auto-spawn at every event, the
dispatcher posts a permission prompt via the gate before firing
a managed-compute spawn. The prompt carries:

- estimated runtime (per-machine-size empiric)
- estimated cost (per-machine per-platform rate table)
- current free-tier usage ("23/100 spawns used this month")
- two action buttons: **Approve** / **Queue**
- optional third on first prompt: **Never ask again under $X**
  (raises the auto-approve threshold)

The prompt is the cost-transparency surface. Users who hit
"Approve" know what it costs; users who hit "Queue" know what
they're deferring. Mode defaults to `ask`; one cap raise or one
"never ask" tap migrates to the auto-approve path without
ceremony.

The launch sequencing — GH App adapter first (largest pain
relief), TG bot adapter as fast-follow on the same backend, then
permission-prompt API and routing once both gates are live — is
in [`plan-managed-gates-launch.md`](plan-managed-gates-launch.md).

## Surface B — managed compute (failover spawn)

Per-task spawn when the user's daemon is offline (or — as a
v-next opt-in — when it's overloaded). brnrd holds the user's
AI credentials in the vault, holds its own pool-control token for
the managed Fly app, and runs the spawn flow server-side using
the same env-adapter shape a daemon would use locally.

### AI-credential vault — two shapes, one endpoint

The vault stores AI-runner credentials encrypted, in two payload
shapes on the same `POST /v1/accounts/ai-credentials` endpoint:

- **API key**: `brr accounts add-credential anthropic --key
  sk-ant-...`. The default for most users.
- **Credential directory tarball**: `brr accounts add-credential
  anthropic --dir ~/.claude`. The CLI tars the directory, base64-
  encodes, uploads; the sandbox bootstrap script extracts it back
  into `$HOME/.claude/` at spawn time. Preserves subscription-
  auth flows (Claude Pro, Codex Plus, Gemini OAuth) for users
  who'd rather not provision API keys.

Both shapes flow into the same encrypted store; only the spawn
bootstrap branches on shape. The local docker env's existing
"either an API key or a mounted credential dir" UX is preserved
in the cloud, which matters because subscription auth is real
ergonomic value for users already paying for it.

### Shape from the user's perspective

```
brr accounts add-credential anthropic --key sk-ant-...
brr accounts failover --enable \
  --mode ask \
  --monthly-cap 100 \
  --monthly-cost-cap-usd 25
```

After that, daemon-down events trigger a permission prompt via
the originating gate (TG inline buttons, GH comment commands);
the user taps Approve, the sandbox spawns, the task runs, the
result lands via the gate, the branch is on the remote when the
laptop wakes. The full mechanics, including the dispatcher tree
and the spawn flow, are in
[`design-brnrd-protocol.md`](design-brnrd-protocol.md) →
"Failover dispatch"; sequencing is in
[`plan-failover-compute.md`](plan-failover-compute.md).

### Cost transparency

Every spawn is metered (start time, end time, machine size, est
cost, actual cost) and rolled into `brr accounts audit` / the
dashboard. Users see exactly what each task cost and how close
they are to their cap. The pricing rate published in
[`decision-pricing-shape.md`](decision-pricing-shape.md) carries
a small margin over wholesale Fly Machines pricing — sustainable,
transparent, no surprises.

## Surface C — BYO compute (designed, deferred)

The earlier draft of this hub had BYO compute as a launch
surface: the user stores their own Fly / Modal / Daytona / etc.
token on brnrd; brnrd spawns into the user's cloud account
using it. Dropped from launch on 2026-05-25 because the
implementation cost was disproportionate to the launch user
value:

- ~30% more backend surface area (per-platform credential
  storage UI, scope validation, per-platform onboarding docs,
  per-platform failure modes, dispatcher branching on platform
  selection).
- ~5% of launch users care (the cloud-control crowd); the other
  95% would rather paste an API key and have brnrd handle the
  rest.
- Maintenance load is unbounded — each platform we support means
  partial-support-matrix for someone else's cloud.

Easy add-back when usage justifies: the wire protocol supports
BYO additively (new `/v1/accounts/cloud-credentials` endpoint
family parallel to AI credentials; one new field in the failover
policy; one branch in the dispatcher's spawn step). Adapter code
is identical (same plugin, different token source). See
[`design-brnrd-protocol.md`](design-brnrd-protocol.md) →
"BYO compute — designed, deferred" for the preserved sketch and
[`decision-pricing-shape.md`](decision-pricing-shape.md) for the
add-back rationale.

## Dashboard

The user-facing layer on top of brnrd. Minimal at launch (seven
views), HTMX-first to keep build/maintenance cost down,
upgradable to SPA later if interactivity demands it.

Seven views:

1. **Accounts / projects** — list, create, delete; per-project
   daemon status, last activity.
2. **Project detail** — bindings (chats, repos), daemons (online
   status, last seen, name), recent events.
3. **Task / event detail** — per-event timeline (received,
   dispatched, executed, responded); for executed-on-managed-
   compute, the spawn record (cost, duration, exit code); link to
   the resulting branch.
4. **Conversation view** — proxied from the daemon when online;
   per-project chat-style scroll of events + responses.
5. **AI credentials** — list, add, remove; per-credential
   metadata (provider, shape, created, last used).
6. **Failover policy** — enable / disable, mode, caps, current
   usage; cost chart for the month.
7. **Audit log** — paginated, filterable by project / platform /
   outcome / spend window.

Full breakdown in
[`plan-brnrd-dashboard-mvp.md`](plan-brnrd-dashboard-mvp.md).

The dashboard is a *consumer* of the same REST endpoints the
daemon-side cloud-gate adapter consumes — no separate API surface
to maintain.

## Daemon hosting

The "where does the daemon live" question is orthogonal to the
managed surfaces. Default is the user's laptop. For the *common*
laptop-down case, the brnrd dispatcher + managed compute
(Surface B above) is the answer — not a separately-operated
always-on host.

For users who genuinely want a cloud-first home for the daemon
(security policy, no laptop at all, persistent home server vibe),
deployment templates are still worth shipping but their role is
*niche, not default*:

| Target | Setup | Notes |
|--------|-------|-------|
| Free-tier always-on cloud apps (Fly app, Render free worker, Railway) | `flyctl launch` from template / one-click deploy | "Deploy brr in 30 seconds" — for cloud-first users who don't want a laptop daemon at all |
| Read-only PaaS templates (Heroku, Upsun, Render Blueprint, Railway, App Platform) | One-click deploy button | Broadest developer-audience reach; per-task work must fan out to cloud-runner envs (no `docker` env without docker-in-docker) |
| Cheap always-on VPS (Hetzner CX11 €3.79/mo, Oracle Free Tier ARM, low-end OVH / DO / Vultr) | `docker compose up -d brr` + systemd unit | Most flexible (full `docker` env); cheapest at scale for power users running many concurrent tasks |
| Laptop / home server | `brr install-service` for macOS + Linux | Existing default; install-service verb removes the "go add it to your startup scripts" friction |

The deployment-templates work has its own plan at
[`plan-daemon-deployment-templates.md`](plan-daemon-deployment-templates.md);
the install-service verb is a separate future plan
(`plan-install-service.md`, not yet drafted; tracked in
[`notes-pondering-fleet.md`](notes-pondering-fleet.md) §7).

**Why deployment templates demoted.** Earlier framing positioned
the always-on host as the *preferred* answer to laptop-down
dispatch, with brnrd-spawns-sandboxes-on-your-behalf as a
v-next convenience. The 2026-05-22 reframe inverted this: the
always-on host makes the user operate a third thing (laptop +
cloud + box) for a 30%-utilisation use case at 100% cost. The
dispatcher-spawn path uses an already-justified component
(brnrd, which exists for gates anyway) and matches the work
continuity frame — cloud sandboxes appear and vanish per task,
results flow back home. The templates remain useful for the
niche "cloud-first by choice" case; they stop being the answer
for the common case.

## Where the code lives

Per [`decision-monorepo-structure.md`](decision-monorepo-structure.md):

- `src/brr/` — daemon core (today)
- `src/brnrd/` — brnrd backend (FastAPI + workers + sandbox
  image build)
- `src/brnrd_web/` — dashboard (HTMX templates first; SPA later
  if needed)
- `deploy/upsun/` — Upsun deployment template for the brnrd
  backend
- `deploy/fly-daemon/`, `deploy/upsun-daemon/` etc. — daemon-
  hosting templates

Plugin packages (`brr-env-fly-machines`, `brr-env-codespaces`,
future BYO cloud adapters when they come back) live in their own
repos as separately-installable pip packages.

## Boundary

In scope for managed-mode launch:

- Surface A (managed dispatcher) — the cloud-gate adapter on the
  daemon side, the brnrd inbox-as-service API, GH App + TG bot
  webhooks, multi-project routing, permission-prompt API, audit
  log. Free tier.
- Surface B (managed compute) — AI-credential vault, dispatcher
  decision tree, brnrd-owned Fly Machines pool, sandbox image,
  per-spawn task-key + GH App installation token, accounting +
  CSV exporter for manual invoicing. Paid usage-based.
- Dashboard MVP — seven views, HTMX-first.
- `deploy/` templates folder and the `brr/daemon` Docker image
  variant (demoted to launch-nice-to-have, cloud-first users
  only).
- `brr install-service` on macOS + Linux.
- Data minimization principle baked into every endpoint.
- Monorepo restructuring (`src/brnrd/`, `src/brnrd_web/`
  alongside `src/brr/`).

Out of scope, explicitly:

- **BYO compute** (Surface C) — designed, deferred per the
  rationale above; protocol supports clean add-back.
- **Agentic secretary** — the cross-project proactive layer
  (e.g. "schedule a deploy review every Monday", "notice when
  CI breaks across multiple projects and propose a fix"). Tracked
  in [`decision-connectors-layering.md`](decision-connectors-layering.md)
  for the connectors layering question. Not at launch.
- **Scheduler-shaped managed compute.** Surface B is per-task
  spawn-and-teardown via the same path as a daemon's local env;
  just with a different token. No scheduler needed.
- **Server-side spawn for online daemons (load-shedding).**
  Possibly worth doing as a convenience; explicitly deferred
  until usage shows whether it matters.
- **Windows daemon supervision.** Defer per
  [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §4.
- **Stripe / Paddle integration.** Manual invoicing at launch
  (CSV exporter from the accounting table); add a payments
  integration when monthly volume justifies the integration
  cost.

## Read next

1. [`decision-pricing-shape.md`](decision-pricing-shape.md) for
   the pricing model that ties the surfaces together (free
   dispatcher + paid managed compute; per-seat team tier later;
   self-hosted always free).
2. [`design-brnrd-protocol.md`](design-brnrd-protocol.md) for
   the wire format the daemon-side adapter and the brnrd
   service both build against. Covers gates + failover dispatch
   + AI-credential vault + multi-project routing + permission
   prompts + data minimization in one page.
3. [`plan-managed-gates-launch.md`](plan-managed-gates-launch.md)
   for the Surface A launch sequencing (GH-then-TG + routing UX
   + permission-prompt integration).
4. [`plan-failover-compute.md`](plan-failover-compute.md) for
   the Surface B launch sequencing (AI-credential vault,
   dispatcher decision tree, brnrd-owned Fly pool, permission
   gate API, Upsun backend deployment).
5. [`plan-brnrd-dashboard-mvp.md`](plan-brnrd-dashboard-mvp.md)
   for the dashboard launch sequencing.
6. [`research-cloud-runner-patterns.md`](research-cloud-runner-patterns.md)
   for the cross-adapter patterns and per-platform briefs
   underpinning the managed-compute sandbox + daemon-side
   plugins (independent of managed mode, useful for power users).
7. [`decision-connectors-layering.md`](decision-connectors-layering.md)
   for the gates-vs-connectors split that the agentic-mode
   upgrade path depends on.
8. [`decision-monorepo-structure.md`](decision-monorepo-structure.md)
   for where the brnrd backend, dashboard, and plugins live.
9. [`plan-daemon-deployment-templates.md`](plan-daemon-deployment-templates.md)
   for the `deploy/` folder and the `brr/daemon` Docker image
   variant (demoted to launch-nice-to-have; useful for cloud-first
   users).
10. [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §1,
    §2, §4 for the original pondering provenance and the
    2026-05-22 / 2026-05-25 reframe breadcrumbs that drove the
    current shape.
