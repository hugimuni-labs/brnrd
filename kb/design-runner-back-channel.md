# Design: the runner back channel (hooks) & the minimal runner interface

Status: proposed (2026-06-22), back channel verified against runner docs
(2026-06-22, see §Verification) — tracked by
[#171](https://github.com/Gurio/brr/issues/171). Supersedes the `brr portal
wrap` shell-wrapper slice of [`design-portal-grammar.md`](design-portal-grammar.md)
§Implementation sequence #2; the wrapper is retired when the hooks back channel
lands.

This page answers a request to bundle two things into one shape: implement a
**runner back channel** using the *hooks* mechanism that both Claude Code and
Codex CLI ship, and in the same move **retire `brr portal wrap`** and write
down the **minimal runner interface** brr actually depends on. It also folds in
a follow-up: how the resident updates the user mid-thought *without halting* the
run.

> Provenance: Telegram, 2026-06-22. The maintainer's framing: "keep the runners
> interface lean — currently we require it to be an agentic process that can
> read and write files on its own and (optionally) produce stdout output; now
> we are adding a back channel that both Codex and Claude support, so we should
> still define a general minimal clear interface. Hooks is just the right
> shape." Parent: [#159](https://github.com/Gurio/brr/issues/159).

## Why now

The first #159 slice shipped the live-state portal **pull-based**: the daemon
writes `portal-state.json` / `inbox.json` beside the outbox each heartbeat and
exposes discovery handles (`BRR_PORTAL_STATE`, `BRR_OUTBOX_DIR`, …) in the
runner env. The runner only sees fresh state if it *chooses* to read those
files. `brr portal wrap -- <command>` was the first push toward surfacing:
wrap a shell command, and the wrapper appends a portal-state delta to stderr
when the `change_token` moved.

`portal wrap` is the wrong long-term shape and the live dogfood confirmed it:

- It only fires around **shell tool calls the resident remembers to prefix**.
  Non-shell thinking (reads, edits, plain reasoning) never triggers it.
- It is **opt-in per command** — another protocol detail the resident must
  carry, exactly the footgun class the portal grammar is trying to remove.
- It is **one-directional**: it can show state *after* a command, but it can't
  flush a pending outbound message promptly, and it can't push fresh context
  *into* the run.

Both supported runners already expose the right primitive: **lifecycle hooks**.
Claude Code runs configured commands at `PreToolUse` / `PostToolUse` / `Stop` /
`Notification` / `SessionStart`; Codex CLI has the equivalent notify/hook
surface. A hook is a runner-native callback at tool/turn boundaries — automatic,
boundary-complete, and (critically) **bidirectional**: a hook's JSON result can
be injected back into the agent's context. That is the back channel.

## The minimal runner interface

The contract stays lean by staying **tiered** — each tier is optional enrichment
of the one below, and a runner that only satisfies Tier 0 still works:

| Tier | Capability | Used for | Required? |
| --- | --- | --- | --- |
| 0 | A process that, given the assembled prompt as its final argument, **operates files in its working directory** and exits with a status code. | All real work. The irreducible floor. | **Required** |
| 1 | Prints a **final reply on stdout** (progress/debug on stderr). | Plain current-thread delivery (`response_path` capture). | Optional |
| 2 | A **hooks back channel**: invokes a brr-provided callback at tool/turn boundaries and at stop, passing run context and consuming a JSON result. | Event-driven outbound flush, fresh-context injection, premature-stop control. | Optional |

The interface document of record is `src/brr/prompts/runners.md` (the runner
contract preamble) plus the profile frontmatter. Tier 2 adds a `hooks:` capability
to a profile — present for `claude`/`codex`, absent for `gemini`. A runner
without Tier 2 **degrades cleanly to today's heartbeat-polled model**: the
daemon keeps draining the outbox and refreshing `portal-state.json` on its
timer. Nothing about Tier 2 may become load-bearing for correctness, only for
latency and richness — that is what keeps the runner swappable.

## The back channel contract (transport-neutral)

brr exposes **one** hook endpoint, e.g. `brr hook <phase>`, reading a JSON event
on stdin and writing a JSON result on stdout. brr owns the abstract phases; each
runner profile maps its native hook names onto them. The phases brr cares about:

- **post-tool** (a tool call just completed) → the outbound flush point. brr
  drains the outbox and `.card` *immediately* instead of waiting for the next
  heartbeat tick, and, when `change_token` moved, returns a compact
  portal-state delta for the runner to inject as additional context.
- **pre-stop / stop** (the agent is about to end its turn) → final drain, plus
  the decision point for whether a still-pending, foldable event should block a
  premature stop (return a "you still have pending input" nudge) or let the run
  end. Stop-control is the richest affordance and may land in a later slice.
- **session-start / notification** (optional) → seed the run with the initial
  portal-state capsule, or relay a runner-side notification.

Two directions across that single endpoint:

- **Outbound flush** (runner → daemon): the hook tells the daemon "a boundary
  happened," letting delivery be **event-driven** rather than heartbeat-polled.
  This is what makes mid-thought replies land promptly.
- **Inbound injection** (daemon → runner): the hook's JSON result carries a
  fresh portal-state delta (new pending events, delivery acks, budget shifts)
  that the runner weaves into context — the INBOUND-CHECK portal becomes
  *automatic* instead of "remember to read `inbox.json`."

Per-runner mapping (brr generates the hook config from the profile, so the user
does not hand-write it):

- **Claude Code** — `settings.json` `hooks` block: `PostToolUse` → `brr hook
  post-tool`, `Stop` → `brr hook stop`, optionally `SessionStart` for the seed
  capsule. The endpoint emits **JSON on stdout (exit 0)**; the verified shapes:
  - *post-tool flush + injection* —
    `{"hookSpecificOutput": {"hookEventName": "PostToolUse",
    "additionalContext": "<portal-state delta>"}}`. This is **non-blocking**:
    the delta is woven in alongside the tool result and the turn continues.
  - *stop-control* — `{"decision": "block", "reason": "<pending input>",
    "hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": "…"}}`.
    `decision: "block"` prevents the stop and feeds the reason back so the turn
    continues with the pending event in view.
  - *session-start seed* — `SessionStart` is one of the three events where bare
    stdout is injected, so the capsule can be raw text.
  - **Mechanism caveat:** for `PostToolUse`/`Stop`, **plain stdout is debug-log
    only** — context injection requires the JSON `additionalContext` field.
    Only `UserPromptSubmit` / `UserPromptExpansion` / `SessionStart` inject bare
    stdout. The `brr hook` endpoint must therefore speak JSON for post-tool/stop.
- **Codex CLI** — the same lifecycle event set (`SessionStart`, `PreToolUse`,
  `PostToolUse`, `Stop`, `UserPromptSubmit`, …) mapped onto the brr phases.
  Codex hooks return JSON with `additionalContext` (injected into model
  context), `continue: false` (halt), `stopReason`, and `updatedInput`
  (PreToolUse rewrite). `PostToolUse` and `Stop` both carry context-injection
  **and** continuation control, so Codex is **not flush-only** — full Tier 2
  parity (this resolves the earlier "Codex parity" caution, below).

## Verification: hooks *are* a back channel into the runner (2026-06-22)

The load-bearing claim — hooks push fresh context *into* the running agent, not
just emit telemetry *out* — was checked against both runners' current docs at
the maintainer's request. **Confirmed, bidirectional, on both.**

- **Claude Code** ([hooks reference](https://code.claude.com/docs/en/hooks)):
  - `PostToolUse` accepts `hookSpecificOutput.additionalContext` for
    **non-blocking** feedback woven in alongside the tool result — exactly the
    `post-tool` inbound-injection path.
  - `Stop` accepts `decision: "block"` + `reason` (and `additionalContext`),
    which **prevents the stop and continues the turn** — exactly the
    premature-stop-control affordance.
  - `SessionStart` (and `UserPromptSubmit`/`UserPromptExpansion`) inject bare
    stdout into context — usable for the seed capsule.
  - `PreToolUse` can `deny` with a reason fed back to the model and can rewrite
    inputs — not needed for the first slice but available.
- **Codex CLI** ([codex hooks](https://developers.openai.com/codex/hooks)):
  the same event set with `additionalContext` injected into model context,
  `continue: false` to halt, and `updatedInput` to rewrite. Codex's own doc
  states hooks are "not fire-and-forget."

The fire-and-forget worry (hooks only good for "analytics and shit") is
**unfounded** for the runners brr targets. The design holds; the only
refinement the check forced is the mechanism caveat above (JSON
`additionalContext`, not plain stdout, for post-tool/stop).

## Writing to the user without a halt (the follow-up)

The follow-up asked whether updating the user mid-thought requires "an internal
runner's halt." It does not — and naming why sharpens the back-channel design:

A runner's only actuator is **emitting tokens / tool calls**. There is no
continuous side channel mid-sentence; the runner acts in discrete steps. But a
step is not a *halt of the run*. Today the resident updates the user by writing
a markdown file into its outbox with an ordinary `Write` tool call, then keeps
going — the run does not end, and the daemon's heartbeat drains that file to the
gate while generation continues. The **only** true halt is terminal stdout (the
run ends to deliver its final reply) and the parked PLAN→approve portal (which
*should* halt — approval needs a fresh wake).

So "let me update you while I work, without interruption" already exists; it is
the outbox. What it lacks is **immediacy** — delivery waits for the next
heartbeat tick — and a **reverse channel**. The hooks back channel fixes exactly
those: a `post-tool` hook flushes the just-written outbox file at the tool
boundary instead of on the timer, and the same hook can hand back fresh state.
No new "halt-free write" primitive is needed; the back channel *is* the answer
to the follow-up. (If we later want the resident to nudge the user without even
a tool-call boundary, that is a streaming-stdout-tap problem, separate and not
worth it now.)

## What `portal wrap` retirement removes

When Tier 2 hooks land, delete the wrapper and its prose — it is strictly
dominated:

- `brr portal wrap` subcommand + `cmd_portal_wrap` in `cli.py` (and its tests).
- The `brr portal wrap` paragraph in `src/brr/docs/portals.md` and the wrapper
  line in the Run Context Bundle wording (`prompts.py`).
- The portal-grammar implementation-sequence framing of #2 as a shipped
  wrapper slice (rewritten to: superseded by the hooks back channel).

**Keep** `brr portal state` — it stays useful as the inspected text view *and*
as the source the hook renders for injection. The retirement is about the
*manual wrapper*, not the state portal.

## Open questions

- **Hook security.** Hooks run commands on the host with the same trust as the
  bypass-approval runner already has. brr generates the hook config, so the
  command is brr's own endpoint — but document it under the trust model
  ([#80](https://github.com/Gurio/brr/issues/80)).
- **Config installation.** Auto-write hooks into the runner's `settings.json` /
  Codex config per run, vs. a one-time `brr init` step, vs. opt-in. Per-run
  generation keeps it self-contained and ephemeral (matches the worktree
  model); lean toward that.
- **Stop-control scope.** Blocking a premature stop is the highest-value, most
  intrusive affordance. Ship outbound-flush + inbound-injection first; gate
  stop-control behind a follow-up slice once the flush path is proven.
- ~~**Codex parity.**~~ *Resolved 2026-06-22:* Codex's hook surface covers
  `PostToolUse` and `Stop` with `additionalContext`/`continue`, so Tier 2 is
  full parity, not flush-only.

### Proposed resolutions (firmed 2026-06-22)

The maintainer asked to address the remaining unknowns now where the answer is
already clear rather than parking all of them. These are proposed (reversible,
in a design doc) so the implementation slice starts from a decision, not a blank:

- **`brr hook` JSON schema — proposed.** One transport-neutral envelope, brr
  owns the abstract phases, the runner profile maps native names on:
  - *stdin (event):* `{"phase": "post-tool|stop|session-start", "run_id": …,
    "event_id": …, "tool": {…}|null, "change_token": …}` — brr fills the bits
    its endpoint needs; runner-native fields are read from the raw hook payload
    that brr also passes through.
  - *stdout (result):* a neutral
    `{"inject": "<text|null>", "block": false, "block_reason": null}`, which the
    profile adapter renders into the runner's native shape — for Claude,
    `inject` → `hookSpecificOutput.additionalContext`, `block`+`block_reason` →
    `{"decision":"block","reason":…}`; for Codex, `inject` → `additionalContext`,
    `block` → `continue:false`+`stopReason`.
- **Config installation — proposed: per-run, ephemeral.** brr generates the hook
  config into the run's worktree-scoped runner settings each run, matching the
  worktree-per-run model; nothing is written to the user's global config, so it
  is self-contained and disappears with the worktree. (Drops the `brr init` /
  opt-in alternatives.)
- **Stop-control activation — decided: deferred behind the first slice.** Ship
  flush + inbound-injection first; Stop `block` lands in a follow-up once the
  flush path is proven (this matches the §back-channel-contract note).
- **Outbox→flush wiring — proposed: the hook *signals*, the daemon stays the
  sole drainer.** I checked the code before firming this: `_drain_outbox` in
  `daemon.py` is coupled to the daemon's in-process emit (`_WorkerEmit`) and
  conversation-log indexing, and the daemon's drain locks are `threading.Lock`
  (in-process only). So an external `brr hook` process **cannot** "run the same
  drain" directly, and it must not drain in parallel — the threading locks would
  not serialize a separate process, risking a double-delivery. The clean shape:
  `post-tool` drops a lightweight signal (touch a control file in the run dir, or
  poke a pipe the daemon selects on) and the daemon drains *immediately on that
  signal* instead of only on the next heartbeat tick. The daemon remains the
  single drainer, so **the concurrency worry dissolves** — no cross-process lock
  needed. Inbound injection is independent and safe to do in-process: the hook
  just reads the daemon-written `portal-state.json` and returns the delta. The
  remaining implementation choice is only the *signal transport* (control-file
  touch vs. pipe/socket); the touch-file matches brr's existing control-file
  idiom (`.keepalive`, `.card`) and is the leaning default.

## See also

- [`design-portal-grammar.md`](design-portal-grammar.md) — parent #159 design;
  this page is the runner-surfacing slice (its §Implementation sequence #2/#4),
  reshaped from shell-wrapper to hooks.
- [`src/brr/docs/portals.md`](../src/brr/docs/portals.md) — shipped control-file
  manual; loses the `portal wrap` paragraph on retirement.
- [`src/brr/prompts/runners.md`](../src/brr/prompts/runners.md) — the runner
  contract preamble + profiles; gains the Tier 2 `hooks:` capability.
- [`design-co-maintainer.md`](design-co-maintainer.md) §11 — continuity and
  delivery spine the back channel serves.
