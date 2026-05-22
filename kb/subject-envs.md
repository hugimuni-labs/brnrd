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

Tasks running in an isolated env run in an **ephemeral** location.
The only outputs that survive are git refs and the response file on
the host. Trace artefacts and per-task scratch (worktree directory,
container, remote scratch dir) are env territory and get torn down on
clean completion — see the salvage rule below for the exact
conditions.

The daemon enforces the contract from the host: after `finalize()`
returns, it checks that the response file exists at
`response_path_host` and the promised branch is reachable in the
host's git. It does not inspect the env's internals. That keeps the
protocol observable and the same shape for plugins.

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

There is no central merge coordinator. `WorktreeEnv.finalize`
classifies the final worktree state into `publish_status` and
`publish_branch`; `daemon.publish` then publishes that branch in one
step. The env layer never fast-forwards a non-task ref. Push conflicts
flip the task to `publish_status=conflict` with the branch preserved;
the next human or agent run owns the resolution.

The full branch-resolution logic lives one subject over in
[`subject-tasks-branching.md`](subject-tasks-branching.md) and
[`design-publish-kernel.md`](design-publish-kernel.md); the env's job
is to classify the workspace state and preserve scratch when the
salvage rule says to.

## Read next

1. [`design-env-interface.md`](design-env-interface.md) for the full
   protocol, the per-env mechanics, the response-path split, the
   plugin / script-env model, and the configuration surface.
2. [`subject-tasks-branching.md`](subject-tasks-branching.md) for how
   the daemon resolves publish plans feeding into `Env.finalize` and
   `daemon.publish`.
3. [`plan-concurrent-worktrees.md`](plan-concurrent-worktrees.md) for
   the original "one task per worktree" reasoning that informed the
   current worktree env shape; the merge-coordinator path it sketched
   was abandoned in favour of the decentralised model above.
4. [`src/brr/docs/envs.md`](../src/brr/docs/envs.md) for the
   user-facing reference: when to pick each env, configuration keys,
   troubleshooting.
5. [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §10 for the
   plugin candidates (Daytona, Firecracker, E2B) that would ride on
   the entry-point mechanism once the registry surface is wired.
