# Notes: Fleet, Managed Mode & Steering — provenance map

Status: paused; promoted strands live in subject / plan / decision pages.

Companion to [`subject-fleet-overlays.md`](subject-fleet-overlays.md)
and [`deck-brr-fleet-steering.md`](deck-brr-fleet-steering.md). This page
keeps the old pondering routes discoverable without preserving the full
running chronicle inline. Current state lives in the linked canonical
pages; old chat-shaped detail lives in git history and
[`kb/log.md`](log.md).

Lineage: compressed on 2026-05-27 after the managed-mode, pricing,
daemoning, registry, and cloud-env strands were promoted. Earlier
versions retained long reframe transcripts here; those are now replaced
by section-level receipts and links to the promoted pages.

## 1. Managed mode — promoted

Managed mode is no longer an open note. Current synthesis lives in
[`subject-managed-mode.md`](subject-managed-mode.md). The core shape:
`brnrd` is the hosted product at `brnrd.dev`; managed dispatcher and
managed compute ship as the hosted continuity path; subscribers can BYO
cloud-platform credentials for cloud envs brnrd also offers as managed;
self-hosted brnrd keeps full feature parity.

Receipts:

- [`design-brnrd-protocol.md`](design-brnrd-protocol.md) — wire contract
  for gates, failover dispatch, credential vault, subscription endpoints,
  conversation context, permission prompts, and deployment notes.
- [`decision-pricing-shape.md`](decision-pricing-shape.md) — Free plus
  unnamed Subscribed tier, credit buckets, BYO-for-subscribers, soft
  throttling, signup bonus, and honest nudge policy.
- [`design-billing.md`](design-billing.md) — Stripe recurring
  subscription plus one-shot credit wallet, bucket ledger, refund /
  dormancy policy, overdraft envelope, and accounting framing.
- [`plan-managed-gates-launch.md`](plan-managed-gates-launch.md),
  [`plan-failover-compute.md`](plan-failover-compute.md),
  [`plan-brnrd-dashboard-mvp.md`](plan-brnrd-dashboard-mvp.md), and
  [`plan-env-fly-machines.md`](plan-env-fly-machines.md) — the accepted
  implementation plans that break the hosted surface into launch slices.
- [`decision-cli-shape.md`](decision-cli-shape.md),
  [`decision-connectors-layering.md`](decision-connectors-layering.md),
  [`decision-monorepo-structure.md`](decision-monorepo-structure.md),
  [`decision-licensing-and-defense.md`](decision-licensing-and-defense.md),
  and [`decision-websites.md`](decision-websites.md) — adjacent product,
  packaging, legal, CLI, and web-property decisions.

Key breadcrumbs:

- 2026-05-22 — always-on third-box hosting stopped being the primary
  laptop-down answer; brnrd failover dispatch became the load-bearing
  work-continuity answer.
- 2026-05-25 — `brnrd` was kept as the hosted-product name at
  `brnrd.dev`; conversation continuity moved to metadata graph +
  on-demand gate / git replay rather than storing conversation bodies.
- 2026-05-26 — pricing, BYO-for-subscribers, credit buckets,
  two-website shape, machine-scoped daemon assumptions, and several
  launch defaults were locked in the accepted page family above.

## 2. Cloud execution candidates — promoted

The cloud-runner-vs-env split collapsed: cloud execution is just envs
with remote substrate. Current shape lives in
[`subject-envs.md`](subject-envs.md),
[`design-env-interface.md`](design-env-interface.md), and
[`research-cloud-envs.md`](research-cloud-envs.md).

Current rules:

- Brr core ships `host`, `worktree`, and `docker`.
- First-party cloud envs can ship as package extras such as
  `brr[fly]`; third-party envs use the `brr.envs` entry point from the
  env protocol.
- The first accepted cloud env is Fly Machines in
  [`plan-env-fly-machines.md`](plan-env-fly-machines.md).
- PaaS platforms with read-only application containers are good daemon
  hosting targets, not per-task sandbox targets; see
  [`plan-daemon-deployment-templates.md`](plan-daemon-deployment-templates.md).

The old platform survey and plugin-candidate notes were promoted into
[`research-cloud-envs.md`](research-cloud-envs.md). Older references to
this page's former §10 should be read as references to that research
page plus [`subject-envs.md`](subject-envs.md).

## 3. brnrd and fleet steering

The old split between "managed brr" and a future operator-agent product
has been renamed. `brnrd` now names the hosted product / fleet manager
that sits beside brr; a future agentic-secretary layer is still deferred
and should not leak into launch surfaces. The current product frame is
in [`subject-managed-mode.md`](subject-managed-mode.md) → "brnrd as the
product".

The broader overlays / fleet orientation is in
[`subject-fleet-overlays.md`](subject-fleet-overlays.md). Keep brr's
runtime per-repo; do not add cross-repo task scheduling or shared memory
to brr core under the banner of brnrd. Those remain v-next hosted-product
concerns.

## 4. Cross-platform daemon supervision

Laptop-side daemoning promoted to
[`plan-laptop-daemoning.md`](plan-laptop-daemoning.md) and the current
daemon hub at [`subject-daemon.md`](subject-daemon.md).

Current state:

- Linux and macOS native service lifecycle shipped on 2026-05-26.
  `brr daemon install` writes the per-user systemd unit on Linux and the
  LaunchAgent on macOS; `brr daemon up | down | status | logs |
  uninstall` operate the installed service.
- Both native service files omit `WorkingDirectory` so the later
  machine-scoped runtime is not pinned to a single repo.
- The registry-aware multi-project runtime remains future work: `brr
  init` registry writes, `brr daemon list | adopt | forget`, IPC /
  polling pickup, and per-project async pollers.
- Windows native supervision is deferred until real demand exists and
  the daemon model can support Windows honestly.

Cloud-host daemon templates are separate from laptop daemoning and live
in [`plan-daemon-deployment-templates.md`](plan-daemon-deployment-templates.md).
They remain launch-nice-to-have for cloud-first users, not the primary
laptop-down continuity answer.

## 5. Self-maintaining registry

Promoted into [`plan-laptop-daemoning.md`](plan-laptop-daemoning.md) as
the machine-scoped daemon registry.

Current accepted shape:

- `~/.config/brr/projects.toml` is the registry path; it replaced the
  older `~/.local/state/brr/repos.json` sketch.
- Native Linux and macOS installers already create the empty placeholder.
- `brr init` should append enabled project entries when the runtime slice
  lands.
- `brr daemon list | adopt | forget` operate the registry.
- The daemon still needs the poller / IPC slice before the file becomes
  the runtime source of truth.

## 6. Overlay shape — still paused

The overlay strands were promoted into
[`plan-overlays.md`](plan-overlays.md), which remains blocked behind the
overlay-shape research gate (single-file vs multi-file). Current
placeholder shape:

- Overlay config belongs under `~/.config/brr/` with XDG-respecting
  overrides.
- Git-cloning that directory is the likely multi-machine sync story.
- `brr init` works without overlays; an eventual `brr overlay init` is
  the only extra onboarding knob that should exist.

Promote new overlay decisions into `plan-overlays.md` or a successor,
not back into this notes file.

## 7. Re-promotion guide

Most of the former guide has shipped into real pages. Use this order for
remaining work:

1. Implement accepted managed-mode slices from the current plan pages:
   managed gates, failover compute, dashboard, Fly Machines env, billing,
   and brnrd protocol.
2. Finish the machine-scoped daemon runtime in
   [`plan-laptop-daemoning.md`](plan-laptop-daemoning.md).
3. Ship cloud-host daemon deployment templates from
   [`plan-daemon-deployment-templates.md`](plan-daemon-deployment-templates.md)
   when the Dockerfile split and real target testing are ready.
4. Unblock overlays through [`plan-overlays.md`](plan-overlays.md).
5. Defer Windows daemon supervision and future agentic-secretary / fleet
   brain work until there is user pressure.

## 10. Legacy cloud-env plugin candidates

Former §10 was the first survey of plugin candidates for `brr.envs`.
The canonical replacement is [`research-cloud-envs.md`](research-cloud-envs.md),
with current implementation decisions in [`subject-envs.md`](subject-envs.md)
and [`design-env-interface.md`](design-env-interface.md). Do not add new
cloud-platform analysis here; update the research page or create a
provider-specific plan.
