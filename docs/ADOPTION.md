# Adoption

`brr init` prepares a Git repository for managed AI operation.

## What it does

1. **Clone** (optional) — if given a URL, clones the repo first.
2. **Detect executor** — finds an available AI CLI on PATH (`claude`,
   `codex`, `gemini`) and writes it into the config.
3. **Create AGENTS.md** — writes the YAML frontmatter. If a
   `CLAUDE.md`, `GEMINI.md`, or `CODEX.md` exists, its content
   becomes the body. Otherwise a skeleton is used.
4. **Enrich** (if executor available) — runs the executor to read the
   repo and fill in the AGENTS.md body with actual project details:
   build commands, code conventions, constraints.

If no executor is on PATH, step 4 is skipped and you get the template
to fill in manually (or install an executor and re-run).

## State location

By default, working state lives in `.brr.local/state.md` — local and
gitignored.  For projects that want state committed, override
`state_file` in the `AGENTS.md` header:

```yaml
brr:
  state_file: agent_state.md
```
