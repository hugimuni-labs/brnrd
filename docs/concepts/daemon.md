# The daemon

`brnrd up` starts one long-running process per machine: the daemon. It
watches for incoming tasks and runs them through your configured AI CLI,
one at a time.

## What it does

The daemon is intentionally small — a thin dispatch layer that leaves all
the actual judgment to the AI agent it wakes up for each task. Concretely:

- It watches `.brr/inbox/` (and any connected gates — Telegram, Slack,
  GitHub) for new tasks.
- When a task arrives and nothing else is running, it prepares an
  isolated environment (a git worktree or a container, depending on your
  `environment` setting), invokes your configured runner (Claude Code,
  Codex, Gemini, or a custom command) with the task and the project's
  `AGENTS.md` + knowledge-base context, and captures the result.
- It delivers the result back through whichever gate the task came from —
  a chat reply, a pushed branch, an opened PR — and pushes any committed
  work.
- It stops cleanly on `brnrd down` (or a `SIGTERM`): if a task is
  mid-flight, the daemon stops the runner, finalizes whatever state
  exists, and exits rather than leaving a task half-done with no record.

## One task at a time (single-flight)

The daemon runs **one task at a time, start to finish**, rather than a
pool of parallel workers. A task that's already running keeps running
uninterrupted; anything else that arrives queues and waits its turn.

This is a deliberate simplicity trade: a single in-flight task can take
its time, use the full isolated environment without contention, and
narrate its own progress, while the daemon itself stays a small, easy-to-
reason-about loop. If you send a second message while the first task is
still working, it's queued — not dropped — and the daemon picks it up
the moment the current task finishes (or folds it into the reply already
in progress, if the task notices it and it's clearly related).

## Reacting quickly, not just polling

New tasks don't sit around waiting for a fixed timer to notice them.
Anything that happens inside the daemon's own process (a chat gate
receiving a message, a scheduled task coming due) wakes the loop
immediately; a short poll interval (a few seconds) is only the backstop
for things that can't signal the daemon directly, like another process
writing a task file straight to `.brr/inbox/`.

## Running it as a background service

For day-to-day use you'll usually want the daemon running persistently
rather than in a foreground terminal:

```bash
brnrd daemon install   # installs a systemd user service (Linux) or LaunchAgent (macOS)
brnrd daemon status
brnrd daemon logs
```

`brnrd up` (foreground) is the right choice while you're setting things
up or actively watching what's happening; `brnrd daemon install` is the
right choice once you're happy with the configuration and want it to
just run.

## Where this is going

See [Gates and portals](gates-and-portals.md) for how tasks get in and
results get out, and the [CLI reference](../reference/cli.md) for the
`environment=` and `runner=` settings that shape what the daemon does
with each task.
