---
claude:
  cmd: 'claude --print --dangerously-skip-permissions --setting-sources local --system-prompt "You are a brr runner. Follow the supplied prompt and operate on the files available in the working directory."'
  stream: claude
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
  stream: codex
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

The *mechanism* for boundary injection is **runner-specific** — don't confuse it
with the concept:
  - **claude** — brr **drives the stream** (`--input-format stream-json
    --output-format stream-json`) and injects the delta as a message itself. No
    `hooks:` field; opts in with `stream: claude`. (Built and default-on —
    `src/brr/runner_stream.py`, `kb/plan-streaming-runner-injection.md`.)
  - **codex** — brr reads `codex exec --json` JSONL. The CLI is single-turn,
    so command-completion boundaries flush outbound work and a terminal pending
    user follow-up resumes the recorded `thread_id` once with the folded-in
    body. No `hooks:` field; opts in with `stream: codex`.
  - **gemini** — native lifecycle hooks: the runner invokes a brr callback
    (`brr hook <phase>`) consuming a JSON result. A profile opts in with a
    `hooks: <flavour>` field; brr renders the native config and a runtime
    precheck gates activation. The field is *intent*; firing is unverified until
    a live test (the precheck asserts prerequisites, not firing).

brr only generates native hook config for a profile that explicitly declares
`hooks:`. It does not infer hooks from the runner name; a `stream:` runner gets
its back channel from the streaming driver, and a profile with neither field
uses the heartbeat-polled fallback.

The `claude` profile declares **no** `hooks:` field — because claude's Tier-2
mechanism is **not** hooks. Empirically (Claude Code v2.1.185+) the headless
`claude --print "<prompt>"` mode does not run settings-file lifecycle hooks at
all, so a `hooks: claude` declaration would advertise a callback that never
fires. Instead the profile opts into boundary injection with `stream: claude`:
brr drives a persistent stream-json session (`--print`/`-p` is **stripped** —
it forces a single-turn session with no stop-control), weaving the portal delta
in at each tool boundary and folding a still-pending event's body in verbatim at
the terminal result (`src/brr/runner_stream.py`). A profile **without**
`stream:` (the `--bare` aliases, a `runner_cmd` override) runs Tier 0/1 on the
daemon's heartbeat-polled model (outbox drain + `portal-state.json` refresh on
the timer), which carries *outbound* mid-thought flush but not *inbound*
injection. `--setting-sources local` is kept for settings **isolation**: it
excludes the user's global and the project's committed settings without the
collateral damage of `--safe-mode`, which sets `CLAUDE_CODE_SAFE_MODE=1` and
disables CLAUDE.md, skills, plugins, and MCP. The `--bare` alias profiles
declare neither `hooks:` nor `stream:`.

`codex` uses the verified JSONL streaming surface (`stream: codex`) rather
than a native hook declaration. Live probes on codex-cli 0.141.0 showed
`item.completed` command events for boundaries, `item.completed`
`agent_message` for final text, `turn.completed` for the terminal seam, and
`thread.started` for the resumable session id. `gemini` keeps its `hooks:`
declaration as *intent*: brr can render native hook config once supported and
the runtime capability precheck will gate activation. Treat Gemini Tier 2 as
confirmed only after a live firing test, not assumed from the declaration.

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
