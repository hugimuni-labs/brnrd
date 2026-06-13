# Subject: tasks and branching

Hub page for how brr turns incoming events into tasks, isolates the
work, and decides which branch to publish. This area is spread across
[`task.py`](../src/brr/task.py),
[`worktree.py`](../src/brr/worktree.py),
[`envs/__init__.py`](../src/brr/envs/__init__.py),
[`branching.py`](../src/brr/branching.py), and
[`daemon.py`](../src/brr/daemon.py); this page is the current
synthesis.

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

For worktree-backed tasks, brr resolves a deterministic
`PublishPlan`: seed ref, optional `expected_publish_branch` (when the
event named a target), resolver source string, host checkout branch as
context, and an optional `expected_remote_oid` captured from the
remote-tracking ref at task start for force-with-lease pushes. It then
creates `.brr/worktrees/<task-id>/` on a fresh `brr/<task-id>` branch
from the seed ref. The agent owns the runtime branching choice:

- commit on the original `brr/<task-id>` branch when the plan is
  right;
- switch to a new or existing named branch when the task body — or
  the recent conversation context the prompt includes — overrides the
  plan;
- make no commits for read-only work.

When a structured event names a target branch, env prep normally
switches the new worktree onto that local branch before the agent
starts. If Git reports that the target is already checked out in
another worktree, env prep deliberately keeps the collision-free
`brr/<task-id>` branch instead, records a branch-setup notice on the
task, and prints the fallback. Because the task branch was sprouted
from the resolved seed ref (preferring `origin/<target>` when present,
otherwise the local target), the agent still starts at the target tip;
if it commits there, the publish kernel's refspec arm pushes the unique
local branch to the event's target without touching the checked-out
local ref.

On success, `WorktreeEnv.finalize` reads the worktree's final git
state, classifies it into a `publish_status`, and records the branch
to publish. Finalize never touches a non-task ref. Worktree teardown
is outcome-aware: clean `ready` with no uncommitted files tears the
worktree down (the branch ref plus traces are the durable artefact);
detached HEAD or uncommitted leftovers keep the worktree alive so the
operator can inspect what the agent left behind. Docker uses the
same worktree-backed branch contract, with the runner command
executed in a container.

## Publishing and the publish kernel

The agent leaves work on a branch. The daemon publishes that branch.
That's the kernel — finalize classifies, `daemon.publish` ships the
branch. Pull-request open/refresh is now a separate agent-owned delivery
step: for diffense-backed review, the resident projects the pack and
sends `gate: forge`, which the GitHub gate turns into an idempotent PR
create/update.
Pull-side freshness (so a follow-up task seeds from the previous
task's publish) lives in [`sync.py`](../src/brr/sync.py); publishing
is one step.

The resolver's order of operations is unchanged from the predecessor
design: structured event branch fields (`branch_target`,
`target_branch`, `base_branch`, then legacy `branch`) name the
`expected_publish_branch` and the seed. When a remote-tracking ref
exists for that target, brr seeds from `<remote>/<target>` so a runner
starts from the forge-visible branch even if the daemon's local copy
diverged. Without a structured target, `branch.fallback=preserve`
seeds from the repo default branch (falling back to host `HEAD`) and
records no expected publish target; the committed task branch is
preserved and pushed under its own name. The previous `current`
fallback (and the long-defunct `inbox` / `default`) was removed when
the publish kernel collapsed the local-land path; legacy config values
warn once and downgrade to `preserve`.

The host's current branch travels into the prompt as context but is
never treated as a publish target — agents need to know which branch
the user was looking at, but the resolver doesn't infer publishing
intent from it.

Finalize produces one of four `publish_status` values:

- `ready` — the task branch has commits; `publish_branch` names what
  to ship (the original `brr/<task-id>` if the agent stayed put, or
  the agent's chosen branch otherwise).
- `nothing` — agent stayed on the task branch and made no commits;
  task branch is deleted along with the worktree.
- `detached` — agent left HEAD detached; worktree is kept for the
  operator to recover the commits.
- `conflict` — emitted by `daemon.publish` when the push itself
  failed, so gates render the delivery failure instead of celebrating
  a successful run.

`daemon.publish` chooses one of five mutually exclusive arms by
reading `task.meta["publish_branch"]`,
`task.meta["expected_publish_branch"]`, and
`task.meta["expected_remote_oid"]`:

| Arm | When | Behaviour |
| --- | ---- | --------- |
| noop | no publish branch, no new commits, or no remote | skip |
| plain | publish branch has upstream, name matches | `git push origin <branch>` |
| upstream | new local branch, name matches | `git push -u origin <branch>` |
| refspec | agent kept `brr/<task-id>` but event named a different `expected_publish_branch` | `git push origin brr/<task-id>:<expected>` |
| lease | publish branch equals expected publish branch *and* `expected_remote_oid` is set *and* local is not an ancestor of `origin/<branch>` (i.e. agent rewrote history — the PR-rebase case) | `git push --force-with-lease=<ref>:<oid> origin <branch>:<ref>` |

Other branches stay ordinary pushes; brr does not grow a general
"force whatever changed" path.

## Cross-task freshness

The local-land path that earlier shapes ran before pushing only ever
advanced the *host* checkout's local ref. Removing it doesn't lose
freshness because the remote was authoritative anyway: every gate
routes through it. `sync.refresh_before_task` already fetches origin
and best-effort fast-forwards before the resolver runs, and the
resolver prefers `<remote>/<target>` when present, so a follow-up task
seeds from the previous task's published state even when the
operator's local copy never moved.

## Read next

1. [`design-publish-kernel.md`](design-publish-kernel.md) for the
   accepted publish kernel design, decision tables, and the lineage
   from the predecessor landing-branch design.
2. [`decision-remove-triage.md`](decision-remove-triage.md) for why
   task construction is mechanical and branching moved to runtime.
3. [`subject-envs.md`](subject-envs.md) for the env protocol,
   durability contract, and salvage rule the worktree/docker
   finalizers implement; [`design-env-interface.md`](design-env-interface.md)
   for the underlying design.
4. [`subject-daemon.md`](subject-daemon.md) for the daemon's
   concurrency model and per-branch publish lock.
5. [`design-daemon-landing-branch.md`](design-daemon-landing-branch.md)
   (superseded 2026-05-21) for the predecessor landing-branch design
   notes; read only for the rationale of the constraints the kernel
   inherits.
6. [`plan-branch-modes.md`](plan-branch-modes.md) for the older design
   history and discarded per-task branch fields.
7. [`research-branch-plan-simplification-2026-05-12.md`](research-branch-plan-simplification-2026-05-12.md)
   for the resolver critique whose follow-through landed in the
   2026-05-21 publish-kernel collapse.
