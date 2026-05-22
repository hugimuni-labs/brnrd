# Subject: fleet and overlays

This hub synthesizes brr's current thinking about scaling from one
repo-local daemon to a user-owned fleet. The canonical split is three
axes: user-level steering overlays, a future fleet operator (`brnrd`),
and execution environments. The environment axis has its own live hub in
[`subject-envs.md`](subject-envs.md); this page keeps the overlay and
fleet strands coherent while they are paused.

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
  gates (Telegram + GitHub App) and BYO cloud execution adapters — and
  where the daemon lives when the laptop is down. Active design strand;
  see [`subject-managed-mode.md`](subject-managed-mode.md). Cross-cuts
  the env axis (cloud-runner adapters extend the env protocol) and
  intentionally distinct from `brnrd`.
- **Fleet / brnrd** answers how a user sees and commands many brr-managed
  repos as a set. It remains future work and should stay outside brr's
  repo-local runtime unless a narrow brr-side primitive is clearly needed.
  Recontextualised in [`notes-pondering-fleet.md`](notes-pondering-fleet.md)
  §3 as a separate further-postponed product distinct from managed-brr.

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
   2026-05-22: hosted gates (Dimension A), BYO cloud execution
   (Dimension B), daemon hosting, and the OSS / paid split that
   ships at launch. The hub fans out to a design
   ([`design-managed-gates.md`](design-managed-gates.md)), a
   research page
   ([`research-cloud-runner-patterns.md`](research-cloud-runner-patterns.md)),
   and three plan pages
   ([`plan-managed-gates-launch.md`](plan-managed-gates-launch.md),
   [`plan-env-fly-machines.md`](plan-env-fly-machines.md),
   [`plan-daemon-deployment-templates.md`](plan-daemon-deployment-templates.md)).
4. [`notes-pondering-fleet.md`](notes-pondering-fleet.md) for the
   remaining capture: §1 / §2 are now provenance for the managed-mode
   page family; §3-§6 still cover `brnrd`, the cross-platform
   supervisor, the self-maintaining registry, and the overlay shape
   strands as capture-only. `brnrd` is recontextualised there as a
   separate further-postponed product distinct from managed-brr.
5. [`subject-envs.md`](subject-envs.md) for the active environment axis.
6. [`decision-remove-triage.md`](decision-remove-triage.md),
   [`decision-drop-streams.md`](decision-drop-streams.md), and
   [`decision-kb-shape.md`](decision-kb-shape.md) for later simplifications
   that supersede several specifics in the original deck.
