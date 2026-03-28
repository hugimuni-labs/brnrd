# brr

Your repos keep working while you're away.

brr is a lightweight daemon that runs AI coding agents on your laptop and
lets you steer them from your phone via Telegram.  You describe tasks in
plain language, brr delegates to whichever AI tool you prefer (Claude Code,
Codex, Gemini CLI, or a custom script), and reports back with progress and
results.

All project knowledge lives in your Git repository as plain Markdown files.
There is no database, no cloud service, no vendor lock-in.

## Quick start

```bash
pip install brr

cd your-project
brr init                         # scan the repo, generate AGENTS.md
brr auth telegram                # set up your Telegram bot
brr connect telegram             # bind this repo to a chat topic
brr up                           # start the daemon
```

Now open Telegram on your phone and tell your repo what to do:

```
> fix the failing tests in auth/
> what's the current status?
> write a migration for the new user fields
```

brr routes your message to the right repo, runs the task through your
chosen AI executor, commits the result and reports back.

You can also use it locally without Telegram:

```bash
brr run "fix failing tests in the auth module"
brr status
brr report
```

## How it works

brr adds two files to your repo:

- **`AGENTS.md`** -- a machine-readable header (YAML) plus human-readable
  instructions that tell any AI tool how to build, test and operate your
  project.
- **`agent_state.md`** -- persistent working memory: current focus,
  decisions, discoveries, next steps.  Rewritten on every run, versioned
  by Git.

These files *are* the configuration.  AI executors read them directly.
brr just manages the lifecycle.

## Architecture

```
You (Telegram / CLI)
        |
     brr daemon
        |
   +---------+---------+
   |         |         |
 repo A    repo B    repo C
   |         |         |
executor  executor  executor
(Claude)  (Codex)   (shell)
```

The daemon manages multiple repos.  Each repo has its own executor
configuration, state file and chat topic.  Everything local stays in
`.brr.local/` (gitignored).

## Commands

| Command              | Purpose                                      |
|----------------------|----------------------------------------------|
| `brr init`           | Scan repo and generate instruction files      |
| `brr run "<task>"`   | Run a single task through the executor        |
| `brr status`         | Show project state at a glance                |
| `brr report`         | Generate a narrative progress report          |
| `brr auth telegram`  | Set up Telegram bot credentials               |
| `brr connect telegram` | Bind repo to a Telegram chat topic         |
| `brr up`             | Start the daemon for managed operation        |

## Executors

brr does not generate code itself.  It delegates to an executor:

- **Claude Code** -- `claude` CLI
- **Codex** -- OpenAI Codex CLI
- **Gemini** -- Google Gemini CLI
- **Shell** -- any command that accepts a prompt on stdin

The executor is configured per-repo in the `AGENTS.md` header.  You can
switch executors without changing anything else.

## Design principles

- **Minimal moving parts.** Small Python package, zero runtime dependencies,
  plain-text state.
- **Git is the source of truth.** Durable knowledge lives in the repo.
  Transient state lives in `.brr.local/`.
- **Executor-agnostic.** brr orchestrates; your AI tool of choice does the
  thinking.
- **Adopt, don't impose.** `brr init` reads your existing setup and
  normalizes it rather than starting from scratch.

See [docs/PRINCIPLES.md](docs/PRINCIPLES.md) for the full design philosophy.

## License

MIT
