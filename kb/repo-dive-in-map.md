# Repo Dive-In Map

Bottom-up reading guide for the `brr` repository. Use it when you need
to understand the project file by file; use the subject hubs when you
need the current synthesis for one area.

This page intentionally stays as an orientation map, not a second copy
of the source tree. Concrete behavior belongs in the linked source and
tests; long design rationale belongs in the linked kb pages.

## Current Model

`brr` turns external messages into frontmatter-backed event files,
constructs task files mechanically, resolves branch intent and
environment policy, runs a configured AI CLI, records lifecycle packets
in a per-gate-thread conversation log, and delivers a plain-text
response file through the originating gate.

```text
gate -> event -> conversation -> task -> env -> runner -> response -> gate
```

Carry these current-shape facts while reading:

- Public CLI commands are `init`, `run`, `auth`, `bind`, `setup`,
  `up`, and `down`.
- Runtime state lives under `.brr/`; durable project knowledge lives
  under `kb/`; bundled tool docs live under `src/brr/docs/`.
- Tasks are built mechanically from event files. There is no LLM
  triage call and no frontmatter contract on response files.
- `environment=auto` selects `docker` when `docker.image` is
  configured, otherwise `worktree`. `host` is explicit only. The
  shipped backends are `host`, `worktree`, and `docker`.
- Before resolving the branch plan, the daemon runs a best-effort
  `sync.refresh_before_run`: one `git fetch` plus ff-only refreshes
  of the local default branch and any structured event branch.
- Worktree and Docker runs start from the resolved seed ref on a
  fresh `brr/<run-id>` branch. Finalization classifies the worktree's
  final state into a `publish_status` (`ready` | `nothing` |
  `detached`); `daemon.publish` then publishes the recorded branch,
  using a refspec push when the agent kept the run branch but the
  event named a different `target_branch`, and a leased
  force-push when the agent rewrote that branch (the PR-rebase case).
- Runner stdout is the response. `runner.invoke_runner()` captures the
  final stdout and writes `.brr/responses/<event-id>.md`.
- After response validation, `_run_worker()` marks the inbox event
  `done` before kb maintenance, env finalization, and push, so gates
  can deliver the response while post-response housekeeping continues.
- The daemon uses a bounded worker pool (`max_workers=4` default).
  Concurrency is safe because mutable runtime files are partitioned
  per event, per task, or per branch; git ref updates use per-branch
  locks.
- Telegram and Slack render live progress cards from `UpdatePacket`s
  through `run_progress`. GitHub delivers final issue/PR comments and
  can trigger on labels, mentions, or the token-expensive `any` mode.

## Link Policy

Links are repository-relative so they work on feature branches, in
local editors, and on GitHub. When this page says to read source, read
the linked tests immediately after; the tests are usually the tightest
behavioral specification.

## Quick Route

Read these in order for the fastest useful model:

1. [README](../README.md) for product shape and CLI surface.
2. [Gate protocol](../src/brr/gates/README.md) for file-based I/O.
3. [Protocol source](../src/brr/protocol.py) with
   [protocol tests](../tests/test_protocol.py).
4. [Run model](../src/brr/run.py) with
   [run tests](../tests/test_run.py).
5. [Conversation log](../src/brr/conversations.py) with
   [conversation tests](../tests/test_conversations.py).
6. [Runner plumbing](../src/brr/runner.py) and
   [prompt assembly](../src/brr/prompts.py), with their tests.
7. [Environment backends](../src/brr/envs/__init__.py) with
   [env tests](../tests/test_envs.py).
8. [Daemon worker](../src/brr/daemon.py) with daemon, concurrency,
   conversation, progress-packet, heartbeat, and dev-reload tests.
9. [Bundled execution map](../src/brr/docs/execution-map.md) to reread
   the system top-down after seeing the parts.

## Reading Rings

### Ring 0: Package Skin

Start with:

- [pyproject.toml](../pyproject.toml)
- [README](../README.md)
- [`src/brr/AGENTS.md`](../src/brr/AGENTS.md)
- [`src/brr/__main__.py`](../src/brr/__main__.py)
- [`src/brr/cli.py`](../src/brr/cli.py)

Keep in mind:

- The console script is `brr = brr.cli:main`.
- `python -m brr` delegates to the same CLI.
- `src/brr/AGENTS.md` is the canonical playbook template copied by
  `brr init`; the repo-root `AGENTS.md` is a symlink.

### Ring 1: Filesystem Atoms

Read:

- [`src/brr/protocol.py`](../src/brr/protocol.py)
- [`src/brr/config.py`](../src/brr/config.py)
- [`src/brr/gitops.py`](../src/brr/gitops.py)
- [`src/brr/worktree.py`](../src/brr/worktree.py)

Keep in mind:

- Events are markdown files in `.brr/inbox/`; responses are markdown
  files in `.brr/responses/`.
- Frontmatter parsing is a small local parser, not PyYAML.
- `.brr/config` is flat key-value config.
- `gitops.shared_brr_dir()` matters in linked worktrees because the
  runtime directory belongs to the main checkout.

Tests: [protocol](../tests/test_protocol.py),
[config](../tests/test_config.py), [git/worktree](../tests/test_gitops.py).

### Ring 2: Runtime State Objects

Read:

- [`src/brr/run.py`](../src/brr/run.py)
- [`src/brr/branching.py`](../src/brr/branching.py)
- [`src/brr/conversations.py`](../src/brr/conversations.py)
- [`src/brr/updates.py`](../src/brr/updates.py)
- [`src/brr/run_progress.py`](../src/brr/run_progress.py)
- [`src/brr/run_context.py`](../src/brr/run_context.py)

Keep in mind:

- `Run` is the work unit derived from an event. It carries the
  concrete env backend, status, source, conversation key, and runtime
  metadata.
- `PublishPlan` is resolved once per run from structured event
  fields (`branch_target`, `target_branch`, `base_branch`, legacy
  `branch`) and `branch.fallback`. It records `seed_ref`, optional
  `target_branch`, source, host-context branch, and an
  optional `expected_remote_oid` captured from the remote-tracking
  ref at run start for force-with-lease pushes (the PR-rebase case).
- Conversations are directories of per-event jsonl files:
  `.brr/conversations/<key>/<event-id>.jsonl`. Each worker writes one
  file; readers merge by timestamp.
- `UpdatePacket` is lifecycle telemetry persisted to the conversation
  log and optionally rendered by gates.
- `RunProgressView` is a projection over conversation records, not a
  persisted state file.
- `run_context.py` writes `.brr/runs/<run-id>/context.md` as recovery
  detail for daemon-launched agents.

Tests: [task](../tests/test_task.py),
[branching](../tests/test_branching.py),
[conversations](../tests/test_conversations.py),
[run progress](../tests/test_run_progress.py),
[daemon conversations](../tests/test_daemon_conversations.py), and
[daemon progress packets](../tests/test_daemon_progress_packets.py).

### Ring 3: Execution Contract

Read:

- [`src/brr/runner.py`](../src/brr/runner.py)
- [`src/brr/prompts.py`](../src/brr/prompts.py)
- [`src/brr/envs/__init__.py`](../src/brr/envs/__init__.py)
- [`src/brr/prompts/run.md`](../src/brr/prompts/run.md)
- [`src/brr/kb_preflight.py`](../src/brr/kb_preflight.py)
- [`src/brr/kb_health.py`](../src/brr/kb_health.py)

Keep in mind:

- `RunnerInvocation` describes one external AI CLI call.
- `RunnerResult.validation_ok` combines subprocess success, required
  artifacts, and non-empty stdout when a response path is requested.
- Daemon retry triggers on empty stdout.
- `RunContext` carries both host-visible and env-visible response
  paths so Docker and future remote envs can translate paths honestly.
- Docker forwards known runner and GitHub token env vars, mounts known
  credential directories when present, runs as the host UID, and
  injects git `safe.directory=*` config for bind-mounted repos.

Tests: [runner](../tests/test_runner.py),
[prompts](../tests/test_prompts.py), [envs](../tests/test_envs.py),
[Dockerfile](../tests/test_dockerfile.py),
[kb preflight](../tests/test_kb_preflight.py), and
[kb health](../tests/test_kb_health.py).

### Ring 4: Orchestration Spine

Read:

- [`src/brr/daemon.py`](../src/brr/daemon.py)
- [`src/brr/sync.py`](../src/brr/sync.py)
- [`src/brr/forges.py`](../src/brr/forges.py)
- [`src/brr/dev_reload.py`](../src/brr/dev_reload.py)

`daemon.start()` owns the dispatch loop: poll dev-reload, reap worker
futures, re-exec only when the pool is drained, and dispatch pending
events up to `max_workers` capacity. Each worker thread runs
`_run_worker_and_finalize()`.

Read `_run_worker()` in lifecycle passes:

1. Derive the conversation key and local `_WorkerEmit` closure.
2. Refresh local refs with `sync.refresh_before_run`.
3. Resolve `PublishPlan`.
4. Append event arrival and sync packets.
5. Build and persist the `Run`.
6. Resolve and prepare the env backend.
7. Write the run context file.
8. Build the daemon prompt with the Run Context Bundle.
9. Invoke the runner with heartbeat packets and retry on empty stdout.
10. Record the response artifact.
11. Mark the inbox event `done` so the gate may deliver the response.
12. Run kb preflight, graph stats, and optional kb maintenance.
13. Finalize the environment — classify the worktree's final state
    into a `publish_status` and record the branch to publish on
    `run.meta`.
14. Emit terminal run packets and hand off to the worker-tail
    wrapper, which calls `daemon.publish`.

Then `_run_worker_and_finalize()` publishes the recorded branch under
a per-branch lock — refspec push when the agent kept the run branch
but the event named a different `target_branch`, leased
force-push when the agent rewrote that branch (PR-rebase), plain
push otherwise — and attaches a `forges.view_branch_url` link to
`push_done` when derivable.

Tests: [daemon](../tests/test_daemon.py),
[heartbeat](../tests/test_daemon_heartbeat.py),
[single-flight](../tests/test_daemon_single_flight.py),
[dev reload](../tests/test_dev_reload.py), and
[sync](../tests/test_sync.py).

### Ring 5: Gates And Operator Views

Read:

- [`src/brr/gates/__init__.py`](../src/brr/gates/__init__.py)
- [`src/brr/gates/telegram.py`](../src/brr/gates/telegram.py)
- [`src/brr/gates/slack.py`](../src/brr/gates/slack.py)
- [`src/brr/gates/github/`](../src/brr/gates/github/) — package
  (`client`/`paths`/`cache`/`parse`/`state`/`wizard`/`polling`/`delivery`/`progress`/`loop`)
- [`src/brr/gates/README.md`](../src/brr/gates/README.md)
- [`src/brr/docs/`](../src/brr/docs/)

Keep in mind:

- Built-in gates are `telegram`, `slack`, and `github`.
- Gates create event files and deliver response files; they do not own
  daemon internals.
- Telegram and Slack opt into `render_update()` and store one progress
  card state file per task.
- GitHub polls with `requests`, supports label, mention, and
  `any` triggers, and posts final responses as comments. PR events and
  PR comments carry `branch_target` so the sync hook can refresh the
  PR head before the worker starts.
- There is no local status module. Troubleshooting follows run
  context, task metadata, conversation records, traces, response
  files, and preserved worktree/container metadata.

Tests: [Telegram gate](../tests/test_telegram_gate.py),
[GitHub gate](../tests/test_github_gate.py),
[gate setup](../tests/test_gate_setup.py),
[Telegram render update](../tests/test_telegram_render_update.py), and
[Slack render update](../tests/test_slack_render_update.py).

## Core Entities

| Entity | Source | Durable location | Main rule |
| --- | --- | --- | --- |
| Event | [`protocol.py`](../src/brr/protocol.py) | `.brr/inbox/<event-id>.md` | Gates create events; daemon moves them through `pending`, `processing`, and terminal statuses. |
| Run | [`run.py`](../src/brr/run.py) | `.brr/runs/<run-id>/run.md` | Mechanical work unit derived from an event and config. |
| Conversation | [`conversations.py`](../src/brr/conversations.py) | `.brr/conversations/<key>/<event-id>.jsonl` | Routing/history context, not durable project knowledge. |
| UpdatePacket | [`updates.py`](../src/brr/updates.py) | Conversation jsonl record | Gate-agnostic lifecycle event. |
| RunProgressView | [`run_progress.py`](../src/brr/run_progress.py) | Derived on demand | Projection gates render; not persisted. |
| RunnerInvocation / RunnerResult | [`runner.py`](../src/brr/runner.py) | Optional traces | One external AI CLI call and its validation result. |
| RunContext | [`run_context.py`](../src/brr/run_context.py) | `.brr/runs/<run-id>/context.md` | Recovery detail plus host/env path mapping. |
| PublishPlan | [`branching.py`](../src/brr/branching.py) | Run metadata | Deterministic seed, expected publish target, and remote lease anchor. |
| SyncResult | [`sync.py`](../src/brr/sync.py) | `synced` packet payload | Best-effort freshness; never blocks run execution. |
| ForgeMatch | [`forges.py`](../src/brr/forges.py) | `push_done.view_url` when available | Pure remote-URL parsing; no network or auth. |
| EnvBackend | [`envs/__init__.py`](../src/brr/envs/__init__.py) | Run metadata plus env scratch | `prepare -> invoke -> finalize`. |
| Gate module | [`gates/`](../src/brr/gates/) | Gate state under `.brr/gates/` | Transport adapter around event and response files. |

## Module Map

- `__main__.py` delegates to `cli.main`.
- `cli.py` dispatches to `adopt.py` (`init`), `runner.py` (`run`),
  `daemon.py` (`up`/`down`), and gate modules (`auth`, `bind`,
  `setup`).
- `adopt.py` handles `brr init` using git detection, config writing,
  prompt assembly, and runner invocation.
- `protocol.py` is the low-level event/response/frontmatter helper
  consumed by gates, daemon, runner profiles, and task persistence.
- `task.py`, `branching.py`, `conversations.py`, `updates.py`,
  `run_progress.py`, and `run_context.py` are the runtime state layer.
- `runner.py` owns runner detection, command construction, subprocess
  execution, trace writing, and direct `brr run`.
- `prompts.py` owns bundled prompt loading and daemon/setup/kb
  prompt assembly.
- `envs/__init__.py` owns the shipped env backends: `HostEnv`,
  `WorktreeEnv`, and `DockerEnv`.
- `daemon.py` is the integration point: PID file, gate startup,
  worker pool, sync, branch planning, env lifecycle, runner attempts,
  kb maintenance, finalization, and push.
- `sync.py` and `forges.py` stay small and observational: one handles
  best-effort ref freshness, the other converts remote URLs into
  branch-view URLs.
- `kb_preflight.py` and `kb_health.py` provide deterministic kb
  findings and graph stats for the maintenance prompt.

## Runtime Invariants

### `.brr/` Is Runtime State

Runtime files include inbox events, responses, tasks, runs,
conversations, traces, reviews, worktrees, gate state, prompt
overrides, doc overrides, and config. Do not commit `.brr/`.

### `kb/` Is Durable Knowledge

Project knowledge lives in `kb/` and is maintained as current-state
synthesis. Chronology belongs in `kb/log.md`; source-level behavior
belongs in source and tests.

### Runner Success Has Three Layers

`RunnerResult.validation_ok` means: subprocess exit was zero, required
artifacts exist, and stdout was non-empty when a response path was
requested.

### Branching Is Runtime-Owned By The Agent

The daemon resolves seed and optional expected publish target before
the run. Inside the worktree, the agent may stay on the run branch or
switch to a named branch. Finalization reads git state after the fact
and records the branch to publish; `daemon.publish` then ships it
(via refspec push when the agent kept the run branch but the event
named a different publish target, leased force-push for PR rebases,
or a plain push otherwise).

### Progress Is A Projection

The source of truth is the conversation log. Gate UI should go through
`run_progress` rather than derive status separately.

### Concurrency Is Partitioned

Conversation records are per event, progress card files are per task,
worktrees/branches are per task id, and git refs are protected by
per-branch locks. New shared state should follow that partitioning
model or justify a resource-specific lock.

### KB Maintenance Is Preflight Plus Redundancy

After successful work, `kb_preflight.scan()` and
`kb_health.compute_graph_stats()` decide whether to run the LLM
maintenance pass. The LLM pass is a redundancy check against the
AGENTS.md kb rules, not the primary schema definition.

## Design History To Read After Source

Subject hubs:

- [Subject: the kb itself](subject-kb.md)
- [Subject: daemon and process lifecycle](subject-daemon.md)
- [Subject: tasks and branching](subject-runs-branching.md)
- [Subject: environments](subject-envs.md)
- [Subject: fleet and overlays](subject-fleet-overlays.md)

Key decisions:

- [Remove triage decision](decision-remove-triage.md)
- [Drop streams decision](decision-drop-streams.md)
- [kb shape decision](decision-kb-shape.md)
- [Bundled docs decision](decision-bundled-docs.md)

Current designs:

- [Env protocol design](design-env-interface.md)
- [Publish kernel design](design-publish-kernel.md)
- [Git layer rework design](design-git-layer-rework.md)
- [Developer daemon reload design](design-daemon-dev-reload.md)
- [Concurrent execution design](design-concurrent-execution.md)

Plans and research worth knowing:

- [Concurrent worktrees plan](plan-concurrent-worktrees.md)
- [Branch modes plan](plan-branch-modes.md)
- [State-first kb maintenance plan](plan-kb-state-first-maintenance.md)
- [Agent orientation layering](plan-agent-orientation-layering.md)
- [Test suite grooming research](research-test-suite-grooming-2026-05-16.md)
- [Branch plan simplification research](research-branch-plan-simplification-2026-05-12.md)
- [Daemon runner context ergonomics research](research-runner-context-ergonomics-2026-05-09.md)
- [brr vs gh-aw research](research-brr-vs-gh-aw.md)

## Practical Navigation

- Event files, response files, or frontmatter: read
  [protocol.py](../src/brr/protocol.py).
- Seed refs, expected publish targets, or structured branch fields:
  read [branching.py](../src/brr/branching.py) and
  [subject-runs-branching.md](subject-runs-branching.md).
- Thread continuity or recent conversation injection: read
  [conversations.py](../src/brr/conversations.py) and
  [prompts.py](../src/brr/prompts.py).
- Lifecycle packets or progress cards: read
  [updates.py](../src/brr/updates.py) and
  [run_progress.py](../src/brr/run_progress.py).
- Runner subprocess behavior or traces: read
  [runner.py](../src/brr/runner.py).
- Worktrees, Docker, response path translation, or credential wiring:
  read [envs/__init__.py](../src/brr/envs/__init__.py).
- Daemon process lifecycle, worker pool, response release, kb
  maintenance, finalization, or publish: read
  [daemon.py](../src/brr/daemon.py) beside
  [subject-daemon.md](subject-daemon.md).
- GitHub/Telegram/Slack transport behavior: read
  [gates](../src/brr/gates/) and the gate tests.

## Maintenance Rule

Update this page when a source-reading path changes: public CLI
commands, event/task/conversation formats, shipped env backends,
daemon lifecycle, packet vocabulary, runner response contract, gate
hook surface, or built-in gate set. Keep the page compact; if a
section starts duplicating source, replace it with a link and a
current-state rule.
