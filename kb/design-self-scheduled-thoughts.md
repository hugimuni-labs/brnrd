# Self-scheduled thoughts — the resident wakes itself

Status: accepted on 2026-06-09; **shipped** the same day (slice 7 — 7a
self-scheduling, 7b the sync companion). Realises the "self-scheduled crons"
mechanic sketched in [`design-agent-dominion.md`](design-agent-dominion.md) §4,
generalised away from cron syntax. Companion: the agent-owned dominion **sync**
decision below (a refinement of `design-agent-dominion.md` §5 persistence).

## The stuck point

After slices 1–6 the resident is durable (dominion), single-flight, and richly
oriented — but **purely reactive**. Every thought is triggered by an *external*
event: a gate message, a `brr run`. The resident can't act on its own clock —
can't defer ("look at this again after CI finishes"), can't run periodic
upkeep ("reconcile my dominion each morning"), can't continue a deferred train
of thought. Continuity-of-memory without continuity-of-initiative is half a
resident.

## The shape: self-emitted events, not a cron daemon

"Cron" is one shape of a more general primitive: **the resident emits an event
addressed to its own future.** Time-based recurrence is sugar over "fire this
when due." So the mechanism is a small declarative **schedule** the resident
owns in its dominion; the daemon's reflex loop fires due entries as ordinary
inbox events, which then flow through the existing single-flight pipeline
unchanged. A self-scheduled wake *is just an event* — consistent with the
agent-as-memory thesis (a thought is a runner woken by an event; the source
being "itself" changes nothing downstream).

Two trigger forms cover the useful ground without cron's 5-field grammar:

- **`at: <ISO-8601>`** — one-shot, absolute. Deferral, reminders, deadline
  checks. The absolute time lives in the spec, so it survives reinstall / a
  second machine and fires correctly there.
- **`every: <duration>`** — recurring at a fixed interval (`30m`, `1h`,
  `24h`, `1h30m`). Periodic upkeep. Anchored on first sight (adding it does
  not fire instantly — the cron mental model), then fired each interval.

That's deliberately minimal. What it still buys:

- **Ambient initiative emerges, no new mechanism.** "Wake me periodically to
  advance my own goals" is just a recurring self-thought (`every: …`) whose
  body says *make progress on your standing goals*. The interval is the
  built-in throttle that keeps ambient initiative from running away — the thing
  that makes a pure goal-loop scary. So the controllable primitive *also*
  delivers the more-agentic behaviour, safely.
- **Self-continuation** is `at: <now>` — "I'm not done thinking; wake me again
  to continue" (a fresh thought, fresh context rebuilt from memory).
- **The retired pipeline stages** (kb-maintenance, etc.) become a recurring
  self-thought if the resident wants them — exactly as
  `design-agent-dominion.md` §4 anticipated.

Explicitly **out of scope for now**: *conditional* triggers ("wake when CI goes
red") — those need a per-condition watcher/poller, a heavier surface. The
resident approximates one today with a recurring check that self-cancels. Noted
as a future shape, not built.

## Mechanics

**Specs are owned (dominion); firing-state is operational (runtime).** The split
mirrors the memory-layer model:

- **`.brr/dominion/schedule.md`** — the declarative specs, in the agent's owned,
  durable, committed memory. The resident adds / edits / removes entries freely
  ([`schedule.py`](../src/brr/schedule.py) parses it: a `## ` heading is the
  entry id, then `at:` / `every:`, an optional `conversation_key:`, and optional
  body lines). Travels with the dominion to a second machine / failover.
- **`.brr/schedule/state.json`** — last-fired timestamps, keyed by entry id.
  Daemon-owned, gitignored, *ephemeral but machine-persistent* (survives daemon
  restarts; lost only on machine-loss). The daemon mutates this, never the
  agent's committed `schedule.md` — so firing never races the dominion commit
  lock, and the daemon stays out of the agent's memory.

**Firing (reflex).** Each poll tick, before listing pending events, the daemon
reads the specs + state + clock and, for each due entry, calls
`protocol.create_event(inbox, source="schedule", body=<entry body>, …)` and
records `last_fired = now`. The synthesised event is picked up by the normal
spawn-one-when-idle path, so a due schedule waits politely behind a running
thought — no new concurrency.

**Lifecycle of an internal event.** A `schedule`-source event has no gate to
deliver its *own* response to, so the daemon **cleans it up itself** once the
thought completes (delete event + response). The *effect* of a self-scheduled
thought is usually its work — an edited file, a commit, a fixed footgun — not a
chat reply. When a firing *should* say something (a daily summary to Telegram),
it delivers through the **gate-addressed outbox** like any out-of-bound message
(`gate: <name>` + target; see
[`design-multi-response.md`](design-multi-response.md)), which the daemon turns
into a one-shot delivery — no schedule-specific delivery path. Firings also
**thread**: each entry carries a `conversation_key` (default `schedule:<id>`,
overridable to a gate thread like `telegram:<chat>:`), so a recurring entry's
wakes share a readable history and a firing can wake *inside* an existing
conversation. (Earlier, routing a schedule reply was deferred for lack of target
metadata; the gate-addressed outbox subsumed it 2026-06-09 — see
[`review-daemon-coherence-2026-06.md`](review-daemon-coherence-2026-06.md) §3.)

**Reinstall safety.** `every:` re-anchors on a fresh machine (no fire storm). An
`at:` whose time is more than `schedule.stale_grace_seconds` in the past is
anchored-as-fired without firing, so a long-stale one-shot doesn't surprise-fire
after a reinstall.

## Companion: the agent owns dominion sync + conflict resolution

Surfaced reviewing slice 5: `dominion.commit` captured locally then *best-effort
pushed*, and **silently gave up on a diverged remote** — two machines (or a
daemon + a failover host) writing `brr-home` would quietly diverge and never
reconcile. The fix follows the slice-5 principle directly: **merging two
divergent memories is synthesis — judgement — exactly what a daemon/scanner
can't do** (the same reason there's no deterministic dissonance detector). So:

- **Daemon = durability floor.** Keep the serialized local commit at sleep
  (memory is never lost) and a best-effort *push*. That's all the reflex owes.
- **Agent = remote reconciliation.** Fetch / merge / resolve-conflicts / push of
  `brr-home` is the resident's job — git-layer dissonance resolution, done in
  the dominion worktree (whose absolute path the wake prompt already gives it),
  and **gated on presence** (reconcile when you're the one awake, so two live
  thoughts don't fight over the merge).
- **Stop giving up silently.** On a failed push the daemon records a
  `needs_sync` marker (runtime); the wake prompt surfaces "your dominion's
  remote diverged — reconcile it"; a successful push clears the marker. The
  playbook codifies the ownership.

This is also where self-scheduling pays off: the resident can keep its dominion
healthy with a recurring `every:` reconcile-thought instead of waiting to trip
over divergence.

## Links

- [`design-agent-dominion.md`](design-agent-dominion.md) — the substrate; §4
  reflex/deliberation (this realises its "self-scheduled crons"), §5 persistence
  (the sync companion refines it).
- [`subject-daemon.md`](subject-daemon.md) — the reflex loop that fires due
  schedules; pipeline and Society-of-Mind concurrency.
- [`design-environment-shaping.md`](design-environment-shaping.md) — a recurring
  self-thought is a natural carrier for the loop's periodic upkeep.
