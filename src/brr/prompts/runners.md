---
claude:
  cmd: claude --print --dangerously-skip-permissions
codex:
  cmd: codex exec --dangerously-bypass-approvals-and-sandbox
gemini:
  cmd: gemini
---
Runner profiles for brr.

Each key is a CLI name looked up on PATH during detection.
- `cmd` — base command (the prompt is appended as the last arg).
