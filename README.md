# brr

Structured guardrails for AI agent work, managed from a chat.

brr produces `AGENTS.md` — a single instruction file that encodes
your project's build commands, code conventions, workflow rules, and
safety guardrails.  Any AI tool that reads it (Claude Code, Cursor,
Codex, Gemini) gets the same conventions.  brr itself is just the
remote control: it delegates tasks to whichever executor you use and
reports back via Telegram.

**The guardrails live in AGENTS.md, not in brr.** brr creates the
file, enriches it from the repo, and provides the remote execution
layer.  But the conventions work even without brr — they're just
Markdown that any agentic tool can read.

Zero runtime dependencies.  No database, no cloud service, no lock-in.

## Quick start

```bash
pip install brr

brr init                         # create AGENTS.md + detect executor
brr init https://github.com/u/r  # or clone first

brr auth telegram                # set up the chat connector
brr connect telegram             # bind repo to a Telegram topic
brr up                           # start the daemon
```

From chat:

```
> fix the failing tests in auth/
> status
> /cancel
```

Or locally:

```bash
brr run "fix failing tests in the auth module"
```

## What brr produces

`brr init` creates `AGENTS.md` with:

- **YAML frontmatter** — executor config, commands, state file path
  (only used by brr itself)
- **Project instructions** — what it is, how to build/test/run
- **Workflow conventions** — branching, commit policy, state management
- **Guardrails** — dead-end detection, scope drift, proportionality
- **Constraints** — things the agent must not do without asking

If an executor is on PATH, init runs it to fill in the sections from
the repo.  If the repo already has a `CLAUDE.md` or `GEMINI.md`, its
content is incorporated as the body.

Working memory lives in `.brr.local/state.md` (gitignored).

## Architecture

```
AGENTS.md        ← universal: works with any AI tool
  │
  ├── Claude Code reads it
  ├── Cursor reads it
  ├── Codex reads it
  │
  └── brr reads it + adds remote execution:
        │
     Telegram ←→ daemon ←→ executor (subprocess)
        │                      │
     /cancel              TaskRunner
     /status           (one task at a time)
```

brr is a thin layer.  The intelligence is in the executor.
The conventions are in AGENTS.md.  brr connects the two to a chat.

## AGENTS.md format

```yaml
---
brr:
  version: 1
  mode: live
  default_executor: claude
  auto_approve: true
  commands:
    build: "npm run build"
    test: "npm test"
    verify: "npm run lint && npm test"
  state_file: .brr.local/state.md
  commit_policy: commit-at-end-if-material
---
```

| Field              | Values                                               |
|--------------------|------------------------------------------------------|
| `default_executor` | `auto`, `claude`, `codex`, `gemini`, or any on PATH  |
| `mode`             | `paused`, `incubating`, `live`                       |
| `executor_cmd`     | command template: `["tool", "-p", "{prompt}"]`       |
| `state_file`       | path to state file (default: `.brr.local/state.md`)  |

## CLI

| Command                   | What it does                         |
|---------------------------|--------------------------------------|
| `brr init [url]`          | Create AGENTS.md, enrich from repo   |
| `brr run "<task>"`        | Run a task through the executor      |
| `brr status`              | Show project state                   |
| `brr auth telegram`       | Set bot token                        |
| `brr connect telegram`    | Bind repo to a chat topic            |
| `brr up`                  | Start the daemon                     |

Chat: any message is a task.  `/status`, `/cancel` are built-in.

## Extending

**Connectors** are single-file Python modules.  `telegram.py` is the
reference.  The `TaskRunner` class in `executor.py` handles threading,
cancellation, and serial execution — any connector can use it.

**Executors** are CLI commands on PATH.  Built-in profiles exist for
`claude`, `codex`, and `gemini`.  Set `default_executor` to any
executable, or use `executor_cmd` for full command-template control.

## Development

```bash
git clone https://github.com/…/brr.git
cd brr
pip install -e ".[dev]"
pytest
```

## License

MIT
