# Gates and portals

## Gates: how tasks get in and results get out

A **gate** is a transport adapter — Telegram, Slack, GitHub (issue labels,
PR/issue mentions), or a plain file you write yourself. Gates don't
interpret tasks; they just move them in and out through a simple file
protocol:

- Incoming: a gate writes a markdown file to `.brr/inbox/`.
- Outgoing: the daemon writes a result to `.brr/responses/`, and the gate
  delivers it back through whichever channel the task came from — a chat
  reply, a PR comment, a pushed branch link.

Because the contract is just files, writing a new gate doesn't require
touching brr's core at all — any process in any language that can read
and write files can act as one. See the repository's
`src/brr/gates/README.md` for the protocol spec and a minimal bash
example.

Telegram is the simplest to get running: a bot token is all you need.
Once the daemon is up, message the bot and brr records the chat to reply
in from then on.

## Portals: how a running task talks back mid-task

A task that takes a few minutes shouldn't be silent the whole time. While
a task is running, it can:

- Write progress notes to a live "card" so whoever's watching (a chat
  thread, a dashboard) sees what's happening as it happens, not just the
  final result.
- Send interim replies mid-task, in order, before the task finishes.
- Check for new, related messages that arrive while it's still working,
  and fold them into the current task instead of ignoring them until the
  next one starts.

This matters most for anything that runs longer than a few seconds:
without it, "the daemon is working on your request" is indistinguishable
from "the daemon is stuck," and a message you send five seconds after the
first one just sits in a queue with no acknowledgment until the whole
task finishes.

## Next

- [The daemon](daemon.md) for the process that ties gates, portals, and
  the runner together.
- [CLI reference](../reference/cli.md) for `brnrd bind` and the
  per-gate setup commands.
