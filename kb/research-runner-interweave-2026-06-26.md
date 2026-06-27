# Research: runner interweave validation

This page answers the 2026-06-26 maintainer question: what brr is actually doing
for state interweave across Claude, Codex, and Gemini; whether the current shape
is sound; and what common abstraction should survive the runner-specific
mechanisms.

> **Superseding correction (2026-06-27, evt o538 / 1tqp).** The original
> conclusion that Claude needed stream-driving was disproven: a parent Claude
> session leaked `CLAUDE_CODE_SAFE_MODE=1` into child runner probes and silently
> disabled settings-file hooks. With that contaminant stripped,
> `PostToolBatch` / `Stop` / `SessionStart` fire under `claude --print`, and
> Codex `PostToolUse` fires with the same `hookSpecificOutput.additionalContext`
> envelope. brr now strips the parent-session env (`runner.clean_runner_environ()`)
> and has retired the streaming driver. This page is rewritten to current state;
> the abandoned streaming plan remains as lineage in
> [`plan-streaming-runner-injection.md`](plan-streaming-runner-injection.md).

## Short answer

The common concept is **boundary interweave**: brr applies the same policy at
runner seams without making the resident remember to poll side files:

- flush outbound work promptly (`.card`, outbox replies);
- surface fresh live state through the runner-native context channel;
- fold a late user follow-up at the terminal boundary when it belongs in this
  wake, using the hook's stop-control surface;
- keep the fallback path correct when none of that fires.

The active mechanism is native lifecycle hooks:

- **Claude**: `hooks: claude`; brr writes per-run `.claude/settings.local.json`
  with `PostToolBatch`, `Stop`, and `SessionStart` mapped to
  `brr hook <phase>`.
- **Codex**: `hooks: codex`; brr injects `hooks.<Event>` config as `codex exec`
  argv (`-c …`) and pairs it with `--dangerously-bypass-hook-trust`, avoiding
  the project `.codex/config.toml` trust hang.
- **Gemini**: `hooks: gemini` remains declared intent; no emitter or live firing
  test has shipped, so it degrades to the heartbeat-polled floor.

## What the current code does

`src/brr/hooks.py` owns the neutral policy. `compute_neutral()` reads live
`portal-state.json`, touches `.flush` for event-driven daemon drain, renders a
compact `hooks.format_delta()` capsule, and blocks `Stop` once when a foldable
pending event exists. `render_native()` maps the neutral result to each flavour:
Claude gets `decision: "block"` plus `hookSpecificOutput`; Codex gets
`continue: false` / `stopReason` plus the same `hookSpecificOutput`; Gemini gets
`decision: "deny"` + exit 2 for the unshipped emitter.

`src/brr/prompts/runners.md` declares `hooks: claude` and `hooks: codex`.
`daemon._run_worker()` installs per-run Claude settings files when the profile
declares `hooks: claude`; for Codex it threads `hooks.codex_hook_args()` through
`RunnerInvocation.extra_runner_args`. `runner.invoke_runner()` always starts from
`runner.clean_runner_environ()` so parent agent safe-mode/session identity cannot
poison child runner hooks. `runner_stream.py` and `test_runner_stream.py` are
deleted.

## Validation notes

Primary docs and local probes now line up with the hook path:

- Claude Code 2.1.191 fires settings-file `PostToolUse` and `PostToolBatch`
  under `--print`, injects `hookSpecificOutput.additionalContext`, and honours
  `Stop` `decision:block` by continuing the same turn. The brr-exact setup
  (`.claude/settings.local.json` + `--setting-sources local`) fired once
  `CLAUDE_CODE_SAFE_MODE` stopped leaking from the parent session.
- Codex CLI 0.141.0 fires native `PostToolUse` via inline `-c hooks.PostToolUse`
  config and `--dangerously-bypass-hook-trust`; it accepts
  `hookSpecificOutput.additionalContext`. The Codex docs say omitting `matcher`
  matches every occurrence of the supported event, so brr's inline all-event
  hook config is intentional. `Stop` and `SessionStart` are wired from the same
  emitter but still deserve a cheap live smoke when quota permits.
- Codex app-server is a stronger future candidate for true Codex live steering:
  the official app-server protocol is bidirectional JSON-RPC, exposes threads,
  turns, streamed item notifications, and `turn/steer` for appending input to an
  active turn. It is a larger integration than hooks and not needed for this
  slice.
- Gemini CLI documents synchronous hooks, context injection at `SessionStart`,
  tool and agent hooks, and deny/retry controls. brr should not present Gemini as
  Tier 2 until it has a config emitter and a live firing test.

## Cross-runner options

| Option | Claude | Codex | Gemini | Fit |
| --- | --- | --- | --- | --- |
| Native hooks as the common mechanism | Strong: firing + injection + stop continuation verified after env cleanup | Strong enough for this slice: `PostToolUse` + injection verified, stop/session wired from docs | Intent only until emitter + firing test | Chosen. Smallest current shape, deletes the streaming driver, keeps one endpoint. |
| Keep stream-driving for Claude/Codex | Possible but now unjustified | Possible but now unjustified | Not relevant | Abandoned. It solved a false negative and carried too much bespoke runner code. |
| Move Codex from hooks to app-server / SDK | Not relevant | Potentially richer: bidirectional transport, streamed turn/item events, active-turn steering | Not relevant | Future research only if Codex needs active-turn steering beyond lifecycle hooks. |
| Stay poll-only with `portal-state.json` / `inbox.json` | Correct fallback | Correct fallback | Correct fallback | Product floor only. It preserves correctness but loses the responsive resident shape. |
| brr-owned provider API/tool loop | Possible but large | Possible but large | Possible but large | Too much for this slice; would replace runner CLIs rather than integrate them. |

## Recommended common abstraction

Keep one transport-neutral policy and make runner adapters advertise capability
flags instead of inheriting semantics from a runner name:

| Capability | Meaning |
| --- | --- |
| `flush_at_boundary` | The runner exposes a reliable post-tool or equivalent seam where brr can ask the daemon to drain outbox/card now. |
| `inject_at_boundary` | brr can push a fresh portal delta into the live agent context before the run ends. |
| `fold_at_result` | brr can fold a pending user event at the terminal boundary without spawning an unrelated wake. |
| `stop_control` | brr can prevent or continue a premature terminal result in the same runner session/turn family. |
| `seed_context` | brr can inject an initial live capsule through the runner mechanism rather than only through the run prompt. |
| `proof` | The exact CLI version and firing test that proved the capability. No proof means intent, not capability. |

Today that would classify the built-ins as:

| Runner path | `flush_at_boundary` | `inject_at_boundary` | `fold_at_result` | `stop_control` | Proof posture |
| --- | --- | --- | --- | --- | --- |
| Claude native hooks | Yes, via `.flush` from `PostToolBatch` / `Stop` | Yes, via `additionalContext` | Yes, via `Stop` block reason | Yes | Fire-verified on Claude Code 2.1.191 after env cleanup. |
| Codex native hooks | Yes, via `.flush` from `PostToolUse` / `Stop` | Yes, via `additionalContext` | Yes, via `Stop` `stopReason` | Partly live: `PostToolUse` verified; `Stop`/`SessionStart` docs-backed until smoked | Fire-verified `PostToolUse` on codex-cli 0.141.0; argv config shipped. |
| Gemini native hooks | Likely | Likely | Likely | Likely | Docs-only until brr emits config and fires it. |
| Codex app-server | Likely | Possibly, through `turn/steer` | Likely | Possibly | Docs-only; future spike only if hooks are insufficient. |
| Tier 0/1 blocking runner | No | No | No | No | Correct fallback. |

This points to one small future refactor only after Gemini or another adapter
lands: split `hooks.compute_neutral()` into a runner-neutral boundary-policy
module and keep `hooks.py` as the native-hook adapter. Do not do that now; with
streaming deleted, the duplication is gone.

## Standing portal candidates

Several facts should become live portal state rather than prose the resident has
to rediscover:

- the active runner interweave mechanism for this run (`hooks: claude`,
  `hooks: codex`, `hooks: gemini` intent, or fallback);
- the capability flags above plus their proof/version string;
- the last observed hook phase and whether the last `.flush` was drained;
- whether stop fold-in was already consumed;
- any queued pending-event bodies that are foldable versus events that should
  stay for a fresh wake.

That would let a wake start from a live surface: "Codex hooks, PostToolUse
verified, Stop wired docs-backed, fold-in unused" instead of reconstructing it from the
Run Context Bundle, docs, and memory.

## Sources

- [Claude Code hooks reference](https://code.claude.com/docs/en/hooks)
- [Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference)
- [Claude Agent SDK streaming input](https://code.claude.com/docs/en/agent-sdk/streaming-vs-single-mode)
- [OpenAI Codex CLI reference](https://developers.openai.com/codex/cli/reference)
- [OpenAI Codex hooks](https://developers.openai.com/codex/hooks)
- [OpenAI Codex app-server](https://developers.openai.com/codex/app-server)
- [Gemini CLI hooks](https://geminicli.com/docs/hooks/)
- [Gemini CLI hooks reference](https://geminicli.com/docs/hooks/reference/)
