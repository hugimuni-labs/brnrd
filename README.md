# brr

A daemon that runs AI agents on your machine and lets you manage
them remotely from a chat.

You describe tasks in plain language. brr delegates to whichever AI
tool you use (Claude Code, Codex, Gemini CLI, or a custom script)
and reports back. All state lives in your Git repo as plain Markdown.
No database, no cloud service, no lock-in.

## Quick start

```bash
pip install brr

brr init                         # adopt current repo
brr init https://github.com/u/r  # or clone and adopt

brr auth telegram                # set up your connector
brr connect telegram             # bind repo to a chat topic
brr up                           # start the daemon
```

From chat:

```
> fix the failing tests in auth/
> status
> write a migration for the new user fields
```

Or locally:

```bash
brr run "fix failing tests in the auth module"
brr status
```

## How it works

brr adds one file to your repo and keeps working state local:

- **`AGENTS.md`** — YAML header + instructions for AI tools. Committed.
- **`.brr.local/state.md`** — working memory: focus, decisions, next
  steps. Local and gitignored. Rewritten each run.

When executor output exceeds Telegram's message limit, brr posts the
full result to a GitHub gist and sends the link instead.

```
You (chat / CLI)
       |
    brr daemon
       |
  +-------+-------+
  |       |       |
repo A  repo B  repo C
  |       |       |
claude  codex   gemini
```

## Commands

| Command                   | What it does                         |
|---------------------------|--------------------------------------|
| `brr init [url]`          | Adopt a repo (optionally clone first)|
| `brr run "<task>"`        | Run a task through the executor      |
| `brr status`              | Show project state                   |
| `brr auth <connector>`    | Authenticate a chat connector        |
| `brr connect <connector>` | Bind repo to a chat topic            |
| `brr up`                  | Start the daemon                     |

## Connectors

Connectors let you interact with repos from a chat app.
Ships with **Telegram**.

## Executors

Configured per-repo in `AGENTS.md`. When set to `auto` (the default),
brr detects what's installed: `claude`, `codex`, `gemini`.

For anything else, set `default_executor` to the name of any
executable on PATH, or use `executor_cmd` for full command-template
control.

## Extending

brr is small on purpose. Connectors are single-file Python modules.
Executors are CLI commands. Prompts are plain Markdown.
Fork it and make it yours.

## Development

```bash
git clone https://github.com/…/brr.git
cd brr
pip install -e ".[dev]"
pytest
```

## License

MIT
