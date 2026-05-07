---
claude:
  cmd: claude --print --dangerously-skip-permissions
codex:
  cmd: codex exec --dangerously-bypass-approvals-and-sandbox
gemini:
  cmd: gemini -p --yolo
---
Runner profiles for brr.

Each key is a CLI name looked up on PATH during detection. The profile
captures the headless invocation: non-interactive mode plus tool/approval
bypass, since the daemon needs the runner to act without prompts.

- `cmd` — base command. brr appends the prompt as the final argument.

The runner's final reply is read from stdout. brr captures stdout and
writes it to the task's response file automatically; runners do not
need a per-CLI flag for that. Progress, traces, and tool output should
go to stderr (which is the convention for all three runners above).

Users can override `cmd` per-repo by setting `runner_cmd` in
`.brr/config`. The same stdout capture rules apply, and `{prompt}` is
substituted before exec.
