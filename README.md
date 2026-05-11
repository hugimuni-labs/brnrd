# brr

Structured AI agent playbook with persistent knowledge base and remote execution.

brr produces `AGENTS.md` — a playbook that encodes your project's conventions,
workflow, and guardrails.  Any AI tool that reads it (Claude Code, Cursor, Codex,
Gemini) gets the same behavior.  brr adds a remote execution layer: a daemon that
accepts tasks from Telegram, Slack, Git, or anything that writes a file.

**Two layers of value:**

1. **Playbook only** — `AGENTS.md` + `kb/` work with any AI tool, no brr needed.
   Copy the conventions, use them everywhere.
2. **Full tool** — brr daemon handles remote execution, gate I/O, knowledge
   persistence, and git push.

Zero runtime dependencies.  Stdlib Python only.  No database, no cloud, no lock-in.

## Install

```bash
pip install brr
```

Or run from a local checkout while developing or customizing brr itself:

```bash
git clone https://github.com/user/brr
/path/to/brr/brr init
```

For an editable install:

```bash
pip install -e /path/to/brr
```

Forks work with normal Python packaging too:

```bash
pip install git+https://github.com/you/brr.git
```

## Quick start

```bash
brr init                          # detect runner, create AGENTS.md + kb/
brr run "fix the failing tests"   # run a task through the configured environment

brr setup telegram                # configure a remote input
brr up                            # start the daemon
```

From Telegram (or Slack, or a task file):

```
> fix the failing tests in auth/
> research caching strategies for the API layer
> review the latest PR for security issues
```

## What brr creates

`brr init` sets up:

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
  └── brr adds remote execution:

  ┌─────────┐    .brr/inbox/    ┌────────┐    runner     ┌──────────┐
  │  Gates   │───────────────────│ Daemon │──────────────│  Runner   │
  │ tg/slack │    .brr/responses │        │  subprocess  │ (AI CLI)  │
  │ git/any  │◄──────────────────│        │◄─────────────│           │
  └─────────┘                    └────────┘   git push   └──────────┘
```

Gates are transport adapters — they create event files and deliver responses.
The daemon scans the inbox and runs workers.  The runner is whatever AI CLI
you have installed.

Telegram works with just a bot token.  Once the daemon is running, send the
bot a message; brr records the chat ID from each message and replies there.

## CLI

| Command                | What it does                          |
|------------------------|---------------------------------------|
| `brr init [url]`       | Create AGENTS.md + kb/, detect runner |
| `brr run "<task>"`     | Run a task via the configured runner  |
| `brr setup <gate>`     | Configure a gate in one step          |
| `brr auth <gate>`      | Set gate credentials                  |
| `brr bind <gate>`      | Bind a gate channel or watch          |
| `brr up`               | Start the daemon (foreground)         |
| `brr down`             | Stop the daemon                       |

Gates: `telegram`, `slack`, `git`.

## Extending

**Gates** follow a file protocol: write to `.brr/inbox/`, read from
`.brr/responses/`.  Any language works.  See `src/brr/gates/README.md`
for the spec and a bash example.

**Runners** are CLI commands on PATH.  Built-in profiles: `claude`,
`codex`, `gemini`.  Set `runner=<name>` in `.brr/config` or use any
executable.

**Environments** are daemon backends.  Configure the user-facing policy
with `environment=<auto|host|worktree|docker>` in `.brr/config`.
`environment=auto` prefers configured Docker isolation, then falls back
to worktree behavior.  The concrete built-ins today are `host`,
`worktree`, and `docker`; future backends such as `devcontainer`, `ssh`,
or service-specific plugins fit behind the same internal protocol.

Daemon git operations are branch-plan driven. With no structured branch
target, `branch.fallback=preserve` is the default: work starts on
`brr/<task-id>` from the repo default branch, and committed task branches
are preserved for human routing and pushed when a remote is configured.
Other fallback modes are `inbox`, `default`, and `current`.

Docker mode wires credentials automatically: brr forwards
`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` /
`GOOGLE_API_KEY` from the daemon's environment, and bind-mounts your
host's `~/.claude/`, `~/.codex/`, `~/.gemini/` (when present) into the
container so subscription auth works without extra config. See
`src/brr/docs/envs.md` for the full breakdown — image expectations, the
bundled runner image, and the durability contract.

Branching is mostly task-internal.  brr uses branches/worktrees to stage
reviewable code changes or continue an explicitly named branch, but users
usually only choose the environment policy.

**Deep customization** should use a local checkout, editable install, or
fork.  `.brr/config` is for lightweight runtime choices like runner and
environment policy.

## Development

```bash
git clone https://github.com/user/brr
cd brr
pip install -e ".[dev]"
pytest
```

For remote-assisted brr development, run the daemon from the editable
install with developer reload enabled:

```bash
brr up --dev-reload
```

The daemon re-execs itself between tasks when brr package files change.

## License

MIT
