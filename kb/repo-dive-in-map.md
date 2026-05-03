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
branch-strategy ownership changes (`7faf778`, `022a462`, `e7c1ca1`).

## Current ownership snapshot

These are the most important current-shape details to carry while reading:

- Users choose execution isolation with `environment=<auto|host|worktree|docker>`.
- `environment=auto` is deterministic: configured Docker first, then `host` for current-branch tasks and `worktree` for branch tasks.
- Task files still persist the concrete backend as `env`; `env` and `default_env` remain legacy input aliases.
- `branch` is internal staging/delivery state, not the primary user-facing isolation control.
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
- [`src/brr/run_context.py`](../src/brr/run_context.py)

Keep in mind:

- `Task` is the central work unit after triage. It carries the originating event, internal branch/staging state, concrete environment backend, status, source, stream, and metadata.
- `StreamManifest` groups related events/tasks/artifacts into a line of work.
- `UpdatePacket` is lifecycle telemetry for streams and optional gate renderers.
- `run_context.py` writes a per-task context file under `.brr/runs/<task-id>/context.md` so an agent can recover orientation without poking around runtime state.

Tests:

- [task tests](../tests/test_task.py)
- [stream tests](../tests/test_stream.py)
- [daemon-stream tests](../tests/test_daemon_streams.py)
- [status-stream tests](../tests/test_status_streams.py)

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

Purpose: understand how messages enter/leave the core and how humans inspect
runtime state.

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
- Bundled docs live in `src/brr/docs/`; per-repo overrides live in `.brr/docs/`.
- Project-specific durable knowledge lives in `kb/`, not `.brr/`.

Tests:

- [Telegram gate tests](../tests/test_telegram_gate.py)
- [status-stream tests](../tests/test_status_streams.py)
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

Referenced by:

- Daemon emits lifecycle packets.
- Stream event logs persist them.
- Gates may render them if they expose `render_update`.

Persistence:

- `.brr/streams/<stream-id>/events.ndjson`

Stable packet types include:

- `stream_created`
- `event_received`
- `task_created`
- `triage_done`
- `run_started`
- `artifact_created`
- `needs_context`
- `done`
- `failed`
- `conflict`

Read with:

- [updates source](../src/brr/updates.py)
- [daemon-stream tests](../tests/test_daemon_streams.py)

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

- CLI loads gates for `auth` and `bind`.
- Daemon starts configured gates.
- Updates optionally dispatch lifecycle packets to gates.

Required hook shape:

- `auth(brr_dir)`
- `bind(brr_dir)`
- `is_configured(brr_dir)`
- `run_loop(brr_dir, inbox_dir, responses_dir)`

Optional hook:

- `render_update(brr_dir, packet)`

Read with:

- [gate protocol doc](../src/brr/gates/README.md)
- [Telegram gate tests](../tests/test_telegram_gate.py)

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
  - status

- [`updates.py`](../src/brr/updates.py) depends on stream helpers and is used by daemon.

The key distinction:

- `Task` answers "what unit of work are we executing?"
- `StreamManifest` answers "what line of conversation/work does this task belong to?"
- `UpdatePacket` answers "what happened in that line of work?"

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
- response validation
- optional KB maintenance
- git push attempt

When debugging behavior, read daemon tests before modifying daemon source:

- [daemon tests](../tests/test_daemon.py)
- [daemon-stream tests](../tests/test_daemon_streams.py)

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

The triage prompt classifies the event into a `Task`. It decides how brr should
stage code changes (`branch`) and usually leaves `environment` as `auto` so
project config can resolve the backend. The daemon prompt asks an agent to
execute that task. This matters when reading tests: many daemon tests mock two
runner calls.

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

## Tests as a second reading path

If source-first reading feels too abstract, run the test path instead:

1. [protocol tests](../tests/test_protocol.py)
2. [task tests](../tests/test_task.py)
3. [stream tests](../tests/test_stream.py)
4. [runner tests](../tests/test_runner.py)
5. [git/worktree tests](../tests/test_gitops.py)
6. [env tests](../tests/test_envs.py)
7. [daemon tests](../tests/test_daemon.py)
8. [daemon-stream tests](../tests/test_daemon_streams.py)
9. [gate tests](../tests/test_telegram_gate.py)
10. [status-stream tests](../tests/test_status_streams.py)
11. [adopt tests](../tests/test_adopt.py)
12. [integration tests](../tests/test_integration.py)
13. [CLI tests](../tests/test_cli.py)
14. [docs tests](../tests/test_docs.py)

This order mirrors dependency growth: file protocol, durable state, execution,
orchestration, adapters, and finally CLI/bootstrap.

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
