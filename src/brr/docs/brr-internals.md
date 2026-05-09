# brr Internals

Orientation for an agent running under brr. This document ships with
the `brr` tool itself — it is not project-specific.

If you (the agent) are running and something about the environment is
confusing (unfamiliar folders, unexpected metadata in your prompt, a
per-task log file), consult the generated run context file before
guessing.

## You might be running under brr

You can tell you are running under a brr-driven invocation by the
following signals in your prompt:

- An `Event:` and/or `Task ID:` line in the metadata block.
- A `### Delivery contract` block telling you stdout is the chat reply
  and pointing at a specific `.brr/responses/<event-id>.md` path.
- A `Shared runtime dir:` pointing at the main checkout's `.brr/`.
- A generated `.brr/runs/<task-id>/context.md` file named in the
  Task Context Bundle.

When you see these, you are not in a normal interactive session. You
are one step of a pipeline. Behave accordingly:

- Do your best-effort work within the task scope.
- Do not invent extra work to be "helpful".
- Do not explore or edit `.brr/` beyond what the task explicitly
  requires. It is runtime scratch space.
- Your exit / final message will be captured and forwarded to a human
  over a gate (Telegram, Slack, Git PR comment), so keep it focused.

## `.brr/` layout

All runtime state lives under `.brr/` at the repo root. It is
gitignored; do not commit its contents.

| Folder       | Purpose                                                            |
| ------------ | ------------------------------------------------------------------ |
| `inbox/`     | Incoming events from gates, one markdown file per event            |
| `tasks/`     | Task manifests, one per event (source of truth for the worker)     |
| `responses/` | Agent final responses destined for gate replies                    |
| `runs/`      | Generated per-task context files for daemon runner invocations     |
| `conversations/` | Per-gate-thread append-only logs of events, tasks, artifacts, lifecycle updates |
| `traces/`    | Prompt + stdout + meta for every runner invocation (debug mode)    |
| `reviews/`   | Self-review notes the agent writes about its own runs              |
| `worktrees/` | Isolated git worktrees for concurrent tasks                        |
| `gates/`     | Per-gate auth/state JSON                                           |
| `prompts/`   | Legacy per-repo prompt overrides                                   |
| `docs/`      | User overrides of bundled docs (see below)                         |
| `config`     | Key=value runtime config                                           |

## Agent recovery surface

Agents should orient from the Task Context Bundle in the prompt. When
they need to re-check runtime details, they should read the generated
`.brr/runs/<task-id>/context.md` file named in the bundle. That file
replaces the old command cheat sheet for task/event recovery.

The agent does not run daemon lifecycle commands. `brr up` and
`brr down` are managed by the human operator.

## Override model

brr ships prompts and docs with the package. Lightweight runtime
choices belong in `.brr/config`, especially `runner`, `runner_cmd`,
and environment policy. Deep prompt or orchestration customization is
done by using a local checkout, editable install, or fork of brr.

Use `environment` for the user-facing execution policy:

- `environment=auto` — prefer configured Docker isolation, then fall
  back to worktree behavior.
- `environment=docker` — require Docker and `docker.image`.
- `environment=worktree` — run in a separate git worktree.
- `environment=host` — run directly in the main checkout (no isolation).

The legacy `env` and `default_env` config keys are still accepted, but
new config should use `environment`.

Branching is no longer carried on the task file. Worktree and Docker
runs always start on a fresh `brr/<task-id>` branch sprouted from the
current `HEAD`. The agent decides at runtime whether the work should
land back on the base branch (commit on the current branch, brr
fast-forwards) or be preserved on its own branch (`git switch -c
<name>` first, brr leaves it untouched).

Legacy per-repo override folders may still be read by the library, but
there is no public command to seed them:

| Bundled at                     | Per-repo override         |
| ------------------------------ | ------------------------- |
| `src/brr/prompts/<name>.md`    | `.brr/prompts/<name>.md`  |
| `src/brr/docs/<name>.md`       | `.brr/docs/<name>.md`     |

The runner checks prompt overrides first, then falls back to the
bundled copy. Docs helpers do the same for doc overrides when used
internally.

Project-specific knowledge belongs in `kb/` (the knowledge base),
never in `.brr/`. The split is:

- `kb/` — permanent, project-specific, committed to the repo. Owned by
  agents working in this repo.
- `.brr/` — tool runtime. Ephemeral unless debug mode says otherwise.
- `src/brr/docs/` (bundled) + `.brr/docs/` (override) — tool
  documentation, same across all repos unless a user overrides.

## KB maintenance: preflight + redundancy pass

After every successful task, the daemon runs a deterministic kb
consistency scan (`brr.kb_preflight.scan(run_root)`) over `kb/` and
*may* follow it with a short LLM redundancy pass invoked with
`prompts/kb-maintenance.md`. The decision logic is:

```
policy = cfg["kb_maintenance"]  # default: "auto"

if policy == "never":   skip both preflight and LLM pass
findings = kb_preflight.scan(run_root)
kb_changed = git diff / ls-files in kb/

if policy == "auto" and not kb_changed and not findings:
    skip the LLM pass — the safety net is clean
else:
    inject findings into the maintenance prompt and run the LLM pass
```

The preflight is cheap and structural — it only flags things a
deterministic scanner can be confident about:

- `missing-from-index` — a kb page exists on disk but isn't linked
  from `kb/index.md`.
- `stale-index-entry` — `kb/index.md` links to a path that doesn't
  exist on disk.
- `broken-link` — any kb page (other than `log.md`) links relatively
  to a path that doesn't exist.

Lifecycle-marker drift, contradictions with the log, and other
judgement calls are left to the LLM redundancy pass — they need
synthesis the scanner can't do.

### "Did this task touch kb/" check

```
git diff --name-only -- kb/                     # tracked-file changes
git ls-files --others --exclude-standard -- kb/ # new untracked files
```

If either output is non-empty, the kb was touched. If the git command
fails (missing, timeout) the check returns False — the preflight
findings still carry the maintenance pass on their own when needed.

### Why preflight + redundancy

Earlier drafts ran the LLM pass on every kb-touched task and skipped
otherwise — a task-body heuristic. The preflight inverts the contract:
deterministic checks are cheap enough to run every time, so they
become the safety net that catches drift left by *previous* tasks too
(say, a slashed page that another page still links to). The LLM pass
is reserved for the synthesis-heavy work, with the concrete findings
already in the prompt.

A failed maintenance pass is logged but never fails the parent task.

### Configuring it

In `.brr/config`:

- `kb_maintenance=auto` (default) — preflight always; LLM pass only
  when kb changed or the preflight has findings.
- `kb_maintenance=always` — LLM pass after every successful task,
  even with a clean kb and clean preflight.
- `kb_maintenance=never` — skip both the preflight and the LLM pass.

Set to `always` if you want stricter kb/ hygiene at the cost of one
extra runner invocation per task. Set to `never` if you prefer to do
kb maintenance manually.

## Run progress UX

The daemon emits typed lifecycle packets through `brr.updates` for
every task: `task_created`, `env_prepared`, `container_started`,
`attempt_started`, `attempt_failed`, `retrying`, `run_started`,
`artifact_created`, `finalizing`, `container_preserved`,
`push_started`, `push_done`, plus the terminal `done` / `failed` /
`conflict`.

Gates may opt in to a `render_update(brr_dir, packet)` hook to surface
progress to a human:

- The Telegram gate sends one progress message per task in the
  originating chat or topic on `task_created`, then edits the same
  message via `editMessageText` for later packets. State lives at
  `.brr/gates/telegram_progress.json`.
- The Slack gate posts one threaded reply per task on `task_created`,
  then updates it with `chat.update`. State lives at
  `.brr/gates/slack_progress.json`.
- The Git gate is a no-op for live progress. Git is not a great surface
  for live status; commits and PRs remain its primary delivery path.

Local commands (`status`, `inspect_task`) are now troubleshooting
helpers. They render the same `RunProgressView` as gates so that if a
remote run looks wrong, `brr status` shows the same view a Telegram
card would.

## Concurrency model

The daemon processes events serially in v1. Worktree-based tasks
isolate the working directory and branch, but they do not yet run in
parallel — the worker pool is single-threaded. See
`kb/plan-concurrent-worktrees.md` for the roadmap in this repo.

When a worktree-backed task finishes, the daemon inspects the
worktree's git state. If the agent left commits on the original
`brr/<task-id>` branch and the base branch can fast-forward, the
branch is folded back; otherwise (the agent created/checked out a
different branch, or the merge would not be fast-forward) the branch
is preserved as-is and the worktree is removed unless debug mode keeps
it for inspection. Docker tasks use the same worktree-backed branch
behavior, with the runner command executed inside the configured
container image.

The full env story — built-ins, configuration knobs, the docker
credential wiring, the durability contract, and the salvage rule —
lives in [`envs.md`](envs.md).

## Debug mode

`brr up --debug` (or `debug=true` in config) changes two behaviours:

- Traces are written for every runner invocation (prompt, stdout,
  stderr, meta, artifacts) under `.brr/traces/<kind>/...`.
- Worktrees and Docker containers are preserved after their task finishes
  instead of being removed — useful for post-mortem inspection.

Reviewers can correlate a task's generated run context file with trace
directories under `.brr/traces/`.
