# Active Task — orientation guide

Short orientation for agents running under brr. If you see an `Event:`
or `Task ID:` line in your prompt, you are a step in a brr pipeline,
not a standalone session. Most of what you need is already in the
prompt. Use this page when you need a refresher.

## At a glance

Every daemon-driven task ships a `Task Context Bundle` near the top of
the prompt. It contains:

- The workstream you belong to (id, title, intent, summary, open
  questions).
- The task itself: event id, task id, base branch, current branch,
  shared runtime dir, response path, log file (in worktree mode).
- The delivery contract: where to write the final response and how to
  treat the branch.
- The original event body when it fits inline.

Read it once at the start of the task. You should rarely need to call
`brr status` or `brr inspect` to orient yourself.

## When to fall back to commands

`brr` ships a few commands for deeper inspection. These remain useful
when the bundle is not enough:

- `brr status` — daemon state, active streams, active worktrees.
- `brr inspect <task-id>` — cross-linked manifest for any task.
- `brr inspect --event-body --prompt <task-id>` — original event and
  the latest runner prompt verbatim, useful when something looks
  inconsistent or pruned.
- `brr stream show <stream-id>` — full stream manifest with task and
  artifact history.
- `brr docs streams` — model overview for streams.
- `brr docs brr-internals` — the `.brr/` layout, KB maintenance, debug
  mode.

## What to write

- Final response → exact path given as `response path` in the bundle.
- Log entry → `kb/log.md` by default, or the `log file` path the
  bundle gives you (worktree mode).
- KB pages → `kb/<page>.md` only when the task warrants persistence
  (decisions, research, gotchas).
- Stage notes → only when the bundle says `stage feedback requested`.
  Keep them short and structured.

## What not to do

- Do not poke around `.brr/` beyond what the task asks for. It is
  runtime scratch, not project knowledge.
- Do not retarget your branch unless the task says so.
- Do not invent extra work to be helpful — proportionality wins.
- Do not `commit --amend` upstream history; one task = one commit on
  the current branch.

If something feels off — unfamiliar metadata, a missing path, an
ambiguous stream — say so in the response and stop. The orchestrator
prefers a clear `needs_context` over a guessing agent.
