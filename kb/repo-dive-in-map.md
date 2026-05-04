# Repo Dive-In Map

This page is a bottom-up reading guide for the `brr` repository. It is meant
for a human trying to understand the whole project file by file without losing
the cross-references between concepts.

## Link policy

Links are relative repository links, not absolute GitHub URLs. This is
intentional: relative links work in GitHub, GitHub mobile, local editors, and
non-main branches without pinning the reader to the wrong branch.

When this guide says "source", read the linked file first, then read the linked
tests immediately after. The tests are often the most compact description of
the intended behavior.

Last validated against `feat/task-abstraction` after the environment-policy and
branch-strategy ownership changes (`7faf778`, `022a462`, `e7c1ca1`) and the
run-progress-and-streams UX rework (`run_progress.py`, expanded daemon
lifecycle packets, Telegram/Slack `render_update` live cards, and the demoted
local `status.py`).

## Current ownership snapshot

These are the most important current-shape details to carry while reading:

- Users choose execution isolation with `environment=<auto|host|worktree|docker>`.
- `environment=auto` is deterministic: configured Docker first, then `host` for current-branch tasks and `worktree` for branch tasks.
- Task files still persist the concrete backend as `env`; `env` and `default_env` remain legacy input aliases.
- `branch` is internal staging/delivery state, not the primary user-facing isolation control. The triage agent infers `branch` from the request; the resolver picks the concrete environment.
- Live run UX is remote-first: gates render a per-task progress card from `UpdatePacket`s via the `run_progress` projection. Local `status` is now a troubleshooting view that shares the same projection.
- The stewardship section in [AGENTS.md](../AGENTS.md) is part of the architecture: future changes should improve the underlying design instead of layering conditions onto weak abstractions.

## One-sentence model

`brr` turns external messages into frontmatter-backed event files, triages them
into task files, resolves the user-facing environment policy into a concrete
backend, runs a configured AI CLI there, records the work in streams and traces,
and delivers a response file back through the originating gate.

The whole runtime can be held as:

```text
gate -> event -> stream -> triage -> task -> env -> runner -> response -> gate
```

## Start here

Read these in order if you want the quickest useful mental model:

1. [README](../README.md) for the product shape and CLI surface.
2. [Gate protocol](../src/brr/gates/README.md) for the file-based I/O contract.
3. [Protocol source](../src/brr/protocol.py) with [protocol tests](../tests/test_protocol.py).
4. [Task model](../src/brr/task.py) with [task tests](../tests/test_task.py).
5. [Stream model](../src/brr/stream.py) with [stream tests](../tests/test_stream.py).
6. [Runner plumbing](../src/brr/runner.py) with [runner tests](../tests/test_runner.py).
7. [Environment backends](../src/brr/envs/__init__.py) with [env tests](../tests/test_envs.py).
8. [Daemon worker](../src/brr/daemon.py) with [daemon tests](../tests/test_daemon.py) and [daemon-stream tests](../tests/test_daemon_streams.py).
9. [Bundled execution map](../src/brr/docs/execution-map.md) to re-read the system top-down after seeing the parts.

## Spiral reading route

### Ring 0: package skin

Purpose: know how execution enters the package before studying internals.

Read:

- [pyproject.toml](../pyproject.toml)
- [README](../README.md)
- [AGENTS.md](../AGENTS.md)
- [`src/brr/__init__.py`](../src/brr/__init__.py)
- [`src/brr/__main__.py`](../src/brr/__main__.py)
- [`src/brr/cli.py`](../src/brr/cli.py)

Keep in mind:

- The console script is `brr = brr.cli:main`.
- `python -m brr` delegates to the same CLI.
- The public CLI is intentionally small: `init`, `run`, `auth`, `bind`, `up`, `down`.
- Rich status/inspection helpers exist in [status.py](../src/brr/status.py), but the current CLI tests assert that older public diagnostic commands are not registered.
- [AGENTS.md](../AGENTS.md) now has explicit stewardship guidance: reason from the project's long-term health before changing behavior or design.

Tests:

- [CLI tests](../tests/test_cli.py)

### Ring 1: filesystem atoms

Purpose: understand the primitive file and git contracts. These are the atoms
that all higher-level modules assume.

Read:

- [`src/brr/protocol.py`](../src/brr/protocol.py)
- [`src/brr/config.py`](../src/brr/config.py)
- [`src/brr/gitops.py`](../src/brr/gitops.py)
- [`src/brr/worktree.py`](../src/brr/worktree.py)

Keep in mind:

- Events are markdown files in `.brr/inbox/`.
- Responses are markdown files in `.brr/responses/`.
- Both use a restricted YAML-like frontmatter parser, not PyYAML.
- `.brr/config` is a flat key-value file.
- `gitops.shared_brr_dir()` is critical: in a linked worktree it resolves the shared runtime directory in the main checkout.
- Worktrees live under `.brr/worktrees/<task-id>`.

Tests:

- [protocol tests](../tests/test_protocol.py)
- [config tests](../tests/test_config.py)
- [git/worktree tests](../tests/test_gitops.py)

### Ring 2: state objects

Purpose: learn the durable runtime entities before reading orchestration.

Read:

- [`src/brr/task.py`](../src/brr/task.py)
- [`src/brr/stream.py`](../src/brr/stream.py)
- [`src/brr/updates.py`](../src/brr/updates.py)
- [`src/brr/run_progress.py`](../src/brr/run_progress.py)
- [`src/brr/run_context.py`](../src/brr/run_context.py)

Keep in mind:

- `Task` is the central work unit after triage. It carries the originating event, internal branch/staging state, concrete environment backend, status, source, stream, and metadata.
- `StreamManifest` groups related events/tasks/artifacts into a line of work.
- `UpdatePacket` is lifecycle telemetry for streams and optional gate renderers. The packet vocabulary now covers env prep, attempts, retries, finalize, push, and Docker container births/preservations.
- `RunProgressView` (in `run_progress.py`) folds stream records (manifest + events + tasks + artifacts) into a compact per-task projection that both gates and local diagnostics render. Adding new lifecycle UX should extend this projection, not reinvent rendering per gate.
- `run_context.py` writes a per-task context file under `.brr/runs/<task-id>/context.md` so an agent can recover orientation without poking around runtime state.

Tests:

- [task tests](../tests/test_task.py)
- [stream tests](../tests/test_stream.py)
- [run-progress tests](../tests/test_run_progress.py)
- [daemon-stream tests](../tests/test_daemon_streams.py)
- [daemon-progress-packet tests](../tests/test_daemon_progress_packets.py)
- [status-stream tests](../tests/test_status_streams.py)
- [status-troubleshooting tests](../tests/test_status_troubleshooting.py)

### Ring 3: execution contract

Purpose: understand how `brr` delegates actual work to an external AI runner,
and how the chosen environment shapes that runner invocation.

Read:

- [`src/brr/runner.py`](../src/brr/runner.py)
- [`src/brr/envs/__init__.py`](../src/brr/envs/__init__.py)
- [`src/brr/prompts/runners.md`](../src/brr/prompts/runners.md)
- [`src/brr/prompts/run.md`](../src/brr/prompts/run.md)
- [`src/brr/prompts/triage.md`](../src/brr/prompts/triage.md)
- [`src/brr/prompts/kb-maintenance.md`](../src/brr/prompts/kb-maintenance.md)

Keep in mind:

- `RunnerInvocation` describes one external AI CLI call.
- `RunnerResult` is more than an exit code; it also validates expected artifacts.
- Daemon execution expects a response artifact. A runner can exit successfully and still fail validation if the response file is missing.
- `RunContext` splits host-visible and environment-visible response paths.
- The user-facing policy key is `environment=<auto|host|worktree|docker>` in `.brr/config`; legacy `env` and `default_env` are still accepted.
- Task files still store the concrete backend as `env`.
- Current built-in backends on this branch are `host`, `worktree`, and `docker`. Design notes also discuss future `ssh` and `devcontainer` backends.

Tests:

- [runner tests](../tests/test_runner.py)
- [env tests](../tests/test_envs.py)

### Ring 4: orchestration spine

Purpose: read the actual event-to-response loop after the lower layers make
sense.

Read:

- [`src/brr/daemon.py`](../src/brr/daemon.py)
- [daemon tests](../tests/test_daemon.py)
- [daemon-stream tests](../tests/test_daemon_streams.py)

Read `_run_worker()` in passes rather than all at once:

1. Resolve the incoming event to a stream.
2. Emit stream/event lifecycle updates.
3. Run triage and parse a `Task`.
4. Resolve branch and environment policy into a concrete backend.
5. Prepare the environment.
6. Write the run context file.
7. Build the daemon prompt.
8. Invoke the runner, with retries for missing response artifacts.
9. Parse response frontmatter for outcomes such as `needs_context`.
10. Optionally run KB maintenance.
11. Finalize the environment.
12. Update event/task/stream status.

Keep in mind:

- The daemon is serial in v1: it processes one pending event at a time.
- Gate threads run beside it, but task execution itself is not a worker pool yet.
- Triage and execution are two separate runner invocations.
- `needs_context` is a valid terminal task state, not an exception.
- `branch` is task-internal staging/delivery state. Users usually choose `environment`, not branch strategy.
- Worktree/Docker branch tasks isolate the working directory while sharing the runtime `.brr/`.

### Ring 5: edges and operator views

Purpose: understand how messages enter/leave the core, how live progress is
rendered into remote channels, and how humans inspect runtime state when
something looks wrong.

Read:

- [`src/brr/gates/__init__.py`](../src/brr/gates/__init__.py)
- [`src/brr/gates/telegram.py`](../src/brr/gates/telegram.py)
- [`src/brr/gates/slack.py`](../src/brr/gates/slack.py)
- [`src/brr/gates/git_gate.py`](../src/brr/gates/git_gate.py)
- [`src/brr/status.py`](../src/brr/status.py)
- [`src/brr/docs/__init__.py`](../src/brr/docs/__init__.py)
- [`src/brr/docs/brr-internals.md`](../src/brr/docs/brr-internals.md)
- [`src/brr/docs/streams.md`](../src/brr/docs/streams.md)
- [`src/brr/docs/active-task.md`](../src/brr/docs/active-task.md)

Keep in mind:

- Gates are transport adapters. They should not know about daemon internals.
- Gates create event files and deliver response files.
- `updates.emit()` can call optional gate `render_update()` hooks, but gate-side failures are swallowed.
- Telegram and Slack gates render a live per-task progress card via `render_update`: send-on-`task_created`, edit-on-progress through `editMessageText`/`chat.update`, fallback to a fresh send when the original message is gone. State lives at `.brr/gates/telegram_progress.json` and `.brr/gates/slack_progress.json`.
- The Git gate is a deliberate no-op for live rendering — Git is not a great surface for live progress; commits and PRs remain its primary delivery.
- `status.py` is now a troubleshooting helper, not the primary UX. It uses the same `RunProgressView` projection to keep local and remote views consistent.
- Bundled docs live in `src/brr/docs/`; per-repo overrides live in `.brr/docs/`.
- Project-specific durable knowledge lives in `kb/`, not `.brr/`.

Tests:

- [Telegram gate tests](../tests/test_telegram_gate.py)
- [Telegram render-update tests](../tests/test_telegram_render_update.py)
- [Slack render-update tests](../tests/test_slack_render_update.py)
- [status-stream tests](../tests/test_status_streams.py)
- [status-troubleshooting tests](../tests/test_status_troubleshooting.py)
- [docs tests](../tests/test_docs.py)

## Main entities

### Event

Source:

- [`protocol.create_event()`](../src/brr/protocol.py)
- [`protocol.list_pending()`](../src/brr/protocol.py)
- [`protocol.set_status()`](../src/brr/protocol.py)

Referenced by:

- Gates create events.
- Daemon scans events.
- Task creation copies selected event metadata.
- Streams record event summaries and gate thread keys.

Persistence:

- `.brr/inbox/<event-id>.md`

Important fields:

- `id`
- `source`
- `status`
- gate-specific metadata such as `telegram_chat_id`, `slack_channel`, or `git_file`
- body text after frontmatter

Read with:

- [protocol tests](../tests/test_protocol.py)
- [Telegram gate tests](../tests/test_telegram_gate.py)

### Task

Source:

- [`Task`](../src/brr/task.py)
- [`Task.from_event()`](../src/brr/task.py)
- [`Task.from_triage_output()`](../src/brr/task.py)
- [`resolve_env()`](../src/brr/task.py)
- [`Task.resolve_branch_name()`](../src/brr/task.py)

Referenced by:

- Daemon creates and updates tasks.
- Environments use tasks to decide worktree/branch behavior.
- `run_context.py` renders task metadata into context files.
- `status.py` reads persisted tasks for inspection.

Persistence:

- `.brr/tasks/<task-id>.md`

Important fields:

- `id`
- `event_id`
- `body`
- `branch` for internal staging/delivery behavior
- `env` for the concrete backend (`host`, `worktree`, `docker`, or plugin/future name)
- `status`
- `source`
- `stream_id`
- freeform `meta`

Environment policy details:

- New config should use `environment`.
- `environment=auto` prefers configured Docker isolation, then falls back to `host` for `branch: current` and `worktree` for branch work.
- `env` and `default_env` are legacy aliases still accepted by the resolver.
- Triage may output `environment`, but should usually leave it as `auto` unless the event explicitly asks for a concrete environment.

Read with:

- [task tests](../tests/test_task.py)
- [daemon tests](../tests/test_daemon.py)

### StreamManifest

Source:

- [`StreamManifest`](../src/brr/stream.py)
- [`resolve_for_event()`](../src/brr/stream.py)
- [`append_event()`](../src/brr/stream.py)
- [`append_task()`](../src/brr/stream.py)
- [`append_artifact()`](../src/brr/stream.py)

Referenced by:

- Daemon resolves every event to a stream.
- Runner prompt builders receive stream context.
- Status helpers render streams.
- Updates append lifecycle records to stream logs.

Persistence:

- `.brr/streams/index.json`
- `.brr/streams/<stream-id>/stream.md`
- `.brr/streams/<stream-id>/events.ndjson`
- `.brr/streams/<stream-id>/tasks.ndjson`
- `.brr/streams/<stream-id>/artifacts.ndjson`

Important concepts:

- Explicit `stream_id` wins.
- Related task stream can be reused.
- Gate thread key can reuse a stream.
- Otherwise a fallback stream is created.

Read with:

- [stream tests](../tests/test_stream.py)
- [streams doc](../src/brr/docs/streams.md)

### UpdatePacket

Source:

- [`UpdatePacket`](../src/brr/updates.py)
- [`emit()`](../src/brr/updates.py)
- [`PACKET_TYPES`](../src/brr/updates.py)

Referenced by:

- Daemon emits lifecycle packets at every meaningful step in `_run_worker`.
- `_push_if_needed` emits push packets attributed to the most recent task's stream.
- Stream event logs persist them.
- Gates may render them if they expose `render_update`.
- `run_progress.project_task` walks them to derive the per-task `RunProgressView`.

Persistence:

- `.brr/streams/<stream-id>/events.ndjson`

Stable packet types include:

- `stream_created`
- `event_received`
- `task_created`
- `triage_done`
- `env_prepared`
- `container_started`
- `attempt_started`
- `attempt_failed`
- `retrying`
- `run_started`
- `artifact_created`
- `finalizing`
- `container_preserved`
- `push_started`
- `push_done`
- `needs_context`
- `done`
- `failed`
- `conflict`

Read with:

- [updates source](../src/brr/updates.py)
- [daemon-stream tests](../tests/test_daemon_streams.py)
- [daemon-progress-packet tests](../tests/test_daemon_progress_packets.py)

### RunProgressView

Source:

- [`RunProgressView`](../src/brr/run_progress.py)
- [`project_task()`](../src/brr/run_progress.py)
- [`project_stream_latest()`](../src/brr/run_progress.py)
- [`render_text()`](../src/brr/run_progress.py)

Referenced by:

- Telegram and Slack gates render compact cards from this view.
- `status.get_status` uses it to surface the active task.
- `status.show_stream` uses it to append the latest task's progress.
- `status.inspect_task` uses it for the per-task progress block.

Persistence:

- Derived on demand from `.brr/streams/<stream-id>/{stream.md,events.ndjson,tasks.ndjson,artifacts.ndjson}`. The view itself is not persisted.

Important fields:

- `stream_id`, `task_id`
- `phase` (queued, triage, preparing, running, finalizing, delivering, delivered, needs_context, failed, conflict)
- `state` (active, succeeded, failed, needs_context)
- `branch`, `branch_name`, `base_branch`, `env`, `attempt`
- `started_at`, `updated_at`, `detail`
- `artifacts`, `container_ids`, `response_path`
- `gate_context`, `reply_route` (carried over from the stream manifest)

Important rule:

- New live UX should add packet types to `updates.py`, then teach `run_progress` to fold them into `RunProgressView`. Do not bypass the projection by reading `events.ndjson` directly from each gate.

Read with:

- [run-progress tests](../tests/test_run_progress.py)
- [Telegram render-update tests](../tests/test_telegram_render_update.py)
- [Slack render-update tests](../tests/test_slack_render_update.py)

### RunnerInvocation and RunnerResult

Source:

- [`RunnerInvocation`](../src/brr/runner.py)
- [`RunnerResult`](../src/brr/runner.py)
- [`invoke_runner()`](../src/brr/runner.py)

Referenced by:

- `adopt.py` for setup.
- `runner.run_task()` for the direct `brr run` path.
- `daemon.py` for triage, execution, and KB maintenance.
- `envs` for environment-specific invocation.

Persistence:

- Optional traces under `.brr/traces/<kind>/<label>-<timestamp>/`
- Trace files include prompt, stdout, stderr, metadata, and copied required artifacts.

Important rule:

- `RunnerResult.ok` means subprocess exit code was zero.
- `RunnerResult.validation_ok` means required artifacts exist.
- The daemon cares about the response artifact, not just runner stdout.

Read with:

- [runner tests](../tests/test_runner.py)

### RunContext

Source:

- [`RunContext`](../src/brr/envs/__init__.py)
- [`write_context_file()`](../src/brr/run_context.py)

Referenced by:

- Environment backends return it from `prepare()`.
- Daemon uses it to build prompts and validate response paths.
- `run_context.py` renders it to a recovery document.

Persistence:

- `.brr/runs/<task-id>/context.md`

Important fields:

- `name`
- `cwd`
- `repo_root`
- `runtime_dir`
- `response_path_host`
- `response_path_env`
- `branch_name`
- `base_branch`
- `log_file`
- `env_state`

Read with:

- [run context source](../src/brr/run_context.py)
- [daemon tests](../tests/test_daemon.py)

### EnvBackend

Source:

- [`EnvBackend`](../src/brr/envs/__init__.py)
- [`HostEnv`](../src/brr/envs/__init__.py)
- [`WorktreeEnv`](../src/brr/envs/__init__.py)
- [`DockerEnv`](../src/brr/envs/__init__.py)
- [`get_env()`](../src/brr/envs/__init__.py)

Referenced by:

- Daemon calls `get_env(task.env)`.
- Host and worktree envs call into `runner`, `gitops`, and `worktree`.
- Docker wraps the runner command inside `docker run` and uses worktree behavior for non-current branches.

Important phases:

- `prepare()`
- `invoke()`
- `finalize()`

Read with:

- [env tests](../tests/test_envs.py)
- [env design note](design-env-interface.md)

### Gate module

Source:

- [`gates/__init__.py`](../src/brr/gates/__init__.py)
- [`gates/telegram.py`](../src/brr/gates/telegram.py)
- [`gates/slack.py`](../src/brr/gates/slack.py)
- [`gates/git_gate.py`](../src/brr/gates/git_gate.py)

Referenced by:

- CLI loads gates for `setup`, `auth`, and `bind`.
- Daemon starts configured gates.
- Updates optionally dispatch lifecycle packets to gates.

Daemon hook shape:

- `is_configured(brr_dir)`
- `run_loop(brr_dir, inbox_dir, responses_dir)`

CLI setup hooks:

- `setup(brr_dir)` for the preferred one-step flow
- `auth(brr_dir)` and `bind(brr_dir)` as split setup, or as fallback when
  `setup` is missing

Optional update hook:

- `render_update(brr_dir, packet)` — gates that opt in render a per-task
  progress card from the `RunProgressView` projection. Telegram does this
  via `sendMessage` + `editMessageText`; Slack via `chat.postMessage` +
  `chat.update`. Per-gate progress state lives at
  `.brr/gates/<gate>_progress.json`. The Git gate skips this hook on
  purpose (no live UX).

Read with:

- [gate protocol doc](../src/brr/gates/README.md)
- [Telegram gate tests](../tests/test_telegram_gate.py)
- [Telegram render-update tests](../tests/test_telegram_render_update.py)
- [Slack render-update tests](../tests/test_slack_render_update.py)

## Module cross-reference map

### Entry and commands

- [`__main__.py`](../src/brr/__main__.py) imports [`cli.main`](../src/brr/cli.py).
- [`cli.py`](../src/brr/cli.py) dispatches to:
  - [`adopt.py`](../src/brr/adopt.py) for `brr init`
  - [`runner.py`](../src/brr/runner.py) for `brr run`
  - [`daemon.py`](../src/brr/daemon.py) for `brr up` and `brr down`
  - [`gates.import_gate()`](../src/brr/gates/__init__.py) for `auth` and `bind`

### Bootstrap and project adoption

- [`adopt.py`](../src/brr/adopt.py) depends on:
  - [`gitops.py`](../src/brr/gitops.py) for repo detection
  - [`config.py`](../src/brr/config.py) for `.brr/config`
  - [`runner.py`](../src/brr/runner.py) for setup prompt execution

Tests:

- [adopt tests](../tests/test_adopt.py)
- [integration tests](../tests/test_integration.py)

### Filesystem protocol

- [`protocol.py`](../src/brr/protocol.py) is consumed by:
  - gates, to create events and read responses
  - daemon, to list and update events
  - runner, to parse runner profiles
  - task, to parse/persist task frontmatter
  - stream, to parse stream manifests
  - status, to recover event body text

This is one of the lowest-level modules. Read it early.

### Task and stream state

- [`task.py`](../src/brr/task.py) is consumed by:
  - daemon
  - envs
  - run_context
  - status

- [`stream.py`](../src/brr/stream.py) is consumed by:
  - daemon
  - updates
  - run_progress
  - status

- [`updates.py`](../src/brr/updates.py) depends on stream helpers and is used by daemon. It also dispatches packets to gate `render_update` hooks.

- [`run_progress.py`](../src/brr/run_progress.py) depends on `stream.py`. It is consumed by:
  - Telegram and Slack gate `render_update` hooks
  - status (`get_status`, `show_stream`, `inspect_task`)

The key distinction:

- `Task` answers "what unit of work are we executing?"
- `StreamManifest` answers "what line of conversation/work does this task belong to?"
- `UpdatePacket` answers "what happened in that line of work?"
- `RunProgressView` answers "what is the live state of this task right now, in a form a gate or an operator can render?"

### Runner and prompts

- [`runner.py`](../src/brr/runner.py) owns:
  - runner profile detection
  - command construction
  - subprocess execution
  - trace writing
  - prompt construction
  - recent KB log injection

It is called from:

- [`adopt.py`](../src/brr/adopt.py)
- [`daemon.py`](../src/brr/daemon.py)
- [`envs/__init__.py`](../src/brr/envs/__init__.py)
- [`cli.py`](../src/brr/cli.py)

Prompt files to read with it:

- [`setup.md`](../src/brr/prompts/setup.md)
- [`agents-template.md`](../src/brr/prompts/agents-template.md)
- [`run.md`](../src/brr/prompts/run.md)
- [`triage.md`](../src/brr/prompts/triage.md)
- [`runners.md`](../src/brr/prompts/runners.md)
- [`kb-maintenance.md`](../src/brr/prompts/kb-maintenance.md)

### Execution environments

- [`envs/__init__.py`](../src/brr/envs/__init__.py) depends on:
  - [`gitops.py`](../src/brr/gitops.py)
  - [`worktree.py`](../src/brr/worktree.py)
  - [`runner.py`](../src/brr/runner.py)
  - [`task.py`](../src/brr/task.py)

Host execution:

- runs in the main repo checkout
- requires `branch_name is None`
- finalization is a no-op

Worktree execution:

- creates `.brr/worktrees/<task-id>`
- runs on a concrete branch
- writes per-task log instructions through `RunContext.log_file`
- merges and deletes auto/task branches after successful completion
- preserves worktree state in debug mode or non-done outcomes

Docker execution on the current feature branch:

- requires Docker CLI and `docker.image`
- wraps the normal runner command in `docker run`
- bind-mounts the repo at the same absolute path
- uses worktree-backed branch behavior for non-current branches
- tracks containers for cleanup or salvage

Environment resolution:

- User-facing config should use `environment`.
- `environment=auto` defers to deterministic resolver behavior rather than triage guessing for speed.
- If Docker is configured via `docker.image`, auto selects `docker`.
- Without Docker, auto selects `host` for current-branch tasks and `worktree` for branch tasks.
- If `host` is requested with a non-current branch, the resolver returns `worktree` because host execution cannot run on a separate branch without disturbing the checkout.

### Daemon

[`daemon.py`](../src/brr/daemon.py) is the main integration point. It imports
nearly every core module because it owns the lifecycle:

- config loading
- PID file management
- gate startup
- inbox scan
- stream resolution
- triage
- task persistence
- env prepare/invoke/finalize
- attempt loop with retries and lifecycle packets
- response validation
- optional KB maintenance
- git push attempt with `push_started` / `push_done` packets

The worker emits the full run-progress packet stream (`env_prepared`,
`attempt_started`, `attempt_failed`, `retrying`, `finalizing`, plus
`container_started` / `container_preserved` for the Docker env). Read these
helpers in `daemon.py` next to the worker loop:

- `_emit_new_containers` — diffs `env_ctx.env_state["docker_containers"]` between attempts.
- `_emit_preserved_containers` — fires `container_preserved` when finalize left containers behind.

When debugging behavior, read daemon tests before modifying daemon source:

- [daemon tests](../tests/test_daemon.py)
- [daemon-stream tests](../tests/test_daemon_streams.py)
- [daemon-progress-packet tests](../tests/test_daemon_progress_packets.py)

## Runtime invariants

### `.brr/` is runtime state

Runtime files live in `.brr/` and are gitignored. They include inbox events,
responses, tasks, runs, streams, traces, reviews, worktrees, gate state, prompt
overrides, doc overrides, and config.

Do not confuse `.brr/` with durable project knowledge.

### `kb/` is durable project knowledge

This file lives in `kb/` because it is repo-specific knowledge. It should be
committed and updated when the repo structure changes.

### `src/brr/docs/` is bundled tool documentation

Bundled docs are package data. They explain the tool itself and can be
overridden per repo by `.brr/docs/<topic>.md`.

Relevant decision:

- [Bundled Docs Location](decision-bundled-docs.md)

### Runner success requires artifacts

The runner process can exit zero while still failing the daemon contract if it
does not produce the required response file. Always track both:

- process result
- required artifact validation

### Triage and execution are separate agent calls

The triage prompt classifies the event into a `Task`. It owns the `branch`
inference: code-changing requests get `auto` (or a named branch when the user
points to one), read-only/question requests stay on `current`. It usually
leaves `environment` as `auto` so project config can resolve the backend.
The daemon prompt asks an agent to execute that task. This matters when
reading tests: many daemon tests mock two runner calls.

### Environment is user-facing; branch is internal

Most users should choose an environment policy:

- `environment=auto`
- `environment=host`
- `environment=worktree`
- `environment=docker`

`branch` remains in task files because brr still needs staging/delivery state:
current checkout, generated task branch, new named branch, or existing branch.
It is not the main user-facing isolation control.

### `needs_context` is a first-class outcome

If a task cannot be completed without more input, the response frontmatter can
mark `status: needs_context`. The daemon preserves that task state instead of
turning it into a generic error.

### Streams are not KB

Streams are runtime coordination state. They summarize related events and
artifacts, but durable project knowledge still belongs in `kb/`.

### Run progress is a projection, not state

`RunProgressView` is derived on demand from stream records. The source of
truth is still `events.ndjson` plus the manifest. Rendering UX (gates, local
status) should always go through `run_progress`; introducing parallel
ad-hoc derivations across modules is the path to drift.

### Local status is troubleshooting

The remote gate is the primary surface for run progress. `status.py` exists
to answer "is the daemon healthy, what is the active task, and where are
the trace/response/preserved-container files for a failed run?". It is no
longer the place to add new product UX.

## Tests as a second reading path

If source-first reading feels too abstract, run the test path instead:

1. [protocol tests](../tests/test_protocol.py)
2. [task tests](../tests/test_task.py)
3. [stream tests](../tests/test_stream.py)
4. [run-progress tests](../tests/test_run_progress.py)
5. [runner tests](../tests/test_runner.py)
6. [git/worktree tests](../tests/test_gitops.py)
7. [env tests](../tests/test_envs.py)
8. [daemon tests](../tests/test_daemon.py)
9. [daemon-stream tests](../tests/test_daemon_streams.py)
10. [daemon-progress-packet tests](../tests/test_daemon_progress_packets.py)
11. [gate tests](../tests/test_telegram_gate.py)
12. [Telegram render-update tests](../tests/test_telegram_render_update.py)
13. [Slack render-update tests](../tests/test_slack_render_update.py)
14. [status-stream tests](../tests/test_status_streams.py)
15. [status-troubleshooting tests](../tests/test_status_troubleshooting.py)
16. [adopt tests](../tests/test_adopt.py)
17. [integration tests](../tests/test_integration.py)
18. [CLI tests](../tests/test_cli.py)
19. [docs tests](../tests/test_docs.py)

This order mirrors dependency growth: file protocol, durable state, the
run-progress projection, execution, orchestration, adapters (including their
live-progress hooks), troubleshooting helpers, and finally CLI/bootstrap.

## Design history to read after source

The source tells you what is implemented. These KB pages explain why the system
is shaped this way and where it is going:

- [Branch Modes Plan](plan-branch-modes.md)
- [Concurrent Worktrees Plan](plan-concurrent-worktrees.md)
- [Env Interface design](design-env-interface.md)
- [Workstreams bundled doc](../src/brr/docs/streams.md)
- [Deck: brr today](deck-brr-current.md)
- [Deck: brr fleet and steering](deck-brr-fleet-steering.md)

## Practical navigator notes

Use these heuristics while reading:

- If a file talks about event files, jump to [protocol.py](../src/brr/protocol.py).
- If a file talks about branch/environment/status, jump to [task.py](../src/brr/task.py).
- If a file talks about thread continuity, reply route, or artifacts, jump to [stream.py](../src/brr/stream.py).
- If a file talks about lifecycle packets or `render_update`, jump to [updates.py](../src/brr/updates.py).
- If a file talks about live progress phases, attempt counts, or rendering a per-task card, jump to [run_progress.py](../src/brr/run_progress.py).
- If a file talks about command execution or prompts, jump to [runner.py](../src/brr/runner.py).
- If a file talks about cwd, worktrees, Docker, or response path translation, jump to [envs/__init__.py](../src/brr/envs/__init__.py).
- If a file talks about transport, auth, polling, or delivery, jump to [gates](../src/brr/gates/).
- If a file feels like "everything at once", you are probably in [daemon.py](../src/brr/daemon.py). Read it in lifecycle passes, not top-to-bottom once.

## Maintenance rule for this guide

Update this page when any of these change:

- public CLI commands
- event/task/stream file formats
- environment backends
- daemon lifecycle
- runner artifact contract
- gate hook surface
- bundled docs vs KB ownership
- test files that become the best behavioral reference for a module
