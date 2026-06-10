# Execution Map

How an event flows through brr, and where each artifact lives.

This document ships with the `brr` tool. Users can override it
per-repo by dropping a file at `.brr/docs/execution-map.md`.

## Pipeline

```
event (inbox) → task (persisted) → context file → run env → response → response release → kb preflight → finalize
```

### 1. Event arrives

A gate (Telegram, Slack, GitHub, future forge gates) or a script writes a
markdown file to `.brr/inbox/`. The file has frontmatter (`id`,
`source`, `status`) and a body with the user's message. The resident also
emits events to its **own** future: each reflex tick the daemon fires any
due entry from the dominion's `schedule.md` as a `schedule`-source inbox
event (`schedule.py`; `at:` one-shot / `every:` interval), so a
self-scheduled wake enters this same flow — see
`kb/design-self-scheduled-thoughts.md`.

### 2. Task created

The daemon constructs a `Task` directly from the event with
`Task.from_event` — no LLM-driven triage step. Environment policy is
resolved deterministically from the event source and `.brr/config`.
The task is saved to `.brr/tasks/<task-id>.md` and tracks: event ID,
env, status, source, and manifest metadata (response path, branch
name, worktree path, run context path, trace directories). Task files
still store the concrete backend as `env`; user-facing config should
prefer `environment`.

Branch behavior is no longer carried on the task. The daemon resolves a
branch plan before env prep: seed ref, optional auto-land target, and
authority. Worktree and Docker runs start on a fresh `brr/<task-id>`
branch sprouted from the seed ref. If the plan has no auto-land target,
commits on that task branch are preserved for human routing and
published when a remote is configured. The agent can still switch to a
named branch at runtime; brr preserves the branch it ends on.

### 3. Execution

The daemon hands the task off to one of the env backends — `host`,
`worktree`, or `docker` today. Each backend prepares the working
directory, invokes the runner, and finalizes the result. See
[`envs.md`](envs.md) for the full breakdown: when to pick each, the
docker credential wiring, the durability contract, and the salvage
rule.

The runner receives `run.md` + recent `kb/log.md` context + daemon
metadata (task ID, event ID, execution root, seed ref, optional
auto-land target, current branch, response path, interim-response outbox,
other pending events, shared runtime dir, generated run context file).
The bundle's delivery contract is explicit: stdout is the user's chat
reply, kb writes are optional — agents log only when there's something
worth logging (see AGENTS.md → Knowledge base).

Prompt assembly also injects the resident's dominion digest (per its
`self-inject` index) and, when the deterministic kb preflight isn't
clean, a `kb health` block of findings for the resident to fold into
its work (see [`brr-internals.md`](brr-internals.md) → KB maintenance).
The daemon path additionally injects `daemon-substrate.md` — brr's
driver's manual for the daemon-only machinery (single-flight, the
capture-at-sleep net, self-scheduled wakes) that the host-agnostic
dominion playbook leaves out; `brr run` skips it. `brr agent inject`
prints this assembled wake-context (dominion digest + matched pitfalls +
recent log) so a non-brr wrapper can reuse the same `self-inject`
semantic.

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

Once the response file is validated, the daemon marks the inbox event
`done` before environment finalization or branch push. Gates deliver
`done` events and clean up the inbox and response files after a
successful send, while the progress card can continue to show
post-response housekeeping.

The agent may *also* stream replies mid-thought (the multi-response
protocol; see [`brr-internals.md`](brr-internals.md) → Multi-response).
It drops markdown files in its per-event outbox (`.brr/outbox/<event-id>/`);
the daemon drains them on every heartbeat and once after the runner
returns, promoting each to a per-event partials queue
(`.brr/responses/<event-id>.partials/`). Gates stream queued partials —
for `processing` or `done` events — ahead of the terminal reply. An
outbox file whose frontmatter names another pending event
(`event: <id>`) is delivered to *that* event's thread and marks it
handled, so a quick request can be folded in without its own spawn.

After the runner returns, the daemon also **captures the resident's
dominion** (`.brr/dominion/`, the `brr-home` branch) with a serialized
commit — on success and failure alike — so working-memory edits survive
to the next wake without the agent committing by hand. The commit step is
serialized across processes by a file lock so a concurrent ad-hoc session
never races the shared git index.

If the runner exits cleanly but stdout is empty, the daemon retries up
to `response_retries` times before failing the task. Hard failures
(non-zero exit, timeout — controlled by `runner.timeout_seconds`,
default 3600s) are surfaced to the gate immediately with the captured
error rather than burning another expensive attempt.

### 5. Finalization

For worktree tasks, the daemon inspects the worktree's git state. If
the agent left commits on the original `brr/<task-id>` branch and the
branch plan has an auto-land target, that target is fast-forwarded.
With no target, or when the agent moved to another branch, the branch
is preserved as-is. If the target cannot fast-forward, the task becomes
`conflict` and the task branch is preserved. The worktree is removed
on a clean success with nothing uncommitted left behind; failures,
conflicts, and uncommitted/untracked leftovers keep the worktree for
inspection.

When `brr up --dev-reload` or `dev_reload=true` is active, this is also
the safe boundary where the daemon may re-exec itself if brr package
files changed. Reload never interrupts a running worker.

## Artifact locations

| Artifact      | Path                                        | Persists across runs                |
| ------------- | ------------------------------------------- | ----------------------------------- |
| Events        | `.brr/inbox/<event-id>.md`                  | Yes (until cleanup)                 |
| Tasks         | `.brr/tasks/<task-id>.md`                   | Yes                                 |
| Responses     | `.brr/responses/<event-id>.md`              | Yes                                 |
| Interim queue | `.brr/responses/<event-id>.partials/`       | Until streamed + cleaned up         |
| Agent outbox  | `.brr/outbox/<event-id>/`                   | Drained mid-run; removed at finalize |
| Presence      | `.brr/presence/<id>.json`                   | While a thought/session is active; pruned on read |
| Dominion      | `.brr/dominion/` (branch `brr-home`)        | Durable; committed at sleep, travels with the remote |
| Schedule state | `.brr/schedule/state.json`                 | Machine-persistent (firing-state); specs live in dominion `schedule.md` |
| Run context   | `.brr/runs/<task-id>/context.md`            | Yes                                 |
| Traces        | `.brr/traces/<kind>/<label>-<timestamp>/`   | Kept on `error` / `conflict`, removed on clean `done` |
| Reviews       | `.brr/reviews/`                             | Reserved for explicit review artifacts; not part of the default lifecycle |
| Worktrees     | `.brr/worktrees/<task-id>/`                 | Removed on clean success; kept on failure / conflict / uncommitted leftovers |
| Gate state    | `.brr/gates/<gate>.json`                    | Yes                                 |
| Config        | `.brr/config`                               | Yes                                 |

There are no per-task kb log files. Durable project knowledge goes in
`kb/` only when the task produced material worth preserving; `kb/log.md`
is the curated chronological narrative, not a mandatory completion
receipt.

## Cross-linking

The task file (`.brr/tasks/<task-id>.md`) is the central manifest.
Its frontmatter contains:

- `event_id` → links to `.brr/inbox/` and `.brr/responses/`
- `branch_name` → the git branch used
- `seed_ref` / `expected_publish_branch` → the resolved publish plan
- `publish_branch` / `publish_status` → recorded by finalize for the
  publish step (status is `ready` | `nothing` | `detached` |
  `conflict`)
- `worktree_path` → the worktree directory (if applicable)
- `context_path` → generated run context file
- `response_path` → the response file
- `trace_dirs` → comma-separated trace directories under `.brr/`
