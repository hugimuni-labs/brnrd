# Review: Concurrency / Merge Coordinator Follow-up

Date: 2026-04-14

## Summary

Second review pass after the triage wiring change.

The current codebase has the **task abstraction** needed for concurrent
execution, but it still does **not** implement the planned concurrent runner
architecture. In particular, there is no `worktree.py`, no `pool.py`, no merge
coordinator, and `daemon.py` still runs exactly one worker at a time in the
main repo checkout.

This means:

- branch/env decisions now exist and are persisted on `Task`
- `needs_context` is implemented coherently as a task outcome
- concurrent execution itself is still future work
- cancellation should remain future work too, because it only becomes valuable
  once multiple long-running workers exist

## Evidence

### What is implemented

- `src/brr/task.py` persists branch/env/status on `Task`
- `src/brr/daemon.py` now performs triage before execution
- `src/brr/runner.py` supports extra prompt metadata such as alternate log file
  paths

### What is still missing

- `src/brr/worktree.py` does not exist
- `src/brr/pool.py` does not exist
- `src/brr/env.py` does not exist
- `src/brr/gitops.py` has no merge/worktree helpers
- `src/brr/status.py` has no pool/worktree visibility
- `daemon.py` still processes `events[0]` only, then waits for that worker to
  finish before touching the next event

## Clarification: what "concurrent execution" means here

In the plan, concurrency is not "triage happens before run" and it is not
"multiple gate threads writing inbox files". It means:

1. the daemon can have multiple active task executions at once
2. each execution runs in an isolated environment, usually a git worktree
3. completed branches are merged back sequentially by a merge coordinator
4. conflicts are handled at merge time instead of by preventing parallelism up
   front

Today the daemon remains serial, so none of that runtime behavior exists yet.

## Review findings

### 1. Merge coordinator is not implemented yet

This is the main conclusion.

The plan defines the merge coordinator as a concrete phase and names new modules
for it, but the codebase does not contain those modules yet. The current daemon
still says "serial v1" and executes a single `_run_worker(...)` call inline.

Conclusion: the merge coordinator is not something to review for correctness
yet; only the design is reviewable today.

### 2. The current code is coherent as scaffolding, but not yet minimal if it
is described as concurrency

The existing changes are coherent if framed as:

"prepare the daemon for later concurrent execution by introducing task triage,
branch/env metadata, and `needs_context` handling."

They are not coherent if framed as:

"implement concurrent worktree execution."

That distinction matters because the architecture in the plan has a large
runtime boundary that is still absent.

### 3. Cancellation should stay out of scope for now

Cancellation is harder than a prompt tweak once multiple workers exist:

- you need task-to-process ownership
- you need safe handling for worktree cleanup
- you need task/event state transitions that distinguish cancelled vs error
- you need a policy for partial changes and branch preservation
- you likely need gate-side UX for targeting the right running task

Given the current code is still serial, cancellation adds very little practical
value right now. It becomes much more justified after the pool/worktree path is
real, because that's when "stop one long-running worker while others continue"
is actually useful.

## Recommendation

Keep the next slice small:

1. implement `worktree.py` with create/remove/list primitives only
2. teach `daemon.py` to execute one task in a worktree when `task.needs_worktree`
3. add merge-back as a narrow, explicit helper
4. only then introduce a small `WorkerPool`
5. defer cancellation until after that path is working and tested

This keeps "minimal code" credible: add the smallest execution path that makes
`branch` and `env` real, then add parallel dispatch on top.
