# Subject: managed mode — work continuity via brr.run

Hub for brr's "managed" tier: the work that lets adopters skip the
per-user bot setup, keeps their tasks moving when their laptop is
offline, and offers a coherent paid path without contradicting the
"everything is OSS self-hostable" stance. Companion to
[`subject-envs.md`](subject-envs.md) (the env protocol the
cloud-runner adapters extend) and
[`subject-fleet-overlays.md`](subject-fleet-overlays.md) (the
broader fleet axes, of which managed mode is one strand).
Provenance lives in
[`notes-pondering-fleet.md`](notes-pondering-fleet.md) §1, §2, §4.

## The frame: work continuity, not laptop continuity

The brr pitch is "your laptop, accessible from anywhere." A user
buying that pitch is buying *work continuity* — they want their
ongoing work (deploys, log checks, quick code asks, light-bulb
fixes) to keep happening when they're not in front of their laptop.
The laptop is the default *home* for the work because that's where
their editor, dotfiles, credentials, conversation history, and
trust live. But "home" and "always-on" are different requirements:
when the laptop is briefly away, the work should still progress,
landing back home when home returns.

This frame matters because it eliminates a bad-shape answer
(deploy brr to an always-on third box, operate it as infra) and
clarifies a good-shape answer (brr.run is the always-on dispatcher;
ephemeral cloud sandboxes execute when home is offline; results
flow back to home via git). Earlier pondering had the always-on
box as the preferred BYO answer to laptop-down dispatch; the
2026-05-22 reframe demoted it — see Daemon hosting below.

## Current state

Managed mode is in **design**, not implementation. The dominant
constraint shaping it: the same paid tier needs to ship at launch
so early adopters see a clearly-articulated free / paid split
rather than a bait-and-switch after they've invested. Pricing
shape is captured in
[`decision-pricing-shape.md`](decision-pricing-shape.md); the wire
contract that ties everything together is in
[`design-brr-run-protocol.md`](design-brr-run-protocol.md).

Three paid surfaces, all riding the same brr.run protocol:

| Surface | What it is | Pricing | Adoption pain it removes |
|---------|-----------|---------|--------------------------|
| **A. Managed gates / IO** | Hosted bots (GitHub App, Telegram, later Slack / Discord / GitLab) routing events to a per-account brr.run inbox | Free tier (with rate caps) | Per-user GitHub App / BotFather setup — currently the longest single friction in adoption |
| **B. BYO failover compute** | When the user's daemon is offline, brr.run spawns a per-task ephemeral sandbox in the user's cloud account (Fly / Modal / Daytona / E2B / Codespaces / etc.) and forwards the result through the gate | Free tier (with rate caps on dispatches; user pays own cloud bill) | "My laptop has to be up" — without the user operating a separate always-on box |
| **C. Managed compute** | Same failover spawn, but in brr.run's cloud account; user pays per-task with margin | Usage-based, pass-through with margin | "I don't want to set up any cloud account" — easiest BYO-less path |

Surface A is the *entry point* (free, broad reach). Surface B is
the *self-hostable elaboration* (same code, user's tokens, user's
cloud bill). Surface C is the *paid convenience* (same code,
brr.run's cloud account, usage-billed with margin). All three are
the same dispatcher + same cloud-runner adapters; what differs is
who holds the cloud token and who pays the cloud bill.

## How the dispatcher works

brr.run is the always-on thing. Every event flows through one
dispatcher decision:

```
event arrives at brr.run (TG message / GH @brr comment / etc.)
         │
         ▼
┌──────────────────────────┐
│  is the user's daemon    │
│  online (recent poll)?   │
└──────┬───────────────┬───┘
       │ yes           │ no
       ▼               ▼
  enqueue        ┌───────────────────────┐
  for daemon     │  failover enabled     │
  to drain       │  AND under caps?      │
  (today's       └─┬─────────────────┬───┘
   path)           │ yes             │ no
                   ▼                 ▼
            spawn per-task     queue event +
            sandbox in         notify user
            configured cloud   via gate
            (BYO token OR
             brr.run's token)
                   │
                   ▼
            sandbox runs the
            runner, pushes the
            branch, POSTs the
            response, tears
            itself down
```

The same cloud-runner adapter that a laptop daemon uses for BYO
compute (Surface B in *active* mode) runs server-side under
brr.run when the daemon is offline (Surface B in *failover* mode,
or Surface C with brr.run's token). Same protocol, two callers.
See [`design-brr-run-protocol.md`](design-brr-run-protocol.md) →
"Failover dispatch" for the precise decision tree and
[`research-cloud-runner-patterns.md`](research-cloud-runner-patterns.md)
for the per-adapter implementation notes.

## Surface A — managed gates

Today's gates are BYO: each adopter creates a Telegram bot via
@BotFather or registers a GitHub App, copies the token / app
secret into `.brr/config`, and the daemon polls or receives
webhooks directly. Setup is the longest single friction in
adoption (more so for GitHub than Telegram).

Managed gates collapse this to one CLI verb plus a bot
interaction:

1. User runs `brr connect brr.run`, authenticates, gets a pairing
   code or install URL.
2. User `/invite @brr_bot` on Telegram or installs the brr.run
   GitHub App on selected repos.
3. brr.run's hosted bot receives events, routes them to the
   user's per-account inbox-as-service.
4. The user's daemon (wherever it runs) long-polls brr.run and
   drains the inbox the same way it drains `.brr/inbox/` today.

The wire protocol and brr.run-side API surface live in
[`design-brr-run-protocol.md`](design-brr-run-protocol.md). The
launch sequencing — GH App adapter first (largest pain relief),
TG bot adapter as fast-follow on the same backend — is in
[`plan-managed-gates-launch.md`](plan-managed-gates-launch.md).

## Surface B — BYO failover compute

Per-task fan-out when the user's daemon is offline (or — as a
v-next opt-in — when it's overloaded). The architectural shape is
already covered by the existing
[`design-env-interface.md`](design-env-interface.md) — cloud-runner
envs are variations of the designed `ssh` env, with the transport
swapped for a per-platform SDK or REST API. No new env-protocol
design needed.

The *new* work for Surface B is server-side: brr.run holds the
user's per-platform tokens, calls the adapter on the user's behalf
when the daemon is offline, and accounts for the spawn. That
machinery is specified in
[`design-brr-run-protocol.md`](design-brr-run-protocol.md) →
"Failover dispatch" + "Cloud-token security model" and sequenced
in [`plan-failover-compute.md`](plan-failover-compute.md).

The shape from the user's perspective:

```
brr accounts add-credential fly      ; paste a Fly app-scoped token
brr accounts failover --enable \     ; flip the policy bit
  --platform fly \
  --monthly-cap 100
```

After that, daemon-down events spawn a per-task Fly machine, run
the task, push the branch, post the response — without the user
ever operating a second box. The token is scoped to one Fly app
(`brr-failover`) with machine-create + destroy only; brr.run
stores it encrypted and uses it solely at spawn time.

Per-platform "what brr has to add" deltas and the cross-adapter
patterns (credential delivery, repo delivery, result delivery,
cold-start handling) live in
[`research-cloud-runner-patterns.md`](research-cloud-runner-patterns.md).
The first adapter has its own plan at
[`plan-env-fly-machines.md`](plan-env-fly-machines.md); a
Codespaces plan follows as the cheapest second adapter with the
biggest audience overlap.

## Surface C — managed compute

Same failover spawn machinery as Surface B, but the cloud account
is brr.run's and the user pays per-task with margin. Implemented
as a flag in the failover policy: `platform = "brr-managed"`
selects brr.run's pool instead of a user-stored credential.
Pricing model in
[`decision-pricing-shape.md`](decision-pricing-shape.md).

This is *not* the older "fully-managed compute tier" v-next note —
that framing assumed a scheduler. There's no scheduler; it's just
per-task spawn-and-teardown invoked through the same code path as
Surface B. The complexity is in billing accounting and capacity
planning, not in orchestration.

## Daemon hosting

The "where does the daemon live" question is orthogonal to the
three surfaces. Default is the user's laptop. For the *common*
laptop-down case, brr.run-as-failover-dispatcher (Surface B / C)
is the answer — not a separately-operated always-on host.

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
dispatch, with brr.run-spawns-sandboxes-on-your-behalf as a
v-next convenience. The 2026-05-22 reframe inverted this: the
always-on host makes the user operate a third thing (laptop +
cloud + box) for a 30%-utilisation use case at 100% cost. The
dispatcher-spawn path uses an already-justified component
(brr.run, which exists for gates anyway) and matches the work
continuity frame — cloud sandboxes appear and vanish per task,
results flow back home. The templates remain useful for the
niche "cloud-first by choice" case; they stop being the answer
for the common case.

## Boundary

In scope for managed-mode launch:

- Surface A (managed gates) — the cloud-gate adapter on the
  daemon side, the brr.run inbox-as-service API, GH App + TG bot
  webhooks. Free tier.
- Surface B (BYO failover compute) — cloud-credential storage on
  brr.run, the dispatcher decision tree, the cloud-runner
  adapter server-side caller, the first platform (Fly Machines).
  Free tier (with rate caps; user pays own cloud bill).
- Surface C (managed compute) — the same code path with
  brr.run's pool. Paid usage-based.
- The `deploy/` templates folder and the `brr/daemon` Docker
  image variant (demoted to launch-nice-to-have, cloud-first
  users only).
- `brr install-service` on macOS + Linux.

Out of scope, explicitly:

- **brnrd.** Operator-agent product on its own clock; consumes
  brr and brr.run later, doesn't extend them. The work-continuity
  frame makes the boundary clearer: managed mode keeps individual
  task work flowing; brnrd thinks at the fleet / planning level.
  See [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §3.
- **Scheduler-shaped managed compute.** The earlier "v-next
  needs a scheduler" framing was wrong — Surface C is per-task
  spawn-and-teardown via the same path as Surface B, just with a
  different token. No scheduler needed.
- **Server-side spawn for online daemons (load-shedding).**
  Possibly worth doing as a convenience; explicitly deferred
  until usage shows whether it matters.
- **Windows daemon supervision.** Defer per
  [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §4.
- **Web dashboard for managing accounts / bindings /
  credentials.** CLI-first; dashboard is v-next.

## Read Next

1. [`decision-pricing-shape.md`](decision-pricing-shape.md) for
   the pricing model that ties the three surfaces together (free
   dispatcher; usage-based managed compute; optional team tier
   later).
2. [`design-brr-run-protocol.md`](design-brr-run-protocol.md) for
   the wire format the daemon-side adapter and the brr.run
   service both build against. Covers gates + failover dispatch +
   cloud-token security in one page.
3. [`research-cloud-runner-patterns.md`](research-cloud-runner-patterns.md)
   for the cross-adapter patterns and per-platform briefs
   underpinning Surfaces B and C.
4. [`plan-managed-gates-launch.md`](plan-managed-gates-launch.md)
   for the Surface A launch sequencing (GH-then-TG).
5. [`plan-failover-compute.md`](plan-failover-compute.md) for
   the Surface B + C launch sequencing (credential storage,
   dispatcher decision tree, first server-side adapter caller).
6. [`plan-env-fly-machines.md`](plan-env-fly-machines.md) for the
   first BYO cloud-runner adapter — used by Surface B locally and
   server-side both.
7. [`plan-daemon-deployment-templates.md`](plan-daemon-deployment-templates.md)
   for the `deploy/` folder and the `brr/daemon` Docker image
   variant (demoted to launch-nice-to-have; useful for cloud-first
   users).
8. [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §1, §2,
   §4 for the original pondering provenance and the 2026-05-22
   reframe breadcrumb that drove the current shape.
