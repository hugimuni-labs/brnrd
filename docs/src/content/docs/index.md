---
title: brnrd
description: Local coding agents, reachable from anywhere, with continuity across runs.
---

**Local agents go brr. From anywhere.**

Claude Code, Codex, and Gemini CLI live where the work is: your repo,
shell, credentials, and test setup. brnrd gives them a doorbell, a memory, and
a live line back to you.

Send a task from Telegram, Slack, GitHub, or the web. Watch the progress card
change while the agent works. Correct course at runner boundaries. Get a
branch, a pull request, or an answer back in the same thread.

brnrd is **not another coding agent**. It runs the CLI agents you already
chose, locally and under your rules, and gives each repo a resident with
continuity across runs.

## The loop

```text
you · Telegram / Slack / GitHub / dashboard
                         │
                         ▼
                    a small gate
                         │
                         ▼
          brnrd daemon · your machine · your repo
                         │
             Claude Code · Codex · Gemini CLI
                         │
                         ▼
                progress · replies · git
```

Your checkout and run execution stay on your machine. Remote messages use the
transport you choose, and managed mode has an additional, documented mirror
for derived project knowledge. Read [Security & privacy](./security/) before
opening a gate.

## Start here

1. [Install brnrd](./getting-started/install/).
2. [Choose a managed or self-hosted connection](./getting-started/connect/).
3. [Send your first task](./getting-started/first-task/).

## Current posture

brnrd is **alpha software, already used to build itself**. The resident loop,
local daemon, managed Telegram path, live dashboard, runner switching,
worktree/Docker execution, and git handoff are real. Public docs,
multi-project proving, managed billing/failover, and some operational polish
are still release work.

If you want a quiet appliance, wait. If you want a local agent coworker with a
remote door and are willing to report sharp edges, start with the install.

Source: [Gurio/brr](https://github.com/Gurio/brr). Managed service information:
[brnrd.dev](https://brnrd.dev).
