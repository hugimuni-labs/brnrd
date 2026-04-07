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

Or clone into a project for full prompt customization:

```bash
git clone https://github.com/user/brr .brr-tool
.brr-tool/brr init
```

If you have [uv](https://github.com/astral-sh/uv):

```bash
uv run .brr-tool/brr init
```

## Quick start

```bash
brr init                          # detect runner, create AGENTS.md + kb/
brr run "fix the failing tests"   # run a task locally

brr auth telegram                 # set up a gate
brr connect telegram              # bind to a chat
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

## CLI

| Command                | What it does                          |
|------------------------|---------------------------------------|
| `brr init [url]`       | Create AGENTS.md + kb/, detect runner |
| `brr run "<task>"`     | Run a task locally via runner         |
| `brr status`           | Show project state + recent activity  |
| `brr auth <gate>`      | Set credentials for a gate            |
| `brr connect <gate>`   | Bind repo to a gate channel           |
| `brr up`               | Start the daemon (foreground)         |
| `brr down`             | Stop the daemon                       |
| `brr eject`            | Copy prompts to .brr/prompts/ to edit |

Gates: `telegram`, `slack`, `git`.

## Extending

**Gates** follow a file protocol: write to `.brr/inbox/`, read from
`.brr/responses/`.  Any language works.  See `src/brr/gates/README.md`
for the spec and a bash example.

**Runners** are CLI commands on PATH.  Built-in profiles: `claude`,
`codex`, `gemini`.  Set `runner=<name>` in `.brr/config` or use any
executable.

**Prompts** live in `src/brr/prompts/`.  Run `brr eject` to copy them
to `.brr/prompts/` for per-repo customization.

## Development

```bash
git clone https://github.com/user/brr
cd brr
pip install -e ".[dev]"
pytest
```

## License

MIT
