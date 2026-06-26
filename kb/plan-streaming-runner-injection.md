# Plan: the streaming runner — claude Tier-2 boundary injection

Status: in flight 2026-06-26 (evt vtyq) — **step 1 shipped** (the stream-json client
module `src/brr/runner_stream.py`, 20 tests); steps 2–4 below remain. This is the
concrete build behind [`design-runner-back-channel.md`](design-runner-back-channel.md)
§Streaming-driven injection. Parent: [#159](https://github.com/Gurio/brr/issues/159),
[#171](https://github.com/Gurio/brr/issues/171).

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

## Architecture — a parallel runner path, not a rewrite of the old one

Keep `invoke_runner` (the blocking `--print` Popen) as the Tier-0/1 path for
codex / gemini / `runner_cmd` / `--bare` and as claude's fallback. **Add** a
streaming driver alongside it; route to it only for a profile that opts in.

- **Opt-in flag.** A profile field, e.g. `stream: claude`, names the streaming
  dialect (today only `claude`). Absent → today's path, unchanged. This keeps the
  most load-bearing surface (every run) safe and the new path behind a switch.
- **`StreamingRunner`** (new module, e.g. `runner_stream.py`):
  - Popen with the stream-json flags; stdin kept open; `_active_proc` registered
    so `kill_active()` (budget/shutdown) still works unchanged.
  - A reader loop parses events: `system/init`, `assistant` (may carry
    `tool_use`), `user` (carries `tool_result`), `result` (terminal). Detect a
    **tool boundary** = an assistant `tool_use` followed by its `tool_result`.
  - At each boundary, **in-process** (the driver runs in the daemon's TaskRunner
    thread — no separate `brr hook` subprocess): (a) drain the outbox via the
    existing daemon drain path (the threading-lock concurrency worry from the
    hook design dissolves — same process, same locks); (b) if `change_token`
    moved, render the delta via `hooks.format_delta` and write it as a user
    message on stdin. `change_token`-gate it so unchanged state injects nothing.
  - Pending user follow-ups (foldable events) get relayed **as the user's own
    words**; operational meta stays informational.
  - Terminal `result` → capture as the response (same `response_path` contract as
    Tier 1).
- **Reuse, don't fork:** `hooks.format_delta` / the capsule renderer is
  mechanism-neutral; the streaming path and the (codex/gemini) hook path render
  the same capsule. Don't duplicate the rendering.

## Sequencing (each step shippable, firing-tested before the next)

1. **stream-json client module** — Popen + event parser + tool-boundary detector,
   tested against recorded event fixtures (capture a real session's stdout once,
   replay in tests). No injection yet. Proves brr can drive the loop and read it.
2. **Inject + drain at the boundary** — wire outbox drain and `change_token`-gated
   delta injection; framing rules from the spike. Dogfood behind the flag on a
   throwaway branch.
3. **Flag claude onto it** — `stream: claude` in the profile; run a real daemon
   wake through it; confirm a mid-thought follow-up is perceived without a poll.
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
