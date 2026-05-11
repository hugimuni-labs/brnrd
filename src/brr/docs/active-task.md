# Active Task — orientation guide

Short orientation for agents running under brr. If you see an `Event:`
or `Task ID:` line in your prompt, you are a step in a brr pipeline,
not a standalone session. Most of what you need is already in the
prompt. Use this page when you need a refresher.

## At a glance

Every daemon-driven task ships a `Task Context Bundle` near the top of
the prompt. It contains:

- The task itself: event id, task id, seed ref, optional auto-land
  branch, current branch, shared runtime dir, generated run context
  file, and response path.
- The delivery contract: stdout is the chat reply, brr captures it to
  the response path, and the branch rules for this task.
- A `Recent in this conversation` block when prior events from the
  same gate thread are available, so you can route consistently with
  what already happened.
- A generated run context file under `.brr/runs/<task-id>/context.md`
  for read-only recovery when the inline bundle is not enough.
- The original event body when it fits inline.

Read it once at the start of the task. You should not need `brr`
inspection commands to orient yourself.

## When to read the context file

The run context file is generated for the current task and lives in the
gitignored `.brr/` runtime directory. Read it when the inline bundle is
not enough or when you need to re-check original event text, the
conversation log path, runtime paths, or environment details.

Treat it as read-only. It is runtime scratch, not durable project
knowledge, and agents should not edit it.

## What to write

- Final response → print the exact intended user-visible reply as stdout.
  brr captures it to the response path; agents should not write that
  response file directly.
- Log entry → `kb/log.md` only when the task produced meaningful
  project knowledge worth preserving.
- KB pages → `kb/<page>.md` only when the task warrants persistence
  (decisions, research, gotchas, lines of work that span runs).

## What not to do

- Do not poke around `.brr/` beyond what the task asks for. It is
  runtime scratch, not project knowledge.
- Do not invent extra work to be helpful — proportionality wins.
- Do not `commit --amend` upstream history; one task = one commit on
  the current branch.

## Branching

You start on a fresh `brr/<task-id>` branch sprouted from the seed ref
named in the Task Context Bundle. Three valid outcomes:

- **Q&A / read-only** — answer in the response file and stop. No
  commit needed.
- **Work with an auto-land target** — commit on the current branch.
  brr fast-forwards the named target after the run.
- **Work with no auto-land target** — commit on the current task
  branch. brr preserves it for human routing and publishes it when a
  remote is configured.
- **Work for a different branch** — run `git switch -c <meaningful-name>`
  before committing. brr preserves whatever branch you end up on without
  merging.

If something feels off — unfamiliar metadata, a missing path, an
ambiguous instruction, a service you cannot reach — say so in the
response and stop. Reply with what you tried, what you need, and why
you stopped. Do not guess; the operator will follow up with another
event.
