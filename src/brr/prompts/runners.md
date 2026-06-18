---
claude:
  cmd: 'claude --print --dangerously-skip-permissions --safe-mode --system-prompt "You are a brr runner. Follow the supplied prompt and operate on the files available in the working directory."'
claude-bare-api-only:
  binary: claude
  cmd: 'claude --print --dangerously-skip-permissions --bare --system-prompt "You are a brr runner. Follow the supplied prompt and operate on the files available in the working directory."'
claude-bare-api-only-sonnet:
  binary: claude
  cmd: 'claude --model "claude-sonnet-4-6" --print --dangerously-skip-permissions --bare --system-prompt "You are a brr runner. Follow the supplied prompt and operate on the files available in the working directory."'
claude-bare-api-only-opus:
  binary: claude
  cmd: 'claude --model "claude-opus-4-8" --print --dangerously-skip-permissions --bare --system-prompt "You are a brr runner. Follow the supplied prompt and operate on the files available in the working directory."'
claude-bare-api-only-fable:
  binary: claude
  cmd: 'claude --model "claude-fable-5" --print --dangerously-skip-permissions --bare --system-prompt "You are a brr runner. Follow the supplied prompt and operate on the files available in the working directory."'
codex:
  cmd: 'codex exec --dangerously-bypass-approvals-and-sandbox -c base_instructions="You are a brr runner. Follow the supplied prompt and operate on the files available in the working directory." -c include_permissions_instructions=false -c include_apps_instructions=false -c include_collaboration_mode_instructions=false -c include_skill_instructions=false'
gemini:
  cmd: gemini -p --yolo
---
Bundled runner profiles for brr.

The runner contract is deliberately abstract: a runner is a process that
can intelligently operate files in its working directory. brr passes the
assembled prompt as the final command argument, captures stdout as the
final reply, treats stderr as progress/debug output, and interprets the
exit status as the process result. The runner does not need to know the
response-file path for the common case.

These bundled profiles are defaults, not the user's source of truth. To
manage runner profiles for a project, create `.brr/runners.md` with the
same frontmatter shape; brr reads that before the bundled defaults. The
legacy `.brr/prompts/runners.md` override is still accepted, but new
configuration should use `.brr/runners.md` because runner profiles are
execution-medium data, not prompt templates. For a one-off command,
`runner_cmd` in `.brr/config` remains the smallest override.

Each frontmatter key is a runner name. During detection brr checks
whether the profile's CLI is on PATH — either the key itself (`claude`,
`codex`, `gemini`) or an explicit `binary` field for alias profiles such
as `claude-bare-api-only`.

The profile captures the headless invocation: non-interactive mode plus
tool/approval bypass, since the daemon needs the runner to act without
prompts. Repository orientation, AGENTS.md, dominion context, and the Run
Context Bundle belong in the assembled prompt, not in these command
strings.

- `cmd` — base command. brr appends the prompt as the final argument.
- `binary` — optional PATH binary for alias profiles. When set, the
  profile is opt-in via `runner=` in `.brr/config` (not auto-detected).

Alias profiles with `binary` are for variants of the same CLI, for example
`claude-bare-api-only` uses `--bare` and requires `ANTHROPIC_API_KEY`
(OAuth / `~/.claude` subscription auth is not used).

The runner's final reply is read from stdout. brr captures stdout and
writes it to the event's response file automatically; runners do not
need a per-CLI flag for that. Progress, traces, and tool output should
go to stderr (which is the convention for all three runners above).

Users can override `cmd` per-repo by setting `runner_cmd` in
`.brr/config`. The same stdout capture rules apply, and `{prompt}` is
substituted before exec.

Quota and price signals are metadata about a runner medium, not part of
the command string. Today brr reads them from `runner.quota.*`,
`BRR_RUNNER_QUOTA_*`, or `.brr/runner-quota.json`; a fuller runner-medium
registry can grow from this contract without making built-in commands
pretend to know provider billing.
