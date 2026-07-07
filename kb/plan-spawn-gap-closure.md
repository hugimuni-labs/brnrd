# Plan: closing the remaining `spawn:` gaps (2026-07-08)

Written on direct instruction: "name and plan the gap extensively first,
because otherwise you may drift from the original vision as you implement
it" — a same-thread follow-up to the 2026-07-07 exchange where the
maintainer pushed back on a too-forgiving read of the `spawn:` contract
("the things we agreed on in recent design and planning should not fall
out") and asked, in his own words, to "figure out what is missing from the
spawning to actually behave how we agreed it should." This page is that
naming, done once and in full, before any further code change — not a
new design, a closure plan against the design that already exists in
[`design-director-loop.md`](design-director-loop.md) §"Concurrent
sub-spawns" and its four dated addenda.

## The original vision, restated precisely (not paraphrased from memory)

Pulled from the actual conversation, not reconstructed: 2026-07-07,
11:08-11:09 CEST —

> Reread design-director-loop.md and daemon-substrate.md: you are right.
> The agreed spawn: model is a concurrent worker-stack child, with
> completion landing back as a pending event for the still-running
> parent; the parent is expected to review/fold the result before
> closeout.
>
> — "well maybe not necessarily waiting but owning"
>
> "Owning" is the better word. The parent does not have to literally
> block in silence; it can keep working, poll/linger, or explicitly
> degrade to later handling when budget/urgency forces that. But it owns
> the child result until it has either reviewed/folded it or made a clear
> handoff.

Four properties, all load-bearing, none of them up for revision here:

1. **Concurrent, not sequential.** `spawn:` starts alongside the
   still-running parent, in the same daemon process — unlike `respawn:`,
   which only ever starts once the parent ends.
2. **Owning, not necessarily blocking.** The parent doesn't have to sit
   idle; it can keep working, poll, or (under real budget/urgency
   pressure) explicitly hand off later — but "later" is a named
   degradation, not the default.
3. **Completion is a live signal into the still-running thread**, not a
   guessed-time self-wake — the self-wake convention is the fallback for
   when the parent has nothing else to do and ends before the child
   finishes, not the primary mechanism.
4. **Review before close.** A `worker: true` spawn's raw output never
   stands as the thread's last word unreviewed.

## What already correctly delivers that vision — don't re-litigate

- `spawn:` outbox frontmatter + the daemon's second `current_spawn`
  dispatch slot (`src/brr/daemon.py`, `pool = ThreadPoolExecutor
  (max_workers=2)`) — concurrent dispatch, cap of 1, shipped 2026-07-06.
- `run_ledger.parent_run_id`/`is_subspawn` — cost rollup, shipped.
- `_notify_spawn_parent` / `_notify_spawn_parent_of_crash` (PR #266) —
  completion *and* crash both land as a pending event in the parent's
  conversation. Closed, regression-tested
  (`test_notify_spawn_parent_of_crash_lands_pending_event`).
- The prompt-level "own, don't necessarily wait" contract —
  `src/brr/prompts/daemon-substrate.md`'s `spawn:` bullet and the account
  dominion playbook §Delegation were tightened 2026-07-07 specifically so
  a future wake reads "linger and review in this same run" as the
  default, "a later wake folds it in" as the named degradation.
- End-to-end proof it works: PR #263 (dispatched by run-260707-0911-rdw4,
  reviewed and merged by the self-scheduled follow-up run-260707-0959-mnrr
  after a `--dev-reload` boundary) and PR #259 (dispatched, reviewed, and
  merged by run-260707-1158-alaq, closing the loop the interim run
  jyzb had falsely claimed to close).

None of the above is the gap. Re-implementing or re-documenting any of it
is exactly the drift this page exists to prevent.

## The two gaps that are still real, named with coordinates

### Gap 1 — no working-directory isolation under `environment=host` (fixed this run, see below)

Confirmed live 2026-07-07 (run-260707-1321-auhp): a `spawn:` child's `git
checkout -b` ran in the *same* working directory as the still-running
parent's own shell — `git branch --show-current` in the parent's tool
calls flipped from `main` to the child's branch mid-edit. No data was
lost only because the child happened to make zero commits before exiting.
Had it committed, the parent's own uncommitted edit could have ridden
along into the child's branch, or a genuine checkout race could have
surfaced.

Root cause: `_queue_spawn_request` (`src/brr/daemon.py:3000`, pre-fix)
never set an environment override on the event it queues; nothing
downstream forced a spawned worker onto an isolated `WorktreeEnv`
regardless of the repo's own `.brr/config` `environment=` policy — and
this repo runs `environment=host` (`HostEnv.prepare`: `cwd=repo_root`, no
isolation, `src/brr/envs/__init__.py`). `spawn:` is the *only* dispatch
path that shares a process with a still-running peer — `respawn:` and a
fresh event both only ever start after whatever came before them ended —
so it's the only one where sharing a cwd is a live collision, not a
theoretical one.

This directly contradicts vision property 2 ("owning, not necessarily
blocking" — the parent should be able to keep working while the child
runs): today, on this repo's own config, the parent structurally cannot
safely keep editing while a spawn is in flight.

**Fix shipped this run.** `_queue_spawn_request` now sets
`meta["environment"] = "worktree"` unconditionally on the event it
creates. `run.py::_event_environment_policy` already gives an event's own
`environment` key precedence over the repo config default — the
precedence mechanism this leans on is generic and already covered by
`test_event_environment_overrides_config`; what was missing was the
spawn path actually using it. `tests/test_daemon.py::
test_drain_outbox_queues_spawn_child` now asserts the queued event carries
`environment: worktree`. No dispatch-loop change, no cap/single-flight
change — additive, scoped to env selection only, which is why this one
didn't need to be parked as a fork (see the "clear and reversible" test
below).

### Gap 2 — `reload_requested` gates spawn dispatch together with re-exec (still open — genuine fork, not decided here)

`DevReloadWatcher.changed()` (`src/brr/dev_reload.py:46`) snapshots every
watched `.py`/`.md` file under `src/brr/` and flips a single
`reload_requested` flag the instant any of them changes — committed or
not, relevant to spawning or not. The main dispatch loop
(`src/brr/daemon.py`, `start()`, ~4730/~4752/~4764) gates *both* the
re-exec decision (`if reload_requested and current is None`) and the
spawn-dispatch decision (`if current_spawn is None and not
reload_requested`) on that same flag.

Effect, confirmed twice across two different structural causes
(2026-07-07 Findings 2-3, `design-director-loop.md`): any resident
thought that both edits brr's own package files *and* wants to dispatch a
`spawn:` in the same thought has the spawn silently stall until a later
wake, on a persistent `--dev-reload` daemon — for a project whose job is
improving its own daemon, that's close to "most substantive resident
turns." It contradicts vision property 1 (concurrent dispatch) exactly
in the case where it matters most: the runs most likely to want a
concurrent child are the ones doing real engineering work, which is also
the work most likely to touch a watched file.

This was already correctly diagnosed and *already correctly left open* —
`design-director-loop.md`'s own verdict: "That trade-off (availability vs.
staleness risk) is value-laden enough to name back rather than decide
unilaterally." This page doesn't overturn that verdict; it restates it
with the two candidate shapes side by side, so the next pass at it starts
from a decision, not a re-diagnosis:

- **B1 — split the flag.** A dedicated `spawn_gate_stale` (name
  illustrative) tracks only whether the watched-file set that changed
  overlaps `daemon.py`/`dev_reload.py` (the files spawn-dispatch and
  reload logic itself live in) — spawn dispatch blocks only on *that*,
  re-exec keeps blocking on the full watch set as today. Every other
  package edit (the common case — a fix to `envs/__init__.py`, a prompt
  file, a router) stops silently stalling concurrent dispatch.
  Risk: a spawn is a separate subprocess that doesn't share the
  re-exec'd process image, so dispatching it against momentarily-stale
  in-memory daemon code carries little of the risk re-exec-gating exists
  to prevent — *except* in the narrow case this flag is designed to still
  catch (the edit changing spawn-dispatch logic itself), where stale-code
  dispatch could silently reproduce the exact bug the edit was fixing.
- **B2 — leave the coupling as-is.** Document it as a known, accepted
  cost of dogfooding `spawn:` on this repo's own long-lived `--dev-reload`
  process (a production deploy restarts per-run anyway, so the coupling
  is invisible there) and lean on the review-self-wake fallback for the
  rare in-repo case.

Recommendation, not a decision: **B1**, scoped to the narrow overlap
check above rather than an unconditional split — it fixes the common
case (most package edits) while keeping the one case where staleness is
actually dangerous (editing the dispatch/reload logic itself) exactly as
protected as it is today. But this changes a live invariant in the
dispatch loop that single-flight itself depends on
(`design-director-loop.md` slice 1 was explicitly "flagged back rather
than built blind" for the same reason) — it gets your nod before it gets
a diff, same bar.

## What shipped this run vs. what's parked

Per `run.md`'s Reconsider guidance ("clear and reversible ⇒ make the call
in this same thought... a genuine fork ⇒ name it, wait for the nod"):
Gap 1 is a scoped, additive, already-recommended fix with no design
ambiguity left in it — shipped. Gap 2 changes a core dispatch-loop
invariant with two real candidate shapes and a value trade-off between
them — named here with a recommendation, not decided.

## Non-goals (explicit, so a later pass doesn't invent scope)

- Not touching the cap-of-1 concurrent-spawn limit.
- Not touching the review-before-close contract (already correct,
  already prompt-documented, already dogfooded twice).
- Not proposing a `review: true` daemon flag (named elsewhere,
  `design-director-loop.md` "Concurrent sub-spawns", as a *different*,
  larger primitive — suppressing direct delivery until reviewed — that
  interacts with the `last_chat_id` delivery-guarantee fallback and needs
  its own deliberate pass, not folded into this closure plan).
- Not re-opening the "does the daemon correctly hand the resident its
  full interaction history" question the maintainer raised same-thread,
  same day (11:58 CEST, `.tmp/07-07-2026-telegram-interaction.md`) — that
  is a separate suspicion about gate history completeness, orthogonal to
  the spawn dispatch gaps this page scopes, and (per the 21:44/21:47
  exchange same day) the "you don't see topics we agreed on" half of that
  report was already traced to a different root cause (the dark-canvas
  incident, not a history-feed bug) and closed via PR #277.
