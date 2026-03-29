# Files

## `AGENTS.md`

Instruction file for the repository. Two parts:

1. YAML frontmatter under the `brr` key — mode, executor, commands,
   state file location, commit policy.
2. Markdown body — project-specific instructions for AI tools.

## `agent_state.md`

Working memory, rewritten each run:

- **Current focus** — what the agent is working on.
- **Conversation topics** — compacted recent threads with the user.
- **Decisions** — architecture/design choices.
- **Discoveries** — findings and gotchas.
- **Next steps** — ordered actions.
- **Open questions** — things to clarify.

Can be placed under `.brr.local/` via the `state_file` config key
for repos that don't want it committed.

## `.brr.local/`

Machine-local, gitignored. Contains:

- `telegram.json` — connector credentials and chat binding.
  Other connectors follow the same pattern (`<connector>.json`).
- `runtime.json` — daemon PID and state.
