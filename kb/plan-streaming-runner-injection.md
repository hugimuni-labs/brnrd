# Plan: the streaming runner — Claude and Codex boundary injection

Status: in flight 2026-06-26 (evt vtyq; reviewed + live-driven evt 8f8y; step 2
shipped evt wcxs; **step 3 shipped + claude default-on** evt wlap; **Codex
JSONL stream shipped** evt 47ew) — steps 1–3 are done in
`src/brr/runner_stream.py`; only step 4 (retire the pull-reliance fallback)
remains. **Step 1** = the stream-json client, re-verified against the live claude
v2.1.191 CLI. **Step 2** = the persistent (no-`--print`) driver with boundary
injection: `build_stream_cmd` strips `--print`/`-p` (`_DROP_FLAGS_BY_FLAVOUR`);
`consume_stream` grew an `on_result` stop-control seam; `run_stream` binds a stdin
`Injector` to the boundary/result callbacks and, when none are wired, builds a
default `StreamInjectionPolicy` from the run env's `BRR_PORTAL_STATE`. **Step 3** =
daemon routing + the step-2 deferrals: the `claude` profile declares
`stream: claude`; `runner.invoke_runner` routes a `stream:`-declaring profile (no
`runner_cmd` override) to `run_stream`, keeping the heartbeat/budget/`kill_active`
contract (the driver registers `_active_proc` itself). The policy now touches the
shared `.flush` signal at each boundary/result so the heartbeat drains the outbox
promptly (deferral #1, reusing the daemon's existing flush mechanism — no daemon
coupling), and folds a still-pending event in by its **body verbatim** under a
neutral relay header rather than the op summary (deferral #2; the portal-state
event record already carries the full body). Validated live (claude v2.1.191 haiku,
real profile flags survive stream mode). Host + worktree envs stream; the docker
env (own invoke) stays blocking. This is the concrete build behind
[`design-runner-back-channel.md`](design-runner-back-channel.md)
§Streaming-driven injection. Parent: [#159](https://github.com/Gurio/brr/issues/159),
[#171](https://github.com/Gurio/brr/issues/171).

**Codex extension (evt 47ew).** `codex exec --json` is now routed through the
same module with `stream: codex`. Its CLI stream is single-turn, not a persistent
stdin loop: live probes on codex-cli 0.141.0 showed `item.completed`
`command_execution` events as tool boundaries, `item.completed` `agent_message`
as final text, `turn.completed` as the terminal seam, and `thread.started` as
the resumable id. The driver therefore flushes outbound work at completed
command items and, when a pending follow-up is still live at terminal turn,
launches one `codex exec resume --json <thread_id> <verbatim follow-up>` turn.
That gives Codex the same user-visible closeout fold-in without advertising a
native hook path we have not made fire.

## Why this build (the responsiveness gap it closes)

Today a claude thought is one `subprocess.Popen` of `claude --print … <prompt>`
that brr blocks on until stdout (`runner.invoke_runner`). brr is **blind to the
tool-call loop inside it**. Two halves of mid-thought responsiveness fall out of
that blindness differently:

- **Outbound flush** (the resident's replies reaching the user) — works today via
  the daemon's heartbeat draining the outbox. Fine.
- **Inbound injection** (the resident *perceiving* a new event / follow-up without
  remembering to poll `inbox.json`) — **does not work** for claude. The heartbeat
  refreshes `inbox.json`/`portal-state.json` into *files*, and the longer a thought
  runs the more it drifts from the procedure of reading them. This is the lived
  failure the maintainer named: "you still forget to check the inbox for my
  follow-ups." Pull defeats drift.

The settings-file-hooks mechanism that was meant to close this never fires under
`claude --print` (see the design page's two failures). The verified alternative:
**brr drives the stream and injects the delta itself.**

## The verified mechanism (spike, 2026-06-26)

`claude --print --input-format stream-json --output-format stream-json` is an
interactive loop: brr writes newline-delimited JSON user messages on stdin and
reads JSON events on stdout, with stdin kept **open**. The spike
(`/tmp/brr_stream_spike`, claude 2.1.191) proved:

1. A user message written **after a tool boundary** is woven into the model's
   context mid-thought and attended to — no harness hook, brr owns the loop.
2. **Framing is load-bearing.** A delta framed as a coercive system interrupt
   (`"INTERRUPT FROM THE DAEMON: you MUST …"`) was perceived but **refused**
   (correct prompt-injection defense). The same content as the user's genuine
   relayed follow-up was **acted on**. → Relay user follow-ups verbatim as the
   user's words; render operational deltas (new-event-waiting, budget, SCM)
   **informational, never imperative**.

## Driver re-verification through the module (2026-06-26, evt 8f8y)

The step-1 module (`runner_stream.py`) was driven against a **live** claude-haiku
v2.1.191 stream-json session (four harnesses in `/tmp/brr_stream_livetest`,
reusing `parse_event` / `consume_stream` / `user_message_json`). The parser is
**confirmed against the real CLI** — `consume_stream` replayed over the captured
real stream gave identical boundary/result counts, and the live schema's
interleaved noise (`system/init`, `rate_limit_event`, `system/thinking_tokens`,
assistant `thinking` blocks) is skipped cleanly (now pinned by
`test_consume_stream_tolerates_real_cli_noise`). But the live drive surfaced a
**load-bearing distinction the spike framing missed — `--print` vs persistent
session:**

- **`--print` + stream-json is _single-turn_.** Mid-loop injection works **only
  while tool calls are still pending**: a follow-up written *after the model has
  decided to finish* the turn is silently dropped, and the process **exits on the
  first `result`** regardless of what is on stdin. Empirically: a one-tool task
  (`echo ALPHA` → done) ignored a post-boundary injection entirely; a three-tool
  task picked up the same injection between calls and acted on it. So `--print`
  gives mid-loop injection but **no stop-control** — there is no way to block a
  premature finish or fold a late-arriving event into the run.
- **Persistent session (drop `--print`) is _multi-turn_** and is the architecture
  step 2/3 should build on. Verified in one process: tool calls run headlessly
  without `--print`; mid-loop injection between tool calls is attended; and —
  decisively — **after a `result`, a new user message starts a fresh turn the
  model addresses** (`echo FOLD-INJECT` ran after the model had already said
  "Done!"). That post-result fold-in *is* stop-control: brr decides at each
  `result` whether a foldable pending event exists → inject it as a new turn, or
  no pending input → close stdin and capture the last `result` as the response.

**Consequence — corrections to this plan and the module** (all three **applied in
step 2**, evt wcxs — kept here as the rationale):

1. **`build_stream_cmd` currently inherits `--print` from the claude profile cmd**
   and only *adds* the stream flags, so today it produces the strictly-weaker
   single-turn channel. For step 2/3 it must **strip `--print`** when streaming
   (run a persistent session). This also unifies the two seams the design splits:
   "post-tool injection" and "Stop-control" are the *same* stdin-write mechanism,
   differing only in whether a tool call or a `result` preceded them.
2. **The §Stop-control note below is only true in persistent mode.** "Don't close
   stdin while a pending event is unhandled, inject the nudge instead" does
   **not** work under `--print` (the process is already gone after `result`);
   it works exactly as written once `--print` is dropped.
3. **`run_stream`'s `on_boundary` cannot inject** — the callback receives a
   `StreamBoundary` but no stdin handle / injector, so the boundary seam is
   read-only today. Step 2 must give the boundary callback a way to write back
   (pass an `inject: Callable[[str], None]` bound to `proc.stdin`, or move the
   drive loop inline so it holds both). This is the concrete next edit.

The barebones `--print` floor still degrades cleanly to the heartbeat-polled
model (outbound flush only); the persistent-session path is the Tier-2 ceiling.

## Architecture — a parallel runner path, not a rewrite of the old one

Keep `invoke_runner` (the blocking Popen) as the Tier-0/1 path for gemini /
`runner_cmd` / `--bare` and as the fallback for stream-capable runners. Route to
the streaming driver only for a profile that opts in.

- **Opt-in flag.** A profile field, e.g. `stream: claude` or `stream: codex`,
  names the streaming dialect. Absent → today's path, unchanged. This keeps the
  most load-bearing surface (every run) safe and the new path behind a switch.
- **`StreamingRunner`** (new module, e.g. `runner_stream.py`):
  - Claude: Popen with the stream-json flags; stdin kept open; `_active_proc`
    registered so `kill_active()` (budget/shutdown) still works unchanged.
  - Codex: Popen with `--json`; stdin is closed, the prompt rides argv, and a
    terminal fold-in resumes the emitted `thread_id` once.
  - A reader loop parses both schemas: Claude `assistant`/`user`/`result` and
    Codex `item.started`/`item.completed`/`turn.completed`. Detect a **tool
    boundary** as Claude tool result completion or Codex command item completion.
  - At each Claude boundary, if `change_token` moved, render the delta via
    `hooks.format_delta` and write it as a user message on stdin. At each Codex
    command boundary, touch the shared `.flush` signal; there is no live stdin
    channel, so inbound user follow-ups are handled at terminal resume.
  - Pending user follow-ups (foldable events) get relayed **as the user's own
    words**; operational meta stays informational.
  - Terminal result (`result` for Claude, `turn.completed` after Codex
    `agent_message`) → capture as the response (same `response_path` contract as
    Tier 1).
- **Reuse, don't fork:** `hooks.format_delta` / the capsule renderer is
  mechanism-neutral; the streaming path and the gemini hook path render
  the same capsule. Don't duplicate the rendering.

## Sequencing (each step shippable, firing-tested before the next)

1. **stream-json client module** — Popen + event parser + tool-boundary detector,
   tested against recorded event fixtures (capture a real session's stdout once,
   replay in tests). No injection yet. Proves brr can drive the loop and read it.
2. **Persistent session + inject at the boundary** — ✅ **shipped at module level**
   (evt wcxs). `build_stream_cmd` strips `--print`/`-p`; `run_stream` binds a stdin
   `Injector` to the boundary/result seams; the default `StreamInjectionPolicy`
   does `change_token`-gated delta injection at each tool boundary and, at each
   terminal `result`, folds a still-pending event once (stop-control) else closes
   stdin. Reuses `hooks.format_delta`. The maintainer (evt wcxs) confirmed the
   reframe behind this: **stdout result-capture is a compat fallback, not the
   delivery model** — the resident already delivers via the outbox, and the daemon
   already accepts that (`_result_satisfied_delivery` counts `outbound`/`commit`/
   folded-in replies, not just stdout). So this step's *purpose* narrowed to the
   one thing the maintainer still wanted: reliably deliver pending events to the
   resident mid-run without it polling `inbox.json`. **Deferred to step 3:** outbox
   *drain* at the boundary (the heartbeat already drains outbound, and the in-process
   drain is daemon-coupled), and relaying a folded event's **body verbatim** as the
   user's words (the policy injects the portal *delta* — summaries — today; the full
   verbatim relay wants the event body the daemon holds). Framing rules from the
   spike still apply (relayed follow-ups as the user's words; operational deltas
   informational).
3. **Flag claude onto it + daemon wiring** — ✅ **shipped**. `stream: claude` in the profile; route
   `_invoke_with_heartbeat` to `run_stream` when `stream_flavour` is set (keeping the
   heartbeat/budget/`kill_active` contract); run a real daemon wake through it; confirm
   a mid-thought follow-up is perceived without a poll. Fold in the step-2 deferrals
   here: in-process outbox drain at the boundary, and relaying a folded event's body
   verbatim (the daemon holds the event body) rather than only the portal summary.
3b. **Flag codex onto JSONL streaming** — ✅ **shipped**. `stream: codex` in the
   profile; `build_stream_cmd` adds `--json`; `consume_stream` understands
   Codex `thread.started`, command `item.*`, `agent_message`, and
   `turn.completed`; `run_stream` flushes on command completion and resumes the
   thread once for a pending follow-up. Live probes validated the schema and a
   real `codex exec --json` run.
4. **Retire the claude pull-reliance** — once stable, the heartbeat poll becomes
   claude's *fallback*, not its primary inbound channel. Revisit `.keepalive` and
   the tail-injection capsule (both were blocked on "claude has no push channel"
   — now unblocked) per the portal-reshape synthesis.

## Risks & open questions

- **Most load-bearing surface.** Every claude run. Mitigated by the opt-in flag
  and keeping `invoke_runner` untouched; the streaming path earns the default only
  after dogfooding.
- **Event-schema drift.** stream-json is a Claude Code surface that can change
  across versions; the parser must degrade safely (unknown event → ignore, never
  crash the run) and the result-capture must not depend on optional fields.
- **Pure-reasoning stretches** have no tool boundary, so no injection seam there —
  acceptable (nothing urgent waits mid-reasoning; the terminal boundary still
  catches the closeout, the Stop-equivalent fold-in check).
- **Stop-control** (block a premature stop when a foldable event waits): the
  stream gives a natural seam (don't close stdin while a pending event is
  unhandled, inject the nudge instead). Design parity with the hook `Stop` phase;
  detail at step 2.
- **Cost** is not a new concern: the per-tool-call halt already happens in
  `--print`; the 5-min prefix cache keeps respawns ~0.1×. Stream-driving rides
  boundaries that exist anyway.

## See also
- [`design-runner-back-channel.md`](design-runner-back-channel.md) — the concept
  (boundary injection) and why claude's mechanism is stream-driving, not hooks.
- [`design-portal-grammar.md`](design-portal-grammar.md) — parent portal design.
- The resident's `portal-reshape-synthesis.md` (dominion) — perception=injection
  lineage; the tail-injection capsule + `.keepalive` relocation unblocked by this.
