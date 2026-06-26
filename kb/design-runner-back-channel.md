# Design: the runner back channel & the minimal runner interface

Status: accepted on 2026-06-22 — the back-channel *machinery* shipped on `main`
(#171/#175); `brr portal wrap` retired 2026-06-23. Tracked by
[#171](https://github.com/Gurio/brr/issues/171). Superseded the `brr portal wrap`
shell-wrapper slice of [`design-portal-grammar.md`](design-portal-grammar.md)
§Implementation sequence #2 — that wrapper is deleted.

> **Concept vs mechanism — the load-bearing correction (2026-06-26, evt ckc3).**
> An earlier framing fused two things under the word *hooks* and the fusion sent
> the design wrong. They are distinct:
>
> - **Boundary injection** is the *concept* and the **core of portals**, not a
>   tier-2 "add-on." It is the perception=injection rung: at each tool boundary
>   the resident's outbound messages flush *and* fresh portal state is woven into
>   its context — so responsiveness stops depending on the resident *remembering
>   to poll* `inbox.json`. (The lived failure this fixes: the longer a thought
>   runs, the more it drifts from procedures and forgets to check for the user's
>   follow-ups. Pull defeats drift; injection beats it.)
> - **Hooks** are *one mechanism* for boundary injection — the runner's own
>   lifecycle-callback feature. The mechanism is **runner-specific**:
>   - **codex / gemini** — their native hook systems (declared; firing still
>     unverified end-to-end — Tier 2 only after a live firing test).
>   - **claude** — **not** hooks. Claude Code's settings-file lifecycle hooks do
>     not fire under headless `claude --print` (isolated by elimination
>     2026-06-23). The working channel is **brr driving the stream**
>     (`--input-format stream-json --output-format stream-json`): brr owns the
>     message loop and weaves the portal delta in as a message at each tool
>     boundary. No harness hook needed — brr holds the loom. **Verified firing
>     2026-06-26** (see §Streaming-driven injection for claude).
>
> So the `claude` profile correctly declares **no `hooks:` field** (its mechanism
> isn't hooks), but that is *not* a demotion of the concept: claude's Tier-2
> boundary-injection path is real, just stream-driven. The barebones
> `--print` + Telegram-proxy case stays the honored Tier-0/1 **floor** (degrades
> cleanly to the heartbeat-polled model) — a supported fallback, not the ceiling.

**Why claude's mechanism is stream-driving, not hooks (the two firing failures).**
§Verification below confirmed hooks are a back channel *in the runners' docs* —
but a docs check is not a firing test, and for claude's settings-file hooks firing
broke, twice:

1. *First failure (2026-06-23):* the `claude` profile invoked `--safe-mode`, which
   sets `CLAUDE_CODE_SAFE_MODE=1` and disables hooks. Fixed by swapping to
   `--setting-sources local`.
2. *Second failure (2026-06-23, structural):* even with the local settings source
   and a provably-correct brr side (`brr hook post-tool` returns the right
   capsule, env handles present, `settings.local.json` generated), Claude Code
   v2.1.185 **still** never invokes the hook in `claude --print "<prompt>"` mode —
   no `.hook-state.json`, no injected delta. Isolated by elimination (a nested
   `claude --print` experiment with a sentinel-touching hook): not firing under
   any setting-source, not under a forced-trusted dir, not with `matcher:"*"` + a
   confirmed tool call, not under `--output-format stream-json` alone. Conclusion:
   settings-file lifecycle hooks do not run in headless `--print` mode.

So for claude, hooks are a dead mechanism — **but boundary injection is not.** The
working channel is brr **driving** the stream rather than relying on the harness:

## Streaming-driven injection for claude (verified 2026-06-26, evt ckc3)

The firing test the whole tier-2 frame rested on but no one had run: brr starts
claude in `--input-format stream-json --output-format stream-json`, keeps stdin
open, and after a tool boundary writes a *new user message* carrying the portal
delta. Result (spike, claude 2.1.191, nested in a temp dir):

- **Mid-loop injection fires and is attended.** The model perceives a message
  pushed between tool calls — it does not have to remember to poll. This is the
  drift fix, working, with **no Claude Code hook involved**: brr owns the loop.
- **Framing is a design parameter (injection-defense is real).** A delta framed
  as a coercive `"[brr portal update] INTERRUPT FROM THE DAEMON: you MUST …"` was
  perceived but **refused** — the model correctly treated it as a possible
  prompt-injection. The *same* content framed as the user's genuine relayed
  follow-up (`"oh and can you also mention … please, forgot to say"`) was
  **acted on**. Consequence for the build: relay user follow-ups as the user's
  own words; keep operational deltas (new-event-waiting, budget, SCM posture)
  **informational, not imperative** — they inform judgment, they don't command.

This vindicates "retirement without an alternative is too early": the alternative
exists and is now verified. It is also *truer* to perception=injection than hooks
were — with hooks brr borrowed the harness's callback; driving the stream, brr is
the loom that weaves the resident's scroll. Cost note: the per-tool-call halt is
already how tool-using agents step (5-min prefix cache keeps respawns ~0.1×), so
stream-driving rides boundaries that exist anyway — see §Halt vs respawn.

> **Persistence correction (2026-06-26, evt 8f8y — live-driven, not spiked).**
> Driving the step-1 module against a live haiku v2.1.191 session sharpened the
> mechanism: **`--print` stream-json is single-turn.** Mid-loop injection lands
> only while tool calls are still pending; once the model decides to finish, the
> process exits on the first `result` and any late stdin is dropped — so `--print`
> has **no stop-control**. A **persistent session (drop `--print`)** is multi-turn:
> after a `result`, a new user message starts a fresh turn the model addresses
> (verified — an `echo FOLD-INJECT` ran after the model had said "Done!"). So the
> two seams this design names — post-tool injection and Stop-control — are the
> *same* stdin-write mechanism in a persistent session, and the streaming runner
> must run persistent, not `--print`. The build plan
> ([`plan-streaming-runner-injection.md`](plan-streaming-runner-injection.md)
> §Driver re-verification) carries the concrete edits.

**Current state of the machinery.** The `claude` profile declares **no `hooks:`
field** — correct, because its mechanism is stream-driving, not hooks (the
`--setting-sources local` flag stays for settings isolation). The hooks machinery
— `src/brr/hooks.py`, the `brr hook` endpoint, `render_native`, the capability
precheck — stays for `codex`/`gemini`, whose hooks are **declared intent,
unverified end-to-end**: Tier 2 only after a live firing test. The
`hooks.format_delta` capsule renderer is mechanism-neutral and is reused by the
stream-driven path. **The streaming runner is built and default-on for claude**
(`src/brr/runner_stream.py`, profile flag `stream: claude`; shipped 2026-06-26,
see [`plan-streaming-runner-injection.md`](plan-streaming-runner-injection.md)):
`invoke_runner` routes a `stream:`-declaring profile to the persistent stream-json
driver, which strips `--print`, weaves a change-token-gated portal delta in at
each tool boundary, touches the `.flush` signal so the heartbeat drains the
outbox promptly, and at the terminal result folds a still-pending event's body in
verbatim (the inbound injection that the heartbeat-polled `--print` path could
not carry). The `--bare` aliases and `runner_cmd` overrides stay on the Tier-0/1
blocking path.

> **Ladder lesson.** `hook_capability()` was a rung-1 *assumption dressed as a
> check*: it asserts prerequisites (flavour renderable, endpoint on PATH, cwd
> writable) but not *firing*, so it reported Tier 2 while claude was silently
> Tier 0. Until a runner has a live firing test, a `hooks:` declaration is intent,
> not capability. **Corollary (the deeper miss):** the same untested-firing habit
> nearly cost the streaming path too — last wake *dropped* it as "a safety-net the
> reactive model never needed," reasoning from the same docs-not-firing footing,
> only inverted. The fix in both directions is the same: **fire it before you rule
> on it.** A 10-minute spike turned "drop the rewrite" into "the rewrite is
> justified and the mechanism is proven." Don't retire a capability on argument
> when a firing test is cheap.

**Still open (independent of the above):** `.keepalive` retirement, gated on the
no-timeout-for-Tier-0/1 behaviour (see §Retiring).

This page bundles two things into one shape: a **runner back channel** built on
the *hooks* mechanism every target CLI agent ships, and — in the same move — the
retirement of `brr portal wrap` plus a written-down **minimal runner interface**
brr actually depends on. It also resolves the follow-up: how the resident updates
the user mid-thought *without halting* the run.

> Provenance: Telegram, 2026-06-22, two rounds. Framing: "keep the runner
> interface lean — currently we require an agentic process that reads/writes its
> own files and (optionally) prints stdout; now we add a back channel that Codex,
> Claude *and* Gemini all support, so define a general minimal interface. Hooks is
> the right shape." Parent: [#159](https://github.com/Gurio/brr/issues/159).

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

Every target runner already exposes the right primitive: **lifecycle hooks** — a
runner-native callback at tool/turn boundaries, automatic, boundary-complete, and
(critically) **bidirectional**: a hook's JSON result can be injected back into the
agent's context. That is the back channel.

## The minimal runner interface

The contract stays lean by staying **tiered** — each tier is optional enrichment
of the one below, and a runner that only satisfies Tier 0 still works:

| Tier | Capability | Used for | Required? |
| --- | --- | --- | --- |
| 0 | A process that, given the assembled prompt as its final argument, **operates files in its working directory** and exits with a status code. | All real work. The irreducible floor. | **Required** |
| 1 | Prints a **final reply on stdout** (progress/debug on stderr). | Plain current-thread delivery (`response_path` capture). | Optional |
| 2 | A **hooks back channel**: invokes a brr-provided callback at tool/turn boundaries and at stop, passing run context and consuming a JSON result. | Event-driven outbound flush, fresh-context injection, premature-stop control, operational meta-awareness. | Optional |

The interface document of record is `src/brr/prompts/runners.md` (the runner
contract preamble) plus the profile frontmatter. Tier 2 adds a `hooks:` capability
to a profile — present for `claude` / `codex` / `gemini` (see §Verification).

**Tier 2 is not merely "latency and richness."** An earlier draft framed it that
way; the maintainer corrected it and the correction is load-bearing. The hooks
channel is the substrate of a **holistically aware resident**: it carries the
operational meta — events arriving, execution time, accrued run cost, funds and
quotas available (combined and per-runner) — that lets the resident run a
*balanced proactive-and-reactive* flow instead of a purely reactive one. A runner
without it is not "the same offering, slightly less responsive"; it is a
**different, thinner offering**. So Tier 2 stays non-load-bearing for
*correctness* (a Tier-0/1 runner still completes every task), but it is
load-bearing for the *class of product* brr can be on top of that runner.

**The lean case stays first-class — and should get easier.** A plain
"Telegram wrapper on top of a local CLI agent" (Tier 0/1, no hooks, no holistic
awareness) must remain a fully supported shape, *more* than today: the current
mandatory `brr init` + KB setup + usage onboarding is heavier than that case
needs. The two ends are deliberate: a frictionless reactive wrapper at one end,
a fully self-aware resident at the other, with hooks as the seam between them.

A Tier-0/1 runner **degrades cleanly to today's heartbeat-polled model**: the
daemon keeps draining the outbox and refreshing `portal-state.json` on its timer.

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
  end. Ships with the first slice (see §Resolutions).
- **session-start / notification** (optional) → seed the run with the initial
  portal-state capsule, or relay a runner-side notification.

Two directions across that single endpoint:

- **Outbound flush** (runner → daemon): the hook tells the daemon "a boundary
  happened," letting delivery be **event-driven** rather than heartbeat-polled.
  This is what makes mid-thought replies land promptly.
- **Inbound injection** (daemon → runner): the hook's JSON result carries a
  fresh portal-state delta (new pending events, delivery acks, budget shifts,
  the operational meta above) that the runner weaves into context — the
  INBOUND-CHECK portal becomes *automatic* instead of "remember to read
  `inbox.json`."

Per-runner mapping (brr generates the hook config from the profile, so the user
does not hand-write it):

- **Claude Code** — `settings.json` `hooks` block: `PostToolUse` → `brr hook
  post-tool`, `Stop` → `brr hook stop`, optionally `SessionStart` for the seed
  capsule. The endpoint emits **JSON on stdout (exit 0)**:
  - *post-tool flush + injection* — `{"hookSpecificOutput": {"hookEventName":
    "PostToolUse", "additionalContext": "<delta>"}}`. **Non-blocking:** woven in
    alongside the tool result, turn continues.
  - *stop-control* — `{"decision": "block", "reason": "<pending input>",
    "hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": "…"}}`.
    `decision: "block"` prevents the stop and feeds the reason back.
  - **Mechanism caveat:** for `PostToolUse` / `Stop`, **plain stdout is
    debug-log only** — injection requires the JSON `additionalContext` field
    (only `UserPromptSubmit` / `UserPromptExpansion` / `SessionStart` inject bare
    stdout). So `brr hook` must speak JSON for post-tool/stop.
- **Codex CLI** — the same lifecycle event set, mapped onto the brr phases.
  Hooks return JSON with `additionalContext` (injected), `continue: false`
  (halt), `stopReason`, and `updatedInput` (PreToolUse rewrite). `PostToolUse`
  and `Stop` both carry injection **and** continuation control, so Codex is full
  Tier 2, not flush-only.
- **Gemini CLI** — a richer event taxonomy mapped onto the same three phases:
  `AfterTool` → post-tool (context injection ✓, block-result ✓), `AfterAgent` →
  stop (retry/halt ✓), `SessionStart` → seed (✓); `BeforeTool`/`BeforeModel`
  carry rewrite/deny if ever needed. Field names: `decision: "deny"` + exit 2
  for blocking is documented; the exact injection-field schema lives in Gemini's
  hooks *reference* page (not yet pinned here — see §Resolutions → still open).

### What the delta carries by boundary (the injected capsule)

`hooks.format_delta` renders the portal-state payload into the injected text.
Three boundaries, two of which render **unconditionally** (added 2026-06-23,
maintainer's dogfooding feedback on run `…-1348-u62q`):

- **session-start (seed)** — the full initial capsule, always.
- **post-tool (mid-run)** — *gated* on `change_token`. Renders only when
  attention-relevant state moved (new pending event, delivery ack, budget
  shift); otherwise stays silent so editing churn injects no noise.
- **stop (closeout)** — renders **unconditionally**, not token-gated. Two
  reasons, both from the maintainer's point that *silence is ambiguous*:
  1. **Affirmative empty signal.** "Knowing there are no events explicitly is
     itself an agentic signal." A closeout header that says `0 pending
     event(s)` is a confirmation the resident can act on; silence is not. So
     stop always emits the header (`[brr portal closeout] …`), pending count
     included even when zero.
  2. **SCM posture (commit/push reminder).** The capsule carries an `scm`
     facet — `{known, branch, unpushed_commits, modified_files}`, computed
     locally and failure-safe from `worktree.unpushed_commit_count` /
     `uncommitted_file_count` against the run's worktree. Rendered at
     seed/stop only, and only when there is something to act on (unpushed
     commits or modified files), so a clean tree stays quiet. This is the
     fix for the lived gap that "the initial context doesn't stress pushing
     enough" — a wake about to end now *sees* "N commit(s) not pushed, M
     modified file(s)" as injected context. `scm` is deliberately **excluded
     from `change_token`** (like `elapsed_seconds`) so mid-run editing churn
     never trips a post-tool injection; it is a boundary signal, not a
     live-churn one.

## Halt vs respawn — two different concepts (the follow-up)

The follow-up asked whether updating the user mid-thought requires "an internal
runner's halt." It does not — and the maintainer sharpened *why* by separating
two words that were being used loosely:

- **Halt is an LLM-streaming concept.** Each tool call halts the underlying
  streaming API turn: the model stops, the tool runs, a *new* streaming turn
  starts with the result appended. With prompt caching this is cheap and routine
  — it is simply how tool-using agents step. It is inherent to the
  request/response tool-call architecture; changing it would take a different
  *streaming or model architecture* (e.g. a persistent bidirectional stream), and
  that is out of scope. Naming it just bounds the decision space: brr does not
  fight the per-tool-call halt, it rides the boundaries it creates.
- **Respawn is a brr-resident concept.** It is brr ending a thought and waking a
  fresh one — a deliberate run-lifecycle act, unrelated to the streaming halt.

Mid-thought user updates need **neither**. A runner's only actuator is emitting
tokens / tool calls; it acts in discrete steps, but a step is not a *run halt*.
Today the resident writes a markdown file into its outbox with an ordinary `Write`
call and keeps going — the daemon's heartbeat drains that file to the gate while
generation continues. The only true run-ending halts are terminal stdout (final
reply) and the parked PLAN→approve portal (which *should* halt — approval needs a
respawn).

So "let me update you while I work, without interruption" already exists; it is
the outbox. What it lacks is **immediacy** (delivery waits for the next heartbeat
tick) and a **reverse channel**. The hooks back channel fixes exactly those: a
`post-tool` hook flushes the just-written outbox file at the tool boundary, and
the same hook hands back fresh state. No new "halt-free write" primitive is
needed — the back channel *is* the answer. (Nudging the user without even a
tool-call boundary would be a streaming-stdout-tap problem; separate, not worth
it now.)

## Retiring `portal wrap` and the keepalive

Two control surfaces become redundant once the back channel lands.

**`portal wrap`** was strictly dominated — **deleted 2026-06-23** (commit on
`brr/retire-portal-wrap`), now that the hooks back channel is on `main`:

- `brr portal wrap` subcommand + `cmd_portal_wrap` in `cli.py` and its three
  tests — removed.
- The `brr portal wrap` paragraph in `src/brr/docs/portals.md` and the wrapper
  line in the Run Context Bundle wording (`prompts.py`) — removed; the docs now
  describe hook-pushed injection (Tier 2) with a `portal-state.json` /
  `brr portal state` pull fallback.
- The portal-grammar implementation-sequence framing of #2 — rewritten to:
  superseded by the hooks back channel, wrapper retired.

**Keep** `brr portal state` — it stays useful as the inspected text view *and* as
the source the hook renders for injection. The retirement is the *manual wrapper*,
not the state portal.

**The `.keepalive` budget-extension control file** should likely retire too, along
the same logic (maintainer's suggestion, 2026-06-22 — leaning yes, to be
confirmed when the slice lands):

- **Hook runners** carry budget/cost/quota state *bidirectionally* through the
  back channel, so the resident already knows its standing and the daemon can act
  on a live signal — there is nothing left for a one-way "please don't kill me
  yet" file to do.
- **No-hook (Tier 0/1) runners** can simply *not impose* a hard daemon timeout;
  bounding the run becomes the user's responsibility (as it effectively is for a
  plain local CLI agent). That removes the other reason `.keepalive` exists.

Either way the keepalive — a one-directional liveness hack — is dominated by the
bidirectional channel for hook runners and unnecessary for the lean case. Fold its
removal into this work rather than carrying it forward.

## Verification: hooks *are* a back channel into the runner (2026-06-22)

The load-bearing claim — hooks push fresh context *into* the running agent, not
just emit telemetry *out* — was checked against each runner's current docs.
**Confirmed, bidirectional, on all three.** (The "hooks are only good for
analytics" worry is unfounded for the runners brr targets.)

- **Claude Code** ([hooks reference](https://code.claude.com/docs/en/hooks)):
  `PostToolUse` accepts `hookSpecificOutput.additionalContext` (non-blocking
  injection); `Stop` accepts `decision: "block"` + `reason`/`additionalContext`
  (prevents the stop, continues the turn); `SessionStart` injects bare stdout for
  the seed. Caveat: post-tool/stop inject only via JSON `additionalContext`, not
  plain stdout.
- **Codex CLI** ([codex hooks](https://developers.openai.com/codex/hooks)): same
  event set with `additionalContext` injection, `continue: false` halt, and
  `updatedInput` rewrite; its own doc states hooks are "not fire-and-forget."
- **Gemini CLI** ([gemini hooks](https://geminicli.com/docs/hooks/)):
  `SessionStart` / `BeforeAgent` / `AfterTool` inject context; `AfterAgent`,
  `AfterTool`, `BeforeTool` can block/retry; `BeforeTool`/`BeforeModel` can
  rewrite input; blocking is `decision: "deny"` + exit 2. Exact injection-field
  names are in Gemini's hooks reference page, to be pinned at implementation.

## Resolutions (firmed 2026-06-22, second review round)

What the implementation slice starts from. These are reversible (design-doc
decisions), so the slice begins from a position, not a blank page.

- **`brr hook` JSON envelope — proposed.** One transport-neutral shape; the
  profile adapter renders it into each runner's native fields:
  - *stdin (event):* `{"phase": "post-tool|stop|session-start", "run_id": …,
    "event_id": …, "tool": {…}|null, "change_token": …}` — plus the raw hook
    payload passed through for runner-native fields.
  - *stdout (result):* neutral `{"inject": "<text|null>", "block": false,
    "block_reason": null}`. Claude: `inject` → `hookSpecificOutput.additionalContext`,
    `block`+`block_reason` → `{"decision":"block","reason":…}`. Codex: `inject` →
    `additionalContext`, `block` → `continue:false`+`stopReason`. Gemini: `inject`
    → its injection field (TBD from the reference page), `block` →
    `decision:"deny"`.
- **Config installation — brr-managed, per-run, with user overrides + a
  capability precheck.** brr generates the hook config into the run's
  worktree-scoped runner settings each run (matching the worktree-per-run model;
  nothing written to the user's global config, so it is self-contained and
  disappears with the worktree). Follow each runner's *native* config definition
  (a plain shell-command hook interface) so the shape is familiar, and let user
  overrides layer on top of brr's generated defaults. **A runner is only marked
  `hooks:`-capable once brr can confirm the per-runner-type prerequisites are in
  place** (settings file location writable, hook schema version, native config
  present) — capability is asserted after a presence check, not assumed from the
  profile name. (Drops the earlier `brr init` / opt-in alternatives.)
- **Stop-control — ships with the first slice.** An earlier draft deferred it
  behind a follow-up; the maintainer overrode that: blocking a premature stop is
  squarely in line with the flush/injection work and costs little extra on top of
  it, so ship outbound-flush, inbound-injection, **and** stop-control together
  rather than splitting the slice.
- **Outbox→flush wiring — the hook *signals*, the daemon stays the sole drainer.**
  Checked against the code: `_drain_outbox` in `daemon.py` is coupled to the
  daemon's in-process emit (`_WorkerEmit`) and conversation-log indexing, and the
  drain locks are `threading.Lock` (in-process only). An external `brr hook`
  process therefore **cannot** run the drain itself and must not drain in
  parallel (the threading locks won't serialize a separate process → double
  delivery). Clean shape: `post-tool` drops a lightweight signal (touch a
  control file in the run dir, matching brr's existing `.keepalive` / `.card`
  idiom — the maintainer confirms this is most in line with the current daemon
  design) and the daemon drains *immediately on that signal* instead of on the
  next heartbeat tick. The daemon stays the single drainer, so the concurrency
  worry dissolves. Inbound injection is independent and in-process-safe: the hook
  reads the daemon-written `portal-state.json` and returns the delta.
  - *Separate thread worth pursuing:* the control-file touch is the near-term
    fit, but the daemon has read as **sluggish** before. A more responsive daemon
    core (event-driven select/poll loop rather than heartbeat ticks, even at the
    cost of a substantial refactor) is a standalone improvement the signal design
    should not foreclose — worth its own investigation, not blocking this slice.
- **Hook security.** Hooks run host commands with the same trust the
  bypass-approval runner already has; brr generates the command (its own
  endpoint). Document under the trust model
  ([#80](https://github.com/Gurio/brr/issues/80)).
- **Still open at implementation time:** Gemini's exact injection-field name
  (pin from its hooks reference page); the precise per-runner prerequisite checks
  the capability gate runs; the `.keepalive`-retirement confirmation once the
  no-timeout-for-Tier-0/1 behaviour is wired.

## See also

- [`design-portal-grammar.md`](design-portal-grammar.md) — parent #159 design;
  this page is the runner-surfacing slice (its §Implementation sequence #2/#4),
  reshaped from shell-wrapper to hooks.
- [`src/brr/docs/portals.md`](../src/brr/docs/portals.md) — shipped control-file
  manual; loses the `portal wrap` paragraph (and likely the `.keepalive` budget
  paragraph) on retirement.
- [`src/brr/prompts/runners.md`](../src/brr/prompts/runners.md) — the runner
  contract preamble + profiles; gains the Tier 2 `hooks:` capability for
  `claude` / `codex` / `gemini`.
- [`design-co-maintainer.md`](design-co-maintainer.md) §11 — continuity and
  delivery spine the back channel serves.
