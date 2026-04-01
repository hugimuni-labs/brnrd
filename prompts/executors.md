---
claude:
  cmd: "claude -p"
  approve: "--dangerously-skip-permissions"
codex:
  cmd: "codex exec"
  approve: "--dangerously-bypass-approvals-and-sandbox"
gemini:
  cmd: "gemini"
  approve: ""
---

Executor profiles used by `brr` for auto-detection and command building.

Each profile has:
- **cmd** — base command and default arguments (space-separated).
- **approve** — flag appended when `auto_approve: true` in AGENTS.md.

Detection order matches the YAML key order above: the first profile
whose command is found on PATH wins.

To use a custom executor, either set `default_executor` in AGENTS.md
to any command on PATH, or use the `executor_cmd` config key for full
control over the command template.
