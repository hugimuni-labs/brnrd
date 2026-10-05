---
title: Models & quota
description: Pin, select, escalate, and downshift local Shell and Core profiles.
---

brnrd separates the CLI process from the model it runs:

- **Shell**: the CLI on `PATH` — `claude`, `codex`, `vibe`, or `grok`.
- **Core**: the model and its cost, capability, and quota metadata.

Together they form the Runner for one wake. The resident remains the same when
the Runner changes.

Inspect the profiles available on this machine:

```bash
brnrd runners list
brnrd runners list --all
```

## Pin or let brnrd choose

Runner and daemon settings belong to the connected account, not to an
individual repository. Inspect or change them from any connected checkout:

```ini
brnrd config show
brnrd config set runner.default codex
brnrd config set runner.default_class balanced
brnrd config set runner_policy fixed
```

These commands write `<account home>/daemon.config`. `runner.default` is an
exact profile name; unset it with `brnrd config unset runner.default` and use
`runner_policy=cost-aware` to let brnrd choose the cheapest adequate available
local Runner. Tapping a profile in the dashboard rack sets the same account
default and also parks that profile for the next wake, so the first wake and
the wakes after it agree.

Older `shell`, `core`, `runner`, and `default_class` entries in `.brr/config`
are read for one compatibility release. Daemon boot copies them to the account
file, logs the migration, and leaves the old text in place but ignored.

## Profile catalog

Profiles are data in `<account home>/runners.toml`, with bundled defaults in
`src/brr/runners.toml`. TOML keeps the operator-edited catalog human-readable
while Python 3.11+ can parse it without another runtime dependency. An older
account-home `runners.md` frontmatter catalog is read for one release and
migrated at daemon boot.

Each `[profiles.<name>]` table may contain:

- `cmd`: the headless command. The assembled prompt is piped on stdin unless
  `{prompt}` appears as its own argument.
- `binary`: the executable to probe when the profile name is an alias.
- `hooks`: the runner-specific Tier 2 hook adapter (`claude`, `codex`, `vibe`, or `grok`).
- `provider`, `owner`, `class`, `cost_rank`, and `quota_source`: selection and
  quota metadata.
- `model`: an optional pinned Core. The bundled Core registry also materializes
  model-specific profiles for catalogued Shells.

Core profile names are `<shell>-<model slug>` (`codex-gpt-5.6-sol`,
`claude-sonnet`). The bundled `[aliases]` table keeps the former
`codex-mini`, `codex-terra`, and `codex-full` pins resolving during the
compatibility release; aliases do not appear as duplicate rack rows.

The minimum runner contract is a process that accepts the assembled prompt,
operates in the supplied working directory, and exits with a status code.
Printing a final reply on stdout adds response delivery; declaring `hooks`
adds live tool-boundary injection. Profile commands and `runner_cmd` remain in
the daemon-owned home because both decide which host command executes.

## Mistral Vibe

Install Vibe with `uv tool install mistral-vibe` and sign in using Vibe's own
setup. Select `vibe` as a runner profile, or dispatch a strand with `shell: vibe`.
The adapter pipes the wake on stdin, installs an invocation-specific system
prompt without replacing your settings, and returns the final assistant reply.
It uses Vibe's configured model and saved credentials (including `VIBE_HOME`).

Native boundary hooks are verified with Vibe 2.25.5: post-tool notices reach
the model, file writes use the rooted-write guard, and post-agent denial requests
a turn revision (Vibe caps retries at three). Each daemon invocation installs
hooks in a temporary additional directory; `BRR_VIBE_HOOKS=0` disables them.
Session resume, model attestation and monthly quota/spend collection remain
unavailable. Unknown capacity does not imply unlimited usage; Mistral
subscriptions include a monthly usage allowance.

## Grok Build

Install Grok with `curl -fsSL https://x.ai/cli/install.sh | bash` and sign in
using Grok's own setup (or set `XAI_API_KEY`). Select `grok` as a runner
profile, or dispatch a strand with `shell: grok`. The adapter writes the wake
to a temp file and passes `--prompt-file`, because Grok does not read a piped
prompt. The resident protonucleus is appended with `--rules`, leaving Grok's
own tool instructions in place. A pinned core (`grok-4.7`, `grok-4.6`,
`grok-4.5`, `grok-4.7-build-fast`) sets `-m` for that invocation only. The
unpinned `grok` profile uses whatever model Grok itself would.

Headless runs use `--permission-mode bypassPermissions` and `--trust`. Grok
loads the per-run `.claude/settings.local.json` (it treats that file as
Claude-compatible hooks) and skips event names it does not know, so the
post-tool seam is `PostToolUse` rather than Claude's `PostToolBatch`. Pre-tool
denies and stop blocks use Claude's hook JSON.

The adapter prints Grok's JSON envelope through. The runner reads it the way
it reads Claude's: the reply, the session id, the model that ran, the token
totals, and the cost when Grok stamps one. A cost Grok omits stays omitted
(pool and OAuth traffic often omit it; absence is not free, and per-model
rows are not summed into one). A host-env seat resumes that session with
`--resume`. Grok stores sessions under the working directory they ran in, so
a worktree run does not arm a native resume. Quota and context-window
headroom are not collected, and brnrd will not move a wake onto or off Grok
because of a window it cannot see.

## Escalate and downshift

A resident can hand a hard continuation to a stronger local Core after it has
read the repo, or pin an economy Core for bounded routine work. The handoff
keeps the conversation and prepared worktree. Quality escalation does not
silently opt into paid relay compute.

Quota is part of the live run posture and is shown to the resident before work
starts. Automatic fallback is deliberately narrow: classified local
quota/auth/provider failures may retry on another local Runner in the same or a
cheaper class. Paid relay remains behind explicit consent.
