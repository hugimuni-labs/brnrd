# Conversations

A conversation is the running history of one gate thread — Telegram
chat+topic, Slack channel+thread, or GitHub repo+issue/PR (other forges
mirror the same idea). brr appends
events, task lifecycle rows, artifact records, and lifecycle update
packets under a per-thread directory. That history is the recent-
activity source for the next agent in the same thread; daemon prompts
filter it down to semantic context so ordinary runs see user events,
branch rows, final outcomes, and push summaries rather than raw
lifecycle plumbing.

The model is intentionally small. Conversations have **no manifest,
no title, no intent, no status**. They exist only to thread events
and to give agents a recent-history block in their prompts. Durable
project knowledge still belongs in `kb/`.

## Structure

```
.brr/conversations/
    <safe-key>/
        <event-id>.jsonl    — one pipeline run's append-only records
```

A conversation key is the gate-thread fingerprint:

- `telegram:<chat_id>:<topic_id>`
- `slack:<channel>:<thread_ts>`
- `github:<owner/repo>:<issue_or_pr_number>`

For directory names, `:` is encoded as `__`. Each `<event-id>.jsonl`
file is owned by the one worker that handles that event — the
contention-free layout keeps the concurrent worker pool from sharing
mutable state across pipelines. Every record carries a `ts` (UTC ISO
8601, microsecond precision) and a `kind`:

| `kind`   | What it captures                                          |
|----------|-----------------------------------------------------------|
| `event`  | An incoming event from the gate (with `event_id`, `summary`) |
| `task`   | A task row (`task_id`, `env`, `status`, plus runtime branch info) |
| `update` | A lifecycle update packet for a task (typed via `type:`)  |
| `artifact` | A produced artifact (response file, durable kb page, etc.) |

Tail any one event's jsonl to see that pipeline's lifecycle. Reading
the whole directory and sorting by `ts` reconstructs the full
conversation history (`brr.conversations.read_records`). For prompt
tails and other “last *N* rows” use cases, `read_recent` merges the
newest *N* records by `ts` without loading every line of every file
(assumes `ts` is non-decreasing within each jsonl — the normal
single-writer append contract). The run-progress projection reads the
full merged timeline for a task via `read_records`.

## Lifecycle

Each incoming event is resolved to a conversation key before the
worker runs:

1. Explicit `conversation_key` carried on the event (rare).
2. Gate-thread fingerprint based on the event's source (Telegram chat,
   Slack thread, GitHub issue/PR).

If neither resolves, the event still gets a task and a response, but
no conversation log is written. This is by design — orphan or local
runs don't need threading context.

## Lifecycle update packets

The daemon emits typed packets through `brr.updates` and persists them
on the conversation log. Packet types are stable identifiers gates can
branch on:

```
event_received synced task_created env_prepared container_started
attempt_started attempt_failed retrying run_started artifact_created
heartbeat finalizing container_preserved push_started push_done
kb_maintenance_done done failed conflict
```

`heartbeat` is a no-op for the projection — the daemon emits one every
30 seconds while a runner subprocess is alive (see
`daemon._invoke_with_heartbeat`). Its only job is to re-trigger a gate
render so the live elapsed counter on the chat card visibly bumps
during silent runs (codex with deep reasoning routinely sits quiet
for many minutes).

Gates may opt in to a `render_update(brr_dir, packet)` hook. The
Telegram and Slack gates render a live progress card per task and
edit it as new packets arrive. Non-chat gates typically skip the
hook and let the delivered artifact (a commit, a comment, a file)
speak for the run.

## Run progress projection

`brr.run_progress` folds conversation records into a `RunProgressView`
per task: header fields (runner, env, branch ← seed/target) plus a
`phase_history` of `PhaseEntry` records (preparing / running [per
attempt] / finalizing / delivered|failed|conflict). The projection is
the single source of truth for how a run looks; gate cards render off
this view.

`render_text(view, *, compact, style)` produces the visible card.
Compact mode is the chat surface: the `runner · env · branch ← seed`
or `runner · env · branch ← target` header above a vertical phase log
where closed entries are wrapped in
the gate-supplied strike-through tokens (`<s>…</s>` for Telegram HTML,
`~text~` for Slack mrkdwn) and the live entry shows its rolling
elapsed (`running · 4m 02s`). Verbose mode (`compact=False`) is the
expanded diagnostic renderer and keeps operator-facing rows (branch,
env, runner, container IDs, response path, artifact list).

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
  per-thread jsonl files when needed.
