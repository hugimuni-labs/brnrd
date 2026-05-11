# Conversations

A conversation is the running history of one gate thread — Telegram
chat+topic, Slack channel+thread, or git source file. brr appends
events, task lifecycle rows, artifact records, and lifecycle update
packets to one ndjson per thread. That log is the recent-activity
context the next agent in the same thread sees.

The model is intentionally small. Conversations have **no manifest,
no title, no intent, no status**. They exist only to thread events
and to give agents a recent-history block in their prompts. Durable
project knowledge still belongs in `kb/`.

## Structure

```
.brr/conversations/
    <safe-key>.ndjson    — append-only records for one gate thread
```

A conversation key is the gate-thread fingerprint:

- `telegram:<chat_id>:<topic_id>`
- `slack:<channel>:<thread_ts>`
- `git:<file>`

For filenames, `:` is encoded as `__`. Each file is an append-only
ndjson; every record carries a `ts` (UTC ISO 8601) and a `kind`:

| `kind`   | What it captures                                          |
|----------|-----------------------------------------------------------|
| `event`  | An incoming event from the gate (with `event_id`, `summary`) |
| `task`   | A task row (`task_id`, `env`, `status`, plus runtime branch info) |
| `update` | A lifecycle update packet for a task (typed via `type:`)  |
| `artifact` | A produced artifact (response file, durable kb page, etc.) |

Tail the log to see the conversation history. Filter by `task_id` to
project a single task's lifecycle.

## Lifecycle

Each incoming event is resolved to a conversation key before the
worker runs:

1. Explicit `conversation_key` carried on the event (rare).
2. Gate-thread fingerprint based on the event's source (Telegram chat,
   Slack thread, git source file).

If neither resolves, the event still gets a task and a response, but
no conversation log is written. This is by design — orphan or local
runs don't need threading context.

## Lifecycle update packets

The daemon emits typed packets through `brr.updates` and persists them
on the conversation log. Packet types are stable identifiers gates can
branch on:

```
event_received task_created env_prepared container_started
attempt_started attempt_failed retrying run_started artifact_created
heartbeat finalizing container_preserved push_started push_done
done failed conflict
```

`heartbeat` is a no-op for the projection — the daemon emits one every
30 seconds while a runner subprocess is alive (see
`daemon._invoke_with_heartbeat`). Its only job is to re-trigger a gate
render so the live elapsed counter on the chat card visibly bumps
during silent runs (codex with deep reasoning routinely sits quiet
for many minutes).

Gates may opt in to a `render_update(brr_dir, packet)` hook. The
Telegram and Slack gates render a live progress card per task and
edit it as new packets arrive. The Git gate is a no-op for live
rendering — commits and PRs are its delivery path.

## Run progress projection

`brr.run_progress` folds conversation records into a `RunProgressView`
per task: header fields (runner, env, branch ← base) plus a
`phase_history` of `PhaseEntry` records (preparing / running [per
attempt] / finalizing / delivered|failed|conflict). The projection is
the single source of truth for how a run looks; gate cards and
`brr inspect` render off the same view.

`render_text(view, *, compact, style)` produces the visible card.
Compact mode is the chat surface: the `runner · env · branch ← base`
header above a vertical phase log where closed entries are wrapped in
the gate-supplied strike-through tokens (`<s>…</s>` for Telegram HTML,
`~text~` for Slack mrkdwn) and the live entry shows its rolling
elapsed (`running · 4m 02s`). Verbose mode (`compact=False`) is the
dev surface used by `brr status` and `brr inspect` and keeps the
operator-facing rows (branch, env, runner, container IDs, response
path, artifact list).

## Lines of work

If you need to track an ongoing arc of work that spans multiple
conversations or sessions, write a `kb/` page. There is no special
runtime layer for it — agents read and write `kb/` on every run, and
the playbook expects durable project knowledge to live there. The
removed stream `title`/`intent`/`summary` fields are not coming back;
they were leaky by design when derived from raw chat text.

## What conversations do not replace

- The KB. Decisions, research, architecture notes still live in `kb/`.
- A workflow engine. Conversations are coordination state, not
  orchestration rules. Agents stay in charge of the work itself.
- A dashboard. Richer renderings can be built on top of the same
  ndjson log when needed.
