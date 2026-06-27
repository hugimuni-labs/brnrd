# Research: runner interweave validation

This page answers the 2026-06-26 maintainer question: what brr is actually doing
for state interweave across Claude, Codex, and Gemini; whether the current shape
is sound; and what common abstraction should survive the runner-specific
mechanisms.

> **Superseding correction (2026-06-27, evt o538).** This page's premise — that
> Claude's mechanism must be stream-driving because settings-file hooks "do not
> fire under `claude --print`" — is **disproven**. Live firing tests on Claude
> Code 2.1.191 show `PostToolUse` / `PostToolBatch` / `Stop` (block-continues)
> all fire under `--print` with `additionalContext` injection; Codex
> `PostToolUse` fires too. The earlier failures were a contaminated test env (a
> parent Claude session leaking `CLAUDE_CODE_SAFE_MODE=1` into the spawned
> child, silently disabling settings-file hooks). Decision: retire the streaming
> path and unify on native hooks for both runners. brr now strips that env
> contaminant (`runner.clean_runner_environ()`). Full detail + migration plan:
> [`design-runner-back-channel.md`](design-runner-back-channel.md) top block.
> Read the capability tables below as the *old* posture, not the target.

## Short answer

The common concept should be **boundary interweave**, not hooks and not "stop and
resume between tool calls." brr needs the same policy at each runner seam:

- flush outbound work promptly (`.card`, outbox replies);
- surface fresh live state when the runner can perceive it;
- fold a late user follow-up at the terminal boundary when it belongs in the
  same wake;
- keep the fallback path correct when none of that fires.

The mechanisms differ:

- **Claude**: a persistent stream-json process with stdin open. brr writes the
  prompt as a JSON user message, reads event JSON, and can write another user
  message at a tool boundary or after a `result`. This is the closest fit to
  perception-as-injection.
- **Codex exec**: a single non-interactive JSONL turn. brr can observe command
  boundaries and flush outbound work, but it cannot write into the active turn.
  At the terminal boundary it can resume the emitted thread once with a folded
  follow-up. That is continuation after a turn, not a stop/resume between tools.
- **Gemini**: native hooks are the likely path, but brr has not implemented a
  Gemini hook-config emitter or run a live firing test, so it is intent only.

## What the current code does

`src/brr/runner_stream.py` implements two stream flavours behind profile
`stream:` fields:

- `stream: claude` builds a stream-json command, strips `--print`, sends the
  prompt through stdin with `user_message_json()`, detects Claude tool-result
  boundaries, and uses `StreamInjectionPolicy` to inject portal deltas or folded
  follow-ups through the same stdin channel.
- `stream: codex` builds `codex exec --json`, closes stdin, parses JSONL events,
  treats completed command-execution items as boundaries for outbound flush, and
  runs at most one `codex exec resume --json <thread_id> <follow-up>` after a
  terminal turn when a pending event body is still live.

The fix in this wake tightened the native-hook side: daemon hook config is now
installed only when a profile explicitly declares `hooks:`. The previous fallback
from "no hooks field" to runner name made bundled Claude install native hook
config even though the current design says Claude's mechanism is stream-driving,
not hooks.

## Validation notes

Primary docs and local probes line up with the split above:

- Claude Code documents hook `additionalContext` and Stop continuation, but its
  CLI reference still frames `--input-format stream-json` and
  `--output-format stream-json` as print-mode options. Local Claude Code 2.1.191
  nevertheless accepted those flags without `--print` and emitted stream-json
  events before hitting the session quota. The repo's earlier live persistent
  multi-turn proof remains the load-bearing evidence; keep a small firing test
  around this seam because the public wording is narrower than the observed
  behaviour.
- Codex CLI 0.141.0 documents `exec --json` as newline-delimited JSON events and
  `exec resume [SESSION_ID] [PROMPT]` as the non-interactive continuation path.
  A local smoke on `gpt-5.4-mini` emitted `thread.started`, `turn.started`,
  `item.completed`/`agent_message`, and `turn.completed`, matching the parser's
  assumptions for final-text capture. The earlier command-boundary live probe is
  what pins `command_execution` item boundaries.
- Codex native hooks now document `PostToolUse` `additionalContext`, `Stop`
  continuation, and `continue: false` flow control. That makes native Codex hooks
  a plausible future adapter, but brr's current Codex path should remain JSONL +
  resume until a native-hook firing test beats it.
- Codex app-server is a stronger future candidate for true Codex live steering:
  the official app-server protocol is bidirectional JSON-RPC, exposes threads,
  turns, streamed item notifications, and `turn/steer` for appending input to an
  active turn. It is a larger integration than `codex exec`, but it is the first
  Codex surface that looks conceptually comparable to Claude's live stdin loop.
- Gemini CLI documents synchronous hooks, context injection at `SessionStart`,
  tool and agent hooks, and deny/retry controls. brr should not present Gemini as
  Tier 2 until it has a config emitter and a live firing test.

## Cross-runner options

| Option | Claude | Codex | Gemini | Fit |
| --- | --- | --- | --- | --- |
| Keep the current transport-neutral policy with runner adapters | Strong: current stream path gives live injection and terminal fold-in | Sound but partial: JSONL observes boundaries; resume folds terminal follow-up | Future: native hooks after firing test | Best near-term shape. Honest about capability differences while sharing the policy. |
| Force "native hooks" as the common mechanism | Weak for brr today: prior Claude hook firing failed under headless `--print`, and stream-driving is already working | Plausible, now documented, but unproven in brr | Plausible, documented, unproven in brr | Wrong abstraction. Hooks are an adapter, not the concept. |
| Move Codex from `exec --json` to app-server / SDK | Not relevant | Potentially strong: bidirectional transport, streamed turn/item events, active-turn steering | Not relevant | Best research spike for "Claude-like Codex" if true live Codex input matters enough. Higher integration cost. |
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
| Claude stream | Yes | Yes | Yes | Yes, by keeping stdin open after `result` | Proven locally before this wake; this wake reconfirmed non-`--print` stream-json starts, but quota blocked a full replay. |
| Codex exec JSONL | Yes, for command completions | No | Yes, via one `exec resume` | Partial: continuation turn, not same active turn | Proven on codex-cli 0.141.0; local smoke reconfirmed JSONL final event shape. |
| Codex native hooks | Likely | Likely, via `additionalContext` | Likely, via `Stop` block | Likely | Docs-only until brr fires it. |
| Codex app-server | Likely | Possibly, through `turn/steer` | Likely | Possibly | Docs-only; deserves a spike if we want true Codex live input. |
| Gemini native hooks | Likely | Likely | Likely | Likely | Docs-only until brr emits config and fires it. |
| Tier 0/1 blocking runner | No | No | No | No | Correct fallback. |

This points to a small future refactor: move `StreamInjectionPolicy` and
`hooks.compute_neutral()` toward one shared boundary-policy module, with adapters
for stream stdin, JSONL resume, and native hook JSON. Do not do that before the
next runner adapter lands; the current duplication is still small enough.

## Standing portal candidates

Several facts should become live portal state rather than prose the resident has
to rediscover:

- the active runner interweave mechanism for this run (`stream: claude`,
  `stream: codex`, `hooks: gemini`, or fallback);
- the capability flags above plus their proof/version string;
- the last observed boundary and whether the last `.flush` was drained;
- whether terminal fold-in is still available or already consumed;
- any queued pending-event bodies that are foldable versus events that should
  stay for a fresh wake.

That would let a wake start from a live surface: "Codex exec JSONL, flush yes,
live injection no, terminal resume once" instead of reconstructing it from the
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
