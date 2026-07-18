---
title: CLI reference
description: The shipped brnrd command tree and common project settings.
---

This page reflects the public command tree printed by the installed CLI on
2026-07-17. Run `brnrd <command> --help` for the exact options in your version.

## Core workflow

| Command | Purpose |
|---|---|
| `brnrd init [url]` | Set up a repo; `-i` runs interactive setup. |
| `brnrd run "<instruction>"` | Run one task through the configured Runner. |
| `brnrd review <pack>` | Validate or project a diffense review pack; supports `--check`, `--pr-body`, `--pr-title`, `--relay`, and `--json`. |
| `brnrd up [--foreground] [--dev-reload]` | Start the daemon; shortcut for `daemon up`. |
| `brnrd down` | Stop the daemon; shortcut for `daemon down`. |

## Gates and accounts

| Command | Purpose |
|---|---|
| `brnrd gate setup <gate>` | Authenticate and bind a gate in one flow. |
| `brnrd gate auth <gate>` | Authenticate `telegram`, `slack`, `github`, or `cloud`. |
| `brnrd gate bind <repo> <gate>` | Bind a repo-local gate. |
| `brnrd gate list [--json]` | Show gates configured here. |
| `brnrd account connect [url]` | Pair the daemon with brnrd; accepts `--daemon-name`. |
| `brnrd account add <repo>` | Add a repo to the connected account home. |
| `brnrd account status [--json]` | Show the resolved home and its repos. |
| `brnrd home link` | Back up resident memory and project knowledge to private GitHub repos; use `--help` before this mutating operation. |

The retired top-level spellings `auth`, `bind`, `setup`, `add`, and `connect`
are not aliases. Use the noun-first commands above.

## Daemon lifecycle

| Command | Purpose |
|---|---|
| `brnrd daemon up [--foreground] [--dev-reload]` | Start the installed service, or foreground daemon when selected. |
| `brnrd daemon down` | Stop it. |
| `brnrd daemon status` | Show service and foreground status. |
| `brnrd daemon install` | Install the systemd user service or macOS LaunchAgent; supports `--no-start`. |
| `brnrd daemon uninstall` | Remove the service. |
| `brnrd daemon logs [-n LINES] [--no-follow]` | Read or follow service logs. |

## Knowledge, diagnostics, and resident tools

| Command | Purpose |
|---|---|
| `brnrd docs [topic]` | Read the documentation bundled with the tool. |
| `brnrd kb "<query>" [--limit N]` | Search home and repo knowledge. |
| `brnrd portal state [--json] [--path PATH]` | Inspect live daemon portal state. |
| `brnrd portal facets [--json] [--path PATH]` | List the portal facet catalogue and live population. |
| `brnrd agent inject [--task TEXT]` | Print the wake context a daemon task would receive. |
| `brnrd ergonomics summary [--days N] [--json]` | Summarize captured agent-ergonomics records. |
| `brnrd ergonomics list [--issue ID] [--days N] [--limit N] [--json]` | List captured records. |
| `brnrd ergonomics clear [--before YYYY-MM-DD]` | Delete captured records. |

## Runners, bench, and completions

| Command | Purpose |
|---|---|
| `brnrd runners list [--json] [--all]` | List configured profiles and bundled Cores. |
| `brnrd bench scenarios` | List scripted seam probes. |
| `brnrd bench run [--scenario NAME] [--shell SHELL]` | Run a probe in a sandbox; it spends real Runner quota. |
| `brnrd completions bash` | Print Bash completions. |
| `brnrd completions zsh` | Print Zsh completions. |
| `brnrd completions fish` | Print Fish completions. |

The bench runner also accepts `--root`, `--timeout`, and repeatable
`--config KEY=VALUE` options.

## Common project settings

`.brr/config` uses `key=value` lines:

```ini
environment=worktree
shell=codex
core=default
runner_policy=fixed
```

See [Runs & environments](../../concepts/environments/) and
[Models & quota](../../guides/models/) before changing these settings.
