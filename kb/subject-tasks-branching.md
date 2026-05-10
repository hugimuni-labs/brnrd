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

For worktree-backed tasks, brr creates `.brr/worktrees/<task-id>/` on a
fresh `brr/<task-id>` branch. The runner starts there. The agent owns
the runtime branching choice:

- commit on the original `brr/<task-id>` branch when the work should
  land automatically;
- switch to a new or existing named branch when the work should be
  preserved separately;
- make no commits for read-only work.

On success, `WorktreeEnv.finalize` reads the final branch state. If the
agent stayed on the original task branch and the base can fast-forward,
brr lands the branch and deletes the throwaway worktree/branch. If the
agent switched branches, detached HEAD, or cannot fast-forward, brr
preserves the branch for human follow-up. The daemon push step follows
that finalization result: folded work pushes the daemon checkout
branch, while a preserved branch is pushed only if it already has an
upstream. Docker uses the same worktree-backed branch contract, with
the runner command executed in a container.

## Branch intent and landing

The current weak point is not that design and research tasks can
commit kb changes; that is intentional. The kb is durable project
memory, and AGENTS.md tells agents to commit material findings,
decisions, and designs. The weak point is that the daemon currently
uses the host checkout's current `HEAD` as both the branch seed and
the auto-land target. That makes durable remote work depend on whatever
branch the operator happened to have checked out when the daemon
processed the event.

The active follow-up design is
[`design-daemon-landing-branch.md`](design-daemon-landing-branch.md).
The direction is to resolve a branch plan mechanically before env prep:
a seed ref for `brr/<task-id>`, an optional auto-land target, the
authority source for that choice, and the host current branch as
context. Authority comes from structured event metadata, existing
thread branch context, source metadata such as PR/task refs, then
policy fallback. The host current branch is context for remote tasks,
not automatic authority, and a fixed `landing_branch=` config is now a
rejected shape because it creates hidden branch authority.

This preserves the "agent owns branching" decision. If the agent stays
on the task branch, brr can fast-forward the resolved target when one
exists or preserve the task branch when no safe target exists. If the
agent switches branches after reading the actual request, finalization
records that git state instead of asking a separate pre-run agent to
predict it.

## Read next

1. [`decision-remove-triage.md`](decision-remove-triage.md) for why
   task construction is mechanical and branching moved to runtime.
2. [`plan-branch-modes.md`](plan-branch-modes.md) for the older design
   history and discarded per-task branch fields.
3. [`design-env-interface.md`](design-env-interface.md) for the env
   protocol and worktree/docker durability contract.
4. [`design-daemon-landing-branch.md`](design-daemon-landing-branch.md)
   for the active branch-intent resolver design.
