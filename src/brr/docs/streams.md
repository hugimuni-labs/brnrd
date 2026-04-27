# Workstreams

A workstream is a named line of work — not a one-shot task and not a
free-form thought log. It aggregates related events, tasks, runs, and
artifacts so brr (and any downstream gate or dashboard) can reason
about a *line of work* instead of isolated pings.

The model is intentionally light. Streams are runtime state under
`.brr/streams/`. Durable knowledge still belongs in `kb/`.

## Structure

```
.brr/streams/
    index.json                       — gate-thread → stream_id index
    <stream-id>/
        stream.md                    — manifest (frontmatter + body)
        events.ndjson                — append-only event records
        tasks.ndjson                 — task references
        artifacts.ndjson             — typed artifact index
```

`stream.md` is the manifest. Frontmatter carries id, title, status,
intent, gate context, reply-route policy, and timestamps. The body
holds the current summary and open questions.

`events.ndjson`, `tasks.ndjson`, `artifacts.ndjson` are append-only
JSONL records. Reading the manifest plus the last few records gives
the full state of a stream without locking semantics.

## Lifecycle

Each incoming event is resolved to a stream before triage:

1. Explicit `stream_id` in event metadata.
2. Existing task or branch reference (rare, used for follow-ups).
3. Gate-native thread context — Telegram chat+topic, Slack channel+
   thread, the source git file. The mapping lives in `index.json`.
4. Fallback: create a new stream with a generated title from the
   first event line.

The daemon then emits typed update packets through the run. Packet
types are stable identifiers gates can branch on:

```
stream_created event_received task_created triage_done run_started
artifact_created needs_context done failed conflict
```

Packets are persisted to the stream's `events.ndjson` and offered to
each gate's optional `render_update(brr_dir, packet)` hook. Gates may
ignore them, render concise phase messages (Telegram), open a status
check (git), or render to a dashboard later.

## Reply routing

Each stream carries a `reply_route` policy:

```yaml
reply_route:
  preferred: input_gate
  allowed: [input_gate, stream_default, git_pr]
  selected: input_gate
```

Agents may suggest a reply route in their response frontmatter. brr
enforces policy: `selected` is only updated if the suggestion is in
`allowed`. The default and tiebreaker is the input gate — replies go
back to the channel that fired the event.

## CLI

- `brr streams` — list active streams.
- `brr stream show <id>` — manifest, recent tasks, artifacts, and
  events for a stream.
- `brr inspect <task-id>` — task view that links its stream and
  per-task artifacts.
- `brr status` — daemon overview that summarises active streams.

## What streams do not replace

- The KB. Persistent project knowledge (decisions, research,
  architecture) still lives in `kb/`. Streams index and summarise
  durable outputs; they do not replace them.
- A workflow engine. Streams are coordination state, not orchestration
  rules. Agents stay in charge of the work itself.
- A dashboard. The CLI surface is the first slice; richer renderings
  can be built on top of the same packet log when needed.
