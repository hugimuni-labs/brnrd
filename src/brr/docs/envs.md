# Execution environments

How brr places a runner invocation: where the agent's working directory
lives, how isolation is achieved, and how credentials reach the runner.

This document ships with the `brr` tool. Users can override it per-repo
by dropping a file at `.brr/docs/envs.md`.

## At a glance

| Env         | Where the runner runs                       | Credentials       | Repo isolation                | Notes                                |
| ----------- | ------------------------------------------- | ----------------- | ----------------------------- | ------------------------------------ |
| `host`      | Main repo checkout, current process         | Inherited         | None                          | Default for trivial / Q&A tasks      |
| `worktree`  | `.brr/worktrees/<task-id>/` (`brr/<task-id>` branch) | Inherited | Working dir + branch | Default for code work |
| `docker`    | A container, worktree bind-mounted          | Auto-wired to host| Container + worktree          | Bundled image includes common dev tools |

Other envs (`devcontainer`, `ssh`) are planned but not yet shipped. See
`kb/design-env-interface.md` if you want to follow that work.

## Picking an env

Resolution order in `.brr/config`:

1. `environment=` — the user-facing policy. Use this when configuring.
2. `env=` / `default_env=` — legacy aliases, still accepted.
3. `auto` — the daemon picks: docker if `docker.image` is set and
   Docker is on PATH, otherwise worktree. `host` is never auto-picked;
   set it explicitly if you want to forgo isolation.

The env is resolved deterministically when the task is built — there
is no LLM in the loop. If a request needs different isolation, change
`.brr/config` or wire your gate to set `env=` on the event.

Branch fallback is separate from environment selection. When no
structured event/thread metadata names a branch target, `branch.fallback`
or `branch_fallback` controls the daemon's branch plan:

- `preserve` (default) — seed from the repo default branch and preserve
  the `brr/<task-id>` branch for human routing.
- `inbox` — seed from the default branch and auto-land to `brr/inbox`.
- `default` — seed from and auto-land to the repo default branch.
- `current` — compatibility/development mode: seed from and auto-land
  to the daemon host checkout's current branch.

## `host`

The runner runs in the main checkout, in the daemon's process. There is
no isolation; uncommitted edits land directly in your working tree.
Pick this for read-only tasks (Q&A, review, research) and one-off fixes
where you want the change visible immediately.

## `worktree`

The daemon creates a git worktree under `.brr/worktrees/<task-id>/`
on a fresh `brr/<task-id>` branch sprouted from the resolved seed ref.
The runner cwd points at the worktree; your main checkout is
untouched. After a successful run, the daemon inspects the worktree's
git state:

- Agent committed on the original `brr/<task-id>` branch and the branch
  plan has an auto-land target → fast-forward that target, remove the
  worktree.
- Agent committed on the original `brr/<task-id>` branch and the branch
  plan has no auto-land target → preserve the task branch, publish it
  when a remote is configured, and remove the worktree.
- Agent switched to or created another branch (`git switch -c …`),
  or commits cannot fast-forward the target → leave the branch alone,
  remove the worktree.
- No commits beyond the seed ref → drop the empty branch with the
  worktree.

This is the right default for code-modifying work. Worktrees that
end up in a non-clean state (failures, conflicts, or untracked
files left behind) are kept automatically so you can inspect what
the agent did.

## `docker`

The runner command is wrapped in `docker run`. The repo is bind-mounted
into the container at the same absolute path it has on the host, so
file references in prompts and traces remain valid in both directions.
Docker tasks first set up the same `brr/<task-id>` worktree as the
`worktree` env and mount that directory instead of the main checkout,
keeping the host's working tree clean.

### Required configuration

```ini
default_env=docker
docker.image=ghcr.io/example/your-image:tag
```

That's it for required keys. Everything else is automatic or has a
sensible default.

### Credentials are wired automatically

Two paths cover both API-key and subscription-only auth:

1. **Env-var pass-through.** When set on the daemon's environment, brr
   forwards `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`,
   and `GOOGLE_API_KEY` into the container. Add more with
   `docker.env=KEY1,KEY2`.
2. **Login-dir bind mounts.** When `~/.claude/`, `~/.claude.json`,
   `~/.codex/`, `~/.gemini/`, or `~/.gitconfig` exists on the host,
   it's bind-mounted read-write into `$HOME/<basename>` inside the
   container (i.e. `/brr-home/.codex`). This is what makes Claude
   Pro/Max, ChatGPT Plus/Pro, and Gemini OAuth users work without an
   API key, and what gives `git commit` your real author identity.

You can opt out of the credential-dir mounts with
`docker.mount_credentials=false`. The mounts are read-write so refresh
tokens written inside the container land back on the host — your host
CLI stays authenticated with whatever the agent saw last.

### File ownership inside the container

The container runs as the **host user's UID**: brr passes
`-u "$(id -u):$(id -g)"` and `-e HOME=/brr-home` to `docker run`, and
the bundled image bakes a writable `/brr-home` (mode 1777) so any UID
can use it as HOME. The bind-mounted repo's `.git/objects/` therefore
collects host-owned files — there is no root-owned residue to clean
up after the daemon runs.

Without intervention, git would also refuse to operate on the
bind-mounted repo because its on-disk owner doesn't match the
container's UID register (CVE-2022-24765). brr passes
`safe.directory='*'` via git's `GIT_CONFIG_*` env vars so the agent
can `git status`, `git commit`, and `git diff` without per-image
configuration. This works against any image — including ones you build
yourself — without requiring you to remember the safe-directory line.

### Runtime knobs

| Key                           | Default      | Purpose                                              |
| ----------------------------- | ------------ | ---------------------------------------------------- |
| `docker.image`                | required     | Image reference passed to `docker run`               |
| `docker.network`              | `bridge`     | `--network` argument                                 |
| `docker.env`                  | empty        | Comma-separated extra env-var names to pass through  |
| `docker.mount_credentials`    | `true`       | Mount `~/.{claude,codex,gemini}` when present        |

### Image expectations

The image must:

- Have your configured runner CLI on `PATH` (`claude`, `codex`,
  `gemini`, or whatever you set `runner=` to).
- Have `git` available — the agent commits inside the container.
- Have the shell and repo tools your agents are expected to use. brr's
  bundled runner image includes `bash`, `git`, `ssh`/`scp`, Python
  (`python`, `python3`, `pip`, venv support), `rg`, `curl`, `wget`,
  `jq`, `rsync`, `zip`/`unzip`, and a small native build toolchain
  (`build-essential`, `pkg-config`) because those are common enough
  across code-review, test, and package-install workflows to belong in
  the default image.
- Accept being run as an arbitrary UID (whatever `id -u` returns on
  the host). The bundled image bakes a writable `/brr-home` and sets
  `ENV HOME=/brr-home` so credential and gitconfig mounts work
  regardless of whether the runtime UID has an `/etc/passwd` entry.
  Custom images that hard-code `USER root` and write tokens to
  `/root/...` won't see the credential mounts — either follow the
  bundled image's `/brr-home` pattern or build with the same `HOME`
  the daemon expects.

It does *not* need:

- Your project's installed dependencies, databases, cloud CLIs, or
  language SDKs beyond Node and Python. See "Layering project tooling"
  below.
- An API key baked in. brr wires credentials at run time.
- A `safe.directory` config baked in. brr wires that at run time.

### Minimum viable image

Any image with `git` and one runner CLI works. For example, the
smallest claude-only image is:

```dockerfile
FROM node:22-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN npm install -g @anthropic-ai/claude-code
```

Build with `docker build -t my-brr-runner .` and set
`docker.image=my-brr-runner`. Repeat for `@openai/codex` or
`@google/gemini-cli` for the other runners.

### Layering project tooling

Project-specific tooling still belongs *on top of* the runner image.
The bundled image carries a common baseline, but the project image
should carry what its tests and build need: repo dependencies, service
CLIs, databases, browser drivers, extra language SDKs, and pinned test
tools.

```dockerfile
FROM brr-runner:local
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml ./
RUN pip install -e ".[dev]"
```

Repos that already maintain a `.devcontainer/devcontainer.json` should
prefer the (planned) `devcontainer` env when it ships, rather than
duplicating that recipe here.

### Container lifecycle

| Outcome              | Container is...                                          |
| -------------------- | -------------------------------------------------------- |
| `done`               | Removed (`docker rm -f`)                                 |
| `error` / `conflict` | Preserved (`task.meta.docker_containers` records names)  |

Same rule as worktrees: clean teardown only on a successful run.
Failures preserve the container so you can inspect, re-run, or copy
work out manually.

## Durability contract

Across all envs, brr only guarantees three kinds of output survive:

1. **Git commits** on whatever branch the agent left checked out in
   the worktree (recorded in `task.meta["branch_name"]`), reachable
   from the host's `.git`.
2. **The response file** at `.brr/responses/<event-id>.md`, captured
   from the runner's stdout.
3. **Trace artefacts** under `.brr/traces/<kind>/...`, written for
   every runner invocation. Traces are forensic-only: they're kept
   on `error` / `conflict` and removed on a clean `done`. On a
   successful run the durable record is the git commit + response
   file + kb updates; the trace would only repeat that information.

Anything else an agent writes (untracked files, ephemeral state inside
a container, files in a worktree that didn't get committed) is **not
durable**. The corresponding scratch space — worktree, container,
remote ssh dir — is torn down on a clean success. Salvage rule:
scratch and traces are preserved on `error` or `conflict`, and a
worktree with untracked or unstaged files at finalize time is kept
regardless of status, so a human can recover work.

## Troubleshooting

- **`docker env requires docker.image in .brr/config`** — set
  `docker.image=` to a built or pulled image reference.
- **`fatal: detected dubious ownership in repository`** — should not
  appear with a recent brr; if it does, your container's git is older
  than 2.31 or strips `GIT_CONFIG_*` env vars. Update git in the image
  or add `RUN git config --system --add safe.directory '*'` to the
  Dockerfile.
- **Runner exits with auth error inside container** — confirm the
  matching `~/.<runner>/` exists on host (for subscription auth) or
  the corresponding `*_API_KEY` is exported in the daemon's
  environment. Both paths surface in the `docker run` argv visible in
  trace mode.
- **`python`, `ssh`, or `rg` is missing** — rebuild the local image
  from the current bundled Dockerfile (`brr init -i` can do this during
  setup). Older `brr-runner:*` images predate the baseline dev toolbox.
- **File ownership leaked to root on host** — should not happen with
  a recent brr-runner image, which runs as the host UID. If you see
  it, you're likely on a stale image — rebuild from the current
  bundled Dockerfile (`brr init -i`). One-shot recovery while you're
  rebuilding: `sudo chown -R "$(id -un):$(id -gn)" .git`.
- **Credentials not picked up inside the container** — confirm the
  matching `~/.<runner>/` exists on host *and* the image runs with
  `HOME=/brr-home`. Custom images that hard-code `USER root` and
  expect `/root/...` won't see the mounts; bake your own writable
  HOME or follow the bundled image's `/brr-home` pattern.
