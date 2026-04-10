---
claude:
  cmd: claude --print
  approve: --dangerously-skip-permissions
codex:
  cmd: codex exec --full-auto
  approve: ""
gemini:
  cmd: gemini
  approve: ""
---
Runner profiles for brr.

Each key is a CLI name looked up on PATH during detection.
- `cmd` — base command (the prompt is appended as the last arg).
- `approve` — flags appended when `auto_approve=true` in `.brr/config`.
