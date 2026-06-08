# Multi-response protocol — interim + interleaved delivery

Status: in flight — slice 4 of the resident-agent reshape
([`design-agent-dominion.md`](design-agent-dominion.md) §4). Foundation
(streaming delivery) lands first; agent outbox, interleaving, and the
idle-liveness timer layer on top. This page is the protocol contract;
[`subject-daemon.md`](subject-daemon.md) carries the daemon-pipeline
synthesis once it ships.

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

A drop-zone file may target a *different* pending event. Convention: a
file under `.brr/outbox/<other-eid>/`, or a file carrying a minimal
frontmatter (`event: <eid>`, optional `final: true`). When the daemon
drains a `final` response for an event that is **not** the current
task's event, it routes the body to that event's queue and marks that
event `done` itself — the daemon owns inbox status, so the folded-in
event gets delivered and cleaned up without ever being spawned as its
own thought. The resident learns which events are pending from a
**pending-events list** added to the Task Context Bundle.

We **advise** handling separate features / streams of work as separate
spawns (a cross-context change usually wants its own branch and is
cleaner fresh) but don't insist — the resident decides how to organise
its own work.

## Diffense fold

The diffense review pack is already an "agent writes to a known shared
path, daemon picks up" artifact (`.brr/diffense/<task-id>/pack.json`).
It folds into the same drain/promote machinery rather than staying a
bespoke post-run pickup, so there is one mid-flight emission mechanism,
not two. (Sequenced last in the slice.)

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
