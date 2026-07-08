# Design: multi-workstream concurrency — beyond single-flight

Status: active on 2026-07-08 (maintainer ask, evt-twkg + evt-l6a7/evt-bo51
same-thread follow-ups). Exploration + recommendation, not a build order —
several sub-decisions below are named forks, not shipped.

## The ask, corrected before anything else

The maintainer's own example: one user asks for a UI tweak, then a ToS page,
then a backend spawn-logic change — three independent asks that a
high-quota account (Claude Pro + heavy Codex credits) should be able to work
*at the same time*, not queued behind each other. He floated two poles —
"just active runs, flat, society-of-mind" vs. "resident stays pure
orchestrator, workers do everything" — and asked to explore the whole space,
not pick between only those two.

A same-thread follow-up corrected the premise before the exploration could
go wrong: **Claude and Codex already run in parallel, on the same
subscriptions, at the process/API level.** Single-flight was chosen "because
that was the simplest way to make weaver continuously across runs and keep
the inter-run context" — a coherence/continuity design choice, not a quota
or provider concurrency wall. So the real question isn't "can this be
parallel" (it already can, mechanically); it's **how much of brr's own
dispatch model to keep serial, and why**, which reframes this from a
feasibility study into an architecture-tradeoff one.

## Current state, grounded (not assumed)

- **Resident single-flight.** `daemon.py`'s `start()` loop runs a
  `ThreadPoolExecutor(max_workers=2)`; the `current` slot is the one
  resident-stack (dominion-writing) thought at a time, gated on
  `current is None` (~4784).
- **`spawn:`, cap 1.** `_queue_spawn_request` (~3013) forces `worker: true`
  and `environment: worktree` unconditionally, dispatches immediately into
  the pool's second slot, `current_spawn` (~4700, ~4827), capped at 1
  concurrent child. Completion (success or crash, PR #266) lands as an
  ordinary pending event tagged with the parent's `conversation_key` — the
  parent reviews and folds it in on its own next boundary, never a second
  concurrent dominion writer.
- **`respawn:` stays sequential-only** — `_queue_respawn_request` (~2910)
  only creates a normal inbox event; it's picked up once the *current run
  ends*, by design (cross-Shell/cross-repo/outlives-this-run handoff, not a
  fork).
- **No per-event lock beyond the two slots.** Dedup (`daemon.py` ~1225) is
  by *origin message*, preventing a re-answered message from re-dispatching
  — it does not claim an event mid-flight. Raising the spawn cap doesn't
  reopen an event-ownership problem; there isn't one today beyond "how many
  pool slots exist."
- **`run_ledger` already carries the rollup fields.** `parent_run_id` /
  `is_subspawn` (`run_ledger.py` ~178) exist so a parent's true cost is a
  query, not a rewrite. `task_classification` and `usd_credits_equivalent`
  are largely unpopulated in practice (`design-quota-scheduling-loom.md`'s
  2026-07-06 review) — real, but a data-completeness gap, not a schema gap.
- **Dashboard already renders N peer rows.** `LiveRuns` (#258,
  `design-dashboard-live-surface.md`) reads the presence registry
  account-wide with no cap on row count — a resident `current` and a
  `current_spawn` already show as two distinct rows today. Not done:
  joining `parent_run_id`/`is_subspawn` into that view so a viewer sees the
  parent/child relationship, not just a flat list (named at #258's own
  ship, still open).
- **Telegram topics are wired, but per-gate, not per-run.** `telegram.py`
  has full `topic_id`/`message_thread_id` plumbing (send, receive, restrict,
  card replies) for *one configured topic per gate*. Nothing allocates or
  routes a topic per concurrent workstream.
- **Cross-repo concurrency is a separately deferred decision.**
  `decision-account-centered-daemon.md` §"Open questions" explicitly parks
  this: "v1 stays single-flight across all repos... a later decision." This
  page's scope is same-repo fan-out; cross-repo is a distinct axis, noted
  where it intersects.
- **Docker clone-isolation (#80) is not built.** Worktree isolation (forced
  for every spawn since the 2026-07-08 gap closure) is the only isolation
  spawn actually gets today.
- **Shipped this run:** the `coexisting_runs` facet (`facets.py`,
  `kb/design-resident-boundary.md` §1) was pure schema with zero collector
  wired anywhere — `facets.build()` hardcoded it `unimplemented`
  regardless of input. Wired it to the same presence-registry query
  `_run_worker` already uses for the wake-time-only `context.md` injection
  ("Other thoughts awake right now"), now refreshed live on every
  heartbeat/flush (`daemon.py::_write_live_portal_state`, new `brr_dir`
  param). A running resident now gets a live sibling-run signal instead of
  a snapshot frozen at wake time — infrastructure every slice below needs
  regardless of which fan-out shape ships. Tests:
  `test_facets.py::test_build_coexisting_*`,
  `test_daemon.py::test_write_live_portal_state_coexisting_runs_reflects_presence`.
  Full suite green (1399).

## Why full flat concurrency isn't free — and why that doesn't block this

`design-concurrent-execution.md` is the load-bearing prior art: a threaded,
N-resident-weight-workers daemon was *built*, then reversed the same week
the resident-agent reshape landed, because "a resident agent's continuity
lives in durable memory, not in throughput-parallel workers." Two or more
threads each writing kb/schedule/dominion concurrently is the exact
incoherent-durable-memory problem that reversal closed, and nothing in the
current ask requires reopening it — **the coherence tax applies specifically
to resident-weight (dominion-writing) threads, not to worker-stack children**.
`spawn:`'s existing `worker: true` invariant (no dominion write, no kb
governance, no scheduling authority) already sidesteps it for exactly this
reason (`design-director-loop.md` §"Concurrent sub-spawns"). So: **the
number of durable-memory writers stays 1, always; the number of concurrent
worker-stack children is the only knob actually open to raise.**

This also resolves the apparent tension in the maintainer's own two poles.
"Flat, just active runs, society-of-mind" reads differently depending on
which layer it's asked of:

- **Presentation-flat** (a human looking at a dashboard sees N peer rows,
  not a resident row with children nested three panels deep) — already
  true today (`LiveRuns`), and cheap to make *more* legible by joining the
  rollup fields that already exist.
- **Execution-flat** (N threads independently deciding what's true about
  shared durable memory, with no single owner) — the exact shape that was
  tried and reversed, for reasons that still hold and that the
  maintainer's own message re-derives independently ("who owns conflict
  resolution... two concurrent runs may be stepping on each other's toes").

Recommendation: **flatten the view, not the write-authority.** A dashboard
can and should show every live workstream as a peer card; underneath, one
resident thread stays the only writer to durable memory, folding each
child's result in serially as it lands.

## The design space (independent toggles, not a single decision)

1. **Fan-out width `N`.** Today's `current_spawn` is a single slot; the
   mechanical change is a small pool (`current_spawns: dict[run_id,
   Future]`) capped by a config knob (e.g. `max_concurrent_spawns`,
   `.brr/config`), default conservative, hard ceiling well below "unbounded"
   so a burst of asks can't starve the one resident thread's attention
   (the same caution slice-1 shipped with, just parameterized instead of
   hardcoded at 1).
2. **Quota-pool-aware placement.** The runner catalog already partitions
   quota pools by Shell (`claude-local` vs `codex-local`,
   `design-runner-cores.md`). Two children on *different* pools are close
   to free — no added contention; two on the *same* pool genuinely compete.
   Placement should weight fan-out by pool diversity, not just a flat
   width — this is the concrete mechanism behind "a user with Claude Pro
   and a ton of Codex credits should get parallelism the quota can actually
   absorb."
3. **Isolation granularity.** Worktree isolation is already forced for
   every spawn (2026-07-08 gap closure). Docker clone-isolation (#80) is a
   stronger, still-unbuilt wall for a genuinely risky or untrusted
   workstream — orthogonal to fan-out width, not a prerequisite for raising
   it.
4. **Comms-channel fan-out.** The status quo — every workstream narrates
   into one shared thread — is exactly the collision the maintainer named
   ("the communication channel is shared"). Telegram's topic/supergroup
   plumbing already exists per-gate; extending it to auto-allocate one
   topic per active workstream (parent thread stays the "director" channel,
   each child gets its own) is mechanically close but unbuilt, and is a
   real UX/product call (bot needs elevated permissions, supergroup-only
   feature, changes what "a reply" means to the user) — named as a fork
   below, not decided here.
5. **Fold-in cadence — the one non-toggle.** Regardless of `N`, completions
   still land as ordinary pending events into the single resident thread's
   own boundary, reviewed and folded in one at a time (`dominion-
   playbook.md` §Delegation's wait-and-review contract, unchanged). This is
   what keeps 1-4 safe to raise without reopening the reversed design.
6. **Cross-repo axis.** Orthogonal, already deferred
   (`decision-account-centered-daemon.md`). Matters for this ask only if
   the three example workstreams (UI, ToS, backend spawn-logic) actually
   span repos — today they're plausibly all in one repo's monorepo shape
   (`src/frontend`, `src/brr`), so same-repo fan-out likely covers the
   maintainer's own example without touching this axis at all.
7. **Auto-tuned default width by subscription tier.** The natural home for
   "detect a high-quota account and default `N` up" is the quota-scheduling
   loom's tracking table (`design-quota-scheduling-loom.md`) — but that
   table's own rollup fields (`task_classification`,
   `usd_credits_equivalent`) are still mostly unpopulated in production
   rows (named in the 2026-07-06 PR #254 review). Auto-tuning off
   incomplete data would be guessing dressed as a feature. Recommend a
   manual/explicit toggle now; revisit auto-tuning once the ledger has real
   rows to derive a default from.

## Walking the maintainer's own example

UI tweak + ToS draft + backend spawn-logic change, one user, one thread,
high-quota account, `max_concurrent_spawns` raised above 1:

1. All three asks arrive in the resident's own thread (single conversation,
   unchanged — the shared channel is a presentation question, not a
   dispatch one).
2. The resident triages and dispatches up to `N` as `spawn: true` children,
   each forced into its own worktree/branch (already true), placement
   preferring an under-utilized quota pool per child when Shells differ
   (item 2 above) — e.g. the backend change on a Claude core, the ToS draft
   (research-heavy, cheap-core-suitable) on a Codex-mini core.
3. The resident itself either keeps working the highest-priority item
   directly or shifts into a lighter triage/monitoring posture while
   children run — a per-run judgment call, not a forced mode.
4. Each child's completion lands as a pending event in the resident's own
   thread; review/fold-in happens serially, one at a time, in arrival
   order — no two children's diffs are ever merged into the dominion's
   understanding simultaneously.
5. If item 4 (Telegram topics) ships later, each child's own narration
   (progress, questions) could route to its own topic instead of
   interleaving with the other two in one flat chat — but today, all three
   still narrate into the one shared thread, which is the exact ergonomics
   gap named and left as a fork below.

## Recommendation: bounded fan-out, not flat concurrency

Phased, cheap-to-reversible-first:

- **Slice 0 — shipped this run.** `coexisting_runs` facet live-wired to the
  presence registry (see "Current state" above). Needed by every later
  slice regardless of shape; carried no fork.
- **Slice 1.** Generalize `current_spawn` to a small pool,
  `max_concurrent_spawns` config knob (default modest, e.g. 2, up from the
  hardcoded 1; hard ceiling e.g. 4-6). Join `parent_run_id`/`is_subspawn`
  into the `LiveRuns` dashboard view (the gap #258 itself named and left
  open) so the flattened presentation is real, not just possible.
- **Slice 2.** Quota-pool-aware child placement — prefer the less-contended
  pool when a spawn's Shell/Core isn't pinned.
- **Slice 3 (needs a maintainer nod — real UX call).** Telegram
  topic-per-workstream.
- **Slice 4 (needs a maintainer nod — bigger, crosses a separately deferred
  decision).** Cross-repo fan-out, revisiting
  `decision-account-centered-daemon.md`'s parked call.

## Named forks for the maintainer (not decided here)

1. **Default fan-out width, and how it should scale with subscription
   tier.** A quantitative product call, and the data that would ground it
   (the run-ledger's cost/classification fields) isn't populated yet —
   recommend a manual toggle now, defer auto-tuning.
2. **Comms UX for N concurrent workstreams** — per-workstream Telegram
   topics/supergroup vs. the status-quo shared thread with stricter framing
   vs. something else. Real permission and UX cost, not free to build
   speculatively.
3. **Whether cross-repo fan-out is in scope now**, given the dashboard's
   push toward the zachtronics live control loom, or stays deferred per the
   account-daemon decision until the same-repo slices above are proven.

## What this leaves untouched

Cross-repo concurrency's v1 single-flight decision, docker clone-isolation
(#80), and the run-ledger's unpopulated cost-rollup fields are all named
above as real, pre-existing gaps this page doesn't resolve — each already
has its own tracking (`decision-account-centered-daemon.md`,
`kb/decision-hosted-execution-liability.md`, `design-quota-scheduling-loom.md`).

## Cross-links

`design-concurrent-execution.md` (the reversed full-concurrency design —
read before proposing anything execution-flat again), `design-director-loop.md`
§"Concurrent sub-spawns" (the spawn: mechanism this extends),
`design-dashboard-live-surface.md` §"Shipped (2026-07-07): #258" (the
live-runs view this asks to enrich), `decision-account-centered-daemon.md`
§"Open questions" (the cross-repo axis), `design-quota-scheduling-loom.md`
(the ledger this would eventually auto-tune from), `design-resident-boundary.md`
§1 (the facet schema `coexisting_runs` belongs to).
