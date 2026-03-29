# Managed files

This document explains the purpose of each file created by `brr init` and
maintained by brr thereafter.  Keeping the number of files small and
their roles clear is a core design goal.

## `AGENTS.md`

This is the canonical instruction file for your repository.  It is read
by AI tools (Claude Code, Codex, Gemini, etc.) as part of their context.
The file has two parts:

1. A YAML frontmatter block under the `brr` key.  It defines the
   repository mode (paused, incubating or live), the default and named
   executors, the canonical commands (build, verify, status, etc.),
   task sources, the location of the persistent state file and the
   commit policy.  Tools and humans can parse this block easily.
2. A freeform Markdown body with high‑level instructions.  This section
   tells an AI agent how to build, run and test the project, what good
   code and commit messages look like, how to scope work, and any other
   rules or guidelines.  It can link to deeper documentation in the
   repo.

`AGENTS.md` may also contain explicitly marked regions that brr manages.
These regions are enclosed between:

```markdown
<!-- brr:managed:start -->
... generated content ...
<!-- brr:managed:end -->
```

brr will rewrite only the content within those markers during regeneration.

## `agent_state.md`

This file stores the persistent working memory for the current project.
It is rewritten on every substantive run.  The structure includes:

- **Current focus** – a plain language description of what the agent is
  working on.
- **Conversation topics** – a compacted list of recent threads with the
  user, oldest first.  This gives continuity across one-shot runs so the
  agent remembers what was discussed and can follow up.
- **Decisions** – key architecture or design choices made so far.
- **Discoveries** – important findings, performance notes and gotchas.
- **Next steps** – an ordered list of concrete actions.
- **Open questions** – things the user or agent needs to clarify.

This file is intentionally short.  Git history captures the evolution of
state.  Rewrite it with fresh information each run rather than appending.

## `CLAUDE.md` and `GEMINI.md`

These are compatibility shims for Claude Code and Gemini CLI.  They are
generated only if you opt into those executors.  They point the tool to
`AGENTS.md` and describe how to use `agent_state.md` as persistent
memory.  They should stay concise to keep context sizes small.

## `.brr.local/`

This directory stores machine‑specific and transient state that should
never be committed.  It may contain:

- `telegram.json` – bot token and chat configuration for the Telegram
  connector.  Other connectors follow the same pattern
  (`<connector>.json`).
- `runtime.json` – current run identifiers, offsets and PIDs for the
  daemon and workers.
- `locks/` – files used to coordinate access when concurrent tools
  operate on the same repo.

Everything under `.brr.local/` is ignored by Git.  Do not store
long‑lived project knowledge here; it will not be replicated to other
clones.