# Adoption process

`brr init` converts an existing codebase into a brr‑managed project.  It
preserves your workflow while adding a structured, reproducible layer
understood by AI coding tools.

## Goals

* Respect the repository’s current practices and instructions.
* Extract the essential guidance and rewrite it into a clean structure.
* Avoid silently overwriting files or discarding user intent.
* Produce files that AI tools can read directly without further
  mediation.

## Steps

### 1. Inspect

The initializer scans the repository for signals.  These include:

- Existing instruction files such as `AGENTS.md`, `CLAUDE.md`,
  `GEMINI.md`, and `.cursor/rules`.  brr does not assume any one format
  ahead of time.
- Build/test scripts (`justfile`, `Makefile`, `Taskfile.yml`), commit
  messages, CI configuration, and package metadata.
- TODO files (`TODO.md`, `tasks/`), issue descriptions, or other
  documents that reflect the current workflow.
- Untracked files in Git and ignored files that might conflict with brr’s
  generated files.

If the repository is not a Git repository, brr offers to initialise it.  If
a target file is ignored or untracked, brr refuses to manage it
automatically.

### 2. Extract

brr uses your configured executor (Codex by default) to summarise the
repository’s purpose, important commands, constraints and operating
model.  This extraction prompt lives in `prompts/init_adopt.md` and is
meant to be high level; it does not ask the executor to rewrite code or
perform tasks, only to analyse and summarise.

The result is a structured JSON‑like description of:

- the project’s one‑line purpose and current focus
- key commands (build, test, start, verify)
- important constraints or policies
- preferred workflow habits (e.g. commit messages, branch hygiene)
- existing instruction files and whether they should be preserved
  unchanged, augmented or rewritten
- recommended new structure for `AGENTS.md` and related files

### 3. Propose

brr converts the structured description into a concrete file proposal.  It
generates a new `AGENTS.md` with a machine‑readable header and a clean
instruction body.  It generates or updates `agent_state.md` and any
tool‑specific shims (`CLAUDE.md`, `GEMINI.md`) only if they are needed.
The proposal respects user‑owned sections in existing files by marking
managed regions explicitly.

### 4. Present

Before writing anything to disk, brr shows you the proposed diff.  You can
accept the changes, reject them or edit them manually.  This ensures
that there are no surprises.  If an untracked or ignored file would
conflict, brr aborts unless you move or commit that file first.

### 5. Write

After acceptance, brr writes the new files to disk and, if requested,
commits them with a conventional commit message.  From this point on,
executors read `AGENTS.md` and friends directly.  You no longer need brr
to invoke the agent, but `brr run` and `brr report` remain convenient
wrappers.

## Regeneration

`brr regenerate` repeats the extraction and proposal steps on an already
brr‑managed repository.  It uses a different prompt (`prompts/regenerate.md`)
that includes current `AGENTS.md` and `agent_state.md` content.  It
conservatively updates managed sections and preserves user edits.