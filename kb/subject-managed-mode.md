# Subject: managed mode — work continuity via brnrd

**Status: accepted 2026-05-26** (locked in PR #40 MR review;
hub page for the managed-mode page family — fluid as the
linked design / plan pages evolve during implementation).

Hub for brr's "managed" tier: the work that lets adopters skip the
per-user bot setup, keeps their tasks moving when their laptop is
offline, and offers a coherent paid path without contradicting the
"everything is OSS self-hostable" stance. For the GitHub-specific
OSS-vs-managed boundary (which code each side owns, what's reused via
`paths`/`cache`/`parse`), see
[`design-github-gate-vs-brnrd-app.md`](design-github-gate-vs-brnrd-app.md). Companion to
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

**Two surfaces at launch. Surface B has a subscriber-only
BYO sub-option that parallel-ships with the managed default.**
Pricing details (tier caps, included compute, signup bonus,
project-cap unlock, supporter cohort) live in
[`decision-pricing-shape.md`](decision-pricing-shape.md) §
"Decision" — this hub describes the surfaces, not the prices.

| Surface | What it is | Adoption pain it removes |
|---------|-----------|--------------------------|
| **A. Managed dispatcher** — hosted bots + multi-project routing + permission prompts + audit | Hosted GH App + Telegram bot routing events to a per-account brnrd inbox, multi-project routing on top of one bot per platform, permission prompts before failover spawns, audit log | Per-user GH App / BotFather setup — currently the longest friction in adoption — AND "my laptop has to be up" — together, in one flow |
| **B. Compute** — failover spawn, two sub-options for subscribers | When the user's daemon is offline and the user opts in, brnrd dispatches a per-task ephemeral sandbox. **Managed (default)**: spawns on brnrd-owned Fly Machines pool, decrypts user's AI credentials into the sandbox, runs the task, returns response via the gate. **BYO (subscriber opt-in)**: subscriber stores a cloud-platform credential in the vault (`brr brnrd creds add cloud-platform --provider fly --token …`); the same dispatcher invokes the same env class with the subscriber's token; spawn runs in subscriber's own cloud account; user pays the cloud provider directly. Same env class, two callers per the "Caller axis" pattern in [`research-cloud-envs.md`](research-cloud-envs.md). | "I want managed continuity without a credit card surprise" — subscribers get generous included compute on managed; "I already have a Fly account and don't want compute markup" — subscribers BYO and the wallet is bypassed entirely |

Surface A is the entry point — Free is genuinely usable for
hobbyists, and the subscription unlocks the natural "I'm
using brr seriously" headroom. Surface B is the compute leg,
with the subscription including generous managed compute
(300 credits/month on the house) AND optional BYO for
subscribers who prefer to keep their cloud spend on accounts
they already own. Free stays managed-only by design — the
subscription is the BYO gate (BYO is structurally a
cost-saving feature; if you want to save on compute,
subscribe; if you're on Free, you're trying the platform).
Full "Compute: managed vs BYO" discussion lives in
[`decision-pricing-shape.md`](decision-pricing-shape.md).
Each new cloud env we add managed support for (Modal /
Daytona / etc.) ships BYO for that env in the same release.

**Self-hosted brnrd** stays always-free with full feature
parity. The hosted subscription pays for hosted-service
convenience (infrastructure, multi-tenant scale, email support);
the brr / brnrd OSS itself is unchanged whether you self-host
or not.

Daemon-side cloud envs (a laptop daemon fans out to *the user's*
cloud via a first-party env extra like `brr[fly]` or a
third-party env registered via the `brr.envs` entry point per
[`design-env-interface.md`](design-env-interface.md)) remain
independent of managed mode entirely. Those are env work,
shipped per
[`research-cloud-envs.md`](research-cloud-envs.md)
on their own clock; they don't need brnrd.

### BYO cloud env vs managed compute — they coexist

These two routes look similar (both run tasks on cloud
hardware) but answer different questions and never compete.
Spelling the distinction out once so the docs are unambiguous:

| Concern | BYO cloud env (daemon-side) | Managed compute (brnrd-side, Surface B) |
|---------|---------------------------|----------------------------------------|
| **Who's the caller?** | Your local daemon | brnrd's dispatcher |
| **Whose cloud account?** | Yours (your `FLY_API_TOKEN`) | brnrd's (operator-controlled at the deployment level — hosted `brnrd.dev` uses brnrd's Fly Machines pool; self-hosted brnrd operators wire whatever cloud envs they want) |
| **When does it fire?** | Every task the daemon picks up (or only tasks marked `env: fly_machines` per the schema) | Only when your daemon is offline AND brnrd is connected AND policy allows |
| **Who picks the cloud platform?** | You, by choosing which `brr[<extra>]` env extra to install | brnrd operator, at deployment time. Users don't get a runtime choice in this path. |
| **Is brnrd involved?** | No. Daemon talks directly to your cloud provider's API. | Yes. brnrd holds the AI creds + cloud-provider token (its own) and orchestrates the spawn. |
| **Need brnrd to use it?** | No. Available with or without brnrd connected. | Yes. Requires `brr brnrd connect` + AI creds in the vault + failover enabled. |
| **What does the user pay?** | Their cloud bill directly | brnrd's credit wallet (see [`design-billing.md`](design-billing.md)) |

**Coexistence**: both can be configured simultaneously. Daemon
online → BYO env handles every task. Daemon offline → brnrd's
managed compute covers the gap (if enabled). No conflict, no
runtime arbitration needed — the trigger conditions are
mutually exclusive.

**The env class is the same.** When BYO cloud env support lands
for Fly Machines via `pip install brr[fly]`, the `src/brr/envs/
fly_machines/` module it ships is the **same module** brnrd
imports server-side for managed-compute spawns (per
[`research-cloud-envs.md`](research-cloud-envs.md) → "cloud
envs are envs" and
[`design-env-interface.md`](design-env-interface.md) →
"brnrd server-side caller"). One env implementation; two
callers; two token sources. No protocol fork.

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
- Credentials encrypted at rest with per-account envelope
  keys; root key in a KMS managed separately from the database.
  Same scheme covers AI-runner credentials AND
  docker-registry credentials (the vault is generalised; one
  store, two domains).
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
   the per-spawn GH App installation token. This is just an
   approximation. The actual context should be in sync with
   the local daemon.  

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
[`research-cloud-envs.md`](research-cloud-envs.md)
for the cross-adapter patterns the server-side spawn uses.

## Surface A — managed dispatcher (gates + routing + prompts)

Today's gates are BYO: each adopter creates a Telegram bot via
@BotFather or registers a GitHub App, copies the token / app
secret into `.brr/config`, and the daemon polls or receives
webhooks directly. Setup is the longest single friction in
adoption (more so for GitHub than Telegram).

Managed gates collapse this to one CLI verb plus a bot
interaction:

1. User runs `brr brnrd connect`, gets a pairing flow / auth
   URL (CLI shape per
   [`decision-cli-shape.md`](decision-cli-shape.md)).
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
credentials in the vault (AI runner + docker-registry, all
encrypted), holds its own pool-control token for the managed
Fly app, and runs the spawn flow server-side using the **same
env class** a daemon would use locally — cloud runners are envs
(per [`research-cloud-envs.md`](research-cloud-envs.md) and
[`design-env-interface.md`](design-env-interface.md) →
"brnrd server-side caller"), so the `src/brr/envs/fly_machines/`
module the daemon would `pip install brr[fly]` to use is the
same module brnrd imports. The brnrd backend just does a
daemon-equivalent bootstrap (clone repo with the per-spawn GH
App token, read `brr.toml` for project preferences, layer in
account-scope settings, `docker login` if the image is private,
materialise AI creds, construct a `RunContext`) before invoking
the env.

### Credential vault — one store, two domains

The vault is generalised: it holds two kinds of credentials with
shared encryption / audit / revoke, on the same
`POST /v1/accounts/credentials` endpoint:

1. **AI-runner credentials** (Anthropic / OpenAI / Google /
   GitHub), in two payload shapes:
   - **API key**: `brr brnrd creds add anthropic --key
     sk-ant-...`. Default for most users.
   - **Credential directory tarball**: `brr brnrd creds add
     anthropic --dir ~/.claude`. The CLI tars the directory,
     base64-encodes, uploads; the sandbox bootstrap script
     extracts it back into `$HOME/.claude/` at spawn time.
     Preserves subscription-auth flows (Claude Pro, Codex Plus,
     Gemini OAuth) for users who'd rather not provision API
     keys.
2. **Docker-registry credentials** for private images
   (ghcr.io / docker.io / etc.):
   - `brr brnrd creds add docker-registry --registry
     ghcr.io --username myorg --token <ghcr-pat>`.
   - At spawn time, brnrd extracts the registry host from the
     project's `brr.toml` `docker.image` declaration, looks up
     a matching credential, and runs `docker login` before
     `docker pull`. Public images skip the lookup entirely.

All credentials flow into the same encrypted store with a `kind`
discriminator; only the spawn bootstrap branches on shape. The
local docker env's existing "API key or mounted credential dir"
UX is preserved in the cloud, and private images work the same
in cloud as on a local daemon. AI cred material is cleared from
sandbox memory after hand-off; registry credentials live only
in the build worker's `~/.docker/config.json` for the spawn's
duration (the sandbox itself never sees them).

### Shape from the user's perspective

```
brr brnrd connect                     # pair to brnrd.dev (or self-hosted URL)
brr brnrd subscribe                   # $5/mo subscription via Stripe Checkout
                                      # (or stay Free; metered top-ups still work)
brr brnrd topup 20                    # $20 → 2000 compute credits (optional)
brr brnrd creds add anthropic --key sk-ant-...
brr brnrd creds add docker-registry --registry ghcr.io \
  --username myorg --token <ghcr-pat>   # only if you use a private image
brr brnrd policy set --enable \
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
[`plan-failover-compute.md`](plan-failover-compute.md); the CLI
verb taxonomy is in
[`decision-cli-shape.md`](decision-cli-shape.md).

### Billing — subscription + credit wallet

Two billing legs, each matched to its cost shape:

- **Subscription** ($5/month or $50/year via Stripe recurring)
  covers the platform — bigger project headroom (25 vs 3 on
  Free, unlimited after $10 cumulative top-ups), full
  dashboard, 10K events/month, 300 spawn-credits/month
  included, 90-day audit retention, email support.
  Cancel-anytime via the Stripe Customer Portal (`brr brnrd
  subscription portal`).
- **Credit wallet** (1 credit = $0.01) covers compute over the
  included subscriber grant (or the Free tier's 5 monthly
  credits). One-shot Stripe Checkout top-ups; no card-on-file
  unless the user opts into auto-topup. Paid credits never
  expire; refunds on unused paid credits within 30 days,
  pro-rata.

Spawns debit at finalize from the appropriate sub-bucket
(grant first, then purchased credits). The Free signup bonus
draws first for Free users; the subscriber monthly grant
draws first for
subscribers; paid credits draw only after the included grant
is exhausted.

This shape matches each leg's cost (sub for fixed platform
cost, metered for variable compute cost), aligns with the
data-minimization pitch (no recurring identity-mapping for
wallet top-ups), and gives subscribers predictable monthly
cost while preserving pay-only-for-what-you-use for casual /
power compute users.

Full mechanics — subscription flow, wallet top-up flow,
debit-at-finalize, zero-balance UX (event enqueued + gate
notify, not dropped), refund policy (per-leg), auto-topup,
Stripe + HugiMuni SAS + Qonto integration, audit-log entries —
in [`design-billing.md`](design-billing.md).

### Cost transparency

Every spawn is metered (start time, end time, machine size, est
cost, actual cost in credits) and rolled into `brr brnrd audit`
/ the dashboard. Users see exactly what each task cost and how
close they are to their monthly free-credit grant + their
remaining paid balance. The pricing rate published in
[`decision-pricing-shape.md`](decision-pricing-shape.md) carries
a small margin over wholesale Fly Machines pricing —
sustainable, transparent, no surprises.

## BYO compute (subscriber sub-option of Surface B)

**Reframed on 2026-05-26**: BYO compute is no longer a
separate deferred surface. It's a **subscriber-opt-in
sub-option of Surface B that parallel-ships with managed
support for each cloud env**. At launch only Fly Machines
ships managed; therefore only BYO Fly ships at launch. Each
subsequent managed cloud (Modal / Daytona / Codespaces / …)
unlocks BYO for that env in the same release.

The pre-2026-05-26 "Surface C — designed, deferred" framing
is preserved below for context — it explained the
implementation cost trade-off that drove the original
deferral. The current framing recognises that BYO on top of
already-shipping managed support is a small incremental
(same env class, one new credential `kind`, one dispatcher
branch on credential presence), so the cost-vs-value math
flipped once Fly's managed path was anyway on the launch
critical path.

### Subscriber-only by design

The vault gate on `cloud-platform` credentials sits on
`subscription.tier == "subscribed"`. Free accounts can't store
cloud-platform creds; their compute path is managed-only. Three
reasons (mirrored from `decision-pricing-shape.md`):

1. Free's whole purpose is "try this without setup friction" —
   adding cloud-token onboarding defeats that.
2. BYO is structurally a cost-saving feature; subscribing is
   the cost-saving move. Free + BYO would create a strict-
   better-than-paid path and undercut our own revenue.
3. The subscription is the natural per-paying-customer gate;
   gating BYO behind it gives one clean code path.

### What ships at launch

- **Managed Fly Machines** (default for Free + Subscribed) +
  **BYO Fly Machines** (subscriber-only, parallel-shipped) —
  same `EnvBackend.start(token=...)` invocation with different
  `token`. Implementation lives in
  [`design-brnrd-protocol.md`](design-brnrd-protocol.md)
  § "BYO compute" + the BYO dispatch path pseudo-code in the
  credential-vault section.
- **Audit log distinguishes**: BYO spawns emit `spawn_byo` (no
  wallet debit; estimated cloud cost surfaced on the dashboard
  for the user's visibility). Managed spawns emit `debit_spawn`
  with the wallet sub-bucket per `design-billing.md`.
- **Wallet bypass for BYO**: subscribers' BYO spawns never
  touch the credit wallet. Mixed-mode subscribers (BYO Fly,
  managed Modal once shipped) hit the wallet only on managed
  spawns; the included `subscriber_monthly` grant applies to
  managed spawns only.

### Pre-2026-05-26 deferral rationale (preserved for context)

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

The 2026-05-26 lock-in resolved this by tying BYO availability
1:1 to managed support per cloud — at launch only Fly is
managed, so only Fly is BYO; the cost stays small and bounded.
Free stays managed-only on purpose; the subscriber gate handles
the "who gets BYO" question without per-platform onboarding
docs needing to be Free-friendly.

## Dashboard

The user-facing layer on top of brnrd. Minimal at launch
(eight views — accounts/projects, project detail, task /
event detail, conversation, AI credentials, failover policy,
audit log, allowance + usage), HTMX-first to keep
build/maintenance cost down, upgradable to SPA later if
interactivity demands it.

Allowance + usage is the anchor surface for the dashboard
nudge UX (allowance gauges + banner triggers when crossing
80% / 100% / out-of-bonus / out-of-credit thresholds —
honest nudges, no modals or dark patterns).

Full view spec + implementation slices in
[`plan-brnrd-dashboard-mvp.md`](plan-brnrd-dashboard-mvp.md);
canonical nudge-UX policy (triggers, copy, anti-patterns) in
[`decision-pricing-shape.md`](decision-pricing-shape.md) §
"Dashboard nudges + transparency".

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
| Laptop / home server | `brr daemon install` (per-user systemd unit on Linux, LaunchAgent on macOS) | Existing default; the `install` verb removes the "go add it to your startup scripts" friction without sudo, without re-implementing supervision |

The cloud-host deployment-templates work has its own plan at
[`plan-daemon-deployment-templates.md`](plan-daemon-deployment-templates.md);
the laptop-side cross-platform daemoning (macOS + Linux native
service install via `brr daemon install`) has its own plan at
[`plan-laptop-daemoning.md`](plan-laptop-daemoning.md), tracked
at [issue #29](https://github.com/Gurio/brr/issues/29).

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
  hosting templates (cloud-host case)
- `src/brr/daemon_install/` — laptop-side cross-platform
  service-unit writer (`linux.py` for systemd user units,
  `macos.py` for launchd LaunchAgents) used by `brr daemon
  install | uninstall | logs` per
  [`plan-laptop-daemoning.md`](plan-laptop-daemoning.md)
- `src/brr/kb/` — `brr kb` subcommand surface (parse / graph /
  check / cli) per
  [`plan-kb-subcommand.md`](plan-kb-subcommand.md), shared
  read surface for human users and non-brr agents (#41)
- `src/brr/config/` — three-scope config model (project /
  local / account) with TOML I/O and per-key schema per
  [`design-config-layout.md`](design-config-layout.md)
- `brr.toml` at adopters' repo roots — committed project-scope
  config; brnrd-side spawns read this from the cloned repo

First-party cloud envs (`fly_machines`, `codespaces`, future
ones) live at `src/brr/envs/<name>/` inside the brr package,
gated by `brr[<name>]` pip extras (per the single-package +
extras decision). Third-party envs use the `brr.envs`
entry-point mechanism per
[`design-env-interface.md`](design-env-interface.md) and
publish as their own `brr-env-<name>` pypi packages — the same
mechanism applies if a first-party env later graduates to its
own repo (per
[`decision-monorepo-structure.md`](decision-monorepo-structure.md)
"split-out criterion").

## Boundary

In scope for managed-mode launch:

- Surface A (managed dispatcher) — the cloud-gate adapter on the
  daemon side, the brnrd inbox-as-service API, GH App + TG bot
  webhooks, multi-project routing, permission-prompt API, audit
  log. Free + Subscribed tier.
- Surface B (compute) — generalised credential vault (AI runner
  + docker-registry + cloud-platform `kind`s, encrypted at rest;
  cloud-platform writes/reads subscriber-gated), dispatcher
  decision tree with managed-vs-BYO branch on credential
  presence, brnrd-owned Fly Machines pool for the managed path,
  BYO Fly for the subscriber-opt-in path (same env class invoked
  with the user's token), sandbox image, per-spawn task-key + GH
  App installation token, accounting +
  CSV exporter for accounting. Included credits in the
  subscription; metered top-ups for overage via Stripe
  Checkout per [`design-billing.md`](design-billing.md).
- Subscription billing leg (Stripe recurring, monthly +
  annual, Customer Portal) on top of the existing credit
  wallet, per [`design-billing.md`](design-billing.md).
- Dashboard MVP — eight views, HTMX-first; includes the
  allowance + usage view as a first-class surface with
  honest-nudge banners.
- `deploy/` templates folder and the `brr/daemon` Docker image
  variant (demoted to launch-nice-to-have, cloud-first users
  only).
- `brr daemon install | uninstall | logs` on macOS + Linux,
  per [`plan-laptop-daemoning.md`](plan-laptop-daemoning.md).
- `brr kb status | pages | proposed | log | check | doc` per
  [`plan-kb-subcommand.md`](plan-kb-subcommand.md) — the kb
  read surface for human users and non-brr agents.
- Three-scope config model (`brr.toml` + `.brr/config` +
  account-scope on brnrd) per
  [`design-config-layout.md`](design-config-layout.md), with
  `brr config template | validate` rounding out the existing
  list/get/set/doc verbs.
- Data minimization principle baked into every endpoint.
- Monorepo restructuring (`src/brnrd/`, `src/brnrd_web/`
  alongside `src/brr/`).

Out of scope, explicitly:

- **BYO compute for non-Fly clouds** at launch (Modal /
  Daytona / Codespaces / etc.). Each cloud's BYO ships in the
  same release as its managed support per the one-for-one
  rule; only Fly is managed at launch, so only BYO Fly ships
  at launch. The old "Surface C — designed, deferred" framing
  is collapsed into Surface B as a subscriber-opt-in sub-
  option, NOT a separately-deferred surface.
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
- **Team / per-seat subscription tier.** Subscriptions are
  per-account at launch; teams + per-seat (Linear-shape
  ~$5/seat over the subscription base) is the v-next surface
  per [`decision-pricing-shape.md`](decision-pricing-shape.md).

## Read next

1. [`decision-pricing-shape.md`](decision-pricing-shape.md) for
   the pricing model that ties the surfaces together (Free +
   subscription + metered compute credits; per-seat team tier
   deferred to v-next; self-hosted always free).
2. [`design-brnrd-protocol.md`](design-brnrd-protocol.md) for
   the wire format the daemon-side adapter and the brnrd
   service both build against. Covers gates + failover dispatch
   + generalised credential vault (AI runner + docker-registry)
   + subscription endpoints + multi-project routing +
   permission prompts + data minimization in one page.
3. [`plan-managed-gates-launch.md`](plan-managed-gates-launch.md)
   for the Surface A launch sequencing (GH-then-TG + routing UX
   + permission-prompt integration).
4. [`plan-failover-compute.md`](plan-failover-compute.md) for
   the Surface B launch sequencing (credential vault,
   dispatcher decision tree, brnrd-owned Fly pool, permission
   gate API, Upsun backend deployment).
5. [`plan-brnrd-dashboard-mvp.md`](plan-brnrd-dashboard-mvp.md)
   for the dashboard launch sequencing.
6. [`design-billing.md`](design-billing.md) for the subscription
   mechanics + credit-wallet mechanics behind the tier shape.
7. [`decision-cli-shape.md`](decision-cli-shape.md) for the
   `brr brnrd <subcommand>` namespace and the rest of the CLI
   verb taxonomy.
8. [`research-cloud-envs.md`](research-cloud-envs.md)
   for the cross-env patterns and per-platform briefs
   underpinning the managed-compute sandbox + daemon-side cloud
   envs (independent of managed mode, useful for power users).
9. [`decision-connectors-layering.md`](decision-connectors-layering.md)
   for the gates-vs-connectors split that the agentic-mode
   upgrade path depends on.
10. [`decision-monorepo-structure.md`](decision-monorepo-structure.md)
    for where the brnrd backend, dashboard, and envs live
    (single package + extras).
11. [`plan-daemon-deployment-templates.md`](plan-daemon-deployment-templates.md)
    for the `deploy/` folder and the `brr/daemon` Docker image
    variant (demoted to launch-nice-to-have; useful for
    cloud-first users; cross-platform laptop daemoning tracked
    at [issue #29](https://github.com/Gurio/brr/issues/29) and
    in [`plan-laptop-daemoning.md`](plan-laptop-daemoning.md)).
12. [`plan-laptop-daemoning.md`](plan-laptop-daemoning.md) for
    the `brr daemon install` cross-platform unit-writing work
    (per-user systemd unit on Linux, LaunchAgent on macOS).
13. [`plan-kb-subcommand.md`](plan-kb-subcommand.md) for the
    `brr kb` surface that addresses the kb-state-for-non-brr-
    agents gap raised in [#41](https://github.com/Gurio/brr/issues/41).
14. [`design-config-layout.md`](design-config-layout.md) for
    the three-scope config model that lets brnrd-side spawns
    pick up project preferences from the cloned repo.
15. [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §1,
    §2, §4 for the original pondering provenance and the
    2026-05-22 / 2026-05-25 reframe breadcrumbs that drove the
    current shape.
