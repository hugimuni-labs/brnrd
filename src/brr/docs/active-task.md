# Active Task — orientation guide

Short orientation for agents running under brr. If you see an `Event:`
or `Task ID:` line in your prompt, you are a step in a brr pipeline,
not a standalone session. Most of what you need is already in the
prompt. Use this page when you need a refresher.

## At a glance

Every daemon-driven task ships a `Task Context Bundle` near the top of
the prompt. It contains:

- The task itself: event id, task id, base branch, current branch,
  shared runtime dir, response path, log file (in worktree mode).
- The delivery contract: where to write the final response and how to
  treat the branch.
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

- Final response → exact path given as `response path` in the bundle.
- Log entry → `kb/log.md` by default, or the `log file` path the
  bundle gives you (worktree mode).
- KB pages → `kb/<page>.md` only when the task warrants persistence
  (decisions, research, gotchas, lines of work that span runs).

## What not to do

- Do not poke around `.brr/` beyond what the task asks for. It is
  runtime scratch, not project knowledge.
- Do not invent extra work to be helpful — proportionality wins.
- Do not `commit --amend` upstream history; one task = one commit on
  the current branch.

## Branching

You start on a fresh `brr/<task-id>` branch sprouted from the base
branch. Three valid outcomes:

- **Q&A / read-only** — answer in the response file and stop. No
  commit needed.
- **Work that should land** — commit on the current branch. brr
  fast-forwards it back onto the base branch after the run.
- **Work to keep on its own branch** — run
  `git switch -c <meaningful-name>` before committing. brr preserves
  whatever branch you end up on without merging.

If something feels off — unfamiliar metadata, a missing path, an
ambiguous instruction, a service you cannot reach — say so in the
response and stop. Reply with what you tried, what you need, and why
you stopped. Do not guess; the operator will follow up with another
event.
