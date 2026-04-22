# Design: Env Interface (PR scope)

Focused, executable design for the in-flight worktree PR: extract the
`Env` Protocol, codify the durability contract, add `docker` and `ssh`
built-ins, decentralise merging. The merge of this PR is the unlock for
treating environments as the main brr value proposition.

This page is **tactical**. Strategic context lives in
`deck-brr-fleet-steering.md`. Open items the PR doesn't touch
(overlays, brnrd, discovery, cross-platform supervisor) live in
`notes-pondering-fleet.md`.

---

## Goals (what this PR ships)

1. **`Env` Protocol** — single abstraction with three phases:
   `prepare → invoke → finalize`.
2. **Four built-ins** behind it: `local`, `worktree`, `docker`, `ssh`.
   All tested. All documented in `src/brr/docs/`.
3. **Durability contract** — explicit, enforced by the daemon.
4. **Decentralised "coordinator"** — branch-and-PR is the model;
   merging is a thin best-effort post-task step, not a component.
5. **Plugin point** — third-party envs via Python entry points.

Non-goals: actual concurrent execution, overlays, `brnrd`, env-specific
secret handling beyond what gates already do.

---

## The Env Protocol

```python
# src/brr/envs/__init__.py

from typing import Protocol
from pathlib import Path
from dataclasses import dataclass

@dataclass
class RunContext:
    """Per-task handle returned by Env.prepare()."""
    cwd: Path                  # where the runner should be invoked
    repo_root: Path            # the host repo (always)
    branch: str | None         # the branch the agent will commit on
    response_path: Path        # where the response file ends up on the host
    runtime_dir: Path          # host's .brr/ (read-only mount in remote envs)
    env_state: dict            # opaque to brr; env may stash anything here

@dataclass
class FinalizeReport:
    """What the env actually produced. Daemon checks this against the contract."""
    branch_pushed: bool        # branch ref reachable from host's git
    commits: int               # commit count on the branch (0 = no work)
    response_written: bool     # response_path exists on host
    notes: str = ""            # free-form, surfaced in `brr inspect`

class Env(Protocol):
    name: str                  # "local" | "worktree" | "docker" | …

    def validate(self, cfg: dict) -> None: ...
    def prepare(self, task: Task, repo_root: Path, cfg: dict) -> RunContext: ...
    def invoke(self, ctx: RunContext, prompt: str, cfg: dict) -> RunnerResult: ...
    def finalize(self, ctx: RunContext, task: Task, *, debug: bool) -> FinalizeReport: ...
```

`invoke` keeps returning `RunnerResult` so the existing trace / retry
plumbing in `runner.invoke_runner` is reused unchanged. Envs are
typically thin wrappers around `runner.invoke_runner` plus
prepare/finalize logic.

### Registry & plugin point

```python
# src/brr/envs/__init__.py
_BUILTIN: dict[str, type[Env]] = {
    "local":    LocalEnv,
    "worktree": WorktreeEnv,
    "docker":   DockerEnv,
    "ssh":      SshEnv,
}

def get_env(name: str) -> Env:
    if name in _BUILTIN:
        return _BUILTIN[name]()
    for ep in importlib.metadata.entry_points(group="brr.envs"):
        if ep.name == name:
            return ep.load()()
    raise RuntimeError(f"unknown env: {name}")
```

Third-party envs ship as a separate pip package:

```toml
# someone-else/pyproject.toml
[project.entry-points."brr.envs"]
firecracker = "myorg_brr_envs.firecracker:FirecrackerEnv"
```

`brr` keeps zero runtime deps; plugins bring their own.

---

## The durability contract

> Every task that runs in a non-`local` env runs in an **ephemeral**
> location. Containers exit. Worktrees are removed. ssh scratch dirs are
> rsync'd over. **The only outputs that survive are git refs and the
> response file.** Everything else is lost on `finalize()`.

Concrete rules every `Env.finalize()` must satisfy:

| Output                                      | Where it ends up on the host          | Required when                            |
|---------------------------------------------|----------------------------------------|------------------------------------------|
| Git commits on `ctx.branch`                 | reachable in host's `.git`             | `ctx.branch is not None`                 |
| Response file `<event-id>.md`               | `repo_root/.brr/responses/<id>.md`     | always (existing daemon contract)        |
| Trace artefacts                             | `repo_root/.brr/traces/<kind>/…/`      | `debug=True`                             |
| Per-task log                                | committed in branch as `kb/log-<id>.md`| worktree-style branches                  |

Anything an agent writes outside of a commit, the response file, or a
trace, is **not durable** and the framework makes no guarantee about it.
This is documented in `prompts/run.md` and `docs/brr-internals.md`.

### Enforcement

The daemon doesn't guess. After `finalize` returns its `FinalizeReport`,
`daemon._run_worker()` does:

```python
report = env.finalize(ctx, task, debug=debug_mode)
if not report.response_written:
    # existing retry path; nothing new
    return retry_or_error()
if ctx.branch and not gitops.branch_exists(repo_root, ctx.branch):
    task.update_status("error", tasks_dir)        # branch was promised, never landed
    return task
if ctx.branch and report.commits == 0:
    # branch exists but no work; informational only
    task.meta["empty_branch"] = "true"
```

That's the entire enforcement: file checks + git ref checks. No
filesystem inspection inside the env's territory. The contract is
*observable from the host*.

---

## The four built-ins

### `local`

- **prepare** → `RunContext(cwd=repo_root, branch=None or current, …)`
- **invoke** → `runner.invoke_runner(...)` directly.
- **finalize** → no-op besides building the report (`branch_pushed=True`
  trivially because the agent ran in the host repo).

This is the current `branch: current` path, refactored into the protocol.

### `worktree`

- **prepare** → `git worktree add .brr/worktrees/<task-id> <branch>` (creating the branch if needed); cwd points at the worktree.
- **invoke** → unchanged.
- **finalize** →
  - For `branch: auto | task`: attempt `git merge --ff-only <branch>` against the host's HEAD. On conflict → mark task `conflict` and *leave* the branch (decentralised merge — see below). Always remove the worktree (unless `debug`).
  - For named `branch:` strategies: leave branch alone. Remove worktree (unless `debug`).
  - Response file is already on the host (worktree shares `.git` and `.brr/`).

This is the current behaviour, just relocated. Drop ~80 LOC from
`daemon.py`.

### `docker`

- **prepare**:
  - Image: `cfg["docker"]["image"]` (default: a brr-published `python:3.11-slim`-based image with the configured runner CLI baked in).
  - Bind-mount `repo_root` at `/work` (read-write).
  - Bind-mount `repo_root/.brr/responses` at `/work/.brr/responses` (read-write).
  - Bind-mount `repo_root/.brr/traces` if `debug` (read-write).
  - Network: configurable (`cfg["docker"]["network"]`, default `bridge`).
  - **Branch handling:** because the bind-mount IS the host's `.git`, the agent's commits on `ctx.branch` are immediately visible to the host. Same trick as worktrees, no fetch/push needed.
- **invoke** → `docker run --rm --name brr-<task-id> -v ...:/work -w /work <image> <runner-cmd>`. The cmd line is built from the existing runner profile machinery.
- **finalize** → identical to worktree finalize for branch handling. Container is auto-removed (`--rm`).

For users who want **stronger isolation** (no shared `.git`), a
sub-mode `docker.isolation=clone`: `prepare` clones the repo into a
container-private volume, `finalize` does a `git fetch` from that volume
back to the host. Default is the bind-mount path because it's simpler
and faster.

### `ssh`

- **prepare**:
  - Remote spec: `cfg["ssh"]["host"]`, `cfg["ssh"]["scratch"]` (default `~/.brr/scratch`).
  - `ssh remote 'mkdir -p <scratch>/<task-id>'`
  - `rsync -a --delete <repo_root>/ remote:<scratch>/<task-id>/`
  - `ctx.cwd` is local but `env_state["remote_path"]` is set; invoke proxies through ssh.
- **invoke**: `ssh remote 'cd <scratch>/<task-id> && <runner-cmd>'`. Stdout/stderr piped back; trace is host-side as usual.
- **finalize**:
  - Pull the branch back: `ssh remote 'cd <scratch>/<task-id> && git bundle create /tmp/<task-id>.bundle <branch>'` then `scp` the bundle and `git fetch` it locally to `<branch>`. Bundles handle disconnected transfer cleanly; no need to expose the host's repo over ssh-back.
  - Pull the response file: `scp remote:<scratch>/<task-id>/.brr/responses/<event-id>.md repo_root/.brr/responses/`
  - Pull traces (if `debug`): `rsync remote:<scratch>/<task-id>/.brr/traces/ repo_root/.brr/traces/`
  - Tear down: `ssh remote 'rm -rf <scratch>/<task-id>'`

ssh is the most procedural env. It's also the proof that the contract
generalises: anything that can hold a git repo + write a markdown file
+ run a binary can be a brr environment.

---

## Decentralised "coordinator"

Replacing the central merge coordinator we kept deferring.

### The model

> Every task **always produces a branch**. Merging is opt-in per branch
> strategy. Conflicts are not a coordinator's problem — they are a
> human's problem (or the next agent run's problem).

| `branch:` strategy   | What `finalize` does for the branch                                        |
|----------------------|----------------------------------------------------------------------------|
| `current`            | nothing (no branch)                                                        |
| `auto` / `task`      | best-effort `git merge --ff-only`; on conflict → status=`conflict`, branch kept |
| `<name>` / `new:<x>` | nothing (human or PR tooling owns the merge)                               |

That's the whole "coordinator". It's ~30 LOC (already mostly in
`gitops.merge_branch` and `_finalize_worktree_task`). It moves into
`WorktreeEnv.finalize()` and `DockerEnv.finalize()` (which both end
up calling the same helper).

### Concurrency note

When v2 wants parallel workers, the only new thing is a **mutex on
the host's HEAD ref** — only one finalize can touch the host's working
branch at a time. That's a `threading.Lock()` in the daemon, not a
coordinator. Branches commute well in git; conflicts that can't ff-merge
get parked as `conflict` status and don't block other tasks.

### Why this is enough

- **Q&A tasks** → `branch: current`, no commits, no merge. Nothing to coordinate.
- **Research tasks** → `branch: auto`, single new file in `kb/`. ff-merge succeeds 99% of the time.
- **Refactor tasks** → `branch: auto` or named; if auto fails, `conflict` status surfaces it; if named, it's a PR.
- **Long-lived feature work** → named branch; brr never tries to merge.

CRDT-flavoured framing is real here: branches in git already have a
well-defined merge operation; brr just orchestrates `git merge` calls
and falls back to "leave it for a human" when the operation isn't
trivially defined. No bespoke conflict resolution.

---

## Daemon changes (small)

```python
# daemon._run_worker — pseudo-diff
- if uses_worktree: worktree.create(...)
- ... inline invoke ...
- if uses_worktree: _finalize_worktree_task(...)
+ env = envs.get_env(task.env)
+ env.validate(cfg)
+ ctx = env.prepare(task, repo_root, cfg)
+ try:
+     for attempt in range(...):
+         result = env.invoke(ctx, prompt, cfg)
+         if result.validation_ok: break
+ finally:
+     report = env.finalize(ctx, task, debug=debug_mode)
+ # contract checks (response_written, branch_pushed)
```

Net: `_run_worker` shrinks; the env-specific branches disappear.
`worktree.py` becomes the implementation of `WorktreeEnv` and stops
being daemon's helper.

---

## Triage prompt update

`prompts/triage.md` currently knows `local | worktree | docker`. Add `ssh`
and clarify decision criteria:

```
- local     — current branch, current working dir. Default for trivial / Q&A.
- worktree  — isolated working dir, shares git history. Default for code work.
- docker    — container; use when the task touches host-state we don't trust the agent with, or when the repo's tests need a clean environment.
- ssh       — remote machine; use only if the event explicitly requests it
              (e.g. "run on the GPU box"). Triage shouldn't pick ssh
              by inference.
```

---

## Configuration surface

`.brr/config` keys added in this PR:

```ini
default_env=worktree           # currently local; change with the env work
docker.image=brr/runner:py311  # default if env=docker is picked
docker.network=bridge
ssh.host=                      # required if env=ssh is ever picked
ssh.scratch=~/.brr/scratch
```

All optional. Absent values fall back to documented defaults; `ssh.host`
unset + `env=ssh` → `validate()` raises before `prepare()` runs.

---

## Tests to add (per env)

Each env gets the same test shape:

1. `prepare` returns a usable `RunContext` (dirs exist, branch exists if requested).
2. `invoke` is called with a stub runner (existing `runner.invoke_runner` mocking pattern); stdout/stderr propagate.
3. `finalize` produces a `FinalizeReport` with the right fields:
   - response file present → `response_written=True`
   - branch with N commits → `commits=N`, `branch_pushed=True`
   - empty branch → `commits=0`
4. Daemon-level integration: a fake event end-to-end through the env, asserting durability artefacts on the host and cleanup of the env-private state.

For `docker` and `ssh`, gate the integration tests on `DOCKER_AVAILABLE` /
`SSH_TEST_HOST` env vars; unit tests stub the subprocess calls so CI
doesn't need a docker daemon or a remote box.

---

## Docs to add

- `src/brr/docs/envs.md` — the four built-ins, when to use each, the durability contract, the entry-point plugin recipe.
- Update `src/brr/docs/execution-map.md` to reference `envs.md` instead of inlining worktree behaviour.
- Update `src/brr/docs/brr-internals.md` "concurrency model" section to point at the decentralised-merge framing.

---

## Out of scope (intentionally)

- Concurrent execution (still serial v1; mutex is documented as the v2 unlock).
- Overlays (Phase 1 of the fleet deck; separate PR).
- `brnrd` (separate project; see `notes-pondering-fleet.md`).
- Auto-`git push` to a remote on `auto`/`task` branches (deferred — daemon already pushes after merge succeeds; explicit per-branch push policy is a follow-up).
- Custom `Env` packaging tooling (no `brr env scaffold` command in v1).

---

## Done definition

- All four envs implemented behind the protocol.
- `daemon._run_worker` calls only `env.{validate, prepare, invoke, finalize}`.
- `FinalizeReport` checked at the daemon level.
- New tests green; existing tests untouched or trivially adjusted.
- `src/brr/docs/envs.md` shipped; triage prompt updated.
- PR description summarises the durability contract + decentralised merge framing.
- Branch merged.

After merge, the next focus moves to overlays (Phase 1 of the fleet
deck). Until then, `notes-pondering-fleet.md` is where new ideas land.
