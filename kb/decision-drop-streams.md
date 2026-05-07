## Drop streams; conversations are routing+history, not identity

Status: accepted, 2026-05-05.

Supersedes: the `Workstream ergonomics — first slice` decision implicit
in the [2026-04-27 implementation log entry](log.md). Acknowledges
findings from [the 2026-04-28 runner review](agent-ergonomics-evaluation/task-context-bundle-runner-review-2026-04-28.md)
and [its v2 follow-up](agent-ergonomics-evaluation/task-context-bundle-v2-followup-review-2026-04-28.md);
both rated streams as "mostly working" but flagged a P3 to "let triage
assign concise stream titles" — that recommendation is the visible tip
of the deeper mismatch this decision addresses.

## What we removed

The `.brr/streams/` runtime layer:

- `stream.md` manifest with `title`, `intent`, `summary`, `open_questions`,
  `status`, `gate_context`, `reply_route`, `created`/`updated`.
- `events.ndjson`, `tasks.ndjson`, `artifacts.ndjson` per-stream.
- `index.json` mapping gate-thread → stream id.
- `brr streams` and `brr stream show` CLI.
- `## Workstream` / `### Workstream` blocks in triage prompt, daemon
  Task Context Bundle, and the per-task run-context file.
- The `stream_id` field on `Task` and `UpdatePacket`.
- `src/brr/docs/streams.md`.

## Why

The 2026-04-27 implementation tried to give brr a "line of work"
abstraction so events from the same gate thread could be threaded
together with shared title, intent, summary, and reply-route policy.
In practice the abstraction welded five jobs together:

| Job                                       | Useful? | Right home          |
|-------------------------------------------|---------|---------------------|
| Routing (gate_context, reply_route)       | yes     | event metadata + a small per-thread state |
| Append-only history of events / tasks    | yes     | per-conversation log |
| Identity (`title`, `intent`, `status`)    | not really — leaky | `kb/` if it matters |
| Continuity context for the agent          | yes — recent events suffice | the conversation log |
| Status / lifecycle machinery for streams  | barely used | drop |

Only the first two pulled their weight. Identity is the part that
broke: `_create_stream` derived `title` and `intent` from the slugified
first event body and never updated them. For free-form chat gates
(Telegram, Slack), the first message a user sends defines the
"workstream" forever, and that frozen string is then injected into:

- the triage prompt's `## Workstream` block,
- the daemon prompt's `### Workstream` block in the Task Context Bundle,
- per-task progress cards rendered by gates,
- `brr stream show` / `brr streams` output.

A real incident on 2026-05-05 made this concrete: a prompt-injection
demo pasted into Telegram on 2026-04-27 became the eternal `intent`
of that chat's stream and was re-fed to the triage agent for ten days
afterward, biasing every triage decision and bleeding into every
failure card. Cleaning up tracked repo files did not help — the
manifest lives in `.brr/`, gitignored. The 2026-04-28 reviews had
already flagged the "noisy auto-derived title" as P3; this incident
escalated it from cosmetic to a context-poisoning issue.

The deeper read: brr was doing **unsolicited summarisation**. Capable
agents do not need the orchestrator to assert "this conversation is
about X" based on a guess. They need the current event verbatim, where
to reply, recent events for continuity, and durable knowledge in `kb/`.
Forcing a frozen identity onto a free-form chat is more orchestration,
not less, and it produces lower-quality context than no identity at all.

A second principle reinforced this: `.brr/` is operational scratch and
agents do not have natural reach into it during runs. Putting "meaning"
(what the work *is*) inside `.brr/streams/` puts meaning where the
agent cannot durably read or update it. Meaning belongs in `kb/`, where
the existing playbook already says it goes.

## What replaced it

Conversations as a thin per-thread log:

```
.brr/conversations/
    <gate-thread-key>.ndjson    — append-only event/task/artifact/update records
```

A conversation has no manifest, no title, no intent, no status. It is
a routing anchor (`gate_thread_key`) plus an ordered history. Daemon
appends event arrivals, task lifecycle records, artifact creations,
and lifecycle update packets to a single ndjson per gate thread.

For agent context, prompts now carry a `## Recent in this conversation`
block fed by tailing the conversation log. No frozen identity, just
recent facts. Existing `kb/log.md` continues to provide cross-session
context.

For deliberate tracking of an ongoing line of work, write a `kb/`
page. The agent already reads and writes `kb/` on every run; lines of
work that matter become normal kb pages with whatever schema is useful
for that line of work. There is no special CLI for them.

## CLI surface after the change

| Command            | Status                                                |
|--------------------|-------------------------------------------------------|
| `brr status`       | trimmed: daemon health, runner, AGENTS.md, active task |
| `brr inspect <id>` | unchanged in spirit; drops stream cross-references    |
| `brr streams`      | removed                                               |
| `brr stream show`  | removed                                               |

`brr status` and `brr inspect` are dev-phase troubleshooting tools.
The primary user surface stays the gate (Telegram), which already
shows per-task progress cards. The chat history is the conversation
history — there is no separate "what happened" UI, by design.

## What this decision deliberately defers

- A "line of work" abstraction that spans multiple conversations or
  multiple gates. Not needed today; if pain emerges, add it as an
  explicit `kb/work-<slug>.md` convention or a small new module —
  do not revive the stream manifest.
- A migration of pre-existing `.brr/streams/` data. The directory is
  gitignored runtime scratch; it is harmless and can be deleted by
  hand. Daemon will not read from it.
- Per-task log file lifecycle (`kb/log-task-*.md` vs `kb/log.md`) —
  the 2026-04-28 reviews' P1 recommendation. Independent of this
  decision; tracked separately.

## Notes for future agents

If you find yourself wanting to add a `title` field to a conversation
(or any other field that "summarises what this is about"), stop. That
field is the bug we removed. Either the agent figures it out from
recent events (preferred), or a human writes a `kb/` page (when the
work matters enough to name).
