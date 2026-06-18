# Design: Concurrent task execution via partitioned state

Status: superseded by [`design-agent-dominion.md`](design-agent-dominion.md) on 2026-06-08.

The threaded daemon loop this doc accepted is reversed by the resident-agent
reshape: local execution is now **single-flight** (spawn one thought when idle),
because a resident agent's continuity lives in durable memory, not in
throughput-parallel workers. What **survives** and is not superseded: the
per-run worktree / branch isolation and the partitioned per-event/per-run state
(per-event jsonl conversations, per-run progress json) — those primitives
predate this doc and now anchor in
[`subject-runs-branching.md`](subject-runs-branching.md) and
[`subject-daemon.md`](subject-daemon.md). Only the *concurrency-via-threading*
thesis retires.

Replaces the deferred-serial pose and the abandoned merge-coordinator
shape from
[`plan-concurrent-worktrees.md`](plan-concurrent-worktrees.md). The
plan's per-run worktree/branch/env primitives still hold; what
changes is the daemon loop (now threaded), the conversation layer
(now per-event jsonl), and the gate progress state (now per-run
json). The accepted shape ships these together because the threading
work is honest only when the shared mutable surfaces it would have
locked are removed instead.

## Why now

The deferred-serial decision held while there was a single operator
and most tasks ran to completion before the next event arrived. Two
forces tip it:

- Several configured gates (Telegram + Slack + GitHub) means a single
  burst can deposit multiple unrelated events. Serial execution
  serialises latency for every gate behind the slowest task, even
  when the tasks have no shared scope.
- Long-running CLI runs (codex with xhigh reasoning routinely 5-10
  min) make the head-of-line blocking visible. A "quick question"
  posted while a long task is in flight waits behind it.

The pre-existing isolation primitives — per-run worktrees and
branches, per-event response files, env backends — already paid the
cost of partition-by-task. The remaining shared surfaces were
incidental aggregation in the conversation log and gate progress
files, not deliberate single-writer designs.

## The partitioning rule

A worker thread for one event/task pipeline must only write to files
that name that task or event. Files keyed on conversation, gate, or
branch are read-only from the worker's perspective unless they live
under a per-resource lock. Concretely:

| Resource | Path | Writer scope |
|---|---|---|
| Run manifest | `.brr/runs/<run-id>/run.md` | one worker (this run) |
| Event spec | `.brr/inbox/<event-id>.md` | one writer (gate or daemon stamping status) |
| Response | `.brr/responses/<event-id>.md` | one writer (the runner) |
| Trace dir | `.brr/traces/<run-id>-<label>/` | one worker (this run) |
| Conversation record stream | `.brr/conversations/<key>/<event-id>.jsonl` | one worker (the pipeline for this event) |
| Gate progress card state | `.brr/gates/<gate>/progress/<run-id>.json` | one worker (the pipeline for this run) |
| Worktree | `.brr/worktrees/<run-id>/` | one worker (this run) |
| Local branch ref `brr/<run-id>` | git ref | one worker (this run) |
| Local branch ref for auto-land target | git ref | per-branch lock |
| Remote branch ref under push | git ref | per-branch lock |
| Daemon PID, gate inbox cursor state | various | single writer (one daemon, one gate thread per gate) |

Two workers can pick concurrently from the inbox, run concurrently,
emit concurrently, push concurrently to *different* branches, and
neither needs to acquire any lock the other could contend on. Two
workers landing on the same auto-land target serialise on a
per-branch lock keyed to that branch name; same for pushes that
happen to target the same branch.

## Conversation layer change

Before: `.brr/conversations/<key>.ndjson` — one aggregated file per
conversation key, written from every worker whose event mapped to
that key. Two concurrent workers for the same chat would interleave
writes on the same file.

After: `.brr/conversations/<key>/<event-id>.jsonl` — one jsonl per
pipeline run, sitting in a directory named after the conversation
key. The writer for that file is the worker handling that one event.
Records are jsonl with `ts` (microsecond-precision ISO 8601) plus the
existing `kind` discriminator and type-specific fields. Single-line
writes use `O_APPEND` in binary mode so the kernel guarantees the
offset move and the byte write happen atomically together — within
one file there's exactly one writer anyway, so this is defence in
depth more than a strict requirement.

Read paths:

- `read_records(brr_dir, key)` globs `<key>/*.jsonl`, parses each
  file's lines, merges sorted by `ts`. Stable for projection because
  records carry strictly increasing timestamps within a file (single
  writer) and the projection cares about chronological order across
  files for context.
- `read_recent(brr_dir, key, limit)` returns the last *limit* records
  by ``ts`` using a heap merge over per-file reverse line iteration, so
  prompt tails do not read every line of every jsonl when only a short
  window is needed. ``limit <= 0`` falls back to a full ``read_records``
  merge (same as "give me everything").
- `records_for_run(brr_dir, key, run_id)` benefits from the per-
  event layout: we read just `<event-id>.jsonl` (when we can map
  task id → event id) instead of filtering across all conversation
  records.
- `project_task` and `project_conversation_latest` in
  `run_progress.py` consume the same merged list.

The conversations bundled doc
([`src/brr/docs/conversations.md`](../src/brr/docs/conversations.md))
gets a layout-update paragraph but keeps its current framing
(per-gate-thread context that compounds for in-conversation memory).

## Gate progress state change

Before: `.brr/gates/telegram_progress.json` (and `slack_progress.json`)
— one file holding `{run_id: {...}}` for every in-flight task on
that gate. Render path was load → mutate → save, no lock.

After: `.brr/gates/<gate>/progress/<task-id>.json` — one file per
task. The render path reads and writes only the file for the task
whose packet it is rendering. With one worker per task and packets
for distinct tasks routed by `run_id`, two concurrent renders
touch two distinct files. No locks.

Helpers (`_load_progress_state`, `_save_progress_state`,
`_progress_key`) are removed in favour of `_load_progress_for_task`
/ `_save_progress_for_task` / `_delete_progress_for_task` (the
delete path is new — once a task reaches a terminal state the file
becomes inert disposable cache and can go).

## Packet flow change

`updates.UpdatePacket` gains an explicit `event_id` field
(separate from payload) so `conversations.append_update` knows which
per-event jsonl file to write to. `daemon._run_worker` defines a
closure `_emit(packet_type, **payload)` that fills in
`conversation_key` and `event_id` from the worker scope; existing
emit sites become one-liners instead of multi-line `UpdatePacket(...)`
constructions.

This also makes the on-disk record cleaner: every record carries
`event_id` either via the file it lives in or via an explicit field
on the record body. The projection code stops needing to deduce
event scope from inconsistent payloads.

## Threaded daemon loop

The serial body in `daemon.start()` becomes:

```python
pool = ThreadPoolExecutor(max_workers=max_workers)
futures: dict[Future, dict] = {}

while running:
    events = protocol.list_pending(inbox_dir)
    for event in events:
        if event["_path"] in {f.event_path for f in futures}:
            continue  # already in flight
        if len(futures) >= max_workers:
            break
        protocol.set_status(event, "processing")
        fut = pool.submit(_run_worker_and_finalize, event, ...)
        futures[fut] = event

    # drain completed futures (non-blocking)
    for fut in [f for f in futures if f.done()]:
        ...  # handle reload flag, log errors, free slot

    time.sleep(_SCAN_INTERVAL)
```

`_run_worker_and_finalize` is the existing `_run_worker` body plus
the post-task `protocol.set_status`, `_push_if_needed`, and
dev-reload-flag check that previously lived in the main loop. The
worker thread owns the full pipeline for its event.

`max_workers` reads from `.brr/config` (`max_workers=4` default).
Setting `max_workers=1` exactly reproduces serial-v1 behaviour for
adopters who don't want concurrency; setting it higher trades RAM
and forge API quota for parallelism.

## Per-branch locks

A small `_branch_lock(name) -> threading.Lock` helper backed by a
module-level `defaultdict(threading.Lock)` serves two call sites:

- `WorktreeEnv._land_or_preserve`'s call to
  `gitops.fast_forward_branch(repo_root, target, source, ...)` —
  hold the lock on `target` across the call.
- `daemon._push_if_needed`'s `git push` — hold the lock on
  `branch_name` across the push.

Two workers landing on different targets or pushing different
branches never contend. Two on the same target serialise; whichever
loses the race finds the target advanced and either fast-forwards on
top of it cleanly or registers a preserved-branch outcome as today.

## dev_reload quiescence

The reload watcher is unchanged. The integration point moves: after
each worker finishes (in `_run_worker_and_finalize`), it checks
`watcher.changed()` and sets a daemon-level `reload_requested` flag
if true. The main loop's idle path checks `reload_requested and not
futures` and re-execs only when the pool is empty. This preserves the
"quiescent-only" guarantee from
[`design-daemon-dev-reload.md`](design-daemon-dev-reload.md) under
concurrency: no in-flight worker ever has the process replaced
underneath it.

If the reload flag is set but new events keep arriving, the daemon
stops accepting them on subsequent loop iterations (the dispatch
loop respects the flag) so the pool can drain. This bounds reload
latency to "longest-running in-flight task at flag-set time".

## Shutdown and signals

`brr down` → `SIGTERM` → loop flag flips to `running=False`. The
dispatch loop exits, then waits on the pool to drain with a generous
timeout (existing tasks should not be cancelled). On force-kill
(SIGKILL or process death), the existing recovery semantics still
apply: `set_status("processing")` events on next start are picked up
again, traces and preserved worktrees remain available for forensic
inspection.

## What this design rejects

- **A merge coordinator.** The original 2026-04 plan had a serialized
  merge stage after concurrent runs. That added a fork/join point
  the system had no other use for, and concentrated merge logic
  outside the worker that produced the commits. Per-branch ff locks
  put the synchronisation point exactly where the contention lives.
- **Locking the existing aggregated `<key>.ndjson`.** A lock would
  have worked, but every appender path becomes a critical section,
  the readers have to coordinate, and the disk format keeps an
  obfuscated multi-writer file. Partitioning is honest.
- **An async/await rewrite.** Stdlib threads with a thread-pool
  executor fit the existing sync-IO codebase and the "runner is a
  subprocess" reality. Asyncio would only pay off for I/O-bound
  fan-out, which isn't where the work is.
- **Per-run subprocess workers.** The runner itself already runs as
  a subprocess inside each task; an extra layer of process isolation
  doesn't earn its operational cost for this scale.

## Migration

There are no users persisted across this change. New `.brr/` state
created after this design lands uses the new layout exclusively. Old
`<key>.ndjson` files and old `<gate>_progress.json` files become
inert (no readers); they can be deleted by hand or left as
historical artefacts. No code path reads them.
