---
title: Models & quota
description: Pin, select, escalate, and downshift local Shell and Core profiles.
---

brnrd separates the CLI process from the model it runs:

- **Shell**: the CLI on `PATH` — `claude` or `codex`.
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
- `hooks`: the runner-specific Tier 2 hook adapter (`claude` or `codex`).
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

## Escalate and downshift

A resident can hand a hard continuation to a stronger local Core after it has
read the repo, or pin an economy Core for bounded routine work. The handoff
keeps the conversation and prepared worktree. Quality escalation does not
silently opt into paid relay compute.

Quota is part of the live run posture and is shown to the resident before work
starts. Automatic fallback is deliberately narrow: classified local
quota/auth/provider failures may retry on another local Runner in the same or a
cheaper class. Paid relay remains behind explicit consent.
