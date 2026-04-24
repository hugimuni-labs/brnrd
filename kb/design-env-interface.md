# Design: Env Interface (PR scope)

Focused, executable design for the in-flight worktree PR: extract the
`Env` Protocol, codify the durability contract, add `docker`, `ssh`,
and `devcontainer` built-ins, decentralise merging. The merge of this
PR is the unlock for treating environments as the main brr value
proposition.

This page is **tactical**. Strategic context lives in
`deck-brr-fleet-steering.md`. Open items the PR doesn't touch
(overlays, brnrd, discovery, cross-platform supervisor, plugin
candidates like Daytona) live in `notes-pondering-fleet.md`.

---

## Goals (what this PR ships)

1. **`Env` Protocol** — single abstraction with three phases:
   `prepare → invoke → finalize`.
2. **Five built-ins** behind it: `local`, `worktree`, `docker`, `ssh`,
   `devcontainer`. All tested. All documented in `src/brr/docs/`.
3. **Durability contract** — explicit, enforced by the daemon.
4. **Decentralised "coordinator"** — branch-and-PR is the model;
   merging is a thin best-effort post-task step, not a component.
5. **Plugin point** — third-party envs via either Python entry points
   (`brr.envs`) or drop-in script envs under `~/.config/brr/envs/` or
   `.brr/envs/`. Both dispatch paths share the same protocol.

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
    response_path_env: Path    # where the runner is told to write (agent-visible)
    response_path_host: Path   # where finalize must land it; daemon checks this
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

### Response path split

`response_path_env` is what the runner sees in its prompt ("write your
response to …"). `response_path_host` is where the daemon later verifies
the response landed. For envs that share a filesystem with the host,
they're the same path; for remote envs, `finalize` is responsible for
the transfer.

| Env            | `response_path_env`                            | `response_path_host`                           | Equal? |
|----------------|------------------------------------------------|------------------------------------------------|--------|
| `local`        | `repo_root/.brr/responses/<id>.md`             | same                                           | yes    |
| `worktree`     | `repo_root/.brr/responses/<id>.md`             | same (worktree shares `.brr/`)                 | yes    |
| `docker`       | `/work/.brr/responses/<id>.md` (bind-mount)    | `repo_root/.brr/responses/<id>.md`             | yes (same inode via mount) |
| `ssh`          | `<scratch>/<task-id>/.brr/responses/<id>.md`   | `repo_root/.brr/responses/<id>.md`             | **no** — `finalize` scp's it back |
| `devcontainer` | `/workspaces/<repo>/.brr/responses/<id>.md`    | `repo_root/.brr/responses/<id>.md`             | yes (same inode via devcontainer mount) |
| plugin envs    | env's choice                                   | `repo_root/.brr/responses/<id>.md`             | plugin-dependent |

The daemon only ever checks `response_path_host`. `response_path_env`
is a hint to `prompt` construction; how it's translated into the
runner's prompt is each env's concern.

### Registry & plugin point

Two dispatch modes, one protocol. Resolution order in `get_env(name)`:

1. Built-in Python class in `src/brr/envs/`.
2. Script env in `.brr/envs/<name>/` (per-repo override).
3. Script env in `~/.config/brr/envs/<name>/` (user-wide).
4. Python entry point registered under `brr.envs` in any installed package.
5. Otherwise → `RuntimeError`.

```python
# src/brr/envs/__init__.py
_BUILTIN: dict[str, type[Env]] = {
    "local":        LocalEnv,
    "worktree":     WorktreeEnv,
    "docker":       DockerEnv,
    "ssh":          SshEnv,
    "devcontainer": DevcontainerEnv,
}

def get_env(name: str, repo_root: Path) -> Env:
    if name in _BUILTIN:
        return _BUILTIN[name]()
    for root in (repo_root / ".brr" / "envs", _USER_CFG / "envs"):
        if (root / name).exists():
            return ScriptEnvAdapter(name=name, root=root / name)
    for ep in importlib.metadata.entry_points(group="brr.envs"):
        if ep.name == name:
            return ep.load()()
    raise RuntimeError(f"unknown env: {name}")
```

#### Python plugins

For typed, reusable, shareable envs. Ship as a separate pip package:

```toml
# someone-else/pyproject.toml
[project.entry-points."brr.envs"]
firecracker = "myorg_brr_envs.firecracker:FirecrackerEnv"
```

`brr` keeps zero runtime deps; plugins bring their own. This is how
a **dogfood Daytona plugin** ships after this PR merges — outside of
`brr` core, in its own repo, as proof of the mechanism. See
`notes-pondering-fleet.md` §10 for the list of plugin candidates.

#### Script envs (drop-in, zero install)

For "bash script on my machine, point brr at it" ergonomics. A script
env is a directory whose name *is* the env name. Two supported layouts:

```
.brr/envs/myenv/
├── prepare           # executable
├── invoke            # executable
├── finalize          # executable
└── validate          # optional; executable
```

or a single executable dispatching by first argv:

```
.brr/envs/myenv             # executable; $1 ∈ {validate, prepare, invoke, finalize}
```

Protocol is **JSON-in on stdin, JSON-out on stdout**, with fields
matching the Python dataclasses verbatim (`RunContext`,
`FinalizeReport`, `RunnerResult`). `stderr` is propagated unchanged to
the trace.

Minimal bash stub for the `invoke` step of a script env:

```bash
#!/usr/bin/env bash
set -euo pipefail
# stdin: {"ctx": {...}, "prompt": "...", "cfg": {...}}
# stdout: {"stdout": "...", "stderr": "...", "returncode": 0, "validation_ok": true}
input=$(cat)
cwd=$(jq -r '.ctx.cwd' <<<"$input")
prompt=$(jq -r '.prompt' <<<"$input")
cd "$cwd"
out=$(some-runner --print "$prompt" 2> >(cat >&2))
rc=$?
jq -nc --arg out "$out" --argjson rc "$rc" \
  '{stdout: $out, stderr: "", returncode: $rc, validation_ok: ($rc == 0)}'
```

The Python `ScriptEnvAdapter` shells out to these four executables and
marshals JSON. It's the bridge that keeps protocol parity between
Python and script envs; neither kind is privileged.

For the future `brr env init` scaffolding helper, see the "Env
scaffolding" section further down.

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
| Env-private scratch teardown                | n/a — removed from env's territory     | `status=done` **and** `debug=False`      |

Anything an agent writes outside of a commit, the response file, or a
trace, is **not durable** and the framework makes no guarantee about it.
This is documented in `prompts/run.md` and `docs/brr-internals.md`.

**Salvage rule.** Env scratch state (worktrees, containers, remote ssh
dirs, devcontainers) is torn down only when the task finished cleanly
and we aren't in debug mode. On `error` / `conflict`, or whenever
`debug=True`, the scratch is preserved so the user can inspect or
salvage work. `brr inspect <task-id>` surfaces the preserved location
via `task.meta`.

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

## The five built-ins

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
  - For `branch: auto | task`: attempt `git merge --ff-only <branch>` against the host's HEAD. On conflict → mark task `conflict` and *leave* the branch (decentralised merge — see below).
  - For named `branch:` strategies: leave branch alone.
  - **Worktree teardown rule:** remove the worktree only when `status=done` and `debug=False`. If `status ∈ {error, conflict}` or `debug=True`, preserve the worktree so the user can salvage work or inspect what happened. This changes current behaviour for `status=error` (previously removed).
  - Response file is already on the host (worktree shares `.git` and `.brr/`).

This is the current behaviour, just relocated and with the salvage rule
above. Drop ~80 LOC from `daemon.py`.

#### Why worktree stays a flat env in v1

A decomposed model ("working-copy strategy" × "isolation strategy")
would arguably be cleaner: you could compose e.g. `docker-worktree` for
a fresh checkout inside a container, or `ssh-worktree` for a remote
worktree. Theoretically correct, but it doubles the taxonomy users have
to reason about and forces every env to answer both axes up front.

v1 keeps `worktree` flat because the common intent behind it is
concrete and narrow: **give the agent a fresh folder without polluting
the main checkout** — which flat `worktree` covers cleanly on its own.
Compose-oriented envs (`docker-worktree` etc.) become warranted only
when there's a real request for two axes at once; at that point the
compose axis moves into a follow-up, not v1.

### `docker`

- **prepare**:
  - Image: `cfg["docker"]["image"]` (default: a brr-published `python:3.11-slim`-based image with the configured runner CLI baked in).
  - Bind-mount `repo_root` at `/work` (read-write).
  - Bind-mount `repo_root/.brr/responses` at `/work/.brr/responses` (read-write).
  - Bind-mount `repo_root/.brr/traces` if `debug` (read-write).
  - Network: configurable (`cfg["docker"]["network"]`, default `bridge`).
  - **Branch handling:** because the bind-mount IS the host's `.git`, the agent's commits on `ctx.branch` are immediately visible to the host. Same trick as worktrees, no fetch/push needed.
- **invoke** → `docker run --name brr-<task-id> -v ...:/work -w /work <image> <runner-cmd>`. The cmd line is built from the existing runner profile machinery. Note: **no `--rm`** — cleanup is `finalize`'s job so we can preserve the container for salvage.
- **finalize** → branch handling identical to worktree finalize. Container teardown rule matches `worktree`: `docker rm brr-<task-id>` only when `status=done` and `debug=False`; preserve when `status ∈ {error, conflict}` or `debug=True`.

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
  - Tear down: `ssh remote 'rm -rf <scratch>/<task-id>'` — only when `status=done` and `debug=False`. Otherwise preserve the remote scratch dir for salvage, matching the `worktree` and `docker` rule.

ssh is the most procedural env. It's also the proof that the contract
generalises: anything that can hold a git repo + write a markdown file
+ run a binary can be a brr environment.

### `devcontainer`

For repos that already ship a `.devcontainer/devcontainer.json`. Reuses
the user's existing container recipe rather than asking them to
maintain a parallel `docker.image` for brr.

- **validate** → `devcontainer` CLI on PATH + `<repo_root>/.devcontainer/devcontainer.json` present. Raise if either is missing.
- **prepare**:
  - `devcontainer up --workspace-folder <repo_root>` — starts the container (no-op if already up).
  - Record the container id / workspace folder in `ctx.env_state`.
  - `ctx.cwd = repo_root` on the host side; the devcontainer CLI handles the in-container path.
  - Same bind-mount story as `docker`: the repo is mounted in the container, so commits on `ctx.branch` are visible to the host immediately. `response_path_env` resolves to the in-container path; `response_path_host` stays the host's `.brr/responses/<id>.md`.
- **invoke** → `devcontainer exec --workspace-folder <repo_root> -- <runner-cmd>`. Runner profile machinery unchanged.
- **finalize** → branch handling identical to worktree/docker finalize. Container teardown: `devcontainer down` when `status=done` and `debug=False`; preserve when `status ∈ {error, conflict}` or `debug=True`. Mirrors the worktree salvage rule.

Gated at `validate()` time so triage can pick `devcontainer` only when
the repo actually supports it. The triage prompt explicitly mentions
this (see below).

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
+ env = envs.get_env(task.env, repo_root)
+ env.validate(cfg)
+ ctx = env.prepare(task, repo_root, cfg)
+ try:
+     for attempt in range(...):
+         result = env.invoke(ctx, prompt, cfg)
+         if result.validation_ok: break
+ finally:
+     # finalize reads task.status, honours the salvage rule:
+     # tear down only on done + not debug; preserve otherwise.
+     report = env.finalize(ctx, task, debug=debug_mode)
+ # contract checks (response_written, branch_pushed)
```

Net: `_run_worker` shrinks; the env-specific branches disappear.
`worktree.py` becomes the implementation of `WorktreeEnv` and stops
being daemon's helper. Scratch preservation on `error` / `conflict`
is a behaviour change from current `_finalize_worktree_task` (which
force-removes the worktree on `error`); worth calling out in the PR
description.

---

## Triage prompt update

`prompts/triage.md` currently knows `local | worktree | docker`. Add
`ssh` and `devcontainer`, and clarify decision criteria:

```
- local         — current branch, current working dir. Default for trivial / Q&A.
- worktree      — isolated working dir, shares git history. Default for code work.
- docker        — container with a brr-managed image; use when tests need a clean env
                  or host state shouldn't be touched.
- devcontainer  — the repo's own .devcontainer/ recipe. Prefer over docker when
                  the repo ships a devcontainer.json and the task needs that env.
- ssh           — remote machine; use only if the event explicitly requests it
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
devcontainer.workspace=        # optional override of --workspace-folder
```

All optional. Absent values fall back to documented defaults. `validate()`
raises before `prepare()` runs when required config is missing:
`env=ssh` without `ssh.host`; `env=devcontainer` without the
`devcontainer` CLI or a `.devcontainer/devcontainer.json` in the
repo.

---

## Tests to add (per env)

Each env gets the same test shape:

1. `prepare` returns a usable `RunContext` (dirs exist, branch exists if requested; `response_path_env` vs `response_path_host` agrees with the table).
2. `invoke` is called with a stub runner (existing `runner.invoke_runner` mocking pattern); stdout/stderr propagate.
3. `finalize` produces a `FinalizeReport` with the right fields:
   - response file present → `response_written=True`
   - branch with N commits → `commits=N`, `branch_pushed=True`
   - empty branch → `commits=0`
4. Daemon-level integration: a fake event end-to-end through the env, asserting durability artefacts on the host and cleanup of the env-private state.

Additional cross-cutting cases required by the refinements:

- **Worktree salvage on error.** Run a task whose worker errors out; assert the worktree directory still exists afterwards and `task.meta` points at it. Same test shape for `conflict` status. Equivalent cases for `docker` (container preserved, `docker rm` not called) and `ssh` (remote scratch dir not removed; subprocess mock records absence of `rm -rf`).
- **`devcontainer` unit test.** Mock the `devcontainer` CLI via a shim on `PATH`. Assert `validate()` raises when CLI is absent or `.devcontainer/devcontainer.json` is missing; assert `prepare → invoke → finalize` issues `devcontainer up / exec / down` in order when the shim is present. Gate integration tests on `DEVCONTAINER_AVAILABLE`.
- **Script-env dispatch.** Create a `.brr/envs/myenv/` directory with the four stub executables (bash, emitting static JSON). Assert `get_env("myenv", repo_root)` returns a `ScriptEnvAdapter`, the protocol round-trips through JSON-on-stdio, and stderr from the script lands in the trace.
- **Registry precedence.** Built-in beats script-env beats entry-point; verify with stubs registered at each layer using the same env name.

For `docker`, `ssh`, `devcontainer`, gate integration tests on
`DOCKER_AVAILABLE` / `SSH_TEST_HOST` / `DEVCONTAINER_AVAILABLE`
env vars; unit tests stub subprocess calls so CI doesn't need a
docker daemon, a remote box, or a devcontainer host.

---

## Docs to add

- `src/brr/docs/envs.md` — the five built-ins, when to use each, the durability contract, the response-path split, the salvage rule, the entry-point plugin recipe, and the script-env protocol.
- Update `src/brr/docs/execution-map.md` to reference `envs.md` instead of inlining worktree behaviour.
- Update `src/brr/docs/brr-internals.md` "concurrency model" section to point at the decentralised-merge framing.

---

## Env scaffolding (future `brr env init`)

**Not in v1.** Sketched here so the dual script/python plugin path has
a forward and so the `--kind` flag doesn't get retrofitted awkwardly
later.

Proposed shape:

```
brr env init <name> --kind=script [--dir=.brr/envs/<name>]
  → Seeds a new script-env directory with:
      prepare, invoke, finalize, validate   (executable bash stubs)
      README.md                              (the protocol reminder)
  → Default target: .brr/envs/<name>/ (per-repo). Use --dir=~/.config/brr/envs/<name> for user-wide.
  → Stubs print the expected JSON shape on stdout and exit 0, so the env is
    runnable before you edit anything.

brr env init <name> --kind=python --pkg=<package>
  → Scaffolds a minimal pyproject.toml + src/<package>/<name>.py with:
      * a class stub implementing the Env protocol
      * [project.entry-points."brr.envs"] pointing at the class
      * pytest stub mirroring the built-in env test shape
  → Leaves packaging/publishing to the user.
```

Why not v1: the scaffolding is convenience, not capability. The same
outcome is achievable today with `mkdir .brr/envs/myenv && cp …`.
Shipping it now commits brr to a specific scaffold format before we
know what real third-party envs want.

Prior art to steal from when this lands: how `brr eject` copies
bundled prompts (see `cli.cmd_eject`) — same pattern, different
source directory.

---

## Out of scope (intentionally)

- Concurrent execution (still serial v1; mutex is documented as the v2 unlock).
- Overlays (Phase 1 of the fleet deck; see `plan-overlays.md`).
- `brnrd` (separate project; see `notes-pondering-fleet.md`).
- Auto-`git push` to a remote on `auto`/`task` branches (deferred — daemon already pushes after merge succeeds; explicit per-branch push policy is a follow-up).
- `brr env init` scaffolding command (sketched above; sketch only).
- Compose-oriented envs like `docker-worktree` (see "Why worktree stays a flat env in v1").
- First-party Daytona or E2B plugins (they ship outside core as dogfood; see `notes-pondering-fleet.md` §10).

---

## Done definition

- All five built-in envs implemented behind the protocol.
- `RunContext` carries the `response_path_env` / `response_path_host` split and envs populate it correctly.
- `ScriptEnvAdapter` dispatches to `.brr/envs/<name>` and `~/.config/brr/envs/<name>`.
- `daemon._run_worker` calls only `env.{validate, prepare, invoke, finalize}`.
- `FinalizeReport` checked at the daemon level; salvage rule honoured on `error`/`conflict`.
- New tests green (including the worktree-salvage-on-error, devcontainer stub, and script-env dispatch cases); existing tests untouched or trivially adjusted.
- `src/brr/docs/envs.md` shipped; triage prompt updated.
- PR description summarises the durability contract, decentralised merge framing, salvage rule, and dual plugin model.
- Branch merged.

After merge, the next focus moves to overlays — see
[plan-overlays.md](plan-overlays.md). Plugin candidates (Daytona etc.)
are captured in `notes-pondering-fleet.md` §10.
