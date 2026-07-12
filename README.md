# brnrd

![brnrd](https://github.com/Gurio/brr/raw/main/media/brr-logo.gif)

Structured AI agent playbook with persistent knowledge base and remote execution.

brnrd produces `AGENTS.md` — a playbook that encodes your project's conventions,
workflow, and guardrails.  Any AI tool that reads it (Claude Code, Cursor, Codex,
Gemini) gets the same behavior.  brnrd adds a remote execution layer: a daemon that
accepts tasks from Telegram, Slack, GitHub (issue labels and PR / issue
mentions), or anything that writes a file.

**Two layers of value:**

1. **Playbook only** — `AGENTS.md` + `kb/` work with any AI tool, no brnrd needed.
   Copy the conventions, use them everywhere.
2. **Full tool** — the brnrd daemon handles remote execution, gate I/O, knowledge
   persistence, and git push.

Execution stays local. The optional managed service relays remote events and
hosts the dashboard without moving agent work off your machine.

## Install

```bash
pip install brnrd
```

Coming from the AI-coding-tool world, where everything ships through npm:

```bash
npx brnrd init
```

`npx brnrd` is a bootstrapping installer, not a port — first run creates a
durable virtualenv, installs brnrd from PyPI into it, and hands over. If Python
is absent, it downloads a checksum-verified uv binary and lets uv provision a
managed CPython. Everything it installs stays under `~/.local/share/brnrd` (or
`$BRNRD_HOME`); it does not modify your system Python or PATH.

Or, with `uv` already installed:

```bash
uvx brnrd            # zero-install run, straight from PyPI
```

`uvx` uses a throwaway environment — good for a first look, wrong for
`brnrd daemon install`, which needs a real install to point a long-lived
service at. `npx brnrd` and `pip install` both give you one.

Or run from a local checkout while developing or customizing brnrd itself:

```bash
git clone https://github.com/Gurio/brr
/path/to/brr/brnrd init
```

For an editable install:

```bash
pip install -e /path/to/brr
```

Forks work with normal Python packaging too:

```bash
pip install git+https://github.com/Gurio/brr.git
```

## Quick start

```bash
brnrd init                          # detect runner, create AGENTS.md + kb/
brnrd run "fix the failing tests"   # run a task through the configured environment

brnrd bind . telegram               # configure a repo-local remote input
brnrd up                            # start the daemon in the foreground
brnrd daemon install                # install the native user service
```

From Telegram (or Slack, or a task file):

```
> fix the failing tests in auth/
> research caching strategies for the API layer
> review the latest PR for security issues
```

## What brnrd creates

`brnrd init` sets up:

- **`AGENTS.md`** — playbook with workflow, kb conventions, commit protocol,
  artifact rules, guardrails, self-review instructions.
- **`kb/`** — persistent knowledge base committed to the repo.  Compounds
  across sessions.
- **`.brr/`** — runtime directory (gitignored): inbox, responses, config,
  gate state.

## Architecture

```
AGENTS.md + kb/         universal: works with any AI tool
  │
  ├── Claude Code reads it
  ├── Cursor reads it
  ├── Codex reads it
  │
  └── brnrd adds remote execution:

  ┌─────────┐    .brr/inbox/    ┌────────┐    runner    ┌──────────┐
  │  Gates  │───────────────────│ Daemon │──────────────│  Runner  │
  │ tg/slack│    .brr/responses │        │  subprocess  │ (AI CLI) │
  │ gh/any  │◄──────────────────│        │◄─────────────│          │
  └─────────┘                   └────────┘   git push   └──────────┘
```

Gates are transport adapters — they create event files and deliver responses.
The daemon scans the inbox and runs workers.  The runner is whatever AI CLI
you have installed.

Telegram works with just a bot token.  Once the daemon is running, send the
bot a message; brnrd records the chat ID from each message and replies there.

## CLI

| Command                | What it does                          |
|------------------------|---------------------------------------|
| `brnrd init [url]`       | Create AGENTS.md + kb/, detect runner |
| `brnrd run "<task>"`     | Run a task via the configured runner  |
| `brnrd bind <repo> <gate>` | Bind a repo-local gate               |
| `brnrd connect [url]`    | Connect this daemon to brnrd service  |
| `brnrd add <repo>`       | Add a repo to the connected account home |
| `brnrd kb "<query>"`     | Search home/repo knowledge            |
| `brnrd up`               | Start the daemon (foreground)         |
| `brnrd down`             | Stop the foreground daemon            |
| `brnrd daemon up`        | Start the installed daemon service, or foreground daemon if no service is installed |
| `brnrd daemon down`      | Stop the installed daemon service, or foreground daemon if no service is installed |
| `brnrd daemon status`    | Show service and foreground daemon status |
| `brnrd daemon install`   | Install the native user service (systemd or LaunchAgent) |
| `brnrd daemon uninstall` | Remove the native user service |
| `brnrd daemon logs`      | Tail native service logs |

Gates: `telegram`, `slack`, `github`.

On macOS, the first daemon run that opens network sockets can trigger
the system "accept incoming network connections" prompt. Allow it if
you want gates and managed brnrd traffic to reach the local daemon.

## Extending

**Gates** follow a file protocol: write to `.brr/inbox/`, read from
`.brr/responses/`.  Any language works.  See `src/brr/gates/README.md`
for the spec and a bash example.

**Runners** are CLI commands on PATH: any process that can operate files
from a prompt, print the final reply to stdout, and stream progress to
stderr. Built-in profiles cover `claude`, `codex`, and `gemini`; manage
project-specific profiles in `.brr/runners.md`, set `runner=<name>` in
`.brr/config`, or use `runner_cmd` for one custom command.

**Environments** are daemon backends.  Configure the user-facing policy
with `environment=<auto|host|worktree|docker>` in `.brr/config`.
`environment=auto` prefers configured Docker isolation, then falls back
to worktree behavior.  The concrete built-ins today are `host`,
`worktree`, and `docker`; future backends such as `devcontainer`, `ssh`,
or service-specific plugins fit behind the same internal protocol.

Daemon git operations are publish-plan driven. Each task starts on a
fresh `brr/<task-id>` branch from a resolved seed ref. When the event
names a target branch (`branch_target`, `target_branch`, `base_branch`,
or legacy `branch`), brnrd seeds from `<remote>/<target>` and publishes
under that name after the run. Without a structured target the task
branch is preserved as-is and published for human routing when a remote
is configured.

Docker mode wires credentials automatically: brnrd forwards
`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` /
`GOOGLE_API_KEY` from the daemon's environment, and bind-mounts your
host's `~/.claude/`, `~/.codex/`, `~/.gemini/` (when present) into the
container so subscription auth works without extra config. See
`src/brr/docs/envs.md` for the full breakdown — image expectations, the
bundled runner image, and the durability contract.

Branching is mostly task-internal.  brnrd uses branches/worktrees to stage
reviewable code changes or continue an explicitly named branch, but users
usually only choose the environment policy.

**Deep customization** should use a local checkout, editable install, or
fork.  `.brr/config` is for lightweight runtime choices like runner and
environment policy.

## Development

```bash
git clone https://github.com/Gurio/brr
cd brr
pip install -e ".[dev]"
pytest
```

For remote-assisted brnrd development, run the daemon from the editable
install with developer reload enabled:

```bash
brnrd up --dev-reload
```

The daemon re-execs itself between tasks when brnrd package files change.

## License

The local daemon core in `src/brr/` is MIT licensed. The managed backend and
dashboard in `src/brnrd/` and `src/brnrd_web/` are AGPLv3 licensed. See
[`LICENSE-OVERVIEW.md`](https://github.com/Gurio/brr/blob/main/LICENSE-OVERVIEW.md) for the package boundary and
install details.
