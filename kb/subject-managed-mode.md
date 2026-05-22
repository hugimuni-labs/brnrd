# Subject: managed mode — hosted gates and BYO cloud execution

Hub for brr's "managed" tier: the work that lets adopters skip the
per-user bot setup (Dimension A) and run tasks while their laptop is
down (Dimension B), while keeping a 1:1 OSS self-hosted path. Companion
to [`subject-envs.md`](subject-envs.md) (the env protocol that
cloud-runner adapters extend) and
[`subject-fleet-overlays.md`](subject-fleet-overlays.md) (the broader
fleet axes, of which managed mode is one strand). Provenance lives in
[`notes-pondering-fleet.md`](notes-pondering-fleet.md) §1, §2, §4.

## Current state

Managed mode is in **design**, not implementation. The dominant
constraint shaping it: the same paid tier needs to ship at launch so
early adopters see a clearly-articulated OSS / paid split rather than
a bait-and-switch that appears after they've invested.

The architecture splits cleanly along two independent dimensions plus
one orthogonal concern:

| Dimension | What it manages | Adoption pain it removes |
|-----------|-----------------|--------------------------|
| **A. Gates / IO** | Hosted bots (GitHub App, Telegram, later Slack / Discord / GitLab) running on brr.run | Per-user GitHub App / BotFather setup — currently the longest single friction in brr onboarding |
| **B. Cloud execution (BYO)** | Per-task cloud sandbox fan-out via env-protocol adapters | "My laptop has to be up" — task throughput beyond what the daemon's host can handle |
| (orthogonal) **Daemon hosting** | Where the brr daemon process itself lives | "What if the laptop is down" for everyone, regardless of compute fan-out |

Both dimensions and the daemon-hosting story preserve the OSS path
end-to-end. The managed tier is "we operate the bot and / or the
brr.run inbox-as-service on your behalf"; the OSS tier is "you
operate your own bot and your own daemon transport". No code fork.

## Dimension A — managed gates

Today's gates are BYO: each adopter creates a Telegram bot via
@BotFather or registers a GitHub App, copies the token / app secret
into `.brr/config`, and the daemon polls or receives webhooks
directly. Setup is the longest single friction in adoption (more so
for GitHub than Telegram).

Managed gates collapse this to one CLI verb plus a bot interaction:

1. User runs `brr connect brr.run`, authenticates, gets a pairing
   code or install URL.
2. User `/invite @brr_bot` on Telegram or installs the brr.run
   GitHub App on selected repos.
3. brr.run's hosted bot receives events, routes them to the
   user's per-account inbox-as-service.
4. The user's daemon (wherever it runs — see Daemon hosting below)
   long-polls brr.run and drains the inbox the same way it drains
   `.brr/inbox/` today.

The wire protocol and brr.run-side API surface live in
[`design-managed-gates.md`](design-managed-gates.md). The launch
sequencing — GH App adapter first (largest pain relief), TG bot
adapter as fast-follow on the same backend — is in
[`plan-managed-gates-launch.md`](plan-managed-gates-launch.md).

## Dimension B — BYO cloud execution

Per-task fan-out for users who want tasks to run somewhere other
than the daemon's host. The architectural shape is already covered
by the existing
[`design-env-interface.md`](design-env-interface.md) — cloud-runner
envs are variations of the designed `ssh` env, with the transport
swapped for a per-platform SDK or REST API. No new design page
needed.

For the **BYO tier** (the one shipping at launch), brr.run is out
of the per-task path entirely:

```
brr daemon                              cloud platform (user's account)
   │                                            │
   ├──► FlyMachineEnv.prepare ───POST /v1/apps/{app}/machines───►
   ├──► FlyMachineEnv.invoke  ───SSH exec / POST exec───►
   └──► FlyMachineEnv.finalize ───DELETE machine───►
```

The daemon configures one or more cloud-runner env adapters with the
user's platform tokens, then dispatches tasks the usual way. brr.run
involvement is only the gates side (Dimension A), and only if the
user is also on the managed-gates tier.

Per-platform "what brr has to add" deltas and the cross-adapter
patterns (credential delivery, repo delivery, result delivery,
cold-start handling) live in
[`research-cloud-runner-patterns.md`](research-cloud-runner-patterns.md).
The first adapter has its own plan at
[`plan-env-fly-machines.md`](plan-env-fly-machines.md); a Codespaces
plan follows as the cheapest second adapter with the biggest
audience overlap.

The **fully-managed compute tier** (brr.run runs sandboxes on its
own infra, user pays per-task with margin) is explicitly v-next. It
adds a brr.run-side scheduler and a per-task billing surface, both
out of scope for launch.

## Daemon hosting

The "where does the daemon live" question is orthogonal to both
dimensions but resolves the laptop-down concern more cheaply than
brr.run-spawns-sandboxes-on-your-account does. The model is
two-layer:

- **Always-on daemon host** — where the brr process lives.
  Long-polls brr.run for gate events (if on managed gates);
  dispatches tasks via whatever env is configured.
- **Per-task sandbox host** — where individual tasks fan out via
  cloud-runner envs (optional).

Deployment targets ship as a small `deploy/` template family with
one `brr/daemon` Docker image variant (the existing bundled
Dockerfile split into daemon + runner variants; daemon drops the
runner CLIs to stay small). Ranked by setup ease:

| Target | Setup | Notes |
|--------|-------|-------|
| Free-tier always-on cloud apps (Fly app, Render free worker, Railway) | `flyctl launch` from template / one-click deploy | Cheapest; the "deploy brr in 30 seconds" path |
| Read-only PaaS templates (Heroku, Upsun, Render Blueprint, Railway, App Platform) | One-click deploy button | Broadest developer-audience reach; per-task work must fan out to cloud-runner envs (no `docker` env without docker-in-docker) |
| Cheap always-on VPS (Hetzner CX11 €3.79/mo, Oracle Free Tier ARM, low-end OVH / DO / Vultr) | `docker compose up -d brr` + systemd unit | Most flexible (full `docker` env); cheapest at scale |
| Laptop / home server | `brr install-service` for macOS + Linux | Existing default; install-service verb removes the "go add it to your startup scripts" friction |

The deployment-templates work has its own plan at
[`plan-daemon-deployment-templates.md`](plan-daemon-deployment-templates.md);
the install-service verb is a separate future plan
(`plan-install-service.md`, not yet drafted; tracked in
[`notes-pondering-fleet.md`](notes-pondering-fleet.md) §7). brr.run
holding the user's cloud token and spawning sandboxes on their
behalf — the "BYO sandbox dispatch" alternative — is a v-next
convenience layer, deferred per the always-on-host model being
simpler operationally and keeping brr.run's scope tight.

## Boundary

In scope for managed mode launch:

- The cloud-gate adapter on the daemon side and the brr.run
  inbox-as-service API (Dimension A).
- Cloud-runner env adapters as plugins (Dimension B, BYO tier).
- The `deploy/` templates folder and the `brr/daemon` Docker image
  variant.
- `brr install-service` on macOS + Linux.

Out of scope, explicitly:

- **brnrd.** Operator-agent product on its own clock; consumes brr
  and brr.run later, doesn't extend them. See
  [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §3.
- **Fully-managed compute tier.** Adds a brr.run-side scheduler and
  per-task billing; v-next.
- **Cloud token storage on brr.run.** The "store my Fly token so my
  daemon doesn't have to" convenience is deferred; the always-on
  daemon host pattern handles the case without it.
- **Windows daemon supervision.** Defer per
  [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §4.

## Read Next

1. [`design-managed-gates.md`](design-managed-gates.md) for the
   cloud-gate adapter protocol on the daemon side and the
   inbox-as-service API on the brr.run side. The locked interface
   both sides build against.
2. [`research-cloud-runner-patterns.md`](research-cloud-runner-patterns.md)
   for the cross-adapter patterns and per-platform briefs underpinning
   the Dimension B work.
3. [`plan-managed-gates-launch.md`](plan-managed-gates-launch.md) for
   the GH-then-TG launch sequencing and the brr.run backend
   skeleton.
4. [`plan-env-fly-machines.md`](plan-env-fly-machines.md) for the
   first BYO cloud-runner adapter.
5. [`plan-daemon-deployment-templates.md`](plan-daemon-deployment-templates.md)
   for the `deploy/` folder and the `brr/daemon` Docker image
   variant.
6. [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §1, §2, §4
   for the original pondering provenance behind the current
   synthesis.
