# Design: multi-workstream concurrency — beyond single-flight

Status: active on 2026-07-08 (maintainer ask, evt-twkg + evt-l6a7/evt-bo51
same-thread follow-ups; forks answered same day, evt-dzgu; round 2 answered
the same night, evt-mr8v). Slice 1 shipped (spawn pool + `LiveRuns` join,
"Slice 1 — shipped" below). Round 2 (§"Named forks — round 2") mostly
*corrected* rather than opened new design: the cross-repo "workbench" fork
turned out to be already-decided, already-shipped code, not a new idea to
weigh — see that section before re-deriving it again. The loom envelope's
Phase 2 approval-URL sub-design is the one genuinely new, still-open thread
this round added, not shipped code.

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
- **`spawn:`, pool of `spawn.max_concurrent` (default 4, was cap 1).**
  `_queue_spawn_request` (~3044) forces `worker: true` and `environment:
  worktree` unconditionally; the daemon loop holds up to
  `_max_concurrent_spawns(cfg)` in-flight children in `active_spawns`
  (`daemon.py` ~4750, generalized from the old single `current_spawn`
  future). Completion (success or crash, PR #266) lands as an ordinary
  pending event tagged with the parent's `conversation_key` — the parent
  reviews and folds it in on its own next boundary, never a second
  concurrent dominion writer, regardless of how many children ran at once.
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
- **Dashboard renders N peer rows, now with the parent/child join.**
  `LiveRuns` (#258, `design-dashboard-live-surface.md`) reads the presence
  registry account-wide with no cap on row count. `parent_run_id`/
  `is_subspawn` — previously only on the closed-run ledger row — now ride
  the *live* presence entry too (`presence.py::register`, new params),
  through the cloud publish (`gates/cloud.py::_live_runs_snapshot`) and the
  `LiveRunIn` schema (`brnrd/schemas.py`) into the frontend (`liveRuns.ts`,
  `LiveRuns.svelte`'s "↳ spawn" tag). Flat card grid stays flat — see
  "Slice 1 — shipped" below for why that's the deliberate shape, not a
  half-finished tree view.
- **Telegram topics are wired, but per-gate, not per-run.** `telegram.py`
  has full `topic_id`/`message_thread_id` plumbing (send, receive, restrict,
  card replies) for *one configured topic per gate*. Nothing allocates or
  routes a topic per concurrent workstream.
- **Cross-repo concurrency — no longer just "later."** As of this same
  answer (2026-07-08 evening, evt-dzgu) the maintainer named a concrete
  near-term case (a hugimuni-website repo joining the account soon) and
  said the multi-repo shape should be "natively designed as a part of this
  frame, always" — not bolted on after. `decision-account-centered-
  daemon.md` §"Open questions" still parks the actual v1-single-flight-
  across-repos *execution* call, but this page's design space (fan-out
  width, quota-pool-aware placement, comms routing) should now be read as
  repo-parameterized from the start, not same-repo-only with cross-repo as
  an afterthought. See "Cross-repo, upgraded from deferred to
  native-by-design" below.
- **Docker clone-isolation (#80) is not built.** Worktree isolation (forced
  for every spawn since the 2026-07-08 gap closure) is the only isolation
  spawn actually gets today.
- **Shipped 2026-07-08 (slice 0):** the `coexisting_runs` facet (`facets.py`,
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
- **Shipped 2026-07-08 evening (slice 1):** see "Slice 1 — shipped" under
  the Recommendation section below for the full receipt (spawn pool +
  `LiveRuns` join).

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

- **Slice 0 — shipped 2026-07-08.** `coexisting_runs` facet live-wired to
  the presence registry (see "Current state" above). Needed by every later
  slice regardless of shape; carried no fork.
- **Slice 1 — shipped 2026-07-08 evening (this answer, evt-dzgu).**
  `current_spawn` generalized to a small pool: `_max_concurrent_spawns(cfg)`
  reads `spawn.max_concurrent` from `.brr/config` (default **4** — the
  maintainer's own number, "set the concurrency to 4 or something
  already"), clamped to at least 1; `daemon.py`'s main loop tracks
  `active_spawns: list[dict]` instead of one `current_spawn` future, reaps
  and dispatches up to the configured width per tick
  (`daemon.py` ~4028 `_max_concurrent_spawns`, ~4750 loop state, ~4888
  dispatch). `parent_run_id`/`is_subspawn` now ride the *live* presence
  entry, not just the closed-run ledger row: `presence.py::register` gained
  both params, `gates/cloud.py::_live_runs_snapshot` publishes them,
  `brnrd/schemas.py::LiveRunIn` accepts them, and `LiveRuns.svelte` renders
  a small "↳ spawn" tag on a dispatched child's card (hover shows the
  parent's own label when it's still live in the same snapshot) — the flat
  peer-card grid stays flat, per the "flatten the view, not the
  write-authority" recommendation above; this is the visual join, not a
  tree widget. Tests: `test_daemon.py::test_max_concurrent_spawns_config_parsing`,
  `test_daemon.py::test_concurrent_spawn_pool_respects_configured_width`,
  `test_cloud_gate.py::test_loop_publishes_live_runs_snapshot` (extended for
  the subspawn case). Frontend `svelte-check`/`eslint`/`prettier` clean.
  Full backend suite green (1403).
- **Slice 2.** Quota-pool-aware child placement — prefer the less-contended
  pool when a spawn's Shell/Core isn't pinned. Not started.
- **Slice 3 — explicitly postponed 2026-07-08 evening.** Telegram
  topic-per-workstream. See fork 2 answer below; the maintainer flagged a
  related, larger want (richer resident-driven channel control — reactions,
  topic-setting — that the daemon's current channel surface is "maybe a bit
  too limiting" for) as a *later*, separate thread, not folded into this
  page.
- **Slice 4 — upgraded from "needs a nod" to native-by-design, 2026-07-08
  evening.** Cross-repo fan-out. See "Cross-repo, upgraded from deferred to
  native-by-design" below — the design axis is decided; the actual
  multi-repo dispatch build is still a later, separate slice.

## Named forks — answered 2026-07-08 evening (evt-dzgu)

1. **Default fan-out width, and how it should scale with subscription
   tier.** Answered directly: **4**, shipped as slice 1's
   `spawn.max_concurrent` default. But the maintainer's actual reply
   redirected the question rather than just picking a number — read in
   full under "Loom envelope" below, a materially bigger idea this page
   hadn't named: making the *ceiling itself*, and what happens at it,
   visible and felt, not just configurable.
2. **Comms UX for N concurrent workstreams.** Answered: **postponed**, in
   the maintainer's own words — "it should feel like chatting with a
   coworker, but which is really an arcane circuit scroll spirit, so yeah
   lets postpone the topics (for now)." Addendum worth keeping, not just
   the postponement: the maintainer wants "the runner [to have] more
   flexibility in how they communicate with a user, including reactions
   and topic setting, for which daemon currently is maybe a bit too
   limiting" — a broader channel-surface gap than per-workstream topics
   alone, named for a later pass, not this one.
3. **Whether cross-repo fan-out is in scope now.** Answered: **yes,
   natively, always** — "the multi-repo case should be natively designed
   as a part of this frame, always. I am soon gonna add the hugimuni
   website to the list." Reverses this page's earlier lean ("likely moot
   for your own example"). See "Cross-repo, upgraded from deferred to
   native-by-design" below. A second, adjacent gap surfaced in the same
   reply: "the new UI currently doesn't have a way [to] add projects" —
   named in "Add-project UI gap" below, explicitly *not* asked to be part
   of the loom.

## Loom envelope — visualizing and enforcing user-set limits (new fork, not shipped)

The maintainer's own framing, close to verbatim: the UI should give a
clear visual signal of the limits the user has set for brnrd — "being at
the limit visually shouts at you" — and a resident actually hitting one
should "scream," because either the user asked for something beyond a
limit they set themselves, or the agent tried to and that request should
be held for the user's approval rather than silently refused or silently
allowed. Explicitly invited pushback; "the loom should feel entertaining
and functional."

**Why this is the right next question, not scope creep.** Slice 1 just
shipped the *first* real user-tunable ceiling in this whole area
(`spawn.max_concurrent`) — before this, the only comparable knobs were the
quota-pacing floors (`pacing.quota_low_floor_pct`/`quota_critical_floor_pct`,
B1) and the runner timeout backstop, none of which the dashboard surfaces
as a limit today. So the "loom envelope" isn't decorating one new number;
it's the first design pass at a *pattern* this repo is going to need
repeatedly as more tunables like it appear.

**Pushback, as invited:**

1. **Not every ceiling is the same kind of thing, and "scream" shouldn't
   apply uniformly.** `spawn.max_concurrent` at capacity today just means
   the 5th `spawn:` candidate waits quietly for a slot in the next tick or
   two — that's the pool doing its job, not a violation, and treating a
   routine queue wait as an alarm would train the user to ignore the
   alarm. The genuinely scream-worthy case the maintainer actually
   describes — "the agent tried to [exceed a self-set limit], then such
   task is postponed until user approves" — **doesn't exist as a code path
   yet.** A spawn candidate over the pool width isn't rejected-pending-
   approval anywhere; it's simply not dispatched this tick, silently, with
   no record that something *wanted* more room than was available. Before
   this can visually "scream" convincingly, the daemon needs to actually
   notice and record the "wanted more, didn't get it" moment — a small,
   real backend gap this idea surfaces, not just a rendering task.
2. **Some limits are structural, not user-set, and shouldn't share the
   same visual language as tunable ones.** The single durable-memory
   writer (`current`, cap of exactly 1, always) is not a dial the user
   turns — showing it in the same "you're at your limit" register as
   `spawn.max_concurrent` would misrepresent an architectural invariant as
   a preference. The envelope should visualize *configured* ceilings
   (spawn width, quota pacing floors, maybe a future budget cap), not
   every fixed constant in the codebase.
3. **This is closer to a new panel/mode than a retrofit of `LiveRuns` or
   `WindowTrack`.** Both existing loom surfaces represent *live activity*
   (a running thought, a quota window's spend). An envelope represents the
   *boundary* activity presses against — genuinely a different axis, and
   cramming it into an activity card (e.g. tinting `LiveRuns` red at 4/4)
   would conflate "here's what's happening" with "here's what you've
   allowed," the same kind of conflation the status-palette work upstream
   was careful to avoid (a status color never doubling as a series
   identity, `LiveRuns.svelte`'s own comment). Recommend a distinct
   "limits" surface reusing the same dot/bar/palette vocabulary, not a
   modification of the existing cards.

**Recommended phasing, not built this run:**

- **Phase 1 (cheap, mostly-shipped data).** A small panel listing today's
  real user-tunable ceilings — `spawn.max_concurrent` and current
  `active_spawns` count chief among them post-slice-1 — as a pressure
  meter (n/max), reusing `statusPalette.ts`'s amber/frost/void exactly as
  a bar fill, no new backend collection beyond what slice 1 already
  publishes.
- **Phase 2 (real backend work, the "scream" itself).** A rejection path:
  when a `spawn:` candidate can't be dispatched because the pool is full,
  record that fact (which event, which limit, when) instead of silently
  leaving it queued, and surface it as the actual alert state — this is
  the part that makes "the agent tried to and it got postponed until you
  approve" true rather than aspirational. Needs its own design pass
  (approval UX: a Telegram prompt? a dashboard action? auto-approve after
  a timeout?) before it's buildable — not decided here.

Not committing to a build order for either phase in this run; naming the
idea precisely, the real gap it exposes, and the pushback was the ask.

## Cross-repo, upgraded from deferred to native-by-design

`decision-account-centered-daemon.md` §"Open questions" still parks the
*execution* call ("v1 stays single-flight across all repos... a later
decision") — that line isn't rewritten here, and no multi-repo dispatch
code shipped this run. What changed is the *design posture* this page
itself takes: every fan-out axis above (width, quota-pool placement, comms
routing) should now be specified as repo-parameterized from the start,
because a second repo (hugimuni website) is a named near-term reality, not
a hypothetical. Concretely, for the next slice that touches fan-out
mechanics: a spawn candidate's identity should carry which repo it targets
as a first-class field (today implicit — everything assumes the daemon's
own repo), and placement/width reasoning (slice 2) should be written
against "repo × shell/core pool," not "shell/core pool" alone, even before
actual cross-repo *dispatch* is decided. Cheap to do now while the pool
generalization is fresh; expensive to retrofit once several slices assume
single-repo implicitly.

## Add-project UI gap (named, not this page's scope)

The maintainer named a second, adjacent gap in the same reply: the new
frontend has no way to add a project/repo to an account — today that's
registry-side, off-UI. Explicitly *not* asked to be part of the loom
envelope. Worth its own line because it's the concrete, present-tense
blocker for the hugimuni-website case driving the cross-repo posture
above, and it directly answers `decision-account-centered-daemon.md`
§"Open questions" → "Repo discovery" (explicit registry vs. workspace
scan) toward **explicit, user-facing registry** — a UI affordance to add a
project only makes sense under that model, not a background directory
scan. Not scoped or built this run; flagging the connection so the next
pass on either page doesn't re-derive it.

## Named forks — round 2, answered 2026-07-08 night (evt-mr8v)

The maintainer replied to both open items from the round above (loom
envelope pushback, cross-repo posture) in the same message, and separately
re-derived the account-workbench shape from scratch without having read
`decision-account-centered-daemon.md` closely — worth recording precisely
because the reconciliation went two different ways.

**Fork 1 — loom envelope Phase 2, refined.** Maintainer: "the ones that
have been dispatched outside of pool limit should be paused, not dropped,
not screamed at user" — confirms the pushback above. **No code needed for
that half; it was already true.** `daemon.py`'s spawn dispatch
(`spawn_candidates = [...][:open_spawn_slots]`) already leaves any
candidate beyond the open slots at `status: pending` in its inbox — never
touched, never dropped — so it's picked up automatically once a slot frees
next tick. "Paused, not dropped, not screamed" was already the mechanical
behavior; Phase 2's real gap (named above) is only that this quiet wait
produces no *record* a panel could render.

Second half is new, not previously named: **when the agent itself wants
more room than a configured ceiling allows, that should be a request the
daemon lets it make, escalated to the user for approval via a brnrd.dev
approve/confirm URL** — not silently applied, not just a chat-command
approval. Concretely closer to buildable than it first looks: this repo
already has the load-bearing precedent, `src/brnrd/routers/pairing.py`'s
device-flow (`PairRequest`: a code, a hashed poll secret, `approve_core`
gated behind `require_account`, `poll_pair` returning the outcome) — the
same shape a "propose a config change → mint a link → account owner clicks
approve while logged in → daemon observes the approval" flow needs. Also
already half-built on the *local* side: CS6's runner-policy proposal
machinery (`_queue_runner_policy_proposal` / `_handle_runner_policy_control_event`,
`account.runner_policy_proposals_path`) already parks a proposal file,
commits it, and applies it only after an explicit approval — today gated
on a chat-typed `approve runner-policy <id>` reply, and today only for
free-text policy bodies written into `policy.md`, not structured
`.brr/config` key/value ceilings like `spawn.max_concurrent`.

**Not built this run — three sub-decisions genuinely need a nod before
this is wired, named so the next pass doesn't re-derive them:**

1. **Scope of what's agent-proposable.** Start narrow — an allowlist
   beginning with `spawn.max_concurrent` alone — or generalize immediately
   to arbitrary `.brr/config` keys? An allowlist is the safer opening move;
   a fully general "the agent can propose any config key" is a bigger
   trust-boundary call than this fork asked to settle.
2. **Where approval happens.** Reuse `pairing.py`'s pattern directly
   (new `PairRequest`-shaped table + router in `src/brnrd`, gated behind
   `require_account` so only the logged-in account owner's click counts)
   rather than inventing new auth — recommended, not decided.
3. **How the daemon learns the outcome.** `poll_pair` is a polling read;
   a cleaner fit given this account daemon already has an account dispatch
   inbox (`decision-account-centered-daemon.md` part 5's "daemon-owned
   confirmation" step) is for the approve endpoint to write a
   `config_approved`/`config_rejected` event straight into
   `ctx.dispatch_inbox` — the daemon already scans that inbox every tick,
   so no new polling loop, just a new event kind the CS6 apply path (or its
   generalized successor) reacts to.

Recommend as the next ranked move, not shipped speculatively this run: the
DB schema + router + auth surface is real new attack surface (who can
change a running daemon's operating ceilings), and the maintainer's own
"or you can pick a better fitting design if you see one" reads as wanting
the shape confirmed, not blind-built.

**Fork 3 — cross-repo "workbench," corrected rather than designed fresh.**
The maintainer's proposal (account-level home with all repos registered,
symlinked/reachable, a registry, resident memory managed the same way per
project, respawn as the "get me into the right repo" primitive, explicit
call-out that self-hosted single-project must keep working with zero extra
config) is not a new idea to weigh — **it is, near-verbatim, what
`decision-account-centered-daemon.md` already decided on 2026-06-29 and
what the code already ships.** Checked against the running code, not just
the doc, this run:

- The registry exists and is exactly a "which repos, where" map:
  `account/repos.json` (`account.py::_load_registry`/`_write_registry`),
  editable via `brr add <path>` (`cli.py::cmd_add` → `register_repo`).
- The account home already organizes state **per repo inside one
  workbench**, not a dominion fork per project: `repos/<label>/dominion`,
  `plans/<label>/`, `runner-policy/<label>/policy.md` all live under one
  `home_root`, keyed by repo label — this run's own dominion path
  (`.../accounts/acc_.../home/repos/Gurio__brr/dominion`) is a live
  instance of exactly that shape.
- "Respawn gets you into any repo, like a game respawn" already works:
  `RespawnRequest.repo` → `_queue_respawn_request` tags the new event with
  `repo_label` → `_dispatchable_targets` scans every registered repo's
  inbox every tick → `_repo_for_event` resolves the event to that repo's
  worktree. Tested:
  `test_account_dispatch_inbox_routes_message_event_to_registered_repo`,
  `test_account_dispatch_keeps_forge_events_on_repo_local_route`.
- The naming instinct ("brnrd-home folder rather than dominion, cuz it's
  more invasive") was already the decided naming:
  `decision-account-centered-daemon.md` names the account container
  `brnrd-home` (working name) precisely *because* folding everything into
  a literal `dominion` sibling read as too invasive, with `dominion/` kept
  as the nested, repo-scoped, resident-owned subdirectory — the maintainer
  re-derived the same conclusion independently this run.
- Self-hosted single-project stays zero-config by construction, already:
  `account.py::resolve_context`'s `kind` resolution defaults to
  `"project"` (a per-repo home, the pre-account-daemon shape) unless an
  `account.id` or a connected cloud account is present — a bare `brr up`
  with a Telegram token on one repo never touches the account/registry
  machinery at all.

Corrected in `decision-account-centered-daemon.md` §"Open questions" this
same run rather than re-answering the fork here — that page is the durable
home for this decision; this page only needed to stop implicitly treating
it as still-open. **The one genuine gap that survives the correction is
unchanged from the round above: the "add project" UI** — today `brr add`
is CLI-only, no dashboard action exists yet, named in "Add-project UI gap"
below and now the natural next move for this thread (the hugimuni-website
repo is a concrete near-term registrant with no UI path to register it).
Literal symlinking (the maintainer's own phrasing) isn't needed on top of
this: the registry already resolves a label to an absolute path, which is
what a symlink would do less portably (breaks if the path moves, doesn't
survive across machines) — worth naming as a pushback, not silently
dropped: **recommend against literal symlinks**, keep the path-registry
model, since it already does the job the symlink metaphor was reaching for.

## What this leaves untouched

Cross-repo concurrency's v1-single-flight-across-repos *execution* call
(design posture upgraded above, build still open), docker clone-isolation
(#80), the run-ledger's unpopulated cost-rollup fields, and both phases of
the loom envelope are all named above as real, open gaps this page doesn't
resolve — each already has its own tracking (`decision-account-centered-
daemon.md`, `kb/decision-hosted-execution-liability.md`,
`design-quota-scheduling-loom.md`).

## Cross-links

`design-concurrent-execution.md` (the reversed full-concurrency design —
read before proposing anything execution-flat again), `design-director-loop.md`
§"Concurrent sub-spawns" (the spawn: mechanism this extends),
`design-dashboard-live-surface.md` §"Shipped (2026-07-07): #258" (the
live-runs view this asks to enrich), `decision-account-centered-daemon.md`
§"Open questions" (the cross-repo axis and the "Repo discovery" question
the add-project UI gap answers), `design-quota-scheduling-loom.md` (the
ledger this would eventually auto-tune from, and B1's quota-pacing floors
— the loom envelope's other candidate ceiling), `design-resident-
boundary.md` §1 (the facet schema `coexisting_runs` belongs to),
`design-brand-visual-language.md` (the status-palette vocabulary the loom
envelope phase 1 would reuse).
