# Review: daemon-layer coherence + delivery generalization (2026-06)

Status: active — findings #1–#3 landed (2026-06-09) and are folded into the
hubs; this page now primarily holds the open #4 ownership crossroads that the
daemon/delivery hubs link to. Retire when #4 resolves.

Prompted by two observations during the slice-7 work (self-scheduled
thoughts + agent-owned dominion sync):

1. A self-scheduled thought can't deliver to a gate, and the *same core*
   blocks any agent-initiated "out-of-bound" message (ping a chat, post
   to a PR) that isn't a reply to a waiting event.
2. `dominion.commit` quietly gave up on push divergence (since addressed
   by the `needs_sync` marker in slice 7b).

Those led to a full pass over the daemon layer and the agent orientation
(`daemon.py`, `runner.py`, `gates/`, `prompts/run.md`,
`prompts/dominion-playbook.md`, the bundle assembly) for drift and
contradiction. Hubs: [`subject-daemon.md`](subject-daemon.md),
[`design-agent-dominion.md`](design-agent-dominion.md),
[`design-self-scheduled-thoughts.md`](design-self-scheduled-thoughts.md),
[`design-multi-response.md`](design-multi-response.md).

## Findings at a glance

| # | Area | What | Status |
|---|------|------|--------|
| 1 | docs/comments | Stale references to retired machinery (kb-maintenance step, superseded design citations, "roadmap to parallel" framing) | fixed this pass |
| 2 | daemon liveness | No prompt-kill on shutdown; `_active_proc` unused; agent has no budget awareness or way to ask for more time | shipped 2026-06-09 |
| 3 | delivery | Reply-shaped delivery blocks out-of-bound + scheduled delivery; schedule thoughts are threadless | shipped 2026-06-09 |
| 4 | ownership | Daemon-owned push/delivery vs a simpler agent-owned generic flow | open question — framing tightened, behavior unchanged |

## 1. Stale references (fixed this pass)

State-first cleanups; the diff is the receipt. Each was a comment or doc
describing machinery the resident reshape retired:

- **kb-maintenance pipeline step.** `daemon.py`'s module docstring listed
  "kb maintenance" as a worker-pipeline stage, and `docs/brr-internals.md`
  did the same in its dev-reload boundary list — while the *same* doc
  elsewhere correctly states the separate kb-maintenance agent was removed
  2026-06-08 (it's now wake-prompt preflight injection). Removed both
  mentions.
- **Superseded design citations.** `design-concurrent-execution.md` is
  `Status: superseded`; its surviving rationale moved to
  [`subject-daemon.md`](subject-daemon.md) and
  [`subject-tasks-branching.md`](subject-tasks-branching.md). Re-pointed the
  citations in `daemon.py`, `gates/runtime.py`, `conversations.py`, and
  `docs/conversations.md`, and reworded "concurrent worker pool" to
  "overlapping thoughts (ad-hoc sessions, a second daemon)" — the
  partitioning's live justification.
- **"Roadmap to parallel" framing.** `docs/brr-internals.md` →
  *Concurrency model* framed single-flight as a temporary v1 limitation
  with a parallel worker pool as the roadmap (citing the abandoned
  `plan-concurrent-worktrees.md`). That contradicts the resident decision
  that single-flight is the intentional end-state (continuity lives in
  durable memory, not throughput-parallel workers). Rewrote to current
  shape and pointed the open concurrency question at finding #4.

## 2. Cooperative liveness contract

**Problem.** The only thing that kills a runner is its own wall-clock
timeout inside `runner._invoke` (`proc.communicate(timeout=…)`), using a
*local* handle. The heartbeat (`_invoke_with_heartbeat`) only ticks
presence and drains the outbox; it never kills. Consequences:

- `brr down` / SIGTERM doesn't kill the in-flight runner — the daemon
  docstring admits it "drains before the process exits", i.e. it waits out
  the (deliberately long) timeout. With a CLI chewing 5–10 min silently,
  shutdown hangs.
- The module-global `runner._active_proc` is set but never read by anyone
  — a vestige of the retired cancellation/command layer.
- The agent has no idea what its budget is, and no way to say "I'm running
  a long command, don't kill me yet."

**Shipped 2026-06-09.** Implemented as below: `runner.kill_active()` is
the cross-thread handle; `_invoke_with_heartbeat` enforces the
extensible, capped deadline and coerces a budget kill to exit 124; the
bundle states the budget and documents the `.keepalive` extension; the
playbook tells the agent to bound long commands; shutdown kills the
in-flight runner. The *silence-based* idle-kill remains deferred (a flat
budget can't separate wedged from healthy-but-silent).

**Decision (wire it up, don't delete it).** Make the daemon heartbeat the
liveness authority and give `_active_proc` a real job:

- **Shutdown kill.** On loop-flag flip / SIGTERM, kill the in-flight runner
  promptly via `runner.kill_active()` (kills `_active_proc` under
  `_proc_lock`) instead of waiting out the timeout.
- **Heartbeat-enforced deadline.** The heartbeat tick tracks an effective
  deadline; past it (absent a valid extension) it kills via the same path.
  The runner-side `communicate(timeout)` stays as a generous absolute
  backstop.
- **Budget injection.** The wake bundle states the wall-clock budget so the
  agent knows what "too long" means.
- **Self-bounding guidance.** The playbook instructs the agent to bound
  uncertain long-running commands (own `timeout`, or background + poll) so
  one command can't silently eat the whole budget.
- **Extension signal.** The agent can drop a keepalive file (path named in
  the bundle, alongside the outbox) carrying an until-time / `+Nm`; the
  heartbeat reads it each tick and pushes the deadline out.

**Cross-env wrinkle.** For the docker backend `_active_proc` is the
`docker run` child; killing it stops streaming and the env's container
teardown removes the container. Handle the kill + cleanup ordering so a
killed docker task doesn't leak a container.

*Context:* the agentic CLIs are trending toward non-blocking / parallel
execution, which would shrink this surface. Keep it proportional.

## 3. Generic delivery + conversation threading

**Problem.** Delivery is reply-shaped. The outbox drain (`_drain_outbox`)
only delivers to (a) the current event, or (b) **another live pending
event** named in `event:` frontmatter — a target that isn't a pending
event is dropped (don't misroute). There is no way to address a *gate +
destination* that has no waiting event. That single rule blocks both
out-of-bound delivery and scheduled delivery (a `source="schedule"` event
is threadless and no gate delivers it). The auth/target plumbing already
exists: gate deliver closures honor a per-event target (e.g.
`telegram_chat_id`) and fall back to a configured default, and
`deliver_stream` already delivers `done` events and cleans up.

**Shipped 2026-06-09.** Both halves landed as designed:
[`schedule.py`](../src/brr/schedule.py) entries now carry an optional
`conversation_key` (default `schedule:<id>`), wired through `_fire_due_schedules`
so a recurring entry's firings thread; and `_drain_outbox` grew a `gate:` branch
(`_gate_can_deliver` + `_deliver_out_of_bound`) that synthesizes an
already-`done` event for the named gate — `protocol.create_event` gained a
`status=` param so the event is born `done` without a pending-window race.
Reserved keys (`id`/`source`/`status`) can't be overridden by agent frontmatter;
unknown/unconfigured gates are dropped with a note. Contract written up in
[`design-multi-response.md`](design-multi-response.md) → *Gate-addressed
delivery*; the playbook + bundle carry the agent wording.

**Decision.**

- **3a — Thread the schedule.** Let a `schedule.md` entry carry a stable
  `conversation_key` so a recurring entry's firings form a thread the agent
  can read back (today firings are threadless; continuity rides only on the
  dominion). Baseline shape is an explicit key on the entry; alternatives
  (a dedicated "self" stream; naming the thread at schedule-time) noted for
  weighing. The point is to give a scheduled / out-of-bound thought the
  user-facing conversation history.
- **3b — Gate-addressed outbox.** Extend the outbox so a file can name a
  `gate:` + target metadata instead of an `event:`. `_drain_outbox`
  synthesizes an **already-`done`** inbox event for that gate carrying the
  metadata, with the body as its response. The existing gate
  `deliver_stream` delivers it (per-event target or configured default) and
  cleans up; being `done` it never triggers a new thought. `event:` stays
  "reply to a waiting thread"; `gate:` becomes "send to a destination".
  Scheduled delivery falls out for free. `run.md` + the playbook gain the
  wording for the new outbox shape.

This `gate:` primitive is also the first concrete step toward the
agent-owned generic flow weighed in finding #4.

## 4. Daemon-vs-agent ownership (open question)

A genuine crossroads, recorded rather than resolved. Today the daemon owns
commit, push, and reply-shaped delivery; the agent works within that.

- **For the current shape:** it was load-bearing when runners were less
  agentic, it's a working shape settled after trial and error, and it keeps
  the agent from having to get git/delivery exactly right every time.
- **For a simpler, agent-owned shape:** daemon-owned push/delivery
  restricts how the agent delivers work and handles interleaving; a thinner
  daemon plus a generic owned flow is more flexible, and the better agentic
  CLIs arriving soon make it more trustworthy. But it's more error-prone
  today, and it trades away the real concurrency that single-flight +
  interleaving only partially replaces — which users may yet ask for.

**Stance for now:** keep the working behavior; fix only the *framing* that
undersells it (the daemon does best-effort push / remote fast-forward and
hands the agent the wheel on divergence — it is not a pure "local
durability floor"). Revisit the larger bet when the CLI tooling lands.
The #3 `gate:` primitive lets us drift toward the agent-owned flow
incrementally instead of via a big-bang rewrite.

*Framing tightened 2026-06-09.* The agent-facing dominion block
(`prompts.py` `_build_dominion_block`) used to read "a local durability
floor … pushing, pulling, and conflict resolution are yours" — which
implied the daemon never pushes. It now states the daemon **commits and
best-effort pushes**, and the agent owns only reconciliation of a
**diverged** remote, matching the playbook (which already said
"brr best-effort pushes"). The other surfaces
([`design-agent-dominion.md`](design-agent-dominion.md) §4,
[`design-self-scheduled-thoughts.md`](design-self-scheduled-thoughts.md),
[`index.md`](index.md)) already named the push and were left as-is.
Behavior unchanged; this was sibling-drift, not a code fix.

**Vestigial concurrency primitives.** Trimming parallel execution to
single-flight left a few in-process primitives that no longer contend
within one daemon: the `ThreadPoolExecutor(max_workers=1)`, the per-branch
`threading` lock around finalize, and (until #2 wires it) the
`_active_proc` handle. They're cheap and harmless, and some are seams a
concurrency revisit would reuse — so whether to remove or keep them rides
with this question rather than being cleaned up piecemeal.
