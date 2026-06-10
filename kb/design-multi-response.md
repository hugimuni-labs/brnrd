# Multi-response protocol — interim, interleaved + out-of-bound delivery

Status: shipped (interim + interleaved delivery) on 2026-06-09 — slice 4
of the resident-agent reshape
([`design-agent-dominion.md`](design-agent-dominion.md) §4);
**gate-addressed out-of-bound delivery** added 2026-06-09 (coherence
review §3), and the diffense PR finalization fold landed 2026-06-10 by
using that gate-addressed path for forge publication. The
streaming-delivery foundation, the agent outbox + daemon drain,
cross-event interleaving, and gate-addressed sends are live; the remaining
follow-ons are **deliberately deferred** (see "Deferred follow-ons" below).
**Live inflight inbox awareness** landed
2026-06-10: the daemon now refreshes a reserved `inbox.json` control file
in the task outbox on each heartbeat, so newly arrived events are visible
to the running thought at plan boundaries. This page is the protocol contract;
[`subject-daemon.md`](subject-daemon.md) carries the daemon-pipeline
synthesis and [`brr-internals.md`](../src/brr/docs/brr-internals.md)
→ Multi-response the user-facing reference.

## Why

Today's delivery contract is **one event → one final stdout → daemon
captures it → gate delivers once**. The runner blocks on a single
`subprocess.communicate`, so nothing reaches the user until the whole
thought ends. That blocks three things the resident model wants:

- **Interim output.** "Found the bug, fixing now" *before* a long
  stretch, not after. A user who sees the trajectory corrects a bad
  prompt early; a long silence is a worse experience than a short note.
- **Interleaving.** A quick second request needn't wait for the next
  spawn — the in-flight resident folds it in, ships a reply for *that*
  event, and resumes.
- **Liveness.** A finer idle timeout ("no check-in in ~5 min ⇒ assume
  wedged") is only honest once the agent *can* check in mid-run —
  nothing distinguishes a silent-wedged process from a silent-healthy
  one (deep reasoning, a long build) without a check-in. So it is
  sequenced **with** this channel, not before.

## Shape

Additive and backward compatible. The single-response case is
unchanged: the agent prints its final reply on stdout, brr captures it
to `responses/<eid>.md`, the gate delivers it when the event is `done`.
Everything below is extra surface that no-ops when unused.

### Two file regions

- **Agent drop zone** — `.brr/outbox/<eid>/` under the shared brr dir.
  The resident writes interim response files here mid-flight. `.brr/`
  is the same inode inside the sandbox and on the host for both the
  worktree and docker envs (the bind mount is the repo's absolute
  path), so the daemon sees the writes live — the same precedent the
  diffense pack rides on. The agent owns the deliverable-message part of
  this namespace; `inbox.json` is daemon-owned control state, and dotfiles
  are reserved control channels. Neither is promoted as a message.
- **Gate-side queue** — `.brr/responses/<eid>.partials/<seq>.md`,
  ordered, plus the terminal `responses/<eid>.md`. brr's namespace; the
  daemon promotes drop-zone files here, the gate consumes them.

Keeping the two regions separate preserves the existing seam: the agent
*produces* (drop zone), brr *promotes to the gate-facing form* (queue) —
exactly how stdout→`<eid>.md` works today. The agent never needs to know
the gate wire format, sequencing, or which gate is delivering.

### Live inflight inbox

The wake bundle still includes a small snapshot of other pending events
for immediate orientation, but it is no longer the only view. The daemon
also writes `.brr/outbox/<eid>/inbox.json` before invoking the runner and
refreshes it on every heartbeat after draining any outbox replies. The
JSON contains the current event id and the currently pending events
(id, source, status/created metadata, summary, and body), excluding the
event already being processed. The resident re-reads this file at natural
plan / todo boundaries, then decides whether to:

- keep working on the current event;
- fold in a quick pending event by writing an outbox reply with
  `event: <id>`; or
- leave cross-context work pending for its own future wake.

This completes the interleaving contract for events that arrive
mid-thought. It does **not** make the daemon parse agent commands or
pre-claim work; the only state-changing fold-in remains a delivered
`event: <id>` reply, which marks that target event `done`.

### Daemon drain (producer → queue)

The worker blocks on the runner, so the drain hooks the existing
heartbeat tick (every `_HEARTBEAT_INTERVAL`, 30s) **and** runs once more
right after the runner returns (to catch the final writes before
finalize). Each drain:

1. scans `.brr/outbox/<eid>/` for new files, oldest first;
2. promotes each to the target event's partials queue
   (`protocol.write_partial`); the target is the current event by
   default, or another pending event when the file says so (interleaving);
3. emits an `interim_response` progress packet and indexes the artifact
   on the conversation log;
4. removes the consumed drop-zone file;
5. skips daemon-owned control files (`.keepalive`, `inbox.json`) and
   `*.tmp` staging files.

The drain is also the **liveness signal**: a drain that promotes a file
is a check-in. The idle timer (later in the slice) resets on drain
activity, so "silent for N minutes" becomes a real wedge signal rather
than a guess.

### Streaming delivery (queue → user)

`runtime.deliver_stream` (shared by the simple gates; the GitHub gate
reuses the control flow with its own per-message callbacks) replaces the
"only `list_done`" delivery loop. For each **active** event
(`processing` *or* `done`) matching the gate's source, oldest first:

1. deliver each pending partial in order, deleting it after a
   successful send (so delivery is resumable — a transient platform
   error retries from the first undelivered partial next poll);
2. **only when `done`**, deliver the terminal `<eid>.md` and clean up
   the event, the terminal file, and the partials dir.

A `processing` event with no partials is a no-op — exactly today's
behaviour. The terminal always lands last and closes the thread.

## Interleaving (cross-event)

A drop-zone file may target a *different* pending event by carrying a
minimal frontmatter naming it (`event: <eid>`). When the daemon drains a
file whose target is **not** the current task's event, it routes the
body to that event's partials queue and marks that event `done` itself —
the daemon owns inbox status, so the folded-in event gets delivered and
cleaned up without ever being spawned as its own thought. A target that
isn't a live pending event is dropped (don't misroute to a stale
thread). The resident learns which events are pending from the wake-time
pending-events list in the Task Context Bundle plus the live
`inbox.json` refreshed in its outbox during the run.

There is no separate "final" flag: one outbox file is one complete reply
to its target event, so a cross-event reply is terminal for that event
by construction. (An earlier draft floated an optional `final: true`
marker; dropped — folding an event in means answering it, and a
half-answer that wants more work belongs in its own wake.)

We **advise** handling separate features / streams of work as separate
spawns (a cross-context change usually wants its own branch and is
cleaner fresh) but don't insist — the resident decides how to organise
its own work.

## Gate-addressed (out-of-bound) delivery

The two forms above are **reply-shaped**: they deliver to an event that
already exists (the current one, or another pending one). A resident also
needs to *initiate* — ping a chat, post an out-of-bound note, deliver a
scheduled thought's summary — to a destination with no waiting event.

A drop-zone file whose frontmatter names `gate: <name>` (plus any target
fields that gate's deliver closure reads — `telegram_chat_id`, a channel,
a thread — or none, to use the gate's configured default) is an
out-of-bound message. The daemon (`_deliver_out_of_bound`) synthesizes an
already-`done` event for that gate carrying the target metadata and writes
the body as its response. The gate's existing `deliver_stream` picks it up
off `list_active`, delivers it once, and cleans it up; born `done`, it
never spawns a thought. Agent-written frontmatter can't override the
reserved event keys (`id`/`source`/`status`), so a stray `status:` can't
resurrect it as pending. An unknown or unconfigured gate is dropped with a
note — a synthesized event no thread polls would sit forever. `event:` is
"reply to a waiting thread"; `gate:` is "send to a destination" — same
outbox, same drain, one extra branch.

This is also the delivery path for self-scheduled thoughts (a firing that
wants to *say* something writes a `gate:` file; see
[`design-self-scheduled-thoughts.md`](design-self-scheduled-thoughts.md)),
and the first concrete step toward the agent-owned delivery flow weighed
in [`review-daemon-coherence-2026-06.md`](review-daemon-coherence-2026-06.md)
§4.

## Folded follow-ons

### Diffense PR finalization — shipped 2026-06-10

The diffense review pack remains a task-keyed structured artifact
(`.brr/diffense/<task-id>/pack.json`), not a chat partial. The fold landed
at the delivery layer instead: the resident validates and projects the
pack with `brr review`, then writes a `gate: forge` outbox message whose
body is the PR body and whose frontmatter names `head`, `base`, and
`title`. `_deliver_out_of_bound` maps that to the GitHub gate; the GitHub
delivery closure opens or refreshes the PR idempotently.

This preserves the earlier rejection of shoving pack JSON into the chat
queue. The shared mechanism is only "agent writes a ready-to-deliver
message to the outbox"; the pack stays structured until the resident
projects it into a forge-shaped body.

## Deferred follow-ons

Two items remain deliberately deferred. A finer silence-based idle-kill
needs a stronger periodic check-in contract than opportunistic interim
replies. Agent-selected next dispatch and long-running batch claims need
a claim protocol distinct from `event: <id>` replies.

### Agent-selected dispatch / batch claims — deferred

Live `inbox.json` lets a running thought notice new events and fold in
quick replies, but it does not solve idle dispatch selection or claiming
work before a reply is ready. The current durable states are only
`pending`, `processing`, and `done`; the interleaving path changes state
only when the agent has produced a complete reply for a target event. A
real "agent picks next" or "agent batches several waiting events for
longer work" layer needs a separate claim signal plus daemon guard so the
main loop will not spawn claimed events, and so abandoned claims can be
released safely. Until that exists, the daemon remains FIFO when idle and
batching is limited to events answered through the existing outbox
`event: <id>` path.

### Finer idle-liveness timeout — deferred

The hope was that the drain (now a positive check-in) would let a tight
idle timeout replace the generous wall-clock ceiling: "no check-in in N
minutes ⇒ wedged ⇒ kill." But interim replies are **opportunistic** —
a healthy thought doing a long build or a deep stretch of reasoning
legitimately writes nothing to its outbox for a long while. So the
*absence* of a check-in still doesn't separate wedged from
healthy-but-silent, and a hard idle-kill on that signal would
false-positive on exactly the long honest work it's meant to protect.

A safe idle-kill needs a check-in the substrate can **rely on as
periodic** (a heartbeat the agent is obligated to emit, distinct from
opportunistic user-facing replies). Until that contract exists, this
*silence-based* kill stays deferred and the drain serves as an
*informational* liveness signal (enriching the progress card) rather than
a kill trigger. Revisit when there's a reason to add an obligatory agent
heartbeat.

(The flat budget itself is no longer a blunt `communicate` timeout: as of
2026-06-09 it's enforced from the daemon heartbeat, **agent-extensible**
via a `.keepalive` control file, and shutdown kills the in-flight runner.
That's a different axis from the silence-based idle-kill deferred here —
see [`review-daemon-coherence-2026-06.md`](review-daemon-coherence-2026-06.md)
§2.)

## What this is not

- **Not** a daemon command layer. The daemon never parses `/cancel` or
  interprets drop-zone files as instructions — it promotes responses and
  guarantees liveness; cancellation/redirect stays the agent's semantic
  job at plan boundaries (see [`subject-daemon.md`](subject-daemon.md)).
- **Not** mandatory. A thought that prints one final stdout and nothing
  else is still complete and successful — the common case.

## Rejected / deferred

- **Streaming the subprocess stdout/stderr line-by-line** instead of a
  file drop zone. Rejected: it couples delivery to runner stdio framing,
  needs a marker protocol in the agent's *visible* output, and doesn't
  match the diffense precedent. The file drop zone is host-visible,
  gate-agnostic, and survives a crash mid-thought.
- **Letting the agent write the gate-facing queue directly.** Rejected:
  it leaks the wire format + sequencing to the agent and skips the point
  where brr packetizes / indexes / validates each interim response.
