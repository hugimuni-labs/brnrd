# Adoption

`brr init` prepares a Git repository for managed AI operation.

## What it does

1. **Clone** (optional) — if given a URL, clones the repo first.
2. **Detect executor** — finds an available AI CLI (`claude`, `codex`,
   `gemini`) or uses the one configured in `AGENTS.md`.
3. **Analyse** — runs the adoption prompt (`prompts/init_adopt.md`) via
   the executor to extract project purpose, commands, and policies.
4. **Write files** — creates `AGENTS.md` and `agent_state.md` with the
   extracted information. Skips files that already exist.

If no executor is available, brr writes template files for you to fill
in manually.

## State location

By default, `agent_state.md` lives in the repo root and is committed
alongside code. For projects that don't want agent state tracked, set
`state_file` in the `AGENTS.md` header:

```yaml
brr:
  state_file: .brr.local/state.md
```

Everything under `.brr.local/` is gitignored.
