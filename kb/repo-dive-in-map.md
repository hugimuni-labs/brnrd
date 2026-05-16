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

Reflects the current `main`. The major architectural arcs this guide
assumes you'll meet in the codebase are linked under the relevant
ring — this header just names the ones that change the *reading* most:

- `AGENTS.md` is the universal schema every tool reads; it lives in
  the package at [`src/brr/AGENTS.md`](../src/brr/AGENTS.md) and is
  symlinked from the repo root.
- Task construction is mechanical — no LLM triage step,
  see [`decision-remove-triage.md`](decision-remove-triage.md).
- Branch intent is deterministic and structured —
  see [`design-daemon-landing-branch.md`](design-daemon-landing-branch.md);
  the agent owns runtime branching inside the worktree.
- The daemon refreshes local refs before resolving the branch plan —
  one `git fetch` plus a best-effort ff-only of the target branches via
  [`sync.py`](../src/brr/sync.py); see
  [`design-git-layer-rework.md`](design-git-layer-rework.md). The
  invariant: every worktree sprouts from a current view of the remote.
- Environments are pluggable behind a three-phase `prepare → invoke →
  finalize` protocol — see
  [`design-env-interface.md`](design-env-interface.md). Worktree and
  Docker scratch is outcome-aware: torn down on clean `done`,
  preserved on `error`/`conflict`/uncommitted state.
- Gates are transport adapters; `telegram`, `slack`, and `github` ship
  built-in. Telegram/Slack are chat surfaces that render a live progress
  card via `render_update`; the GitHub gate (label-on-issue and
  mention-in-comment triggers) posts replies as PR/issue comments and
  passes through `branch_target` so the sync hook refreshes the PR
  head before the worker runs.
- The kb is the persistent semantic memory; the kb-shape pattern is
  synthesised in [`subject-kb.md`](subject-kb.md). Maintenance is a
  deterministic preflight ([`kb_preflight.py`](../src/brr/kb_preflight.py))
  plus graph stats ([`kb_health.py`](../src/brr/kb_health.py)), feeding
  an inline LLM cleanup pass after task delivery.

Past arcs (the kb-shape arc, the 2026-05-05 streams-to-conversations
refactor, the 2026-05-06 triage removal, the 2026-05-12 branch-plan
simplification, the Docker host-UID rework, the 2026-05-15 git-layer
rework that introduced `sync.py` and the github gate, the 2026-05-16
test-suite grooming) live in `git log` and in their decision/design
pages. The current shape is what this guide describes; lineage
breadcrumbs sit on the relevant kb pages.

## Current ownership snapshot

These are the most important current-shape details to carry while reading:

- Users choose execution isolation with `environment=<auto|host|worktree|docker>`.
- `environment=auto` is deterministic: configured Docker first, otherwise `worktree`. `host` is never auto-picked.
- Task files still persist the concrete backend as `env`; `env` and `default_env` remain legacy input aliases.
- There is no LLM triage step. `Task.from_event` builds tasks mechanically from the inbox event and `.brr/config`.
- Before resolving the branch plan, the daemon runs
  `sync.refresh_before_task`: one `git fetch <default-remote>` plus a
  best-effort ff-only of the local default branch and any structured
  branch named in the event. Opt-outs are `sync.fetch_before_task` and
  `sync.fast_forward_default` in `.brr/config`. Sync outcomes ride on
  the progress card as `synced` packets; the no-op path stays quiet.
- The daemon resolves branch intent before env prep. Worktree/Docker
  tasks start on `brr/<task-id>` from `seed_ref`; commits there
  fast-forward an auto-land target when one exists, otherwise the task
  branch is preserved and pushed when a remote is configured. Switching
  to a new branch with `git switch -c` still preserves the agent's
  runtime choice.
- Responses are plain text — no frontmatter contract on `.brr/responses/`. If the agent can't complete the task, it explains why and the operator follows up in-thread.
- Live run UX is remote-first: gates render a per-task progress card from `UpdatePacket`s via the `run_progress` projection. Long-running attempts emit periodic `heartbeat` packets (every 30s) so the card visibly bumps elapsed time. There is no separate local status module; troubleshooting follows run context, task, conversation, trace, and response artifacts.
- After a successful push, the daemon derives a clickable "view branch" URL via [`forges.py`](../src/brr/forges.py) (GitHub / GitLab / Bitbucket / Gitea host patterns, plus `[forge]` override) and attaches it to the `push_done` packet so the gate can put a real link in front of the user.
- The [stewardship section in `src/brr/AGENTS.md`](../src/brr/AGENTS.md) is part of the architecture: treat the request as input, not as instructions; reason from first principles before changing behaviour; and **surface contradictions** between the request and the codebase rather than silently following either side. Functional, not aspirational — failing to bubble up a contradiction is a real bug in the workflow, not a stylistic miss.

## One-sentence model

`brr` turns external messages into frontmatter-backed event files,
constructs task files from them mechanically, resolves branch intent
and user-facing environment policy into a concrete backend, runs a
configured AI CLI there, appends every step to a per-gate-thread
conversation log, and delivers a plain-text response file back through
the originating gate.

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
7. [Environment backends](../src/brr/envs/__init__.py) with [env tests](../tests/test_envs.py) and [Dockerfile tests](../tests/test_dockerfile.py).
8. [Daemon worker](../src/brr/daemon.py) plus
   [developer reload](../src/brr/dev_reload.py) with
   [daemon tests](../tests/test_daemon.py),
   [developer reload tests](../tests/test_dev_reload.py), and
   [daemon-conversation tests](../tests/test_daemon_conversations.py).
9. [Bundled execution map](../src/brr/docs/execution-map.md) to re-read the system top-down after seeing the parts.

## Spiral reading route

### Ring 0: package skin

Purpose: know how execution enters the package before studying internals.

Read:

- [pyproject.toml](../pyproject.toml)
- [README](../README.md)
- [`src/brr/AGENTS.md`](../src/brr/AGENTS.md)
- [`src/brr/__init__.py`](../src/brr/__init__.py)
- [`src/brr/__main__.py`](../src/brr/__main__.py)
- [`src/brr/cli.py`](../src/brr/cli.py)

Keep in mind:

- The console script is `brr = brr.cli:main`.
- `python -m brr` delegates to the same CLI.
- The public CLI is intentionally small: `init`, `run`, `auth`, `bind`, `setup`, `up`, `down`.
- Current CLI tests assert that older public diagnostic commands such as `status` and `inspect` are not registered.
- [`src/brr/AGENTS.md`](../src/brr/AGENTS.md) is the **universal schema** every tool follows (brr daemon, Cursor, Codex CLI, Claude Code) — its contract on commits, kb shape, lifecycle markers, and delivery is shared. The stewardship section names a workflow rule with teeth: surface contradictions between the request and the codebase, don't blindly follow either.

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
- [`src/brr/branching.py`](../src/brr/branching.py)
- [`src/brr/conversations.py`](../src/brr/conversations.py)
- [`src/brr/updates.py`](../src/brr/updates.py)
- [`src/brr/run_progress.py`](../src/brr/run_progress.py)
- [`src/brr/run_context.py`](../src/brr/run_context.py)

Keep in mind:

- `Task` is the central work unit constructed mechanically from an event. It carries the originating event, concrete environment backend, status, source, conversation key, and freeform metadata (worktree path, branch name, response path, etc.). There is no longer a `branch` field — branching is decided by the agent inside the worktree at runtime.
- `BranchPlan` (in `branching.py`) is a frozen dataclass the daemon resolves once per task: `seed_ref`, optional `auto_land_branch`, `source` (e.g. `event:branch_target`, `fallback:current`, `fallback:preserve`), `host_context_branch`, optional `expected_old_oid`. Resolution looks at the structured event field (`branch_target` / `target_branch` / `base_branch` / legacy `branch`) and falls back to the `branch.fallback` config knob. No conversation history, no LLM. Plan facts ride the task file via `BranchPlan.meta_items()`.
- A conversation is just a per-gate-thread append-only ndjson log of events, tasks, artifacts, and lifecycle update packets. There is no manifest, no title, no intent — those leaky stream-identity fields were removed in the 2026-05-05 refactor (see [decision-drop-streams.md](decision-drop-streams.md)).
- `UpdatePacket` is lifecycle telemetry routed to a conversation log and, optionally, gate `render_update` hooks. The packet vocabulary covers sync, env prep, attempts, heartbeats, retries, finalize, push, kb maintenance, and Docker container births/preservations.
- `RunProgressView` (in `run_progress.py`) folds conversation records into a compact per-task projection that gates render; its expanded renderer remains useful for diagnostics. Adding new lifecycle UX should extend this projection, not reinvent rendering per gate.
- `run_context.py` writes a per-task context file under `.brr/runs/<task-id>/context.md` so an agent can recover orientation without poking around runtime state.

Tests:

- [task tests](../tests/test_task.py)
- [branching tests](../tests/test_branching.py)
- [conversation tests](../tests/test_conversations.py)
- [run-progress tests](../tests/test_run_progress.py)
- [daemon-conversation tests](../tests/test_daemon_conversations.py)
- [daemon-progress-packet tests](../tests/test_daemon_progress_packets.py)

### Ring 3: execution contract

Purpose: understand how `brr` delegates actual work to an external AI runner,
and how the chosen environment shapes that runner invocation.

Read:

- [`src/brr/runner.py`](../src/brr/runner.py) — subprocess plumbing only (since phase 3a)
- [`src/brr/prompts.py`](../src/brr/prompts.py) — prompt assembly, Task Context Bundle, conversation injection
- [`src/brr/envs/__init__.py`](../src/brr/envs/__init__.py)
- [`src/brr/prompts/runners.md`](../src/brr/prompts/runners.md)
- [`src/brr/prompts/run.md`](../src/brr/prompts/run.md)
- [`src/brr/prompts/kb-maintenance.md`](../src/brr/prompts/kb-maintenance.md) — thin redundancy pass; pointer at AGENTS.md → "Knowledge base shape"
- [`src/brr/kb_preflight.py`](../src/brr/kb_preflight.py) — deterministic kb consistency scanner that feeds the maintenance prompt
- [`src/brr/kb_health.py`](../src/brr/kb_health.py) — graph stats (pages by kind, in-degree, peer orphans, log size) injected next to preflight findings

Keep in mind:

- `RunnerInvocation` describes one external AI CLI call.
- `RunnerResult.validation_ok` combines three layers: subprocess exit, the optional `required_artifacts` check (used by `adopt` for AGENTS.md / kb files), and the `has_response` check that fires only when the invocation specifies a `response_path`.
- The runner contract is "stdout is the response": `claude --print`, `codex exec`, and `gemini -p --yolo` all print only the final agent message to stdout. `invoke_runner` captures stdout and writes it to the task response file itself, so no per-runner output flag is needed.
- Daemon retry triggers on empty stdout, not a missing file.
- `RunContext` splits host-visible and environment-visible response paths so Docker invocations can resolve mount-aware paths even though brr (not the runner) writes the file.
- The user-facing policy key is `environment=<auto|host|worktree|docker>` in `.brr/config`; legacy `env` and `default_env` are still accepted.
- Task files still store the concrete backend as `env`.
- Current built-in backends on this branch are `host`, `worktree`, and `docker`. Design notes also discuss future `ssh` and `devcontainer` backends.
- The Docker env auto-wires credentials so users don't have to bake them into images: known runner env vars (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`) pass through, host login dirs (`~/.claude`, `~/.claude.json`, `~/.codex`, `~/.gemini`, `~/.gitconfig`) bind-mount into `/brr-home/<basename>` when present, and `safe.directory='*'` is injected via `GIT_CONFIG_*` env vars so git works against the bind-mounted repo regardless of UID. The container itself runs as the host UID (`-u "$(id -u):$(id -g)"`) with `HOME=/brr-home`, so writes inside the bind-mounted repo are host-owned and `.git/objects/` no longer collects root-owned residue. Toggles: `docker.env=KEY1,KEY2` and `docker.mount_credentials=false`. The bundled [`envs.md`](../src/brr/docs/envs.md) is the user-facing reference.

Tests:

- [runner tests](../tests/test_runner.py)
- [prompt tests](../tests/test_prompts.py)
- [env tests](../tests/test_envs.py)
- [Dockerfile tests](../tests/test_dockerfile.py)
- [kb-preflight tests](../tests/test_kb_preflight.py)
- [kb-health tests](../tests/test_kb_health.py)

### Ring 4: orchestration spine

Purpose: read the actual event-to-response loop after the lower layers make
sense.

Read:

- [`src/brr/daemon.py`](../src/brr/daemon.py)
- [`src/brr/sync.py`](../src/brr/sync.py)
- [`src/brr/forges.py`](../src/brr/forges.py)
- [`src/brr/dev_reload.py`](../src/brr/dev_reload.py)
- [daemon tests](../tests/test_daemon.py)
- [daemon-heartbeat tests](../tests/test_daemon_heartbeat.py)
- [sync tests](../tests/test_sync.py)
- [forges tests](../tests/test_forges.py)
- [developer reload tests](../tests/test_dev_reload.py)
- [daemon-conversation tests](../tests/test_daemon_conversations.py)

Read `_run_worker()` in passes rather than all at once:

1. Derive the conversation key from the event (gate-thread fingerprint).
2. Refresh local refs via [`sync.refresh_before_task`](../src/brr/sync.py): one `git fetch <default-remote>` plus ff-only of the local default branch and any structured target branch named in the event. Best-effort; never raises.
3. Resolve the [`BranchPlan`](../src/brr/branching.py): structured event field first (`branch_target` / `target_branch` / `base_branch` / legacy `branch`), then the `branch.fallback` policy (`preserve` default; `current` for self-development).
4. Append the event arrival and emit `event_received`; emit a `synced` packet when sync moved a ref, skipped one, or errored.
5. Build the `Task` from the event with `Task.from_event`; copy the plan onto `task.meta`; emit `task_created`.
6. Resolve the environment policy into a concrete backend.
7. Prepare the environment (worktree creation included); emit `env_prepared`.
8. Write the run context file (with the recent conversation block).
9. Build the daemon prompt via [`prompts.build_daemon_prompt`](../src/brr/prompts.py) — preamble, recent conversation block, Task Context Bundle, delivery contract.
10. Invoke the runner with periodic `heartbeat` packets (every 30s) and retries when the runner prints no final reply on stdout.
11. Capture the plain-text response file (written from stdout).
12. Run [`kb_preflight.scan`](../src/brr/kb_preflight.py) plus [`kb_health.compute_graph_stats`](../src/brr/kb_health.py); if either has findings or `kb/` was touched, run the kb-maintenance LLM pass with findings + stats + the list of task-touched pages injected, then roll up any leftover kb edits as a `brr maintenance` commit and emit `kb_maintenance_done`. Otherwise skip — the pass is now a true safety net.
13. Finalize the environment — `WorktreeEnv.finalize` reads the worktree's git state to decide between fast-forward landing and branch preservation.
14. Update task status, push the branch when there's something to publish (attaching a [`forges.view_branch_url`](../src/brr/forges.py) link to `push_done`), and append matching update packets to the conversation log.

Keep in mind:

- The daemon is serial in v1: it processes one pending event at a time.
- Gate threads run beside it, but task execution itself is not a worker pool yet.
- There is exactly one runner invocation per attempt — no separate triage call.
- The agent owns branching: brr only decides whether to fast-forward back or preserve the branch as-is.
- Worktree/Docker tasks isolate the working directory while sharing the runtime `.brr/`.
- Sync, branch plan, and forge URL inference all happen on the host before any worker subprocess. They never raise — failures degrade gracefully (offline fetch, unparseable remote, dirty tree) and surface as `synced`/`push_done` packet payloads instead of blocking the task.

### Ring 5: edges and operator views

Purpose: understand how messages enter/leave the core, how live progress is
rendered into remote channels, and how humans inspect runtime state when
something looks wrong.

Read:

- [`src/brr/gates/__init__.py`](../src/brr/gates/__init__.py)
- [`src/brr/gates/telegram.py`](../src/brr/gates/telegram.py)
- [`src/brr/gates/slack.py`](../src/brr/gates/slack.py)
- [`src/brr/gates/github.py`](../src/brr/gates/github.py)
- [`src/brr/docs/__init__.py`](../src/brr/docs/__init__.py)
- [`src/brr/docs/brr-internals.md`](../src/brr/docs/brr-internals.md)
- [`src/brr/docs/conversations.md`](../src/brr/docs/conversations.md)
- [`src/brr/docs/active-task.md`](../src/brr/docs/active-task.md)
- [`src/brr/docs/envs.md`](../src/brr/docs/envs.md)
- [`src/brr/docs/execution-map.md`](../src/brr/docs/execution-map.md)

Keep in mind:

- Gates are transport adapters. They should not know about daemon internals.
- Gates create event files and deliver response files.
- `_BUILTIN_GATES = ["telegram", "slack", "github"]` in `daemon.py`; each one only starts when its `is_configured(brr_dir)` returns true. Adding a built-in means registering it here and shipping a module under `gates/`.
- `updates.emit()` can call optional gate `render_update()` hooks, but gate-side failures are swallowed. `_dispatch_to_gates` only walks `("telegram", "slack")` today; chat surfaces render a live card, the GitHub gate does not.
- Telegram and Slack gates render a live per-task progress card via `render_update`: send-on-`task_created`, edit-on-progress through `editMessageText`/`chat.update`, fallback to a fresh send when the original message is gone. State lives at `.brr/gates/telegram_progress.json` and `.brr/gates/slack_progress.json`.
- The GitHub gate polls the REST API (stdlib `urllib` only) for two triggers — `label-on-issue` and `mention-in-comment` — and posts replies as comments on the originating issue or PR. PR-comment events carry the PR head branch as `branch_target` so the daemon's pre-task fetch+ff refreshes that branch before the worker runs. Auth resolution at setup time: `gh auth token`, then `GITHUB_TOKEN`, then interactive paste. State lives at `.brr/gates/github.json`. No webhooks in v1.
- There is no local status module. Keep live progress in `updates.py`, `run_progress.py`, and gate renderers instead of adding transport-specific lifecycle views.
- Bundled docs live in `src/brr/docs/`; per-repo overrides live in `.brr/docs/`.
- Project-specific durable knowledge lives in `kb/`, not `.brr/`.

Tests:

- [Telegram gate tests](../tests/test_telegram_gate.py)
- [GitHub gate tests](../tests/test_github_gate.py)
- [gate setup tests](../tests/test_gate_setup.py)
- [Telegram render-update tests](../tests/test_telegram_render_update.py)
- [Slack render-update tests](../tests/test_slack_render_update.py)
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
- `run_progress.py` projects conversation records into `RunProgressView`.
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
- `synced` (only when sync moved a ref, skipped one, or errored)
- `task_created`
- `env_prepared`
- `container_started`
- `run_started`
- `attempt_started`
- `attempt_failed`
- `retrying`
- `artifact_created`
- `heartbeat` (every ~30s during a running attempt; quiet on the daemon console, folded into the gate card's elapsed counter)
- `finalizing`
- `container_preserved`
- `push_started`
- `push_done` (carries `view_url` when `forges.view_branch_url` could derive one)
- `kb_maintenance_done` (only when `_maybe_kb_maintenance` ran; quiet on the console)
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
- `render_text(..., compact=False)` remains available as an expanded diagnostic renderer for tests and ad hoc debugging.

Persistence:

- Derived on demand from `.brr/conversations/<safe-key>.ndjson` filtered by `task_id`. The view itself is not persisted.

Important fields:

- `conversation_key`, `task_id`, `event_id`
- `phase` (queued, preparing, running, finalizing, delivering, delivered, failed, conflict)
- `state` (active, succeeded, failed)
- `branch_name`, `display_base`, `env`, `attempt`
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
- `branch_plan` (a `branching.BranchPlan`)
- `env_state`

Per-task narrative lands as a curated entry in `kb/log.md` when the
session was substantial enough to record — there is no per-task
`log_file` field. Branch state lives entirely on `branch_plan`; the
auto-land target is `branch_plan.auto_land_branch` and renderers
show "branch ← target" only when an auto-land target is explicitly
set (seed_ref is setup context, not a landing target).

Read with:

- [run context source](../src/brr/run_context.py)
- [daemon tests](../tests/test_daemon.py)

### BranchPlan

Source:

- [`BranchPlan`](../src/brr/branching.py)
- [`resolve_branch_plan()`](../src/brr/branching.py)
- [`STRUCTURED_BRANCH_KEYS`](../src/brr/branching.py)

Referenced by:

- Daemon resolves the plan once per task, before env prep, and threads it through `env_backend.prepare(..., branch_plan=...)`.
- `WorktreeEnv.prepare` uses `seed_ref` to sprout `brr/<task-id>`; `WorktreeEnv.finalize` uses `auto_land_branch` to choose between fast-forward landing and branch preservation.
- `BranchPlan.meta_items()` writes the plan onto `task.meta` (`seed_ref`, `branch_source`, optional `auto_land_branch`, `host_context_branch`, `auto_land_old_oid`) so the run context and prompt can render it.
- The daemon sync hook (`_branches_to_refresh`) reuses `STRUCTURED_BRANCH_KEYS` + `_event_branch_candidate` to compute which branches to ff-only before resolving the plan.

Persistence:

- Plan facts ride on `.brr/tasks/<task-id>.md` via `task.meta`. The dataclass itself is recomputed per task; the daemon does not pickle it.

Important rule:

- Resolution is deterministic and side-effect-free: structured event field first (`branch_target` / `target_branch` / `base_branch` / legacy `branch`), then the `branch.fallback` config knob (`preserve` default; `current` for self-development on the host checkout branch). No conversation parsing, no LLM. Anything fancier is the worker agent's job inside the worktree.

Read with:

- [branching source](../src/brr/branching.py)
- [branching tests](../tests/test_branching.py)
- [daemon branch design](design-daemon-landing-branch.md)

### SyncResult

Source:

- [`SyncResult`](../src/brr/sync.py)
- [`refresh_before_task()`](../src/brr/sync.py)
- [`render_summary()`](../src/brr/sync.py)

Referenced by:

- Daemon calls `sync.refresh_before_task(repo_root, target_branches=_branches_to_refresh(...), cfg=cfg)` between conversation-key derivation and branch-plan resolution.
- `render_summary` formats the result as a one-line `synced: ff main -> abc1234, skipped <branch> (<reason>)` payload that rides on the `synced` packet.

Persistence:

- Not persisted. The packet payload (`summary`, `ff_branches`, `skipped`, `error`) is the durable record on the conversation log.

Important rule:

- `refresh_before_task` never raises. Network failures, dirty trees, diverged history, and branches checked out in another worktree all surface as entries in `skipped` (or `error`) and the daemon proceeds against current local refs. Opt-outs are `sync.fetch_before_task` and `sync.fast_forward_default` in `.brr/config` (both default on).

Read with:

- [sync source](../src/brr/sync.py)
- [sync tests](../tests/test_sync.py)
- [git-layer rework design](design-git-layer-rework.md)

### ForgeMatch

Source:

- [`ForgeMatch`](../src/brr/forges.py)
- [`detect_forge()`](../src/brr/forges.py)
- [`view_branch_url()`](../src/brr/forges.py)
- [`parse_remote()`](../src/brr/forges.py)

Referenced by:

- `daemon._forge_view_url` calls `view_branch_url` after a successful push and attaches the result to the `push_done` payload as `view_url` when a URL could be derived.

Persistence:

- Not persisted. The URL is a transient payload field; failures are silent and the packet just lacks `view_url`.

Important rule:

- Pure observation: parse a remote URL, return a URL. No subprocess, no auth, no network. Host patterns cover GitHub / GitLab / Bitbucket / Gitea-Forgejo cloud and self-hosted prefixes; one-off internal domains go through `forge.kind` and `forge.url_base` overrides in `.brr/config`.

Read with:

- [forges source](../src/brr/forges.py)
- [forges tests](../tests/test_forges.py)

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
- [`gates/github.py`](../src/brr/gates/github.py)

Referenced by:

- CLI loads gates for `setup`, `auth`, and `bind` (recognised names: `telegram`, `slack`, `github`).
- Daemon starts configured gates from `_BUILTIN_GATES = ["telegram", "slack", "github"]`.
- Updates optionally dispatch lifecycle packets to chat-surface gates (`_dispatch_to_gates` walks `("telegram", "slack")` only; the GitHub gate's delivery is the issue/PR comment, not a live card).

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
  `.brr/gates/<gate>_progress.json`. Gates that aren't a chat surface
  (script gates, the GitHub gate posting issue/PR comments, etc.)
  typically skip the hook — the durable artifact (a comment, a commit,
  a file) is the delivery.

Read with:

- [gate protocol doc](../src/brr/gates/README.md)
- [Telegram gate tests](../tests/test_telegram_gate.py)
- [GitHub gate tests](../tests/test_github_gate.py)
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
  - gates (to look up delivery info from `task.meta`)

- [`branching.py`](../src/brr/branching.py) depends only on `gitops`. It is consumed by:
  - daemon (`resolve_branch_plan`, plus `STRUCTURED_BRANCH_KEYS` + `_event_branch_candidate` for the sync hook target list)
  - envs (`BranchPlan` threads through `EnvBackend.prepare(..., branch_plan=...)`)

- [`conversations.py`](../src/brr/conversations.py) is consumed by:
  - daemon
  - updates
  - run_progress
  - run_context

- [`updates.py`](../src/brr/updates.py) depends on `conversations` for routing packets and is used by daemon. It also dispatches packets to chat-surface gate `render_update` hooks (`telegram`, `slack`).

- [`run_progress.py`](../src/brr/run_progress.py) depends on `conversations`. It is consumed by:
  - Telegram and Slack gate `render_update` hooks
  - expanded diagnostics via `render_text(..., compact=False)`

The key distinction:

- `Task` answers "what unit of work are we executing?"
- `BranchPlan` answers "where does the worktree sprout from, and what may finalize fast-forward into?"
- The conversation log answers "what has happened in this gate thread, in order?"
- `UpdatePacket` answers "what just changed for a particular task?"
- `RunProgressView` answers "what is the live state of this task right now, in a form a gate or an operator can render?"

### Runner and prompts

The runner / prompts boundary was split in phase 3a of the kb-shape arc.
They were one file before; the split keeps subprocess plumbing
testable in isolation from prompt assembly.

- [`runner.py`](../src/brr/runner.py) owns:
  - runner profile detection
  - command construction
  - subprocess execution
  - trace writing
  - the `TaskRunner` worker thread for serial `brr run` execution

- [`prompts.py`](../src/brr/prompts.py) owns:
  - reading bundled prompt templates (with `.brr/prompts/<name>.md` overrides)
  - reading [`src/brr/AGENTS.md`](../src/brr/AGENTS.md) and threading
    it into init prompts
  - assembling the **Task Context Bundle** (task body, env, branch,
    delivery contract, recent conversation block)
  - rendering recent `kb/log.md` entries into a conversation
    context block
  - exposing `build_init_prompt`, `build_run_prompt`,
    `build_daemon_prompt`, `build_kb_maintenance_prompt`

- [`kb_preflight.py`](../src/brr/kb_preflight.py) owns the
  deterministic kb consistency scan that feeds the kb-maintenance
  prompt: orphan detection, stale index entries, broken cross-links.
  Synthesis-heavy checks (lifecycle drift, contradictions with the
  log) are deferred to the LLM redundancy pass.

- [`kb_health.py`](../src/brr/kb_health.py) owns the kb graph-shape
  snapshot rendered alongside preflight findings: pages by kind,
  in-degree top-N, peer-orphans (reachable from `index.md` but no
  peer page links to them), `log.md` size, and a "task touched N
  pages this run" cue.  Stdlib-only and side-effect-free; consumed
  only by `_maybe_kb_maintenance` in the daemon.

`runner.py` is called from:

- [`adopt.py`](../src/brr/adopt.py) for the `brr init` setup invocation
- [`daemon.py`](../src/brr/daemon.py) for execution and the
  kb-maintenance LLM pass
- [`envs/__init__.py`](../src/brr/envs/__init__.py) for
  environment-specific invocation
- [`cli.py`](../src/brr/cli.py) for `brr run`

`prompts.py` is called from:

- [`adopt.py`](../src/brr/adopt.py) → `build_init_prompt`
- [`daemon.py`](../src/brr/daemon.py) → `build_daemon_prompt`,
  `build_kb_maintenance_prompt`
- [`runner.py`](../src/brr/runner.py) → `build_run_prompt` (for
  `brr run`)

`kb_preflight.py` is called from:

- [`daemon.py`](../src/brr/daemon.py) inside `_maybe_kb_maintenance`,
  before deciding whether to invoke the LLM pass

`kb_health.py` is called from:

- [`daemon.py`](../src/brr/daemon.py) inside `_maybe_kb_maintenance`,
  to format the graph-stats block injected into the maintenance prompt

Prompt files to read alongside the modules:

- [`setup.md`](../src/brr/prompts/setup.md) — adopter setup; reads
  brr's own [`AGENTS.md`](../src/brr/AGENTS.md) as the model.
- [`run.md`](../src/brr/prompts/run.md) — daemon-originated task
  prompt; carries the delivery contract.
- [`runners.md`](../src/brr/prompts/runners.md) — runner profile
  registry.
- [`kb-maintenance.md`](../src/brr/prompts/kb-maintenance.md) —
  thin redundancy pass; points at AGENTS.md → "Knowledge base shape"
  for the rules.

### Daemon freshness and forge inference

- [`sync.py`](../src/brr/sync.py) depends only on `gitops`. It is
  called once per task by `_run_worker` before branch-plan
  resolution. Pure side-effect-on-disk (a `git fetch` and best-effort
  ff-only) with no exceptions surfaced upward; the result rides on
  the `synced` packet.

- [`forges.py`](../src/brr/forges.py) is pure observation — it
  consumes a remote URL string and returns a URL string. It is called
  by `daemon._forge_view_url` after a successful push to attach
  `view_url` to the `push_done` packet. No subprocess, no network,
  no auth.

### Execution environments

- [`envs/__init__.py`](../src/brr/envs/__init__.py) depends on:
  - [`branching.py`](../src/brr/branching.py)
  - [`gitops.py`](../src/brr/gitops.py)
  - [`worktree.py`](../src/brr/worktree.py)
  - [`runner.py`](../src/brr/runner.py)
  - [`task.py`](../src/brr/task.py)

Host execution:

- runs in the main repo checkout
- requires `branch_name is None`
- finalization is a no-op

Worktree execution:

- creates `.brr/worktrees/<task-id>` on a fresh `brr/<task-id>` branch sprouted from the resolved seed ref
- finalize reads the worktree's git state: fast-forward the resolved auto-land target when one exists, preserve the task branch otherwise
- outcome-aware cleanup: removes the worktree on clean success with nothing uncommitted, keeps it on `error` / `conflict` or when untracked/unstaged files remain

Docker execution:

- requires Docker CLI and `docker.image`
- wraps the normal runner command in `docker run`
- bind-mounts the repo at the same absolute path
- always uses a worktree on a fresh `brr/<task-id>` branch, so the host's working tree stays clean
- tracks containers for cleanup or salvage
- forwards known runner env vars (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`) and any names listed in `docker.env=` when set on the daemon
- runs the container as the host UID via `-u "$(id -u):$(id -g)"` and exports `HOME=/brr-home`, so file writes inside the bind-mounted repo are host-owned and the in-container CLIs find their credentials at `$HOME/...` regardless of whether the runtime UID has an `/etc/passwd` entry
- bind-mounts host login directories (`~/.claude`, `~/.claude.json`, `~/.codex`, `~/.gemini`, `~/.gitconfig`) into `/brr-home/<basename>` when present, unless `docker.mount_credentials=false`
- injects `safe.directory='*'` via git's `GIT_CONFIG_*` env vars so git works against the bind-mounted repo even though the container's runtime UID may differ from the host owner — no per-image baked-in config required

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
- gate startup (`_BUILTIN_GATES = ["telegram", "slack", "github"]`)
- optional developer reload watcher (`dev_reload.py`)
- inbox scan
- conversation key derivation
- pre-task freshness via `sync.refresh_before_task` (one fetch +
  ff-only the targets surfaced by `_branches_to_refresh`)
- branch intent resolution (`branching.resolve_branch_plan`)
- mechanical task construction (`Task.from_event`)
- task persistence
- env prepare/invoke/finalize
- attempt loop with retries, `heartbeat` packets every 30s, and
  lifecycle packets
- response validation
- `kb_preflight.scan` + `kb_health.compute_graph_stats` plus a
  conditional kb-maintenance LLM pass; leftover kb edits rolled up as
  a `brr maintenance` commit and announced via
  `kb_maintenance_done` (see the kb-consistency invariant below)
- branch-aware git push attempt with `push_started` / `push_done`
  packets; `_forge_view_url` attaches a `forges.view_branch_url`
  link to the `push_done` payload when derivable
- quiescent re-exec after package-file changes when `--dev-reload` or
  `dev_reload=true` is active

The worker emits the full run-progress packet stream (`synced`,
`env_prepared`, `attempt_started`, `attempt_failed`, `retrying`,
`heartbeat`, `finalizing`, plus `container_started` /
`container_preserved` for the Docker env). Read these helpers in
`daemon.py` next to the worker loop:

- `_branches_to_refresh` — pre-task target list for `sync.refresh_before_task`.
- `_emit_new_containers` — diffs `env_ctx.env_state["docker_containers"]` between attempts.
- `_emit_preserved_containers` — fires `container_preserved` when finalize left containers behind.
- `_maybe_kb_maintenance` — preflight + graph-stats gate around the LLM pass; commits leftover kb edits and emits `kb_maintenance_done`.

When debugging behavior, read daemon tests before modifying daemon source:

- [daemon tests](../tests/test_daemon.py)
- [daemon-heartbeat tests](../tests/test_daemon_heartbeat.py)
- [developer reload tests](../tests/test_dev_reload.py)
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

Worktree and Docker tasks start on a fresh `brr/<task-id>` branch
sprouted from the daemon's resolved seed ref. The branch plan names an
optional auto-land target and its authority. The agent inside the
worktree decides:

- commit on the current task branch and let brr fast-forward the
  auto-land target when one exists,
- commit on the current task branch and let brr preserve/push it when
  no auto-land target exists, or
- `git switch -c <name>` before committing, so brr preserves the branch
  as-is for human review or PR tooling.

`WorktreeEnv.finalize` reads the worktree's git state to make that
decision — there is no frozen branch strategy on the task file, and the
host checkout branch is context rather than default authority.

### Environment is the user-facing isolation knob

Most users should choose an environment policy:

- `environment=auto`
- `environment=host`
- `environment=worktree`
- `environment=docker`

The env and branch plan are resolved deterministically before env prep.
There is no per-task `branch` field anymore — runtime branch state lives
in `task.meta["branch_name"]`, with `seed_ref`, `auto_land_branch`,
`preserved_branch`, and `landed_branch` recording daemon gitops facts
after `prepare`/`finalize`.

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

### Daemon freshness is best-effort, never blocking

Before resolving the branch plan, the worker calls
`sync.refresh_before_task(repo_root, target_branches=..., cfg=cfg)`:
one `git fetch <default-remote>` plus an ff-only attempt against
each target (the local default branch and any structured event
branch). The invariant is *the seed ref the worktree sprouts from
reflects the remote at task start, not whatever the host last
pulled.* Failures (offline fetch, dirty tree, diverged history,
branch checked out elsewhere) record reasons in `SyncResult.skipped`
or `.error` and the daemon proceeds against current local refs —
sync is never allowed to block task execution.

Opt-outs in `.brr/config`, both default-on:

- `sync.fetch_before_task=false` — never touch the network.
- `sync.fast_forward_default=false` — fetch but leave local refs alone
  (for users sharing the daemon's checkout with active dev work).

When adding a new structured event field that names a branch the
worker should seed from, add the key to
`branching.STRUCTURED_BRANCH_KEYS` so both the branch plan and the
sync hook learn about it in one place.

### KB consistency is preflight + redundancy, not a primary gate

After every successful task, `kb_preflight.scan(run_root)` walks `kb/`
and returns structured findings — `missing-from-index`,
`stale-index-entry`, `broken-link`. The findings drive whether the
LLM kb-maintenance pass runs at all: if both the preflight is clean
*and* `kb/` is unchanged, the pass is skipped. When findings exist
or the task touched `kb/`, the maintenance prompt runs with findings
injected, alongside a graph-shape block from
`kb_health.compute_graph_stats` (pages by kind, in-degree top-N,
peer orphans, `log.md` size, "task touched N pages this run") and
the list of kb / AGENTS.md files the preceding task changed.

The LLM pass is deliberately thin — it points at AGENTS.md →
"Knowledge base shape" for the rules and either addresses the
findings or does a brief redundancy check. The primary maintenance
contract lives in AGENTS.md so external tools (Cursor, Codex CLI,
Claude Code) follow the same rules without needing brr's preflight.

Leftover uncommitted kb edits get rolled into a single `brr
maintenance` commit on the task's current branch (scoped to
`kb/`, `AGENTS.md`, and `src/brr/AGENTS.md`); the outcome rides on a
`kb_maintenance_done` packet so the response card surfaces whether a
maintenance commit landed.

When adding a new structural kb invariant (a new lifecycle marker,
a new naming convention, a new graph rule), prefer extending
`kb_preflight.scan` over expanding the LLM prompt — deterministic
checks are cheap, reproducible, and run on every task. Reserve the
LLM pass for synthesis-heavy judgement (lifecycle drift,
contradictions with the log, cross-subject coherence). New
graph-shape signals (counts, distributions, orphan variants) belong
in `kb_health.py` so they ride on the same advisory block.

### Troubleshooting is artifact-first

The remote gate is the primary surface for run progress. There is no local
status module; when a run needs debugging, use the generated run context,
task metadata, conversation records, traces, response files, and preserved
worktree/container metadata. Earlier versions kept private `status.py`
helpers after removing the public commands; those helpers were removed on
2026-05-14 after they had no runtime callers.

## Tests as a second reading path

If source-first reading feels too abstract, run the test path instead:

1. [protocol tests](../tests/test_protocol.py)
2. [task tests](../tests/test_task.py)
3. [branching tests](../tests/test_branching.py)
4. [conversation tests](../tests/test_conversations.py)
5. [run-progress tests](../tests/test_run_progress.py)
6. [runner tests](../tests/test_runner.py)
7. [prompt tests](../tests/test_prompts.py)
8. [git/worktree tests](../tests/test_gitops.py)
9. [sync tests](../tests/test_sync.py)
10. [forges tests](../tests/test_forges.py)
11. [env tests](../tests/test_envs.py)
12. [Dockerfile tests](../tests/test_dockerfile.py)
13. [kb-preflight tests](../tests/test_kb_preflight.py)
14. [kb-health tests](../tests/test_kb_health.py)
15. [daemon tests](../tests/test_daemon.py)
16. [daemon-heartbeat tests](../tests/test_daemon_heartbeat.py)
17. [daemon-conversation tests](../tests/test_daemon_conversations.py)
18. [daemon-progress-packet tests](../tests/test_daemon_progress_packets.py)
19. [Telegram gate tests](../tests/test_telegram_gate.py)
20. [GitHub gate tests](../tests/test_github_gate.py)
21. [gate setup tests](../tests/test_gate_setup.py)
22. [Telegram render-update tests](../tests/test_telegram_render_update.py)
23. [Slack render-update tests](../tests/test_slack_render_update.py)
24. [adopt tests](../tests/test_adopt.py)
25. [CLI tests](../tests/test_cli.py)
26. [docs tests](../tests/test_docs.py)

Cross-cutting test scaffolding lives in
[`tests/_helpers.py`](../tests/_helpers.py): `init_git_repo`,
`commit_files`, `write_repo_scaffold`, `make_event`, plus the
`StubWorktreeEnv` + `succeed_invoke` pair used by the daemon-level
tests. Reach for these helpers before copying setup inline; see the
2026-05-16 grooming research for the mapping of which inline copies
each one subsumes.

This order mirrors dependency growth: file protocol, durable state,
the run-progress projection, execution (subprocess plumbing then
prompt assembly), filesystem isolation, daemon freshness and forge
inference, kb consistency, orchestration, adapters (including their
live-progress hooks), and finally CLI/bootstrap.

## Design history to read after source

The source tells you what is implemented. These KB pages explain why the system
is shaped this way and where it is going. Lifecycle markers on each page
say which parts are stable, in flight, or paused.

Subject hubs (start here when a whole area is unfamiliar):

- [Subject: the kb itself](subject-kb.md) — synthesis of the kb
  pattern (four memory layers, graph topology, subject genesis,
  cross-tool maintenance, what was rejected).
- [Subject: daemon and process lifecycle](subject-daemon.md) —
  foreground `brr up`, gate/file-protocol boundary, serial worker
  lifecycle, local process control, the development reload direction.
- [Subject: tasks and branching](subject-tasks-branching.md) —
  mechanical task construction, environment resolution, agent-owned
  runtime branching, worktree finalization, and the structured
  branch-intent contract feeding `BranchPlan`.
- [Subject: environments](subject-envs.md) — `Env` protocol
  (three-phase `prepare → invoke → finalize`), durability contract,
  outcome-aware salvage, decentralised fast-forward merging, which
  envs ship today (`host` / `worktree` / `docker`) versus designed
  (`ssh` / `devcontainer`).
- [Subject: fleet and overlays](subject-fleet-overlays.md) —
  three-axis fleet agenda: overlays for user-level steering, `brnrd`
  as a future operator above many repos, environments as the active
  axis delegated to the env hub.

Decisions ("drop the noisy abstraction" trio in chronological order):

- [Remove triage decision](decision-remove-triage.md) — the LLM
  triage stage came off first.
- [Drop streams decision](decision-drop-streams.md) — workstreams
  came off next.
- [kb shape decision](decision-kb-shape.md) — per-task log files
  came off, AGENTS.md became universal schema, kb-maintenance
  became preflight + redundancy.

Other decisions:

- [Bundled docs decision](decision-bundled-docs.md) — why bundled
  `src/brr/docs/` + per-repo `.brr/docs/` overrides.

Designs (current contracts that the code points back to):

- [Env protocol design](design-env-interface.md) — *accepted on
  2026-05-06*. Protocol spec, per-env mechanics, response-path split,
  plugin / script-env model, configuration surface.
- [Daemon branch intent design](design-daemon-landing-branch.md) —
  *accepted, amended on 2026-05-12*. Structured event branch fields
  feed a deterministic `BranchPlan`; conversation history is prompt
  context only, not hidden auto-land authority.
- [Git layer rework design](design-git-layer-rework.md) — *shipped
  on 2026-05-15*. Daemon-side freshness (`sync.refresh_before_task`,
  the seed-ref invariant), the built-in GitHub gate, and a
  prompt-level revisit-signal section for design-loaded tasks.
- [Developer daemon reload design](design-daemon-dev-reload.md) —
  *shipped*. Editable install plus opt-in quiescent re-exec on
  package-file changes, kept behind `--dev-reload` / `dev_reload=true`.

Plans:

- [Concurrent worktrees plan](plan-concurrent-worktrees.md) —
  *shipped* (one-task-per-worktree slice; merge-coordinator path
  abandoned).
- [Branch modes plan](plan-branch-modes.md) — *shipped, with
  revisions* (triage reversed, `needs_context` gone).
- [State-first kb maintenance plan](plan-kb-state-first-maintenance.md) —
  *active*. Refine the kb shape around current-state synthesis +
  short breadcrumbs to git history; replace hidden post-task LLM
  cleanup with explicit, first-class maintenance tasks.
- [Overlays plan](plan-overlays.md) — *blocked*.

Notes:

- [Notes: pondering fleet](notes-pondering-fleet.md) — *paused*.
  Open questions on overlays-as-single-file, brnrd-as-agentic-operator,
  cross-platform supervisor, decentralised merge.

Strategic decks:

- [Deck: brr fleet & steering](deck-brr-fleet-steering.md) —
  *roadmap (env axis active; overlays / brnrd paused)*.

Research:

- [Test suite grooming, 2026-05-16](research-test-suite-grooming-2026-05-16.md) —
  *shipped*. Map of bloat, cross-file helper duplication, and
  intent-quality gaps; high-leverage moves (`test_integration.py`
  removal, `tests/_helpers.py` extraction, `_forge_view_url`
  stub-based rewrite, docker-mounts parametrize) were executed in
  the same pass.
- [Branch plan simplification, 2026-05-12](research-branch-plan-simplification-2026-05-12.md) —
  follow-up critique of the accepted branch-intent implementation:
  preserve the mechanical seed/auto-land/finalization contract, but
  shrink branch planning back to landing defaults and stop treating
  inferred conversation branch history as hidden auto-land authority.
- [Daemon runner context ergonomics, 2026-05-09](research-runner-context-ergonomics-2026-05-09.md) —
  point-in-time review of a live daemon run's prompt/context shape,
  stale bundled-doc contradictions, and Docker tooling gaps.
- [brr vs gh-aw](research-brr-vs-gh-aw.md) — deep comparison with
  GitHub Agentic Workflows.

External framing (upstream inspiration, not a brr design page):

- [LLM Wiki framing](llm-wiki.md) — the source framing that informs
  brr's kb / synthesis layer (linked from `subject-kb.md`).

Bundled docs to read alongside the source:

- [Conversations bundled doc](../src/brr/docs/conversations.md)
- [Envs bundled doc](../src/brr/docs/envs.md)
- [Active task bundled doc](../src/brr/docs/active-task.md)
- [Brr internals bundled doc](../src/brr/docs/brr-internals.md)
- [Execution map bundled doc](../src/brr/docs/execution-map.md)

## Practical navigator notes

Use these heuristics while reading:

- If a file talks about event files, jump to [protocol.py](../src/brr/protocol.py).
- If a file talks about environment/status, jump to [task.py](../src/brr/task.py).
- If a file talks about seed refs, auto-land targets, structured branch fields, or the `branch.fallback` policy, jump to [branching.py](../src/brr/branching.py); the runtime branch-switching behaviour inside the worktree lives with [worktree.py](../src/brr/worktree.py) and `WorktreeEnv` in [envs/__init__.py](../src/brr/envs/__init__.py).
- If a file talks about pre-task `git fetch`, ff-only refreshes, or the `synced` packet, jump to [sync.py](../src/brr/sync.py) and `_branches_to_refresh` / the sync-packet emit in [daemon.py](../src/brr/daemon.py).
- If a file talks about thread continuity or per-thread history, jump to [conversations.py](../src/brr/conversations.py).
- If a file talks about lifecycle packets or `render_update`, jump to [updates.py](../src/brr/updates.py).
- If a file talks about live progress phases, attempt counts, heartbeats, or rendering a per-task card, jump to [run_progress.py](../src/brr/run_progress.py).
- If a file talks about prompt assembly (Task Context Bundle, `kb/log.md` injection, AGENTS.md bundling), jump to [prompts.py](../src/brr/prompts.py). If it talks about subprocess execution, runner detection, or trace persistence, jump to [runner.py](../src/brr/runner.py). The two used to be one file; they were split in phase 3a of the kb-shape arc.
- If a file talks about daemon process lifecycle, PID files,
  drain-and-stop behavior, or development reload, start with
  [subject-daemon.md](subject-daemon.md) and then jump to
  [daemon.py](../src/brr/daemon.py) and
  [dev_reload.py](../src/brr/dev_reload.py).
- If a file talks about kb consistency, orphan pages, broken cross-links, or "should this kb-maintenance pass run?", jump to [kb_preflight.py](../src/brr/kb_preflight.py) and `_maybe_kb_maintenance` in [daemon.py](../src/brr/daemon.py). For pages-by-kind / in-degree / peer-orphans / log size, jump to [kb_health.py](../src/brr/kb_health.py). The maintenance contract itself lives in [AGENTS.md → "Knowledge base shape"](../src/brr/AGENTS.md), not in the brr daemon.
- If a file talks about cwd, worktrees, Docker, response path translation, or runner credential wiring (env passthrough, login-dir mounts, git safe.directory), jump to [envs/__init__.py](../src/brr/envs/__init__.py).
- If a file talks about clickable "view branch" URLs, remote-URL parsing, or `forge.kind` / `forge.url_base` overrides, jump to [forges.py](../src/brr/forges.py) and `_forge_view_url` in [daemon.py](../src/brr/daemon.py).
- If a file talks about transport, auth, polling, or delivery, jump to [gates](../src/brr/gates/). For label-on-issue, mention-in-comment, or PR-comment events carrying `branch_target`, [gates/github.py](../src/brr/gates/github.py) specifically.
- If a file feels like "everything at once", you are probably in [daemon.py](../src/brr/daemon.py). Read it in lifecycle passes, not top-to-bottom once.

## Maintenance rule for this guide

Update this page when any of these change:

- public CLI commands
- event/task/conversation file formats
- environment backends
- daemon lifecycle (including the worker step list and the lifecycle packet vocabulary in `updates.PACKET_TYPES`)
- runner artifact contract
- gate hook surface, including the built-in gate set (`_BUILTIN_GATES`)
- daemon freshness contract (`sync.refresh_before_task`, the opt-out config knobs, the `synced` packet)
- branch-plan resolution (`branching.STRUCTURED_BRANCH_KEYS`, fallback policy, `BranchPlan.meta_items`)
- forge URL inference (`forges.detect_forge` host patterns, `forge.kind` / `forge.url_base` overrides)
- bundled docs vs KB ownership
- kb consistency contract (preflight findings, graph stats, kb-maintenance trigger, AGENTS.md kb schema, `kb_maintenance_done` packet)
- module boundaries that affect "where do I jump?" routing (e.g. the runner / prompts split, kb_preflight + kb_health, sync, forges, branching)
- subject hubs added or retired
- shared test scaffolding in [`tests/_helpers.py`](../tests/_helpers.py)
- test files that become the best behavioral reference for a module
