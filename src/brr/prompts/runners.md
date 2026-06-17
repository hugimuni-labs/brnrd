---
claude:
  cmd: 'claude --print --dangerously-skip-permissions --safe-mode --system-prompt "You are brr agent. Find your orientation in AGENTS.md"'
claude-bare-api-only:
  binary: claude
  cmd: 'claude --print --dangerously-skip-permissions --bare --system-prompt "You are brr agent. Find your orientation in AGENTS.md"'
claude-bare-api-only-sonnet:
  binary: claude
  cmd: 'claude --model "claude-sonnet-4-6" --print --dangerously-skip-permissions --bare --system-prompt "You are brr agent. Find your orientation in AGENTS.md"'
claude-bare-api-only-opus:
  binary: claude
  cmd: 'claude --model "claude-opus-4-8" --print --dangerously-skip-permissions --bare --system-prompt "You are brr agent. Find your orientation in AGENTS.md"'
claude-bare-api-only-fable:
  binary: claude
  cmd: 'claude --model "claude-fable-5" --print --dangerously-skip-permissions --bare --system-prompt "You are brr agent. Find your orientation in AGENTS.md"'
codex:
  cmd: 'codex exec --dangerously-bypass-approvals-and-sandbox -c base_instructions="You are brr agent. Find your orientation in AGENTS.md" -c include_permissions_instructions=false -c include_apps_instructions=false -c include_collaboration_mode_instructions=false -c include_skill_instructions=false'
gemini:
  cmd: gemini -p --yolo
---
Runner profiles for brr.

Each key is a runner name. During detection brr checks whether the
profile's CLI is on PATH — either the key itself (`claude`, `codex`,
`gemini`) or an explicit `binary` field for alias profiles such as
`claude-bare-api-only`.

The profile captures the headless invocation: non-interactive mode plus
tool/approval bypass, since the daemon needs the runner to act without
prompts.

- `cmd` — base command. brr appends the prompt as the final argument.
- `binary` — optional PATH binary for alias profiles. When set, the
  profile is opt-in via `runner=` in `.brr/config` (not auto-detected).

Alias profiles with `binary` are for variants of the same CLI — e.g.
`claude-bare-api-only` uses `--bare` and requires `ANTHROPIC_API_KEY`
(OAuth / `~/.claude` subscription auth is not used).

The runner's final reply is read from stdout. brr captures stdout and
writes it to the task's response file automatically; runners do not
need a per-CLI flag for that. Progress, traces, and tool output should
go to stderr (which is the convention for all three runners above).

Users can override `cmd` per-repo by setting `runner_cmd` in
`.brr/config`. The same stdout capture rules apply, and `{prompt}` is
substituted before exec.
