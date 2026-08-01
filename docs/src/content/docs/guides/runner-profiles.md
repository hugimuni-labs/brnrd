---
title: Runner profiles
description: Add an agentic CLI to brnrd by declaring its command and capability degradations.
---

A Runner profile tells brnrd how to invoke one agentic CLI, or **Shell**.
Profiles live in `runners.md` in the daemon-owned account home, beside
`security.config`. Run `brnrd account status` to find that home.
Repo-side `.brr/runners.md` files are ignored because a profile contains the
command the daemon executes; use `brnrd config promote` to migrate an old one.

## The interface

The interface has three tiers. Only Tier 0 is required.

| Tier | Contract | Degradation when absent |
|---|---|---|
| 0 | Read the assembled prompt, operate files in the working directory, and exit with a status. | The profile is not a usable Runner. |
| 1 | Write the final reply to stdout and progress or diagnostics to stderr. | File edits and commits still work, but there may be no plain chat reply. |
| 2 | Expose native tool/turn hooks for boundary injection. | `heartbeat`: the daemon drains outbound messages and refreshes portal files on its timer, without live inbound injection. |

brnrd sends the prompt on stdin, then closes it. A command can instead place a
whole-argument `{prompt}` token in `cmd`; argv and stdin are mutually
exclusive. Embedded forms such as `--prompt={prompt}` are rejected for profile
commands. If an argv element approaches the operating system's per-argument
limit, brnrd writes that value to a prompt-overflow file and supplies a short
instruction to read it.

Runner subprocesses start from a cleaned environment. Parent agent session
identity and safe-mode variables are removed before profile-specific variables
are added, so a parent session cannot silently disable a child's hooks.

## Capability cells

Every profile declares five cells. Use `native:<mapping>` for a supported
adapter, `degraded:<name>` for an explicit fallback, or
`unknown:<reason>` only when the fact has not been established.
Pre-matrix account catalogs still load through a compatibility path, but new
and edited profiles should declare all five cells.

| Cell | What it declares | Bundled mappings and degradations |
|---|---|---|
| `headless` | The Shell's non-interactive command mode. | `native:--print`, `native:exec` |
| `stdout_reply` | How stdout becomes the final reply and accounting stream. | `native:plain`, `native:claude-json`, `native:codex-jsonl` |
| `boundary_injection` | Native hook adapter. | `native:claude`, `native:codex`, `degraded:heartbeat` |
| `model_pin` | Model flag and insertion anchor. | `native:--model@binary`, `native:--model@exec`, `degraded:fixed-core` |
| `quota_read` | Quota collector. | `native:session-rollout`, `degraded:cached-tui`, `degraded:unreported` |

Mappings name behavior brnrd has implemented and verified; they are not free-form
descriptions. A new Shell can reuse the generic mappings without another
Shell-name allowlist. A genuinely new native protocol still needs its adapter
implemented before the profile may claim it.

## Example

This Tier 0/1 profile uses the common fallbacks and pins its Core in `cmd`:

```yaml
---
my-agent:
  cmd: 'my-agent run --model compact'
  capabilities:
    headless: native:run
    stdout_reply: native:plain
    boundary_injection: degraded:heartbeat
    model_pin: degraded:fixed-core
    quota_read: degraded:unreported
  provider: example
  owner: user
  class: economy
  cost_rank: 20
---
```

`cmd` is the headless invocation, including non-interactive approval and
sandbox flags required by that Shell. Optional selection metadata includes
`provider`, `model`, `owner`, `class`, `cost_rank`, and
`quota_source`. `cost_rank` is a relative ordering hint within a class, not
a price. Alias profiles can set `binary` when their profile name is not the
executable name, plus `auth_env` when availability requires an environment
variable.

Set `probe_models: true` only for a legacy profile without a native
`model_pin` cell. It opts into best-effort CLI model discovery and generated
profile variants; otherwise brnrd takes a custom profile exactly as written.

Check the result with:

```bash
brnrd runners list --all
```

See [Models & quota](../models/) for selection and escalation. The internal
selection and boundary designs live in the project knowledge base rather than
in the executable catalog.
