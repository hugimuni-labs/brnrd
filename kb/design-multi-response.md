# Multi-response protocol — interim, interleaved + out-of-bound delivery

Status: shipped (interim + interleaved delivery) on 2026-06-09 — slice 4
of the resident-agent reshape
([`design-agent-dominion.md`](design-agent-dominion.md) §4);
**gate-addressed out-of-bound delivery** added 2026-06-09 (coherence
review §3). The streaming-delivery foundation, the agent outbox + daemon
drain, cross-event interleaving, and gate-addressed sends are live; two
follow-ons are **deliberately deferred** (see "Deferred follow-ons"
below): folding the diffense pack into this drain, and a finer
*silence-based* idle-kill. This page is the protocol contract;
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
  diffense pack rides on. The agent owns this namespace.
- **Gate-side queue** — `.brr/responses/<eid>.partials/<seq>.md`,
  ordered, plus the terminal `responses/<eid>.md`. brr's namespace; the
  daemon promotes drop-zone files here, the gate consumes them.

Keeping the two regions separate preserves the existing seam: the agent
*produces* (drop zone), brr *promotes to the gate-facing form* (queue) —
exactly how stdout→`<eid>.md` works today. The agent never needs to know
the gate wire format, sequencing, or which gate is delivering.

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
4. removes the consumed drop-zone file.

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
thread). The resident learns which events are pending from a
**pending-events list** added to the Task Context Bundle.

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

## Deferred follow-ons

Two items were scoped into slice 4 and then deliberately deferred — the
core (interim + interleaved delivery) ships without them because each
buys little today and carries real risk.

### Diffense fold — deferred

The diffense review pack is also an "agent writes a known shared path,
daemon picks up" artifact (`.brr/diffense/<task-id>/pack.json`), so the
tempting unification was to route it through this same drain. We didn't,
because the two are different artifacts doing different jobs:

- diffense is **task-keyed**, the chat queue is **event-keyed**;
- diffense is consumed **once, at PR finalization**, to shape a PR
  *body* — not streamed to a chat thread mid-thought;
- it is structured JSON a forge step renders, not a ready-to-send
  markdown message.

Folding it in would mean reshaping it into a chat-reply at the exact
moment it's least chat-shaped, to share a code path it doesn't actually
want. The genuine commonality — "agent writes, daemon picks up" — is
already the shared *pattern*; collapsing them into one *mechanism* is
cosmetic and risks the load-bearing PR-body flow. Left as-is.

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
