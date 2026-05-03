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
- An instruction to write a "final response" to a specific
  `.brr/responses/<event-id>.md` path.
- A `Write your log entry to kb/log-<task-id>.md` line (worktree mode).
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
| `tasks/`     | Parsed task manifests, one per event (source of truth post-triage) |
| `responses/` | Agent final responses destined for gate replies                    |
| `runs/`      | Generated per-task context files for daemon runner invocations     |
| `streams/`   | Workstream manifests, append-only event/task/artifact records      |
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
replaces the old command cheat sheet for task/event/stream recovery.

The agent does not run daemon lifecycle commands. `brr up` and
`brr down` are managed by the human operator.

## Override model

brr ships prompts and docs with the package. Lightweight runtime
choices belong in `.brr/config`, especially `runner`, `runner_cmd`,
and environment policy. Deep prompt or orchestration customization is
done by using a local checkout, editable install, or fork of brr.

Use `environment` for the user-facing execution policy:

- `environment=auto` — prefer configured Docker isolation, then fall
  back to worktree/host behavior.
- `environment=docker` — require Docker and `docker.image`.
- `environment=worktree` — run in a separate git worktree.
- `environment=host` — run directly in the main checkout.

The legacy `env` and `default_env` config keys are still accepted, but
new config should use `environment`.

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

## KB maintenance trigger

After every successful task, the daemon decides whether to run a
lightweight KB consistency pass (a second, short runner invocation
with `prompts/kb-maintenance.md`). The decision logic is:

```
policy = cfg["kb_maintenance"]  # default: "auto"

if policy == "never":   skip
if policy == "always":  run
if policy == "auto":    run only if this task actually touched kb/
```

The "did this task touch kb/" check is a git diff in the execution
root (the worktree for worktree tasks, or the main checkout for host
tasks):

```
git diff --name-only -- kb/          # tracked-file changes
git ls-files --others --exclude-standard -- kb/   # new untracked files
```

If either output is non-empty, the KB was touched and maintenance
runs. If the diff command fails (git missing, timeout) the check
returns False and maintenance is skipped. It's best-effort — a
failed maintenance pass is logged but never fails the parent task.

### Why this heuristic

Earlier drafts used task-body heuristics ("does the instruction look
big?") or triage-side classification. Those all require the triage
agent to guess ahead of time. The git-diff check is post-hoc: it
looks at what actually happened, not at what the agent was told to
do. A one-line fix that happened to add a kb entry gets maintained; a
big refactor that never touched kb/ doesn't pay the runner cost.

### Configuring it

In `.brr/config`:

- `kb_maintenance=auto` (default) — run only when kb/ was modified.
- `kb_maintenance=always` — run after every successful task.
- `kb_maintenance=never` — never run the maintenance step.

Set to `always` if you want stricter kb/ hygiene at the cost of one
extra runner invocation per task. Set to `never` if you prefer to do
kb maintenance manually.

## Concurrency model

The daemon processes events serially in v1. Worktree-based tasks
isolate the working directory and branch, but they do not yet run in
parallel — the worker pool is single-threaded. See
`kb/plan-concurrent-worktrees.md` for the roadmap in this repo.

When a worktree-backed task finishes, the daemon merges the branch back
(for `auto`/`task` strategies) or preserves it (named branches), then
removes the worktree unless debug mode keeps it for inspection. Docker
branch tasks use the same worktree-backed branch behavior, with the
runner command executed inside the configured container image.

The built-in Docker env requires Docker on PATH and `docker.image` in
`.brr/config`. It bind-mounts the repository at the same absolute path
inside the container so prompt paths, response files, traces, and git
metadata stay valid from both host and container.

## Debug mode

`brr up --debug` (or `debug=true` in config) changes two behaviours:

- Traces are written for every runner invocation (prompt, stdout,
  stderr, meta, artifacts) under `.brr/traces/<kind>/...`.
- Worktrees and Docker containers are preserved after their task finishes
  instead of being removed — useful for post-mortem inspection.

Reviewers can correlate a task's generated run context file with trace
directories under `.brr/traces/`.
