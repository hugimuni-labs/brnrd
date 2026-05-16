# Subject: environments

Hub page for how brr runs tasks in different execution contexts — the
host checkout, a git worktree, or a Docker container. The accepted env
design also sketches ssh, devcontainer, and plugin/script backends, but
the implementation currently wired in
[`envs/__init__.py`](../src/brr/envs/__init__.py) ships only the three
built-ins named below. This page is the current synthesis of the
protocol, the durability contract, and the salvage rule that hangs off
it.

## Current shape

Every environment implements the same three-phase `Env` Protocol:
`prepare → invoke → finalize`. The daemon picks the env mechanically
from `.brr/config` and event metadata (`environment=auto` resolves to
`docker` when an image is configured, otherwise `worktree`; `host` is
explicit), then asks the env to set up a workspace, run the runner, and
finalize. Branch resolution and trace handling are env-agnostic and
happen above the protocol; everything filesystem- or transport-specific
lives behind it.

Three envs ship today: `host`, `worktree`, and `docker`. `ssh`,
`devcontainer`, Python entry points (`brr.envs`), and drop-in script
envs in `.brr/envs/<name>/` / `~/.config/brr/envs/<name>/` are accepted
design surface, not wired runtime backends; `get_env()` rejects them
until that registry work lands.

Docker wires runner credentials from the host at invocation time. For
GitHub-originated tasks, brr can inject the GitHub gate token (stored,
environment-provided, or resolved through `gh auth token`) and configures
in-container git to rewrite common GitHub SSH remote forms to HTTPS with
a token-backed credential helper. That gives runner agents a working
`git push` path for PR/rebase work even when no SSH agent is available
inside the container.

## Durability contract

Tasks running in a non-`host` env run in an **ephemeral** location.
The only outputs that survive are git refs and the response file on
the host. Trace artefacts and per-task scratch (worktree directory,
container, remote scratch dir) are env territory and get torn down on
clean completion — see the salvage rule below for the exact
conditions.

The daemon enforces the contract from the host: the response path it
validates is `response_path_host`, and branch/scratch outcomes are
recorded as task metadata by `finalize()`. It does not inspect the
env's internals. That keeps the protocol observable and the same shape
for plugins.

## Salvage rule

Env scratch is outcome-aware. On clean `status=done` with nothing
uncommitted left in the worktree, brr tears down the worktree,
container, or remote scratch dir. On `status ∈ {error, conflict}`, or
when the worktree has untracked/unstaged files, brr preserves the
scratch state so the user can inspect or salvage. Persisted task
metadata records the preserved location in `task.meta`.

Traces follow the same rule: removed on clean done, kept on
error/conflict so the failure is debuggable.

## Decentralised merging

There is no central merge coordinator. Each `finalize` attempts a
fast-forward of the resolved auto-land branch when one is set and the
agent stayed on the task branch; everything else (named branches,
detached HEAD, missing auto-land target) preserves the agent's branch
choice for human routing. Conflicts park the task at
`status=conflict` with the branch preserved; the next human or agent
run owns the resolution. Branches commute well in git, so brr just
orchestrates `git merge --ff-only` calls and falls back to "leave it
for a human" when an ff merge isn't trivial.

The full branch-resolution logic lives one subject over in
[`subject-tasks-branching.md`](subject-tasks-branching.md) and
[`design-daemon-landing-branch.md`](design-daemon-landing-branch.md);
the env's job is to apply that decision inside its workspace.

## Read next

1. [`design-env-interface.md`](design-env-interface.md) for the full
   protocol, the per-env mechanics, the response-path split, the
   accepted-but-pending plugin / script-env model, and the
   configuration surface.
2. [`research-stdlib-dependency-policy-2026-05-16.md`](research-stdlib-dependency-policy-2026-05-16.md)
   for the current dependency-policy review: keep core dependency-free
   by default, allow explicit edge/plugin dependencies when they delete
   real complexity, and reconcile the env plugin promise with the
   shipped `get_env` implementation.
3. [`subject-tasks-branching.md`](subject-tasks-branching.md) for how
   the daemon resolves seed refs and auto-land targets feeding into
   `Env.finalize`.
4. [`plan-concurrent-worktrees.md`](plan-concurrent-worktrees.md) for
   the original "one task per worktree" reasoning that informed the
   current worktree env shape; the merge-coordinator path it sketched
   was abandoned in favour of the decentralised model above.
5. [`src/brr/docs/envs.md`](../src/brr/docs/envs.md) for the
   user-facing reference: when to pick each env, configuration keys,
   troubleshooting.
6. [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §10 for the
   plugin candidates (Daytona, Firecracker, E2B) intended to ride on
   the registry surface once it is wired.
