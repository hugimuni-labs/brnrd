---
title: First task
description: Send a task, watch the live card, steer the run, and receive a durable receipt.
---

## Send it through your gate

The first task arrives as a message, not a terminal command:
[connect](../connect/) a channel and send it from your phone, Telegram,
Slack, or the dashboard. You get a progress card, mid-run steering, and a
durable receipt back in the same thread.

```text
review PR #84 for the auth regression; show me the risky bit before changing it
```

Three things should happen in the same thread:

1. A progress card appears and changes as the resident reads, plans, and works.
2. You can add a fact or redirect the work; brnrd folds it in at a runner
   boundary without killing the thought in flight.
3. The run closes with a durable receipt: a branch, a pull request, or an
   answer.

Check the daemon if nothing appears:

```bash
brnrd daemon status
brnrd daemon logs --no-follow
```

The first recorded end-to-end demo is tracked in
[hugimuni-labs/brnrd#28](https://github.com/hugimuni-labs/brnrd/issues/28).

## No gate yet

Without a connected channel, `brnrd run "<task>"` is a synchronous
fallback — the same repository contract and runner the guided `brnrd`
setup just walked you through, driving the same coding-agent CLI, one task
at a time, right here in the terminal:

```bash
brnrd run "summarize the test layout; do not change files"
```

Good for a smoke test; [Connect](../connect/) is the real front door.
