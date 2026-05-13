# Subject: tasks and branching

Hub page for how brr turns incoming events into tasks, isolates the
work, and decides where committed work lands. This area is spread
across [`task.py`](../src/brr/task.py),
[`worktree.py`](../src/brr/worktree.py),
[`envs/__init__.py`](../src/brr/envs/__init__.py), old branch plans,
and the triage-removal decision; this page is the current synthesis.

## Current shape

Tasks are mechanical. An event from a gate becomes `Task.from_event`
with no LLM triage step. The task carries the event body, source,
conversation key, resolved environment, status, and runtime metadata.
The reasoning for removing LLM-based task routing lives in
[`decision-remove-triage.md`](decision-remove-triage.md); the older
branch/task design history lives in
[`plan-branch-modes.md`](plan-branch-modes.md).

The daemon resolves the execution environment from `.brr/config` and
the event metadata. `environment=auto` means Docker when a Docker image
is configured, otherwise a git worktree. `host` is explicit only. That
choice keeps remote runs isolated by default without asking an LLM to
classify "small" versus "large" tasks ahead of time.

For worktree-backed tasks, brr first resolves a deterministic branch
plan: seed ref, optional auto-land branch, resolver source string,
host checkout branch as context, and expected old OID for safe
fast-forwards. It then creates `.brr/worktrees/<task-id>/` on a fresh
`brr/<task-id>` branch from the seed ref. The agent owns the runtime
branching choice:

- commit on the original `brr/<task-id>` branch when the branch plan is
  right;
- switch to a new or existing named branch when the task body — or the
  recent conversation context the prompt includes — overrides the plan;
- make no commits for read-only work.

On success, `WorktreeEnv.finalize` reads the final branch state and
always tears down the worktree directory; persistent inspection rides
on the branch ref and trace dirs, not on a live worktree pinning the
branch. If the agent stayed on the original task branch and an
auto-land target exists, brr fast-forwards that target and deletes the
throwaway branch. If no target exists, brr preserves the task branch
for human routing and publishes it when a remote is configured. If the
agent switched branches, detached HEAD, or cannot fast-forward the
target, brr preserves the branch for human follow-up. Docker uses the
same worktree-backed branch contract, with the runner command executed
in a container.

## Branch intent and landing

The seed ref comes from the configured `auto_land_branch`, then the
event's `target_branch` metadata, then a fixed config seed, then the
host checkout's current branch as last resort. Auto-land target comes
only from structured event metadata; when absent, brr preserves the
task branch without trying to infer a target.

The host's current branch travels into the prompt as context but is
never treated as an auto-land target — agents need to know what
branch the user was looking at, but the resolver doesn't infer
landing intent from it.

If the agent stays on the task branch and an explicit auto-land
target was set, brr fast-forwards it. If no target exists, brr
preserves the task branch. If the agent switched branches or detached
HEAD, finalization records whatever git state was left and pushes the
agent-chosen branch.

The full design and the design's lineage live in
[`design-daemon-landing-branch.md`](design-daemon-landing-branch.md);
the most recent rewrite (2026-05-12) trimmed the resolver to
event-metadata-only branch authority — see
[`research-branch-plan-simplification-2026-05-12.md`](research-branch-plan-simplification-2026-05-12.md)
for the rationale.

## Read next

1. [`decision-remove-triage.md`](decision-remove-triage.md) for why
   task construction is mechanical and branching moved to runtime.
2. [`plan-branch-modes.md`](plan-branch-modes.md) for the older design
   history and discarded per-task branch fields.
3. [`subject-envs.md`](subject-envs.md) for the env protocol,
   durability contract, and salvage rule the worktree/docker
   finalizers implement; [`design-env-interface.md`](design-env-interface.md)
   for the underlying design.
4. [`design-daemon-landing-branch.md`](design-daemon-landing-branch.md)
   for the accepted branch-intent resolver design and remaining future
   source-metadata expansion points.
5. [`research-branch-plan-simplification-2026-05-12.md`](research-branch-plan-simplification-2026-05-12.md)
   for a follow-up critique of the current resolver surface, especially
   the recommendation to keep mechanical landing defaults while demoting
   inferred conversation branch history to runner context.
