---
claude:
  cmd: 'claude --print --output-format json --dangerously-skip-permissions --setting-sources local --system-prompt "You are a brnrd runner. Follow the supplied prompt and operate on the files available in the working directory."'
  hooks: claude
  provider: anthropic
  owner: user
  class: balanced
  cost_rank: 30
  quota_source: claude-local
claude-bare-api-only:
  binary: claude
  shell: claude
  cmd: 'claude --print --output-format json --dangerously-skip-permissions --bare --system-prompt "You are a brnrd runner. Follow the supplied prompt and operate on the files available in the working directory."'
  provider: anthropic
  owner: user
  class: balanced
  cost_rank: 30
  auth_variant: anthropic-api-key
  auth_env: ANTHROPIC_API_KEY
codex:
  cmd: 'codex exec --dangerously-bypass-approvals-and-sandbox --dangerously-bypass-hook-trust -c base_instructions="You are a brnrd runner. Follow the supplied prompt and operate on the files available in the working directory." -c include_permissions_instructions=false -c include_apps_instructions=false -c include_collaboration_mode_instructions=false -c include_skill_instructions=false'
  hooks: codex
  provider: openai
  owner: user
  class: balanced
  cost_rank: 25
  quota_source: codex-local
---
Bundled runner profiles for brnrd.

Each profile names a **Shell** (the CLI invocation on PATH: `claude`,
`codex`) and, optionally, a **Core** (the model and its
cost/quota metadata). A profile with both Shell and Core pinned is one
selectable Runner. The **resident** inhabits whichever Runner this wake
was given; `prompts/runners.md` (this file) catalogs what's available.

The runner contract is deliberately abstract: a runner is a process that
can intelligently operate files in its working directory. brnrd pipes the
assembled prompt to the runner on **stdin** (written whole, then closed —
prompt, then EOF), captures stdout as the plain current-thread output
artifact, treats stderr as progress/debug output, and interprets the exit
status as the process result. A profile whose `cmd` carries a
whole-argument `{prompt}` placeholder receives the prompt in argv there
instead — argv and stdin are mutually exclusive prompt channels, never
both. The runner does not need to know the response-file path for the
common case.

## The minimal runner interface (tiers)

The contract stays lean by staying *tiered* — each tier is optional
enrichment of the one below, and a runner that satisfies only Tier 0 still
works. See `kb/design-runner-back-channel.md` for the full design.

- **Tier 0 (required).** A process that, given the assembled prompt on
  stdin (or in argv, where its `cmd` asks for that via `{prompt}`),
  operates files in its working directory and exits with a status code.
  The irreducible floor — all real work happens here. stdin became the
  default on 2026-07-14: a prompt in argv is world-readable in `ps` and
  sits in the haystack every `pkill -f`/`pgrep -f` a run performs matches
  against — one run SIGTERM'd *itself* through a quoted command in its own
  prompt. Argv-path safety net: Linux caps a single argv string at 128 KiB
  (`MAX_ARG_STRLEN`) regardless of the larger overall `ARG_MAX` — a wake's
  assembled prompt tripped this in production 2026-07-07 (176 KB, an
  `OSError: [Errno 7] Argument list too long` that killed the thought
  before it started). `invoke_runner` still spills any argv element over
  ~100 KB to `.brr/prompt-overflow/<hash>.md` and passes a short
  "read this file first" pointer instead. A Tier-0 runner never sees this
  directly; it just occasionally gets a pointer instead of the prompt
  inline, and reads it with the same file tools it already has.
- **Tier 1 (optional).** Prints a final reply on stdout (progress/debug on
  stderr). brnrd captures stdout as the plain current-thread reply. This is
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
invokes a brnrd callback (`brnrd hook <phase>`) at tool/turn boundaries and weaves
the JSON result back into its context. A profile opts in with a `hooks:
<flavour>` field. brnrd owns the abstract phases (`post-tool` / `stop` /
`session-start`) and renders one neutral result into each flavour's native
fields. The *config-install mechanism* is runner-specific:
  - **claude** — `hooks: claude`. brnrd writes a per-run
    `.claude/settings.local.json` registering `PostToolBatch` / `Stop` /
    `SessionStart` → `brnrd hook <phase>`. Injection lands via
    `hookSpecificOutput.additionalContext`; `Stop` `decision:block` continues
    the turn for premature-stop control. **Fire-verified** on Claude Code
    2.1.191. `PostToolBatch` (not `PostToolUse`) is the post-tool seam — once
    per tool batch, after every result, before the next model call.
  - **codex** — `hooks: codex`. Codex's project-`.codex/config.toml` install
    hangs under repo-trust, so brnrd injects the hook config as runner argv
    (`-c hooks.<Event>=[…]`) paired with `--dangerously-bypass-hook-trust` in
    the profile cmd. Codex exposes `PostToolUse` / `Stop` / `SessionStart` (no
    `PostToolBatch`) and accepts the same `hookSpecificOutput` injection
    envelope. **Fire-verified** `PostToolUse` + injection on codex-cli 0.141.0.

brnrd only installs hook config for a profile that explicitly declares `hooks:`.
It never infers hooks from the runner name; a profile with no `hooks:` field
(the `--bare` auth variant, a `runner_cmd` override) uses the heartbeat-polled
fallback (outbox drain + `portal-state.json` refresh on the daemon timer),
which carries *outbound* mid-thought flush but not *inbound* injection.

Reliability rests on a clean child env. A parent agent session can leak
`CLAUDE_CODE_SAFE_MODE=1` into a spawned `claude`, which **silently disables
settings-file hooks** while logging a reassuring "managed settings-file hooks
still run" — the false negative that earlier made hooks look unfireable under
`--print` and drove a now-retired streaming workaround. brnrd strips that
contaminant (and parent session-identity vars) from every runner subprocess env
via `runner.clean_runner_environ()`, so hooks fire as they would for a normal
top-level run. `--setting-sources local` is kept for settings **isolation**: it
excludes the user's global and the project's committed settings without the
collateral damage of `--safe-mode`.

These bundled profiles are defaults, not the user's source of truth. To
manage runner profiles for a project, create `.brr/runners.md` with the
same frontmatter shape; brnrd reads that before the bundled defaults. The
legacy `.brr/prompts/runners.md` override is still accepted, but new
configuration should use `.brr/runners.md` because runner profiles are
Shell+Core execution config, not prompt templates. For a one-off command,
`runner_cmd` in `.brr/config` remains the smallest override.

Each frontmatter key is a runner name. During detection brnrd checks
whether the profile's CLI is on PATH — either the key itself (`claude`,
`codex`) or an explicit `binary` field for alias profiles such
as `claude-bare-api-only`.

The profile captures the headless invocation: non-interactive mode plus
tool/approval bypass, since the daemon needs the runner to act without
prompts. Claude profiles also request ``--output-format json``; brnrd unwraps
the JSON ``result`` back into the response file and uses the accounting fields
for terminal spend/context facets. Repository orientation, AGENTS.md, dominion
context, and the Run Context Bundle belong in the assembled prompt, not in
these command strings.

- `cmd` — base command. brnrd pipes the prompt to it on stdin by default.
  To receive the prompt in argv instead, include `{prompt}` as **its own
  argument** (`mycli --run {prompt}`): that element is replaced with the
  whole prompt and stdin stays closed (immediate EOF). Whole-argument
  substitution only — an element that merely *embeds* the placeholder
  (`--flag={prompt}`) is rejected loudly at dispatch, never shipped
  half-substituted. (The legacy `runner_cmd` config override is the one
  place embedded substitution still works.)
- `binary` — optional PATH binary for alias profiles. When set, the
  profile must be named explicitly via `shell=`/`core=` in `.brr/config`
  (not auto-detected).
- `probe_models` — opt-in (`true`) for CLI-help model discovery on a
  custom profile's shell. By default brnrd probes `--help` for model names
  — and fabricates selectable `<name>-<model>` profile variants by
  splicing `--model X` into the base cmd — **only for the bundled shells**
  (`claude`, `codex`). A custom declared profile is taken at its
  word: no fabricated siblings for auto-selection to prefer over it unless
  this flag says so.

Optional **Core metadata** (read by `runner_select.py`, the cost-aware
Core-selection layer) also rides these keys. None is required; a profile
with none is an uncosted Runner the selector uses as-is:

- `provider` / `model` / `owner` — who runs the Core (`owner: user` for a
  local subscription/API key, `owner: brnrd` for a paid relay Core).
- `class` — cost class: `economy` < `balanced` < `strong`, or `relay` for a
  paid brnrd-owned fallback (never auto-selected; needs spend-plan consent).
- `cost_rank` — a coarse, **tunable relative ordering hint** (cheapest first),
  *not* a dollar figure and not a promise of price. The selector sorts by it
  within a class; projects retune it freely in their own `.brr/runners.md`.
- `quota_source` — which collector reads this Core's quota (`codex-local`
  reads the session rollout; `claude-local` is terminal spend/context only).
- `capability_score` / `capability_source` / `capability_freshness` — optional
  benchmark-cache hints. These may derive `class` when no hand-set class exists,
  but never override an explicit `class` and never act as a hard selector.

The selection *policy* is brnrd's, not a table the user hand-tunes: the user
sets `shell=`/`core=` (or leaves unset for auto) and optional
`runner_policy=` (`cost-aware` | `fixed`) in `.brr/config`, and the
resident picks the cheapest adequate available Runner from there. See
`kb/design-runner-cores.md`.

Automatic fallback is narrower than first selection. When a runner exits with a
classified quota/auth/provider failure, brnrd may retry the same run in the same
prepared worktree on another local Runner. That fallback excludes paid relay
profiles, stays in the same or a cheaper class, and avoids the same failure
domain where metadata makes that visible. Relay remains behind spend-plan
consent.

Quality escalation is a different path. It is resident-authored, not automatic
triage: after reading the repo, a cheap Runner can drop an outbox message with
`respawn: true` and either an explicit `shell:` / `core:` or
`quality: escalate`. The latter asks brnrd's deterministic selector for the
stronger local Core advertised in `portal-state.json`
(`resources.runner.quality_escalation`) and queues a fresh event for the same
conversation. Relay profiles remain excluded here too; paid handoff waits for
the spend-consent flow.

Auto mode also reads brnrd's bundled Core registry. For each Shell declared in
the active `runners.md`, brnrd materializes registry rows such as `claude-haiku`
or `codex-mini` as invokable profiles by inserting the Core's model flag into
the base Shell command and inheriting hook/quota metadata from that Shell.
Those generated profiles let `core=haiku` and cost-aware auto-selection choose a
concrete Core without requiring a static profile entry for every model. A
project-owned `.brr/runners.md` remains authoritative: registry profiles are
generated only for Shells that file declares, and any declared profile with the
same name wins.

Alias profiles with `binary` and `auth_variant` are for authentication variants
of the same CLI. For example `claude-bare-api-only` uses `--bare` and requires
`ANTHROPIC_API_KEY` (OAuth / `~/.claude` subscription auth is not used). The
Core registry materializes model-pinned profiles for that auth variant too
(`claude-bare-api-only-sonnet`, `claude-bare-api-only-opus`, etc.), but their
model / class / cost metadata stays in `runner_cores.py`, not in a second static
profile catalog.

When the resident chooses a plain current-thread stdout reply, brnrd reads it
from stdout and writes it to the event's response file automatically;
runners do not need a per-CLI flag for that. Other delivery shapes ride the
outbox / gate / commit / noop portals named in the run prompt. Progress,
traces, and tool output should go to stderr (which is the convention for
both runners above).

Users can override `cmd` per-repo by setting `runner_cmd` in
`.brr/config`. The same stdout capture rules apply. A `runner_cmd` always
owns its argv: `{prompt}` is substituted before exec (embedded occurrences
included, for backward compatibility) and nothing is piped on stdin.

Quota and price signals are metadata about a Core, not part of the
command string. Today brnrd reads them from `runner.quota.*`,
`BRR_RUNNER_QUOTA_*`, or `.brr/runner-quota.json`; a fuller Runner/Core
registry can grow from this contract without making built-in commands
pretend to know provider billing.
