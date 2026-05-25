# Subject: fleet, overlays, and managed gates

This hub tracks brr's cross-project direction. As of issue #39,
managed gates + `brnrd` are active planning work, not paused context.

Canonical design page:

- [`design-managed-gates-and-brnrd.md`](design-managed-gates-and-brnrd.md)

## Current canonical state

- **Execution plane (`brr`) remains repo-local and free**: host/worktree/docker
  execution, repo-scoped runtime artifacts, and git publish behavior stay in
  `brr`.
- **Control plane (`brnrd`) is now the active direction**: global connector
  installs, multi-project dispatch, host-health-aware scheduling, cloud policy,
  and run/cost ledger.
- **Managed gates require project routing**: one Telegram/Slack bot identity can
  route many projects via explicit selector + thread binding + defaults.
- **Cloud runs are permission/policy-gated**: host-first behavior remains the
  default; cloud fallback requires explicit approval or configured cap policy.

## Overlay status in this direction

Overlay work is no longer framed as an isolated paused track. The practical
"steering" need now shows up first as managed routing + policy in `brnrd`.
User-level prompt overlays may still land later, but they are not the lead
implementation slice for issue #39.

## Reading map

1. [`design-managed-gates-and-brnrd.md`](design-managed-gates-and-brnrd.md)
   for the active architecture/product plan and phased rollout.
2. [`subject-envs.md`](subject-envs.md) for shipped execution environments and
   their current constraints.
3. [`deck-brr-fleet-steering.md`](deck-brr-fleet-steering.md) for historical
   framing; treat it as context, not canonical state.
4. [`notes-pondering-fleet.md`](notes-pondering-fleet.md) and
   [`plan-overlays.md`](plan-overlays.md) for older exploratory overlay/fleet
   strands that predate the managed-gates pivot.
