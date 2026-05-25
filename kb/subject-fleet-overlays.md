# Subject: fleet and overlays

This hub synthesizes brr's current thinking about scaling from one
repo-local daemon to a user-owned fleet. The canonical split was
originally three axes: user-level steering overlays, a future fleet
operator (`brnrd`), and execution environments. The environment axis
has its own live hub in [`subject-envs.md`](subject-envs.md). The
fleet-operator axis collapsed into brr.run on 2026-05-25 (one
platform, one name) and lives in
[`subject-managed-mode.md`](subject-managed-mode.md). This page keeps
the overlay strand coherent while it is paused and points at the
relevant successor pages for the other axes.

## Current State

The fleet agenda is deliberately not one feature.

- **Environments** answer where one task executes. `host`, `worktree`,
  and `docker` ship today; `ssh` and `devcontainer` remain designed but
  unimplemented. Read [`subject-envs.md`](subject-envs.md) and
  [`design-env-interface.md`](design-env-interface.md) for the current
  contract.
- **Overlays** answer how a user steers agents across many repos without
  copying prompt edits into each repo. They are blocked on
  [`plan-overlays.md`](plan-overlays.md)'s research gate, which must pick
  the overlay shape before implementation starts.
- **Managed mode** answers what ships as a paid tier at launch — hosted
  gates (Telegram + GitHub App), multi-project routing, permission-
  prompt-gated managed compute on a brr.run-owned Fly pool, AI-
  credential vault, a dashboard MVP, and a data-minimization principle
  that keeps user content off brr.run. Active design strand; see
  [`subject-managed-mode.md`](subject-managed-mode.md). Cross-cuts
  the env axis (the failover sandbox image is built on the env
  protocol). BYO cloud execution adapters remain user-driven plugin
  work, independent of managed mode.
- **Fleet / brnrd** — *retired as a separate name on 2026-05-25*. The
  fleet-management angle collapsed into brr.run as the same product
  (one platform, one name). The dashboard surface in
  [`plan-brr-run-dashboard-mvp.md`](plan-brr-run-dashboard-mvp.md)
  carries the fleet view (project list, daemon status, per-project
  detail, conversation proxy). Any future agentic-secretary layer
  ("proactive cross-project assistant") gets named when it lands;
  the connector-vs-gate split that would underpin it lives in
  [`decision-connectors-layering.md`](decision-connectors-layering.md).

The live product boundary is still per-repo brr. A brr daemon owns one
repo's inbox, task files, conversations, env execution, responses, and
pushes. A future `brnrd` can sit above many brr repos, but brr itself
should not grow hidden fleet awareness.

## Overlay Boundary

Overlays are user-level steering, not project knowledge. Project
conventions live in `AGENTS.md` and durable repo knowledge lives in
`kb/`; an overlay would be read from user config and affect future runner
prompts without copying itself into repo files.

The unresolved design choice is shape:

- a single `~/.config/brr/overlay.md` appended to every prompt; or
- a multi-file/default/profile lookup chain under `~/.config/brr/`.

Both preserve the per-repo `.brr/prompts/<name>.md` escape hatch for a
repo-specific full prompt replacement. Implementation waits for
`kb/research-overlay-shape.md` so brr does not commit to the wrong
customization model.

## Fleet Boundary

`brnrd` is the operator layer, not another env backend and not a hidden
mode inside `brr up`. It may eventually provide a registry, fan-out
commands, response aggregation, scheduling, and supervision across many
repos. Brr's side of that story should stay small: a file-protocol inbox
that anything can write to, repo-local config, and enough explicit
machine-readable state for an external operator to inspect.

Earlier notes called that inspection surface `brr status --json`; public
`status` / `inspect` commands and the private status helper module were
removed on 2026-05-14 because they had no runtime callers. A future fleet
operator may still need a machine-readable health API, but it should be
designed from the current artifacts (`Task`, conversations,
`RunProgressView`, traces, responses), not by reviving the old helper
module by default.

## Reading Map

1. [`deck-brr-fleet-steering.md`](deck-brr-fleet-steering.md) for the
   original three-axis strategy deck. Treat it as roadmap context; some
   prompt names and orchestration details were overtaken by later
   decisions.
2. [`plan-overlays.md`](plan-overlays.md) for the paused overlay
   implementation plan and its blocking research gate.
3. [`subject-managed-mode.md`](subject-managed-mode.md) for the
   managed-mode page family promoted out of the pondering on
   2026-05-22 and reshaped through 2026-05-25: two launch
   surfaces (free dispatcher with 100 managed-compute spawns /
   month; usage-based managed compute over the cap) on a thin
   brr.run (data minimization principle), with multi-project
   routing and cost-transparent permission prompts baked in,
   plus a dashboard MVP and a monorepo layout that keeps brr
   core + brr.run backend + dashboard + plugins coherent. The
   hub fans out to:
   - a design ([`design-brr-run-protocol.md`](design-brr-run-protocol.md))
   - a research page ([`research-cloud-runner-patterns.md`](research-cloud-runner-patterns.md))
   - three decision pages
     ([`decision-pricing-shape.md`](decision-pricing-shape.md),
     [`decision-connectors-layering.md`](decision-connectors-layering.md),
     [`decision-monorepo-structure.md`](decision-monorepo-structure.md))
   - five plan pages
     ([`plan-managed-gates-launch.md`](plan-managed-gates-launch.md),
     [`plan-failover-compute.md`](plan-failover-compute.md),
     [`plan-brr-run-dashboard-mvp.md`](plan-brr-run-dashboard-mvp.md),
     [`plan-env-fly-machines.md`](plan-env-fly-machines.md),
     [`plan-daemon-deployment-templates.md`](plan-daemon-deployment-templates.md)).
4. [`notes-pondering-fleet.md`](notes-pondering-fleet.md) for the
   remaining capture: §1 / §2 are now provenance for the managed-mode
   page family (with 2026-05-22 and 2026-05-25 reframe breadcrumbs
   covering the work-continuity shift, the BYO-deferral, brnrd's
   retirement as a name, the data-minimization principle, and the
   monorepo decision). §3-§6 still cover the cross-platform
   supervisor, the self-maintaining registry, and the overlay shape
   strands as capture-only.
5. [`subject-envs.md`](subject-envs.md) for the active environment axis.
6. [`decision-remove-triage.md`](decision-remove-triage.md),
   [`decision-drop-streams.md`](decision-drop-streams.md), and
   [`decision-kb-shape.md`](decision-kb-shape.md) for later simplifications
   that supersede several specifics in the original deck.
