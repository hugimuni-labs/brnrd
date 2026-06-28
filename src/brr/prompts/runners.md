---
claude:
  cmd: 'claude --print --output-format json --dangerously-skip-permissions --setting-sources local --system-prompt "You are a brr runner. Follow the supplied prompt and operate on the files available in the working directory."'
  hooks: claude
claude-bare-api-only:
  binary: claude
  cmd: 'claude --print --output-format json --dangerously-skip-permissions --bare --system-prompt "You are a brr runner. Follow the supplied prompt and operate on the files available in the working directory."'
claude-bare-api-only-sonnet:
  binary: claude
  cmd: 'claude --model "claude-sonnet-4-6" --print --output-format json --dangerously-skip-permissions --bare --system-prompt "You are a brr runner. Follow the supplied prompt and operate on the files available in the working directory."'
claude-bare-api-only-opus:
  binary: claude
  cmd: 'claude --model "claude-opus-4-8" --print --output-format json --dangerously-skip-permissions --bare --system-prompt "You are a brr runner. Follow the supplied prompt and operate on the files available in the working directory."'
claude-bare-api-only-fable:
  binary: claude
  cmd: 'claude --model "claude-fable-5" --print --output-format json --dangerously-skip-permissions --bare --system-prompt "You are a brr runner. Follow the supplied prompt and operate on the files available in the working directory."'
codex:
  cmd: 'codex exec --dangerously-bypass-approvals-and-sandbox --dangerously-bypass-hook-trust -c base_instructions="You are a brr runner. Follow the supplied prompt and operate on the files available in the working directory." -c include_permissions_instructions=false -c include_apps_instructions=false -c include_collaboration_mode_instructions=false -c include_skill_instructions=false'
  hooks: codex
gemini:
  cmd: gemini -p --yolo
  hooks: gemini
---
Bundled runner profiles for brr.

The runner contract is deliberately abstract: a runner is a process that
can intelligently operate files in its working directory. brr passes the
assembled prompt as the final command argument, captures stdout as the
plain current-thread output artifact, treats stderr as progress/debug
output, and interprets the exit status as the process result. The runner
does not need to know the response-file path for the common case.

## The minimal runner interface (tiers)

The contract stays lean by staying *tiered* — each tier is optional
enrichment of the one below, and a runner that satisfies only Tier 0 still
works. See `kb/design-runner-back-channel.md` for the full design.

- **Tier 0 (required).** A process that, given the assembled prompt as its
  final argument, operates files in its working directory and exits with a
  status code. The irreducible floor — all real work happens here.
- **Tier 1 (optional).** Prints a final reply on stdout (progress/debug on
  stderr). brr captures stdout as the plain current-thread reply. This is
  the `response_path` capture above.
- **Tier 2 (optional).** *Boundary injection*: at each tool/turn boundary the
  resident's outbound messages flush event-driven (not heartbeat-polled) **and**
  fresh portal state is woven back into its context — so responsiveness stops
  depending on the resident remembering to poll. Plus premature-stop control and
  the operational meta a holistically aware resident runs on. A Tier-0/1 runner
  degrades cleanly to the heartbeat-polled model (the daemon keeps draining the
  outbox and refreshing `portal-state.json` on its timer). Tier 2 is never
  load-bearing for *correctness*, but it is the substrate of a fuller resident.

Boundary injection rides each runner's **native lifecycle hooks**: the runner
invokes a brr callback (`brr hook <phase>`) at tool/turn boundaries and weaves
the JSON result back into its context. A profile opts in with a `hooks:
<flavour>` field. brr owns the abstract phases (`post-tool` / `stop` /
`session-start`) and renders one neutral result into each flavour's native
fields. The *config-install mechanism* is runner-specific:
  - **claude** — `hooks: claude`. brr writes a per-run
    `.claude/settings.local.json` registering `PostToolBatch` / `Stop` /
    `SessionStart` → `brr hook <phase>`. Injection lands via
    `hookSpecificOutput.additionalContext`; `Stop` `decision:block` continues
    the turn for premature-stop control. **Fire-verified** on Claude Code
    2.1.191. `PostToolBatch` (not `PostToolUse`) is the post-tool seam — once
    per tool batch, after every result, before the next model call.
  - **codex** — `hooks: codex`. Codex's project-`.codex/config.toml` install
    hangs under repo-trust, so brr injects the hook config as runner argv
    (`-c hooks.<Event>=[…]`) paired with `--dangerously-bypass-hook-trust` in
    the profile cmd. Codex exposes `PostToolUse` / `Stop` / `SessionStart` (no
    `PostToolBatch`) and accepts the same `hookSpecificOutput` injection
    envelope. **Fire-verified** `PostToolUse` + injection on codex-cli 0.141.0.
  - **gemini** — `hooks: gemini` as *intent*. brr can render native hook config
    once an emitter exists and a runtime precheck gates activation; firing is
    unverified until a live test (the precheck asserts prerequisites, not
    firing).

brr only installs hook config for a profile that explicitly declares `hooks:`.
It never infers hooks from the runner name; a profile with no `hooks:` field
(the `--bare` aliases, a `runner_cmd` override) uses the heartbeat-polled
fallback (outbox drain + `portal-state.json` refresh on the daemon timer),
which carries *outbound* mid-thought flush but not *inbound* injection.

Reliability rests on a clean child env. A parent agent session can leak
`CLAUDE_CODE_SAFE_MODE=1` into a spawned `claude`, which **silently disables
settings-file hooks** while logging a reassuring "managed settings-file hooks
still run" — the false negative that earlier made hooks look unfireable under
`--print` and drove a now-retired streaming workaround. brr strips that
contaminant (and parent session-identity vars) from every runner subprocess env
via `runner.clean_runner_environ()`, so hooks fire as they would for a normal
top-level run. `--setting-sources local` is kept for settings **isolation**: it
excludes the user's global and the project's committed settings without the
collateral damage of `--safe-mode`.

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
prompts. Claude profiles also request ``--output-format json``; brr unwraps
the JSON ``result`` back into the response file and uses the accounting fields
for terminal spend/context facets. Repository orientation, AGENTS.md, dominion
context, and the Run Context Bundle belong in the assembled prompt, not in
these command strings.

- `cmd` — base command. brr appends the prompt as the final argument.
- `binary` — optional PATH binary for alias profiles. When set, the
  profile is opt-in via `runner=` in `.brr/config` (not auto-detected).

Alias profiles with `binary` are for variants of the same CLI, for example
`claude-bare-api-only` uses `--bare` and requires `ANTHROPIC_API_KEY`
(OAuth / `~/.claude` subscription auth is not used).

When the resident chooses a plain current-thread stdout reply, brr reads it
from stdout and writes it to the event's response file automatically;
runners do not need a per-CLI flag for that. Other delivery shapes ride the
outbox / gate / commit / noop portals named in the run prompt. Progress,
traces, and tool output should go to stderr (which is the convention for all
three runners above).

Users can override `cmd` per-repo by setting `runner_cmd` in
`.brr/config`. The same stdout capture rules apply, and `{prompt}` is
substituted before exec.

Quota and price signals are metadata about a runner medium, not part of
the command string. Today brr reads them from `runner.quota.*`,
`BRR_RUNNER_QUOTA_*`, or `.brr/runner-quota.json`; a fuller runner-medium
registry can grow from this contract without making built-in commands
pretend to know provider billing.
