# brr – Git-centric AI Repo Operating Layer

**brr** is a minimal control plane for AI‑assisted development.  It makes any Git
repository remotely operable through AI coding tools like Codex, Claude Code,
Gemini or custom shells, without forcing a particular agent implementation or
workflow.  After you run `brr init`, the repository is prepared with
structured, human‑readable instructions and a compact persistent memory.  AI
tools can read these files directly, so the repository continues to behave
consistently even if `brr` itself is not running.

## Why brr?

Traditional coding assistants focus on writing code.  brr focuses on
operability: how to register tasks, maintain context across one‑shot runs and
remotely execute tasks from chat.  brr is Git‑centric and local‑first.  It
preserves your existing workflow and guidance where possible and wraps it with
just enough structure for reliable automation.

### Core values

- **Extreme simplicity.** brr introduces as few files and commands as possible.
- **Executor‑owned execution.** brr orchestrates, but Codex/Claude/Gemini or
  your own runners do the actual work.
- **Repo‑native state.** Durable project knowledge lives in tracked files.
- **Minimal runtime code.** Only irreducible parts (CLI, git safety, chat IO)
  require code; most behavior is in prompts.
- **Adopt, then normalize.** brr reads your existing guidance and rewrites it
  into a known, polished structure.
- **Git‑centric, GitHub‑friendly.** Works on any Git repo, integrates well
  with GitHub without hard dependencies.

See `docs/PRINCIPLES.md` for more detail.

## Quick start

1. Install brr (project packaging is work in progress).
2. Run `brr init` inside a Git repository to prepare it for AI operation.
3. Use `brr auth telegram` to configure your Telegram bot and
   `brr connect telegram` to bind the repo to a chat.
4. Run tasks via `brr run "<instruction>"` locally or from chat.
5. Check the current state with `brr status` or get a narrative update via
   `brr report`.

This repository is under active development.  Contributions are welcome—see
`.github/ISSUE_TEMPLATE` and `.github/PULL_REQUEST_TEMPLATE.md` for guidance.