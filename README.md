# brr

A daemon that runs AI coding agents on your machine and lets you
manage them from your phone via Telegram.

You describe tasks in plain language. brr delegates to whichever AI
tool you use (Claude Code, Codex, Gemini CLI, or a custom script)
and reports back. All state lives in your Git repo as plain Markdown.
No database, no cloud service, no lock-in.

## Quick start

```bash
pip install brr

cd your-project
brr init               # scan the repo, generate AGENTS.md
brr auth telegram      # set up your Telegram bot
brr connect telegram   # bind this repo to a chat topic
brr up                 # start the daemon
```

From Telegram:

```
> fix the failing tests in auth/
> what's the status?
> run the migration for the new user fields
```

Or locally:

```bash
brr run "fix failing tests in the auth module"
brr status
brr report
```

## How it works

brr adds two files to your repo:

- **`AGENTS.md`** — machine-readable YAML header + instructions that
  tell any AI tool how to build, test and operate the project.
- **`agent_state.md`** — working memory: current focus, decisions,
  recent conversation topics, next steps. Rewritten each run,
  versioned by Git.

Executors read these files directly. brr manages the lifecycle.

```
You (Telegram / CLI)
        |
     brr daemon
        |
   +-------+-------+
   |       |       |
 repo A  repo B  repo C
   |       |       |
claude   codex   shell
```

## Commands

| Command                | What it does                            |
|------------------------|-----------------------------------------|
| `brr init`             | Scan repo, generate instruction files   |
| `brr run "<task>"`     | Run a task through the executor         |
| `brr status`           | Project state at a glance               |
| `brr report`           | Narrative progress report               |
| `brr auth telegram`    | Set up Telegram bot credentials         |
| `brr connect telegram` | Bind repo to a Telegram chat topic      |
| `brr up`               | Start the daemon                        |

## Executors

brr delegates to an executor configured per-repo in `AGENTS.md`:

- **Claude Code** — `claude` CLI
- **Codex** — OpenAI Codex CLI
- **Gemini** — Google Gemini CLI
- **Shell** — any command that accepts a prompt on stdin

When set to `auto` (the default), brr detects what's installed.

## License

MIT
