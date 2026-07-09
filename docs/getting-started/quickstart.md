# Quickstart

## Set up a repo

Inside the project you want brr to work on:

```bash
brnrd init                          # detect runner, create AGENTS.md + kb/
```

This creates:

- **`AGENTS.md`** — a playbook with your workflow, kb conventions, commit
  protocol, artifact rules, and guardrails. Any AI tool that reads
  `AGENTS.md`-style files (Claude Code, Cursor, Codex, Gemini) picks up
  the same conventions, with or without the brr daemon running.
- **`kb/`** — a persistent, project-specific knowledge base. Agents
  working on the project write decisions, research, and design notes
  here, and read it back on the next session, so context compounds
  across sessions instead of resetting every time.
- **`.brr/`** — a gitignored runtime directory: inbox, responses, config,
  gate state.

## Run a task directly

```bash
brnrd run "fix the failing tests"
```

This runs one task through your configured AI CLI, right now, in the
foreground — no daemon, no remote gate, just a scripted one-shot
invocation with the same `AGENTS.md` + kb context any other brr-driven run
gets.

## Turn on remote execution

To hand tasks to brr from Telegram (or Slack, or a plain task file)
instead of the terminal:

```bash
brnrd bind . telegram               # configure a repo-local remote input
brnrd up                            # start the daemon in the foreground
```

Telegram works with just a bot token — once the daemon is running, send
the bot a message and brr records the chat ID from that message and
replies there. From then on:

```
> fix the failing tests in auth/
> research caching strategies for the API layer
> review the latest PR for security issues
```

Each message becomes a task. The daemon runs it through your configured
runner, in an isolated environment (worktree or container, depending on
your `environment` setting), and replies in the same chat when it's done
— usually with a link to a pushed branch or an opened PR.

To run brr as a persistent background service instead of a foreground
process:

```bash
brnrd daemon install                # install the native user service (systemd / LaunchAgent)
brnrd daemon status                 # check it
brnrd daemon logs                   # tail its logs
```

## Next

- [CLI reference](../reference/cli.md) for the full command list.
- [Concepts](../concepts/agents-and-kb.md) for how `AGENTS.md`, the kb,
  and the daemon fit together.
- Don't want to run the daemon yourself at all? See
  [brnrd.dev](https://brnrd.dev) for the hosted version — same software,
  operated for you.
