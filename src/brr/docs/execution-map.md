# Execution Map

How an event flows through brr, and where each artifact lives.

This document ships with the `brr` tool. Users can override it
per-repo by dropping a file at `.brr/docs/execution-map.md`.

## Pipeline

```
event (inbox) → triage (classify) → task (persisted) → context file → run env → response → traces/review
```

### 1. Event arrives

A gate (Telegram, Slack, Git) or a script writes a markdown file to
`.brr/inbox/`. The file has frontmatter (`id`, `source`, `status`) and
a body with the user's message.

### 2. Triage

The daemon invokes the runner with `triage.md` to classify the event.
The triage agent decides how brr should stage any code changes
(`branch`) and usually leaves `environment` as `auto`, then outputs a
task spec (frontmatter + refined body). Environment policy is resolved
deterministically from the event and `.brr/config`; triage should not
guess just to optimize runtime.

Triage runs with a reduced log context window (last 3 entries only) —
it's a fast classifier, not an investigator.

### 3. Task persisted

The daemon parses triage output into a `Task` and saves it to
`.brr/tasks/<task-id>.md`. The task file tracks: event ID, branch,
env, status, source, and manifest metadata (response path, branch name,
worktree path, run context path, trace directories). Task files still
store the concrete backend as `env` for compatibility; user-facing
config should prefer `environment`.

`branch` is task-internal staging/delivery state. Users normally
configure `environment`; brr chooses branch behavior unless a request
explicitly names a branch or asks to work in the current checkout.

### 4. Execution

- **host**: runner runs in the main repo checkout.
- **worktree**: a git worktree is created under
  `.brr/worktrees/<task-id>`, the runner runs there, and the branch is
  merged back (for `auto`/`task` strategies) or preserved (for named
  branches) on success.
- **docker**: the runner command is wrapped in `docker run` using
  `docker.image` from `.brr/config`. Current-branch tasks mount the main
  checkout; branch tasks use the same worktree setup as `worktree` so the
  main checkout is not disturbed. Docker containers are removed only
  after a clean non-debug run.
- Other envs such as `devcontainer` or `ssh` are future backends/plugins
  and fail clearly until implemented or installed.

The runner receives `run.md` + recent `kb/log.md` context + daemon
metadata (task ID, event ID, execution root, current branch, response
path, shared runtime dir, generated run context file).

In worktree-backed modes, including Docker branch tasks, the agent writes
its log entry to `kb/log-<task-id>.md` to avoid conflicts with the main log.

### 5. Response

The agent's final reply is its last stdout message. brr captures stdout
and writes it to `.brr/responses/<event-id>.md`. Runners are invoked
headless (`claude --print`, `codex exec`, `gemini -p --yolo`); progress
goes to stderr and only the final reply is on stdout, so no per-runner
output flag is needed.

If stdout is empty, the daemon retries up to `response_retries` times
before failing the task.

### 6. KB maintenance (optional)

If the task modified files in `kb/`, a lightweight maintenance step runs
to verify `kb/index.md` consistency and ensure a log entry exists.
See `brr-internals.md` for the full trigger logic.

### 7. Finalization

For worktree tasks with `auto`/`task` branch strategy, the branch is
merged back to the main checkout and the worktree is removed. For named
branches, the worktree is removed but the branch is preserved.

## Artifact locations

| Artifact      | Path                                        | Persists across runs                |
| ------------- | ------------------------------------------- | ----------------------------------- |
| Events        | `.brr/inbox/<event-id>.md`                  | Yes (until cleanup)                 |
| Tasks         | `.brr/tasks/<task-id>.md`                   | Yes                                 |
| Responses     | `.brr/responses/<event-id>.md`              | Yes                                 |
| Run context   | `.brr/runs/<task-id>/context.md`            | Yes                                 |
| Traces        | `.brr/traces/<kind>/<label>-<timestamp>/`   | Yes (debug mode)                    |
| Reviews       | `.brr/reviews/<event-id>.md`                | Yes                                 |
| Worktrees     | `.brr/worktrees/<task-id>/`                 | Removed after merge (kept in debug) |
| Gate state    | `.brr/gates/<gate>.json`                    | Yes                                 |
| Config        | `.brr/config`                               | Yes                                 |
| Per-task logs | `kb/log-<task-id>.md` (in worktree)         | Merged into `kb/log.md`             |

## Cross-linking

The task file (`.brr/tasks/<task-id>.md`) is the central manifest.
Its frontmatter contains:

- `event_id` → links to `.brr/inbox/` and `.brr/responses/`
- `branch_name` → the git branch used
- `worktree_path` → the worktree directory (if applicable)
- `context_path` → generated run context file
- `response_path` → the response file
- `trace_dirs` → comma-separated trace directories under `.brr/`
