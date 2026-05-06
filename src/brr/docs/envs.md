# Execution environments

How brr places a runner invocation: where the agent's working directory
lives, how isolation is achieved, and how credentials reach the runner.

This document ships with the `brr` tool. Users can override it per-repo
by dropping a file at `.brr/docs/envs.md`.

## At a glance

| Env         | Where the runner runs                       | Credentials       | Repo isolation                | Notes                                |
| ----------- | ------------------------------------------- | ----------------- | ----------------------------- | ------------------------------------ |
| `host`      | Main repo checkout, current process         | Inherited         | None                          | Default for trivial / Q&A tasks      |
| `worktree`  | `.brr/worktrees/<task-id>/` (own branch)    | Inherited         | Working dir + branch          | Default for code work                |
| `docker`    | A container, repo bind-mounted              | Auto-wired to host| Container + (worktree if branch) | New users start here for hardening |

Other envs (`devcontainer`, `ssh`) are planned but not yet shipped. See
`kb/design-env-interface.md` if you want to follow that work.

## Picking an env

Resolution order in `.brr/config`:

1. `environment=` — the user-facing policy. Use this when configuring.
2. `env=` / `default_env=` — legacy aliases, still accepted.
3. `auto` — the daemon picks: docker if `docker.image` is set and Docker
   is on PATH, else worktree, else host.

At the task level, the triage agent may override based on the request,
but it should not pick `docker` or `ssh` unless the event explicitly
asks for that level of isolation.

## `host`

The runner runs in the main checkout, in the daemon's process. There is
no isolation; uncommitted edits land directly in your working tree.
Pick this for read-only tasks (Q&A, review, research) and one-off fixes
where you want the change visible immediately.

## `worktree`

The daemon creates a git worktree under `.brr/worktrees/<task-id>/` on
a fresh task branch (`brr/<task-id>` for `auto`/`task` strategies, or
the named branch you asked for). The runner cwd points at the worktree;
your main checkout is untouched. After a successful run with
`branch=auto|task`, the daemon `git merge --ff-only`s the branch back
into the host's HEAD and removes the worktree. Named branches are
preserved for human review or PR tooling.

This is the right default for code-modifying work. Combine with debug
mode to inspect a worktree post-task.

## `docker`

The runner command is wrapped in `docker run`. The repo is bind-mounted
into the container at the same absolute path it has on the host, so
file references in prompts and traces remain valid in both directions.
Branch tasks first set up the same worktree as the `worktree` env and
mount that directory instead of the main checkout, keeping the host's
working tree clean.

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
   `~/.codex/`, or `~/.gemini/` exists on the host, it's bind-mounted
   read-write into `/root/<basename>` inside the container. This is
   what makes Claude Pro/Max, ChatGPT Plus/Pro, and Gemini OAuth users
   work without an API key.

You can opt out of the credential-dir mounts with
`docker.mount_credentials=false`. The mounts are read-write so refresh
tokens written inside the container land back on the host — your host
CLI stays authenticated with whatever the agent saw last.

### Git ownership inside the container

The container runs as root by default. The bind-mounted repo is owned
by the host user. Without intervention, git refuses to operate on
"dubious ownership" repos (CVE-2022-24765). brr passes
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
- Run as root, or have HOME set such that the credential mounts
  (`/root/.claude/` etc.) line up with where the runner CLI looks for
  tokens. If you change this, also pass `--user` and adjust HOME via a
  custom image; brr does not expose a `docker.user` knob in v1.

It does *not* need:

- Your project's tooling (test runners, language SDKs beyond what the
  runner needs, build tools). See "Layering project tooling" below.
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

Project-specific tooling (Python, pytest, language SDKs, repo CLIs)
belongs *on top of* the runner image, not inside it. The runner image
stays small and reusable; the project image carries what its tests and
build need:

```dockerfile
FROM ghcr.io/example/your-runner:tag
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml ./
RUN pip install -e ".[dev]"
```

Repos that already maintain a `.devcontainer/devcontainer.json` should
prefer the (planned) `devcontainer` env when it ships, rather than
duplicating that recipe here.

### Container lifecycle

| Outcome                  | Container is...                                   |
| ------------------------ | ------------------------------------------------- |
| `done` + not debug       | Removed (`docker rm -f`)                          |
| `done` + debug           | Preserved (`task.meta.docker_containers` records names) |
| `error` / `conflict`     | Preserved regardless of debug, for salvage        |

Same rule as worktrees: clean teardown only when the task succeeded
and the user isn't debugging. Otherwise we keep the artifact so you
can inspect, re-run, or copy work out manually.

## Durability contract

Across all envs, brr only guarantees three kinds of output survive:

1. **Git commits** on `task.branch_name`, reachable from the host's
   `.git`.
2. **The response file** at `.brr/responses/<event-id>.md`, captured
   from the runner's stdout.
3. **Trace artefacts** under `.brr/traces/<kind>/...`, written when
   debug mode is on.

Anything else an agent writes (untracked files, ephemeral state inside
a container, files in a worktree that didn't get committed) is **not
durable**. The corresponding scratch space — worktree, container,
remote ssh dir — is torn down on a clean non-debug success. Salvage
rule: scratch is preserved on `error` / `conflict` and in debug mode,
so a human can recover work.

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
- **File ownership leaked to root on host** — the runner CLI
  delete-and-recreated a token file inside the container (running as
  root). `chown` it back, and consider raising an issue against that
  CLI. Most CLIs do in-place edits and don't have this problem.
