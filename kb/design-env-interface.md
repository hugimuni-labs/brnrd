# Design: env protocol, durability contract, and decentralised merging

Status: accepted on 2026-05-06

This page is the design spec for environments — the `Env` Protocol,
the durability contract, the per-env mechanics, the plugin model, and
the decentralised merge framing. Current rollout status (which envs
ship, which don't, what the salvage rule looks like) lives one level
up in [`subject-envs.md`](subject-envs.md); start there if you want
the synthesis. Strategic context for the fleet axis is in
[`deck-brr-fleet-steering.md`](deck-brr-fleet-steering.md), and open
items adjacent to envs (overlays, brnrd, cross-platform supervisor,
third-party plugin candidates) live in
[`notes-pondering-fleet.md`](notes-pondering-fleet.md).

## Scope

The design covers a single three-phase abstraction (`prepare → invoke
→ finalize`) used by five built-in envs (`local`, `worktree`,
`docker`, `ssh`, `devcontainer`), an explicit durability contract the
daemon enforces from the host, the decentralised branch-and-PR merge
model that replaced an earlier merge-coordinator sketch, and a dual
plugin point — Python entry points under `brr.envs` and drop-in
script envs in `~/.config/brr/envs/` or `.brr/envs/`. Concurrent
execution, overlays, `brnrd`, and env-specific secret handling beyond
what gates already do are explicitly out of scope and live in their
own designs / notes.

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
    def finalize(self, ctx: RunContext, task: Task) -> FinalizeReport: ...
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
| Trace artefacts                             | `repo_root/.brr/traces/<kind>/…/`      | always written; removed on clean `status=done`, kept on `error`/`conflict` |
| Per-task log                                | committed in branch as `kb/log-<id>.md`| worktree-style branches                  |
| Env-private scratch teardown                | n/a — removed from env's territory     | clean `status=done` with no uncommitted files |

Anything an agent writes outside of a commit, the response file, or a
trace, is **not durable** and the framework makes no guarantee about it.
This is documented in `prompts/run.md` and `docs/brr-internals.md`.

**Salvage rule.** Env scratch state (worktrees, containers, remote ssh
dirs, devcontainers) is torn down only when the task finished cleanly
with nothing left uncommitted in the worktree. On `error` /
`conflict`, or when the worktree has untracked/unstaged files,
scratch is preserved so the user can inspect or salvage work.
`brr inspect <task-id>` surfaces the preserved location via
`task.meta`.

### Enforcement

The daemon doesn't guess. After `finalize` returns its `FinalizeReport`,
`daemon._run_worker()` does:

```python
report = env.finalize(ctx, task)
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
  - **Worktree teardown rule:** outcome-aware. Remove the worktree on
    clean `status=done` with nothing uncommitted. Preserve on
    `status ∈ {error, conflict}` or when the worktree has
    uncommitted/untracked files, so the user can inspect or salvage.
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

> **Implementation status (2026-05-06):** `prepare`/`invoke`/`finalize`
> shipped per this design. Credential wiring (env-var pass-through for
> known runner keys, `~/.{claude,codex,gemini}` bind mounts when present,
> and `safe.directory='*'` injection so git works against the
> bind-mounted repo) added on top of the original spec to remove the
> "your image must bake in tokens" hidden requirement. The bundled
> first-party Dockerfile now builds a practical runner image with the
> three runner CLIs plus baseline dev tools (`python`/`pip`, SSH client,
> `git`, `rg`, `curl`/`wget`, `jq`, `rsync`, zip tools, and native build
> tooling). Still pending: publishing that image and auto-resolving blank
> `docker.image=`. User-facing docs live in `src/brr/docs/envs.md`.

- **prepare**:
  - Image: `docker.image` in `.brr/config`. The bundled Dockerfile is
    the local first-party path for a runner image, but this is still
    required until brr publishes a default image and can safely resolve
    blank `docker.image=`. brr wires credentials at run time (env-var
    pass-through plus host login-dir bind mounts), so the image no
    longer needs an API key baked in.
  - Bind-mount `repo_root` at the same absolute path inside the container
    (read-write), so the prompt's host paths remain valid in the env.
  - Network: configurable (`cfg["docker"]["network"]`, default `bridge`).
  - **Branch handling:** current-branch Docker tasks mount the main checkout.
    Non-current branch tasks first create the same `.brr/worktrees/<task-id>`
    checkout that `worktree` uses and run Docker with that as the working
    directory. This keeps branch work from switching or dirtying the host's
    main checkout while keeping commits visible through the shared `.git`.
- **invoke** → `docker run --name brr-<task-id>-<attempt> -v <repo>:<repo> -w <run-root> <image> <runner-cmd>`. The cmd line is built from the existing runner profile machinery. Note: **no `--rm`** — cleanup is `finalize`'s job so we can preserve the container for salvage and support retry diagnostics.
- **finalize** → branch handling identical to worktree finalize. Container teardown matches the worktree salvage rule: `docker rm -f <container>` on clean `status=done`; preserve on `status ∈ {error, conflict}` or when the worktree has uncommitted/untracked files.

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
  - Pull traces always: `rsync remote:<scratch>/<task-id>/.brr/traces/ repo_root/.brr/traces/`
  - Tear down: `ssh remote 'rm -rf <scratch>/<task-id>'` on clean `status=done`. Preserve the remote scratch dir on `status ∈ {error, conflict}` for salvage, matching the worktree/docker rule.

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
- **finalize** → branch handling identical to worktree/docker finalize. Container teardown: `devcontainer down` on clean `status=done`; preserve on `status ∈ {error, conflict}`. Mirrors the worktree salvage rule.

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

That's the whole "coordinator". The original 2026-05 env slice assumed
the helper would be `gitops.merge_branch` plus `_finalize_worktree_task`.
As of 2026-05-11 the branch-intent implementation replaced that with
`branching.BranchPlan`, `gitops.fast_forward_branch`, and
`WorktreeEnv._land_or_preserve()` / `DockerEnv.finalize()`: finalization
fast-forwards a resolved auto-land target or preserves the branch when
no safe target exists.

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
+     # finalize reads task.status and honours the outcome-aware
+     # salvage rule: tear down on clean done, preserve on
+     # error/conflict or when work is left uncommitted.
+     report = env.finalize(ctx, task)
+ # contract checks (response_written, branch_pushed)
```

Net: `_run_worker` shrinks; the env-specific branches disappear.
`worktree.py` becomes the implementation of `WorktreeEnv` and stops
being daemon's helper. Scratch preservation on `error` / `conflict`
is a behaviour change from current `_finalize_worktree_task` (which
force-removes the worktree on `error`); worth calling out in the PR
description.

---

## Env selection

There is no LLM triage step. The daemon picks the env mechanically
from `.brr/config` and event metadata: `environment=auto` resolves to
`docker` when a Docker image is configured, otherwise `worktree`;
`host` is explicit only; `ssh` and `devcontainer` are explicit. See
[`decision-remove-triage.md`](decision-remove-triage.md) for why this
shape replaced the earlier LLM triage idea.

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

## Test shape (per env)

Each env gets the same test shape so the protocol stays observable
from outside:

1. `prepare` returns a usable `RunContext` (dirs exist, branch exists
   if requested; `response_path_env` vs `response_path_host` agrees
   with the table above).
2. `invoke` is called with a stub runner (existing
   `runner.invoke_runner` mocking pattern); stdout/stderr propagate.
3. `finalize` produces a `FinalizeReport` with the right fields:
   response file present → `response_written=True`; branch with N
   commits → `commits=N`, `branch_pushed=True`; empty branch →
   `commits=0`.
4. Daemon-level integration: a fake event end-to-end through the env,
   asserting durability artefacts on the host and cleanup of the
   env-private state.

The salvage rule has dedicated coverage on top of that: a task whose
worker errors out leaves the worktree / container / remote scratch
dir intact, and `task.meta` points at the preserved location. The
script-env dispatch path is exercised with stub executables in
`.brr/envs/<name>/` to verify the JSON-on-stdio protocol round-trips,
and registry-precedence tests pin built-in > script > entry-point
resolution.

Docker, ssh, and devcontainer integration tests gate on
`DOCKER_AVAILABLE` / `SSH_TEST_HOST` / `DEVCONTAINER_AVAILABLE` so CI
doesn't need a docker daemon, a remote box, or a devcontainer host;
unit tests stub subprocess calls everywhere else.

---

## Reference docs

User-facing reference lives in
[`src/brr/docs/envs.md`](../src/brr/docs/envs.md) (when to pick each
env, configuration keys, troubleshooting). The execution map
([`src/brr/docs/execution-map.md`](../src/brr/docs/execution-map.md))
and the internals doc
([`src/brr/docs/brr-internals.md`](../src/brr/docs/brr-internals.md))
point at the same protocol from above.

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

## Boundary

These adjacent concerns sit outside the design on purpose and have
their own homes:

- Concurrent execution (deferred; the daemon stays serial and the v2
  unlock is a host-HEAD mutex, not a coordinator).
- Overlays — see [`plan-overlays.md`](plan-overlays.md).
- `brnrd` — separate project, see
  [`notes-pondering-fleet.md`](notes-pondering-fleet.md).
- Compose-oriented envs like `docker-worktree` — see "Why worktree
  stays a flat env in v1" above.
- First-party plugins (Daytona, Firecracker, E2B) — ship outside core
  as dogfood, see
  [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §10.
- Auto-`git push` policy on auto/task branches — the daemon publishes
  branches when a remote is configured; explicit per-branch push
  policy is a follow-up captured alongside the branch-intent design.

## Lineage

- **2026-05-13** — split current-state synthesis out into
  [`subject-envs.md`](subject-envs.md); compressed the proposal
  scaffolding (Goals, Done definition, Docs/Tests to add) into a
  short scope paragraph and a test-shape section. The design itself
  is unchanged; this is a state-first cleanup so the page reads as a
  reference rather than a PR plan.
- **2026-05-11** — branch-intent rewrite (see
  [`design-daemon-landing-branch.md`](design-daemon-landing-branch.md))
  replaced the original `gitops.merge_branch` /
  `_finalize_worktree_task` mechanics with `branching.BranchPlan`,
  `gitops.fast_forward_branch`, and
  `WorktreeEnv._land_or_preserve` / `DockerEnv.finalize`.
- **2026-05-06** — accepted; `prepare`/`invoke`/`finalize` and the
  `host`/`worktree`/`docker` built-ins shipped; outcome-aware salvage
  rule added on top of the original spec so failures stay
  inspectable.
