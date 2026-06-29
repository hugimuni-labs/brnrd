# Plan: control surface — the dashboard the engine shipped without

Status: active (opened 2026-06-29). Successor home for the reshape direction in
[`review-execution-model-coherence-2026-06-29.md`](review-execution-model-coherence-2026-06-29.md)
§3. Architecture: [`decision-account-centered-daemon.md`](decision-account-centered-daemon.md).
The *engine* half lives in [`plan-repo-gardening.md`](plan-repo-gardening.md)
Part 2 (Core selection, fallback, escalation, relay); this plan is the *control
surface* over it.

## Why this exists

The execution-model review found the engine sound but the work *felt* scrambled
because there is **no control surface over it** — "we shipped the engine without
the dashboard." The maintainer's correction: ship them together. This plan turns
the review's five sequenced reshape steps into slices, now unblocked by the
account-daemon decision (the two forks are resolved). Each slice is reversible;
the first three are pure projection (no architecture change) and land first.

## Slices

### CS1 — Runner envelope facet (highest leverage, pure projection)
Add `resources.runner.catalog` to `portal-state.json`: the available Shells+Cores
(name, class, cost_rank, quota/availability, `selected: true` on the active one).
The data already exists in `runner_cores.available_cores()` — it is simply not
projected. One source feeds both the user-facing card/web view and the resident's
respawn decisions. This is the review's finding #1 (the single highest-leverage
fix) and answers "we don't know what we allow to select and what was selected"
for the *selectable* half (the *selected* half is already in the portal).

Also inject the envelope into the **wake bundle** (the resident is told its own
Runner but not the envelope it can escalate into — review §2, the resident-side
of the same gap).

**Collapse the two Core catalogs first** (review finding #2 / reshape step 2):
retire the static `claude-bare-api-only-*` triplet in `runners.md` whose Cores
duplicate the `_BUNDLED_CORES` registry rows (same cost_ranks, differ only by
auth). Model `--bare`/`ANTHROPIC_API_KEY` as an **auth-variant flag on a registry
Core**, not a separate triplet of profiles. The envelope should project one
source, so this collapse precedes the facet. **Maintainer approval to delete the
bare-API path is granted** (evt-ogga — "no run relies on bare-API … you have my
approval"). Scope note: behaviour-touching, ~31 references across 8 test files use
the triplet as fixtures — land the auth-variant model first, then migrate the
fixtures; don't blind-delete. Worth its own focused wake.

### CS2 — Persist + surface the per-run record
- Render the **attempt ledger** on the card (don't let `attempt_failed` reasons
  vanish — `run_progress` already models `phase_history`/`attempt`/`fallback X->Y`;
  this is rendering/persistence, not new data).
- Persist a **per-run status doc** (gist-per-run) carrying runner/core, **repo**,
  boundary, elapsed, commits, plan position, attempt history. The card links to
  it. Delete on cleanup — no durable store needed.

### CS3 — Repo dimension on runs/cards/activity
Thread a `repo` field through `RespawnRequest`, presence, schedule, and
portal-state so every run/card/activity record **names its repo** (the
maintainer's "the status should also show the repo"). This is migration step 1
from the decision page — a repo dimension *before* the account dimension, so the
view surfaces above can show repo without the process-model change yet. The 2E
activity view already uses `repo_id`; extend cards and per-run records to match.

### CS4 — Account daemon + cross-repo dispatcher
Lift the daemon from per-repo to per-account (decision page, migration step 2):
account config (forge identity + repo registry + default repo); `brr up` reads
it; the event loop selects `repo_root` per run. Add `RespawnRequest.repo` and the
**respawn-in-another-repo** dispatch (step 3). Route on two axes per the decision's
table: *which repo* (forge events repo-addressed at the gate → no dispatcher;
message events → dispatcher output) and *which Runner* (reuse the 2A Shell/Core
pin-skip path). Keep single-flight across repos for v1. **OSS invariant:
local-only first, brnrd projection additive.**

### CS5 — Inter-run plan home + injection
A tracked, web-visible plan file (per the decision's recommendation); the daemon
preloads/auto-injects it into the wake the way Recent Activity is injected
(perception=injection, not a polling tax), and surfaces it in the card + web view.
Cross-repo plans ride the account daemon. **This is a genuine sub-fork** — confirm
the physical location with the maintainer before building (tracked file vs
orphaned branch vs gist; cross-repo store).

### CS6 — Plain-language config + daemon-owned confirmation
Replace `shell=`/`core=`/`runner_policy=` knobs with: show the envelope (CS1), let
the user request changes in prose, the resident proposes a config change, a
*daemon-owned* confirmation step applies it (the resident cannot silently rewrite
its own selection policy). Standing preferences ("escalate to most capable")
become stored policy, not per-run flags. Review reshape step 4.

### CS7 — Cross-run decision/plan ledger
A user-facing through-line of recent decisions/definitions/plan-position so
coherent work stops feeling scrambled. `kb/log.md` is the resident's through-line;
this is its **user-facing projection**. Review reshape step 5; composes with CS5.

## Sequencing

CS1 → CS2 → CS3 are pure projection / additive and land first (they make the
existing engine legible without touching the process model). CS4 is the
architecture change (account daemon) and gates CS5's cross-repo half. CS6/CS7 are
the richer UX and come last. Chunk across wakes per the gardening plan's
established cadence.

## Companion pages

- [`decision-account-centered-daemon.md`](decision-account-centered-daemon.md) — architecture.
- [`review-execution-model-coherence-2026-06-29.md`](review-execution-model-coherence-2026-06-29.md) — the framing review.
- [`plan-repo-gardening.md`](plan-repo-gardening.md) Part 2 — the engine half.
- [`design-runner-cores.md`](design-runner-cores.md) — dispatch policy.
- [`plan-resident-portals.md`](plan-resident-portals.md) — portal/injection plumbing for the view surface.
- [`plan-brnrd-dashboard-mvp.md`](plan-brnrd-dashboard-mvp.md) — the brnrd projection of these surfaces.
