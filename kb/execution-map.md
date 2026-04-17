# Execution Map

How an event flows through brr, and where each artifact lives.

## Pipeline

```
event (inbox) → triage (classify) → task (persisted) → run (worktree or local) → response → traces/review
```

### 1. Event arrives

A gate (Telegram, Slack, Git) or a script writes a markdown file to
`.brr/inbox/`.  The file has frontmatter (`id`, `source`, `status`) and
a body with the user's message.

### 2. Triage

The daemon invokes the runner with `triage.md` to classify the event.
The triage agent decides **branch strategy** and **execution environment**,
then outputs a task spec (frontmatter + refined body).

### 3. Task persisted

The daemon parses triage output into a `Task` and saves it to
`.brr/tasks/<task-id>.md`.  The task file tracks: event ID, branch,
env, status, source, and manifest metadata (response path, branch name,
worktree path, trace directories).

### 4. Execution

- **local** (`branch: current`, `env: local`): runner runs in the main
  repo checkout.
- **worktree** (any other branch strategy): a git worktree is created
  under `.brr/worktrees/<task-id>`, the runner runs there, and the
  branch is merged back (for `auto`/`task` strategies) or preserved
  (for named branches) on success.

The runner receives `run.md` + recent `kb/log.md` context + daemon
metadata (task ID, event ID, execution root, current branch, response
path, shared runtime dir).

In worktree mode, the agent writes its log entry to
`kb/log-<task-id>.md` to avoid conflicts with the main log.

### 5. Response

The agent's final response is written to `.brr/responses/<event-id>.md`.
Some runners capture this automatically; the daemon prompt also instructs
the agent to write it manually if needed.

### 6. KB maintenance (optional)

If the task modified files in `kb/`, a lightweight maintenance step runs
to verify `kb/index.md` consistency and ensure a log entry exists.
Controlled by `kb_maintenance` config (`auto`/`always`/`never`).

### 7. Finalization

For worktree tasks with `auto`/`task` branch strategy, the branch is
merged back to the main checkout and the worktree is removed.  For named
branches, the worktree is removed but the branch is preserved.

## Artifact locations

| Artifact | Path | Persists across runs |
|----------|------|---------------------|
| Events | `.brr/inbox/<event-id>.md` | Yes (until cleanup) |
| Tasks | `.brr/tasks/<task-id>.md` | Yes |
| Responses | `.brr/responses/<event-id>.md` | Yes |
| Traces | `.brr/traces/<kind>/<label>-<timestamp>/` | Yes (debug mode) |
| Reviews | `.brr/reviews/<event-id>.md` | Yes |
| Worktrees | `.brr/worktrees/<task-id>/` | Removed after merge (kept in debug) |
| Gate state | `.brr/gates/<gate>.json` | Yes |
| Config | `.brr/config` | Yes |
| Per-task logs | `kb/log-<task-id>.md` (in worktree) | Merged into `kb/log.md` |

## Cross-linking

The task file (``.brr/tasks/<task-id>.md``) is the central manifest.
Its frontmatter contains:

- `event_id` → links to `.brr/inbox/` and `.brr/responses/`
- `branch_name` → the git branch used
- `worktree_path` → the worktree directory (if applicable)
- `response_path` → the response file
- `trace_dirs` → comma-separated trace directories under `.brr/`

Use `brr inspect <task-id>` to view all linked artifacts for a task.
