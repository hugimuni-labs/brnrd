# Execution Map

How an event flows through brr, and where each artifact lives.

This document ships with the `brr` tool. Users can override it
per-repo by dropping a file at `.brr/docs/execution-map.md`.

## Pipeline

```
event (inbox) → task (persisted) → context file → run env → response → traces/review
```

### 1. Event arrives

A gate (Telegram, Slack, Git) or a script writes a markdown file to
`.brr/inbox/`. The file has frontmatter (`id`, `source`, `status`) and
a body with the user's message.

### 2. Task created

The daemon constructs a `Task` directly from the event with
`Task.from_event` — no LLM-driven triage step. Environment policy is
resolved deterministically from the event source and `.brr/config`.
The task is saved to `.brr/tasks/<task-id>.md` and tracks: event ID,
env, status, source, and manifest metadata (response path, branch
name, worktree path, run context path, trace directories). Task files
still store the concrete backend as `env`; user-facing config should
prefer `environment`.

Branch behavior is no longer carried on the task. Worktree and Docker
runs always start on a fresh `brr/<task-id>` branch sprouted from the
current `HEAD`. The agent decides at runtime whether to commit there
(brr fast-forwards back) or switch to a named branch (brr preserves
it).

### 3. Execution

The daemon hands the task off to one of the env backends — `host`,
`worktree`, or `docker` today. Each backend prepares the working
directory, invokes the runner, and finalizes the result. See
[`envs.md`](envs.md) for the full breakdown: when to pick each, the
docker credential wiring, the durability contract, and the salvage
rule.

The runner receives `run.md` + recent `kb/log.md` context + daemon
metadata (task ID, event ID, execution root, current branch, response
path, shared runtime dir, generated run context file). The bundle's
delivery contract is explicit: stdout is the user's chat reply, kb
writes are optional — agents log only when there's something worth
logging (see AGENTS.md → Knowledge base).

### 4. Response

The agent's final reply is its last stdout message. brr captures stdout
and writes it to `.brr/responses/<event-id>.md`. Runners are invoked
headless (`claude --print`, `codex exec`, `gemini -p --yolo`); progress
goes to stderr and only the final reply is on stdout, so no per-runner
output flag is needed.

Responses are plain text — there is no frontmatter contract. If the
agent cannot complete the task (missing context, ambiguous request,
unreachable service), it should say so plainly in the response and
stop. The operator sees the reply in the gate thread and follows up
with another event.

If stdout is empty, the daemon retries up to `response_retries` times
before failing the task.

### 5. KB maintenance (preflight + optional LLM pass)

After a successful task, `brr.kb_preflight.scan` runs over `kb/` and
returns structured findings (orphan pages, broken links, index
drift). When findings exist or the task modified `kb/`, a short LLM
redundancy pass runs with the findings injected into the prompt;
otherwise the LLM pass is skipped. The primary maintenance contract
lives in AGENTS.md (the universal kb shape rules every tool follows);
this hook is the brr-side safety net. See `brr-internals.md` for the
preflight check list and trigger logic.

### 6. Finalization

For worktree tasks, the daemon inspects the worktree's git state. If
the agent left commits on the original `brr/<task-id>` branch and the
base branch can fast-forward, the branch is folded back. Otherwise
(the agent moved to another branch, or the merge would not be
fast-forward), the branch is preserved as-is. The worktree is removed
unless debug mode keeps it for inspection.

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
