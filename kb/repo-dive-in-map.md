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

Last validated against `ux-imporvements` after Phase 2 of the
[kb-shape decision](decision-kb-shape.md): `AGENTS.md` moved into the
package at `src/brr/AGENTS.md` (symlinked at the root), per-task
`kb/log-<task-id>.md` plumbing removed end-to-end, the delivery
contract was sharpened to make stdout the chat reply with mandatory
commits for any file writes, and the Telegram/Slack progress-card
rendering deduplicates against the last-rendered text. Phase 3a then
split `runner.py` — the prompt-assembly half lives in
[`src/brr/prompts.py`](../src/brr/prompts.py); subprocess plumbing
stays in [`runner.py`](../src/brr/runner.py).

Earlier still-relevant changes carried forward: the environment-policy
ownership rework, the 2026-05-05 streams-to-conversations refactor
that dropped `.brr/streams/`, the workstream manifest, and the
corresponding CLI surfaces (see
[decision-drop-streams.md](decision-drop-streams.md)); the 2026-05-06
docker beginner-friendly slice that added automatic credential wiring,
host login-dir bind mounts, and the `safe.directory` injection (and
the bundled [`envs.md`](../src/brr/docs/envs.md) doc); and the
2026-05-06 removal of the LLM-driven triage stage in favor of
mechanical task construction, agent-owned branching, and plain-text
responses (see [decision-remove-triage.md](decision-remove-triage.md)).

## Current ownership snapshot

These are the most important current-shape details to carry while reading:

- Users choose execution isolation with `environment=<auto|host|worktree|docker>`.
- `environment=auto` is deterministic: configured Docker first, otherwise `worktree`. `host` is never auto-picked.
- Task files still persist the concrete backend as `env`; `env` and `default_env` remain legacy input aliases.
- There is no LLM triage step. `Task.from_event` builds tasks mechanically from the inbox event and `.brr/config`.
- The agent owns branching at runtime. Worktree/Docker tasks always start on a fresh `brr/<task-id>` branch sprouted from HEAD; commits there fast-forward back, switching to a new branch with `git switch -c` preserves it.
- Responses are plain text — no frontmatter contract on `.brr/responses/`. If the agent can't complete the task, it explains why and the operator follows up in-thread.
- Live run UX is remote-first: gates render a per-task progress card from `UpdatePacket`s via the `run_progress` projection. Local `status` is now a troubleshooting view that shares the same projection.
- The stewardship section in [AGENTS.md](../AGENTS.md) is part of the architecture: future changes should improve the underlying design instead of layering conditions onto weak abstractions.

## One-sentence model

`brr` turns external messages into frontmatter-backed event files,
constructs task files from them mechanically, resolves the user-facing
environment policy into a concrete backend, runs a configured AI CLI
there, appends every step to a per-gate-thread conversation log, and
delivers a plain-text response file back through the originating gate.

The whole runtime can be held as:

```text
gate -> event -> conversation -> task -> env -> runner -> response -> gate
```

## Start here

Read these in order if you want the quickest useful mental model:

1. [README](../README.md) for the product shape and CLI surface.
2. [Gate protocol](../src/brr/gates/README.md) for the file-based I/O contract.
3. [Protocol source](../src/brr/protocol.py) with [protocol tests](../tests/test_protocol.py).
4. [Task model](../src/brr/task.py) with [task tests](../tests/test_task.py).
5. [Conversation log](../src/brr/conversations.py) with [conversation tests](../tests/test_conversations.py).
6. [Runner plumbing](../src/brr/runner.py) with [runner tests](../tests/test_runner.py), then [prompt assembly](../src/brr/prompts.py) with [prompt tests](../tests/test_prompts.py).
7. [Environment backends](../src/brr/envs/__init__.py) with [env tests](../tests/test_envs.py).
8. [Daemon worker](../src/brr/daemon.py) with [daemon tests](../tests/test_daemon.py) and [daemon-conversation tests](../tests/test_daemon_conversations.py).
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
- [`src/brr/conversations.py`](../src/brr/conversations.py)
- [`src/brr/updates.py`](../src/brr/updates.py)
- [`src/brr/run_progress.py`](../src/brr/run_progress.py)
- [`src/brr/run_context.py`](../src/brr/run_context.py)

Keep in mind:

- `Task` is the central work unit constructed mechanically from an event. It carries the originating event, concrete environment backend, status, source, conversation key, and freeform metadata (worktree path, branch name, response path, etc.). There is no longer a `branch` field — branching is decided by the agent inside the worktree at runtime.
- A conversation is just a per-gate-thread append-only ndjson log of events, tasks, artifacts, and lifecycle update packets. There is no manifest, no title, no intent — those leaky stream-identity fields were removed in the 2026-05-05 refactor (see [decision-drop-streams.md](decision-drop-streams.md)).
- `UpdatePacket` is lifecycle telemetry routed to a conversation log and, optionally, gate `render_update` hooks. The packet vocabulary covers env prep, attempts, retries, finalize, push, and Docker container births/preservations.
- `RunProgressView` (in `run_progress.py`) folds conversation records into a compact per-task projection that both gates and local diagnostics render. Adding new lifecycle UX should extend this projection, not reinvent rendering per gate.
- `run_context.py` writes a per-task context file under `.brr/runs/<task-id>/context.md` so an agent can recover orientation without poking around runtime state.

Tests:

- [task tests](../tests/test_task.py)
- [conversation tests](../tests/test_conversations.py)
- [run-progress tests](../tests/test_run_progress.py)
- [daemon-conversation tests](../tests/test_daemon_conversations.py)
- [daemon-progress-packet tests](../tests/test_daemon_progress_packets.py)
- [status-troubleshooting tests](../tests/test_status_troubleshooting.py)

### Ring 3: execution contract

Purpose: understand how `brr` delegates actual work to an external AI runner,
and how the chosen environment shapes that runner invocation.

Read:

- [`src/brr/runner.py`](../src/brr/runner.py)
- [`src/brr/envs/__init__.py`](../src/brr/envs/__init__.py)
- [`src/brr/prompts/runners.md`](../src/brr/prompts/runners.md)
- [`src/brr/prompts/run.md`](../src/brr/prompts/run.md)
- [`src/brr/prompts/kb-maintenance.md`](../src/brr/prompts/kb-maintenance.md)

Keep in mind:

- `RunnerInvocation` describes one external AI CLI call.
- `RunnerResult.validation_ok` combines three layers: subprocess exit, the optional `required_artifacts` check (used by `adopt` for AGENTS.md / kb files), and the `has_response` check that fires only when the invocation specifies a `response_path`.
- The runner contract is "stdout is the response": `claude --print`, `codex exec`, and `gemini -p --yolo` all print only the final agent message to stdout. `invoke_runner` captures stdout and writes it to the task response file itself, so no per-runner output flag is needed.
- Daemon retry triggers on empty stdout, not a missing file.
- `RunContext` splits host-visible and environment-visible response paths so Docker invocations can resolve mount-aware paths even though brr (not the runner) writes the file.
- The user-facing policy key is `environment=<auto|host|worktree|docker>` in `.brr/config`; legacy `env` and `default_env` are still accepted.
- Task files still store the concrete backend as `env`.
- Current built-in backends on this branch are `host`, `worktree`, and `docker`. Design notes also discuss future `ssh` and `devcontainer` backends.
- The Docker env auto-wires credentials so users don't have to bake them into images: known runner env vars (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`) pass through, host login dirs (`~/.claude`, `~/.claude.json`, `~/.codex`, `~/.gemini`) bind-mount into `/root/<basename>` when present, and `safe.directory='*'` is injected via `GIT_CONFIG_*` env vars so git works against the bind-mounted repo regardless of UID. Toggles: `docker.env=KEY1,KEY2` and `docker.mount_credentials=false`. The bundled [`envs.md`](../src/brr/docs/envs.md) is the user-facing reference.

Tests:

- [runner tests](../tests/test_runner.py)
- [env tests](../tests/test_envs.py)

### Ring 4: orchestration spine

Purpose: read the actual event-to-response loop after the lower layers make
sense.

Read:

- [`src/brr/daemon.py`](../src/brr/daemon.py)
- [daemon tests](../tests/test_daemon.py)
- [daemon-conversation tests](../tests/test_daemon_conversations.py)

Read `_run_worker()` in passes rather than all at once:

1. Resolve the incoming event to a conversation key (gate-thread fingerprint).
2. Append the event arrival and emit `event_received`.
3. Build the `Task` from the event with `Task.from_event`; emit `task_created`.
4. Resolve the environment policy into a concrete backend.
5. Prepare the environment (worktree creation included); emit `env_prepared`.
6. Write the run context file (with the recent conversation block).
7. Build the daemon prompt (also threading the recent conversation block).
8. Invoke the runner, with retries when the runner prints no final reply on stdout.
9. Capture the plain-text response file (written from stdout).
10. Optionally run KB maintenance.
11. Finalize the environment — `WorktreeEnv.finalize` reads the worktree's git state to decide between fast-forward landing and branch preservation.
12. Update task status and append matching update packets to the conversation log.

Keep in mind:

- The daemon is serial in v1: it processes one pending event at a time.
- Gate threads run beside it, but task execution itself is not a worker pool yet.
- There is exactly one runner invocation per attempt — no separate triage call.
- The agent owns branching: brr only decides whether to fast-forward back or preserve the branch as-is.
- Worktree/Docker tasks isolate the working directory while sharing the runtime `.brr/`.

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
- [`src/brr/docs/conversations.md`](../src/brr/docs/conversations.md)
- [`src/brr/docs/active-task.md`](../src/brr/docs/active-task.md)
- [`src/brr/docs/envs.md`](../src/brr/docs/envs.md)
- [`src/brr/docs/execution-map.md`](../src/brr/docs/execution-map.md)

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
- [gate setup tests](../tests/test_gate_setup.py)
- [Telegram render-update tests](../tests/test_telegram_render_update.py)
- [Slack render-update tests](../tests/test_slack_render_update.py)
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
- Conversation logs record event summaries keyed by gate thread.

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
- [`resolve_env()`](../src/brr/task.py)

Referenced by:

- Daemon creates and updates tasks.
- Environments use tasks to set up the worktree and route the runner.
- `run_context.py` renders task metadata into context files.
- `status.py` reads persisted tasks for inspection.

Persistence:

- `.brr/tasks/<task-id>.md`

Important fields:

- `id`
- `event_id`
- `body`
- `env` for the concrete backend (`host`, `worktree`, `docker`, or plugin/future name)
- `status`
- `source`
- `conversation_key` (gate-thread fingerprint, used to route lifecycle records and progress cards)
- freeform `meta` (carries gate delivery info such as `telegram_chat_id` or `slack_channel`, plus runtime branch/worktree paths populated by env prepare/finalize)

Environment policy details:

- New config should use `environment`.
- `environment=auto` prefers configured Docker isolation, otherwise picks `worktree`. `host` is never auto-picked.
- `env` and `default_env` are legacy aliases still accepted by the resolver.
- The env is resolved deterministically when the task is built — there is no LLM in the loop.

Read with:

- [task tests](../tests/test_task.py)
- [daemon tests](../tests/test_daemon.py)

### Conversation log

Source:

- [`conversation_key_for_event()`](../src/brr/conversations.py)
- [`append_event()` / `append_task()` / `append_artifact()` / `append_update()`](../src/brr/conversations.py)
- [`read_records()` / `read_recent()` / `records_for_task()`](../src/brr/conversations.py)

Referenced by:

- Daemon routes every event to a conversation key and appends lifecycle records.
- Runner prompt builders receive recent records and render them under a `Recent in this conversation` block.
- Status helpers project conversation records into `RunProgressView`.
- Updates append lifecycle update packets to the same per-conversation log.

Persistence:

- `.brr/conversations/<safe-key>.ndjson` — one append-only ndjson per gate thread; `:` is encoded as `__` in filenames.

Important concepts:

- Conversations have no manifest, no title, no intent. Identity is the bug we removed; see [decision-drop-streams.md](decision-drop-streams.md).
- The conversation key is `telegram:<chat>:<topic>`, `slack:<channel>:<thread_ts>`, or `git:<file>` — a gate-thread fingerprint.
- Each record carries `ts` and a `kind` discriminator (`event`, `task`, `artifact`, `update`).
- Lines of work that span runs belong in `kb/`, not in a runtime field.

Read with:

- [conversation tests](../tests/test_conversations.py)
- [conversations doc](../src/brr/docs/conversations.md)

### UpdatePacket

Source:

- [`UpdatePacket`](../src/brr/updates.py)
- [`emit()`](../src/brr/updates.py)
- [`PACKET_TYPES`](../src/brr/updates.py)

Referenced by:

- Daemon emits lifecycle packets at every meaningful step in `_run_worker`.
- `_push_if_needed` emits push packets routed to the task's conversation.
- Conversation logs persist packets as `kind=update` rows.
- Gates may render them if they expose `render_update`.
- `run_progress.project_task` walks them to derive the per-task `RunProgressView`.

Persistence:

- `.brr/conversations/<safe-key>.ndjson` (records with `kind=update`)

Stable packet types, in roughly chronological order (see `PACKET_TYPES` in `updates.py` for the canonical list):

- `event_received`
- `task_created`
- `env_prepared`
- `container_started`
- `run_started`
- `attempt_started`
- `attempt_failed`
- `retrying`
- `artifact_created`
- `finalizing`
- `container_preserved`
- `push_started`
- `push_done`
- `done`
- `failed`
- `conflict`

Read with:

- [updates source](../src/brr/updates.py)
- [daemon-conversation tests](../tests/test_daemon_conversations.py)
- [daemon-progress-packet tests](../tests/test_daemon_progress_packets.py)

### RunProgressView

Source:

- [`RunProgressView`](../src/brr/run_progress.py)
- [`project_task()`](../src/brr/run_progress.py)
- [`project_conversation_latest()`](../src/brr/run_progress.py)
- [`render_text()`](../src/brr/run_progress.py)

Referenced by:

- Telegram and Slack gates render compact cards from this view.
- `status.get_status` uses it to surface the active task across conversations.
- `status.inspect_task` uses it for the per-task progress block.

Persistence:

- Derived on demand from `.brr/conversations/<safe-key>.ndjson` filtered by `task_id`. The view itself is not persisted.

Important fields:

- `conversation_key`, `task_id`, `event_id`
- `phase` (queued, preparing, running, finalizing, delivering, delivered, failed, conflict)
- `state` (active, succeeded, failed)
- `branch_name`, `base_branch`, `env`, `attempt`
- `started_at`, `updated_at`, `detail`, `error`
- `artifacts`, `container_ids`, `response_path`

Important rule:

- New live UX should add packet types to `updates.py`, then teach `run_progress` to fold them into `RunProgressView`. Do not bypass the projection by reading the conversation log directly from each gate.

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
- `daemon.py` for execution and KB maintenance.
- `envs` for environment-specific invocation.

Persistence:

- Optional traces under `.brr/traces/<kind>/<label>-<timestamp>/`
- Trace files include prompt, stdout, stderr, metadata, and copies of any expected files registered through `required_artifacts` (today: adopt's AGENTS.md and kb files).

Important rule:

- `RunnerResult.ok` means subprocess exit code was zero.
- `RunnerResult.has_response` means stdout was non-empty (only meaningful when `invocation.response_path` is set).
- `RunnerResult.validation_ok` is the combined contract: exit zero, no missing required artifacts, and `has_response` whenever a response was requested.
- For daemon-run invocations, the response file is written by `invoke_runner` from captured stdout; the agent does not write that file.

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
  - status, to recover event body text

This is one of the lowest-level modules. Read it early.

### Task and conversation state

- [`task.py`](../src/brr/task.py) is consumed by:
  - daemon
  - envs
  - run_context
  - status
  - gates (to look up delivery info from `task.meta`)

- [`conversations.py`](../src/brr/conversations.py) is consumed by:
  - daemon
  - updates
  - run_progress
  - run_context
  - status

- [`updates.py`](../src/brr/updates.py) depends on `conversations` for routing packets and is used by daemon. It also dispatches packets to gate `render_update` hooks.

- [`run_progress.py`](../src/brr/run_progress.py) depends on `conversations`. It is consumed by:
  - Telegram and Slack gate `render_update` hooks
  - status (`get_status`, `inspect_task`)

The key distinction:

- `Task` answers "what unit of work are we executing?"
- The conversation log answers "what has happened in this gate thread, in order?"
- `UpdatePacket` answers "what just changed for a particular task?"
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

- [`setup.md`](../src/brr/prompts/setup.md) — adopter setup; reads brr's own [`AGENTS.md`](../src/brr/AGENTS.md) as the model
- [`run.md`](../src/brr/prompts/run.md)
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

- creates `.brr/worktrees/<task-id>` on a fresh `brr/<task-id>` branch sprouted from HEAD
- writes per-task log instructions through `RunContext.log_file`
- finalize reads the worktree's git state: fast-forward back if the agent committed on the original branch, preserve otherwise
- preserves worktree state in debug mode or non-done outcomes

Docker execution:

- requires Docker CLI and `docker.image`
- wraps the normal runner command in `docker run`
- bind-mounts the repo at the same absolute path
- always uses a worktree on a fresh `brr/<task-id>` branch, so the host's working tree stays clean
- tracks containers for cleanup or salvage
- forwards known runner env vars (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`) and any names listed in `docker.env=` when set on the daemon
- bind-mounts host login directories (`~/.claude`, `~/.claude.json`, `~/.codex`, `~/.gemini`) into `/root/<basename>` when present, unless `docker.mount_credentials=false`
- injects `safe.directory='*'` via git's `GIT_CONFIG_*` env vars so git works against the bind-mounted repo even though the container runs as root and the host repo is owned by another UID — no per-image baked-in config required

Environment resolution:

- User-facing config should use `environment`.
- `environment=auto` is deterministic: configured Docker first, otherwise `worktree`. `host` is never auto-picked.
- If Docker is configured via `docker.image` and Docker is on PATH, auto selects `docker`.
- Otherwise, auto selects `worktree`. Pick `host` explicitly if you want to forgo isolation.

### Daemon

[`daemon.py`](../src/brr/daemon.py) is the main integration point. It imports
nearly every core module because it owns the lifecycle:

- config loading
- PID file management
- gate startup
- inbox scan
- conversation key derivation
- mechanical task construction (`Task.from_event`)
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
- [daemon-conversation tests](../tests/test_daemon_conversations.py)
- [daemon-progress-packet tests](../tests/test_daemon_progress_packets.py)

## Runtime invariants

### `.brr/` is runtime state

Runtime files live in `.brr/` and are gitignored. They include inbox events,
responses, tasks, runs, conversations, traces, reviews, worktrees, gate state,
prompt overrides, doc overrides, and config.

Do not confuse `.brr/` with durable project knowledge.

### `kb/` is durable project knowledge

This file lives in `kb/` because it is repo-specific knowledge. It should be
committed and updated when the repo structure changes.

### `src/brr/docs/` is bundled tool documentation

Bundled docs are package data. They explain the tool itself and can be
overridden per repo by `.brr/docs/<topic>.md`.

Relevant decision:

- [Bundled Docs Location](decision-bundled-docs.md)

### Runner success has three layers

The runner contract has three layers, all checked by
`RunnerResult.validation_ok`:

- subprocess exit zero (`result.ok`)
- `required_artifacts` all present (used by `adopt` for AGENTS.md and kb
  scaffolding; daemon-run invocations don't register any)
- `has_response` — non-empty stdout — when the invocation specifies a
  `response_path`. brr captures stdout and writes the response file itself,
  so empty stdout is the canonical failure signal for an unproductive run
  and triggers daemon retry.

### Task construction is mechanical, not LLM-driven

There is no triage prompt. `Task.from_event` builds the task directly
from the inbox event and `.brr/config`. Daemon tests mock exactly one
runner invocation per attempt — the execution call. See
[decision-remove-triage.md](decision-remove-triage.md) for the
rationale and what was removed (`prompts/triage.md`,
`Task.from_triage_output`, the `branch` field, the `triage_done` /
`needs_context` packets, the `needs_context` task status, and the
frontmatter contract on response files).

### The agent owns branching at runtime

Worktree and Docker tasks always start on a fresh `brr/<task-id>`
branch sprouted from the current `HEAD`. The agent inside the worktree
decides:

- commit on the current branch and let brr fast-forward it back onto
  the base branch (default for code that should land), or
- `git switch -c <name>` before committing, so brr preserves the
  branch as-is for human review or PR tooling.

`WorktreeEnv.finalize` reads the worktree's git state to make that
decision — there is no frozen branch strategy on the task file.

### Environment is the user-facing isolation knob

Most users should choose an environment policy:

- `environment=auto`
- `environment=host`
- `environment=worktree`
- `environment=docker`

The env is resolved deterministically when the task is built. There is
no per-task `branch` field anymore — runtime branch state lives in
`task.meta["branch_name"]` after `prepare`/`finalize`.

### Responses are plain text

`.brr/responses/<event-id>.md` carries the agent's final stdout
verbatim. There is no frontmatter contract. If the agent cannot
complete the task (missing context, ambiguous request, unreachable
service), it should say so plainly and stop. The operator sees the
reply in the gate thread and follows up with another event.

### Conversations are not KB

Conversation logs are runtime coordination state. They record events and
update packets, but durable project knowledge still belongs in `kb/`. The
2026-05-05 refactor explicitly removed identity fields (title, intent) from
runtime — see [decision-drop-streams.md](decision-drop-streams.md). If a
line of work matters enough to name, it belongs as a `kb/` page.

### Run progress is a projection, not state

`RunProgressView` is derived on demand from conversation records, filtered
by `task_id`. The source of truth is the per-conversation ndjson. Rendering
UX (gates, local status) should always go through `run_progress`; introducing
parallel ad-hoc derivations across modules is the path to drift.

### Local status is troubleshooting

The remote gate is the primary surface for run progress. `status.py` exists
to answer "is the daemon healthy, what is the active task, and where are
the trace/response/preserved-container files for a failed run?". It is no
longer the place to add new product UX.

## Tests as a second reading path

If source-first reading feels too abstract, run the test path instead:

1. [protocol tests](../tests/test_protocol.py)
2. [task tests](../tests/test_task.py)
3. [conversation tests](../tests/test_conversations.py)
4. [run-progress tests](../tests/test_run_progress.py)
5. [runner tests](../tests/test_runner.py)
6. [git/worktree tests](../tests/test_gitops.py)
7. [env tests](../tests/test_envs.py)
8. [daemon tests](../tests/test_daemon.py)
9. [daemon-conversation tests](../tests/test_daemon_conversations.py)
10. [daemon-progress-packet tests](../tests/test_daemon_progress_packets.py)
11. [gate tests](../tests/test_telegram_gate.py)
12. [gate setup tests](../tests/test_gate_setup.py)
13. [Telegram render-update tests](../tests/test_telegram_render_update.py)
14. [Slack render-update tests](../tests/test_slack_render_update.py)
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

- [Env Interface design](design-env-interface.md) — current, in flight
- [Branch Modes Plan](plan-branch-modes.md) — shipped, with revisions
- [Concurrent Worktrees Plan](plan-concurrent-worktrees.md) — shipped
- [Remove triage decision](decision-remove-triage.md)
- [Drop streams decision](decision-drop-streams.md)
- [kb shape decision](decision-kb-shape.md)
- [Conversations bundled doc](../src/brr/docs/conversations.md)
- [Deck: brr fleet & steering](deck-brr-fleet-steering.md) — strategic, partially stale

## Practical navigator notes

Use these heuristics while reading:

- If a file talks about event files, jump to [protocol.py](../src/brr/protocol.py).
- If a file talks about environment/status, jump to [task.py](../src/brr/task.py).
- If a file talks about branching, jump to [worktree.py](../src/brr/worktree.py) and `WorktreeEnv` in [envs/__init__.py](../src/brr/envs/__init__.py) — the agent owns branching at runtime.
- If a file talks about thread continuity or per-thread history, jump to [conversations.py](../src/brr/conversations.py).
- If a file talks about lifecycle packets or `render_update`, jump to [updates.py](../src/brr/updates.py).
- If a file talks about live progress phases, attempt counts, or rendering a per-task card, jump to [run_progress.py](../src/brr/run_progress.py).
- If a file talks about prompt assembly (Task Context Bundle, `kb/log.md` injection, AGENTS.md bundling), jump to [prompts.py](../src/brr/prompts.py). If it talks about subprocess execution / runner detection / trace persistence, jump to [runner.py](../src/brr/runner.py). The two used to be one file; they were split in phase 3a of the kb-shape arc.
- If a file talks about cwd, worktrees, Docker, response path translation, or runner credential wiring (env passthrough, login-dir mounts, git safe.directory), jump to [envs/__init__.py](../src/brr/envs/__init__.py).
- If a file talks about transport, auth, polling, or delivery, jump to [gates](../src/brr/gates/).
- If a file feels like "everything at once", you are probably in [daemon.py](../src/brr/daemon.py). Read it in lifecycle passes, not top-to-bottom once.

## Maintenance rule for this guide

Update this page when any of these change:

- public CLI commands
- event/task/conversation file formats
- environment backends
- daemon lifecycle
- runner artifact contract
- gate hook surface
- bundled docs vs KB ownership
- test files that become the best behavioral reference for a module
