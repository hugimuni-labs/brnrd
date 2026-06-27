# Design: the runner back channel & the minimal runner interface

Status: accepted on 2026-06-22; **unified on native hooks 2026-06-27** — the
managed streaming driver is retired and both Claude and Codex now reach the
boundary back channel through their native lifecycle hooks (`brr hook <phase>`).
The back-channel *machinery* shipped on `main` (#171/#175); `brr portal wrap`
retired 2026-06-23. Tracked by [#171](https://github.com/Gurio/brr/issues/171).
Superseded the `brr portal wrap` shell-wrapper slice of
[`design-portal-grammar.md`](design-portal-grammar.md) §Implementation sequence
#2 — that wrapper is deleted.

> ## Current shape (2026-06-27, after the rip-out)
>
> Boundary injection is **native hooks for every Tier-2 runner** — no special
> per-runner machinery. brr owns the abstract phases (`post-tool` / `stop` /
> `session-start`) and the neutral `{inject, block, block_reason}` result;
> `render_native` maps it to each flavour's fields. The install mechanism is the
> only thing that differs:
>
> - **claude** (`hooks: claude`) — per-run `.claude/settings.local.json` with
>   `PostToolBatch` / `Stop` / `SessionStart` → `brr hook <phase>`. Injection via
>   `hookSpecificOutput.additionalContext`; `Stop` `decision:block` continues the
>   turn. Fire-verified Claude Code 2.1.191.
> - **codex** (`hooks: codex`) — hook config injected as runner argv
>   (`-c hooks.<Event>=[…]`) + `--dangerously-bypass-hook-trust` (the project
>   `.codex/config.toml` install hung under repo-trust). `PostToolUse` / `Stop` /
>   `SessionStart`; same `hookSpecificOutput` envelope. Fire-verified `PostToolUse`
>   + injection on codex-cli 0.141.0.
> - **gemini** (`hooks: gemini`) — intent only; no emitter, no firing test yet.
>
> The `Stop` hook folds a still-pending foldable event into the same thought by
> relaying its **body verbatim** as the user's words (`hooks._fold_in_message`),
> the behaviour ported out of the deleted streaming driver. The env-contamination
> guardrail (`runner.clean_runner_environ()`) is the precondition that makes
> settings-file hooks reliable. `runner_stream.py` and its test are deleted; the
> plan that built them is [`plan-streaming-runner-injection.md`](plan-streaming-runner-injection.md)
> (abandoned 2026-06-27). The shorter lineage note below records how the
> streaming detour happened and was reversed; the rest of this page describes
> the current hook-based design.

> ## ⚠ Load-bearing correction — Claude hooks DO fire under `--print` (2026-06-27, evt o538)
>
> **The central empirical claim of this page was wrong, and the error was a
> contaminated test environment.** Direct firing tests on Claude Code 2.1.191
> and codex-cli 0.141.0 (haiku / gpt-5.4-mini) establish:
>
> - **Claude settings-file `PostToolUse` *and* `PostToolBatch` hooks fire under
>   headless `claude --print`**, and `hookSpecificOutput.additionalContext`
>   injection lands — the model read back a secret word injected through the
>   hook. The brr-exact config (hooks in `.claude/settings.local.json` +
>   `--setting-sources local`) fired cleanly.
> - **`Stop` `decision:block` continues the turn under `--print`** — a blocked
>   stop folded a follow-up instruction into the same turn (model output carried
>   the injected word). So `--print` is **not** "single-turn with no
>   stop-control"; the earlier *persistence correction* was also a contamination
>   artifact.
> - **Codex native `PostToolUse` hooks fire** (same `additionalContext` schema)
>   via inline-TOML `[hooks]` + `--dangerously-bypass-hook-trust`.
>
> **Root cause of the false negative:** the prior firing tests were run *from
> inside a Claude Code session* (the resident agent spawning `claude`). A Claude
> session exports `CLAUDE_CODE_SAFE_MODE=1`, and brr built the runner env with
> `os.environ.copy()`, so the child `claude` inherited safe mode — which
> **silently drops settings-file hooks** while logging the reassuring
> `safe mode disables plugins (managed settings-file hooks still run)`. The
> moment the contaminant is stripped from the child env, hooks fire. The whole
> "Claude's mechanism is stream-driving, not hooks" conclusion rests on that
> leak. (The same safe-mode the §two-firing-failures section already blamed once
> for the `--safe-mode` flag — it just came back via env inheritance.)
>
> **Decision (maintainer, evt o538): retire the managed streaming path and unify
> on the simple hooks injection protocol for Claude and Codex.** The streaming
> driver was an over-complication justified by a false negative. `PostToolBatch`
> is the right Claude seam (once per batch, sees all tool results before the next
> model call), exactly as proposed.
>
> **Landed 2026-06-27 (this evt):** the env-contamination guardrail —
> `runner.clean_runner_environ()` strips `CLAUDE_CODE_SAFE_MODE` and the
> parent-session identity vars from every runner subprocess env, so a daemon
> launched from inside an agent session can no longer silently disable runner
> hooks. This is the precondition for hooks-as-mechanism to be *reliable*.
>
> **Landed 2026-06-27 (the rip-out, evt 1tqp).** `claude` flipped to
> `hooks: claude` (post-tool → `PostToolBatch`), `codex` to `hooks: codex`
> (+ `--dangerously-bypass-hook-trust`). The Codex hook config is injected as
> runner argv via `hooks.codex_hook_args()` (`-c hooks.<Event>=[…]`, threaded
> through `RunnerInvocation.extra_runner_args`) rather than a settings file. The
> verbatim event-body fold-in is ported into `compute_neutral`'s `Stop` phase.
> `runner_stream.py` + `test_runner_stream.py` deleted; the `invoke_runner`
> stream-routing branch is gone. The codex hook schema was reverse-engineered via
> `codex exec --strict-config` probes — it mirrors Claude's
> `hooks.<Event>=[{hooks=[{type="command",command="…"}]}]` shape, with omitted
> matcher intentionally matching every supported occurrence; events are
> `PostToolUse` / `Stop` / `SessionStart` (no `PostToolBatch`). See the Current
> shape block at the very top of this page for the settled state.
>
**Concept vs mechanism — settled after the false negative.** Boundary injection
is the *concept* and the core of portals: at each runner boundary, brr flushes
the resident's outbound messages and, when attention-relevant state moved, weaves
fresh portal state back into context. The mechanism now used for Tier 2 is native
lifecycle hooks:

- **Claude** declares `hooks: claude`. brr writes a per-run
  `.claude/settings.local.json` that registers `PostToolBatch`, `Stop`, and
  `SessionStart` against `brr hook <phase>`. `PostToolBatch` is the post-tool
  seam because it fires once after a batch of tool results, before the next model
  call.
- **Codex** declares `hooks: codex`. brr injects the same hook config as runner
  argv (`-c hooks.<Event>=[…]`) because the project `.codex/config.toml` path
  hung behind repo trust. Omitting `matcher` in the inline config deliberately
  matches every supported event.
- **Gemini** declares `hooks: gemini` as intent only; no config emitter or firing
  test has shipped yet, so it degrades to the heartbeat-polled floor.

The earlier streaming driver was a detour caused by a contaminated firing test:
a parent Claude session leaked `CLAUDE_CODE_SAFE_MODE=1` into the child runner,
silently disabling settings-file hooks and making `claude --print` look
unhookable. Once `runner.clean_runner_environ()` stripped that inherited safe
mode, Claude `PostToolBatch` / `Stop` / `SessionStart` and Codex `PostToolUse`
fired. The streaming plan was abandoned on 2026-06-27 and
`runner_stream.py` / `test_runner_stream.py` were deleted; see
[`plan-streaming-runner-injection.md`](plan-streaming-runner-injection.md) for
the preserved lineage.

**Activation invariant.** brr only wires Tier 2 for a profile that explicitly
declares `hooks:`. It never infers hooks from the runner name, and a
`runner_cmd` override is honoured verbatim. The `hooks:` field records intent;
the runtime precheck records whether brr can install that flavour's config for
this run. Unsupported or failed prechecks fall back to heartbeat-polled outbox
drain and `portal-state.json` refresh, which preserves correctness without live
injection.

> **Ladder lesson.** Fire a runner mechanism before ruling on it. The same
> untested-firing habit first made hooks look dead, then made the streaming
> replacement look more necessary than it was. A cheap live firing test is the
> only thing that separates a real capability from a plausible doc reading.

**Still open (independent of the above):** `.keepalive` retirement, gated on the
no-timeout-for-Tier-0/1 behaviour (see §Retiring).

This page bundles two things into one shape: a **runner back channel** built on
runner-specific boundary mechanisms, and — in the same move — the retirement of
`brr portal wrap` plus a written-down **minimal runner interface** brr actually
depends on. It also resolves the follow-up: how the resident updates the user
mid-thought *without halting* the run.

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
| 2 | A **boundary back channel**: the runner exposes tool/turn seams through native lifecycle hooks. | Event-driven outbound flush, fresh-context injection, premature-stop control, operational meta-awareness. | Optional |

The interface document of record is `src/brr/prompts/runners.md` (the runner
contract preamble) plus the profile frontmatter. Tier 2 is declared by
`hooks:` in the runner profile; Claude and Codex are active, while Gemini is
declared intent until an emitter and firing test land.

**Tier 2 is not merely "latency and richness."** An earlier draft framed it that
way; the maintainer corrected it and the correction is load-bearing. The
boundary back channel is the substrate of a **holistically aware resident**: it
carries the operational meta — events arriving, execution time, accrued run
cost, funds and quotas available (combined and per-runner) — that lets the
resident run a *balanced proactive-and-reactive* flow instead of a purely
reactive one. A runner without it is not "the same offering, slightly less
responsive"; it is a
**different, thinner offering**. So Tier 2 stays non-load-bearing for
*correctness* (a Tier-0/1 runner still completes every task), but it is
load-bearing for the *class of product* brr can be on top of that runner.

**The lean case stays first-class — and should get easier.** A plain
"Telegram wrapper on top of a local CLI agent" (Tier 0/1, no boundary back
channel, no holistic awareness) must remain a fully supported shape, *more*
than today: the current mandatory `brr init` + KB setup + usage onboarding is
heavier than that case needs. The two ends are deliberate: a frictionless
reactive wrapper at one end, a fully self-aware resident at the other, with the
boundary back channel as the seam between them.

A Tier-0/1 runner **degrades cleanly to today's heartbeat-polled model**: the
daemon keeps draining the outbox and refreshing `portal-state.json` on its timer.

## The boundary back channel contract (transport-neutral)

The transport-neutral contract is a small set of **boundary phases**. brr owns
the phase semantics; each hook-backed runner maps its native lifecycle event
names onto `brr hook <phase>` (JSON event on stdin, JSON result on stdout).

The phases brr cares about:

- **post-tool** (a tool call just completed) → the outbound flush point. brr
  drains the outbox and `.card` *immediately* instead of waiting for the next
  heartbeat tick, and, when the mechanism supports live context injection and
  `change_token` moved, surfaces a compact portal-state delta for the runner to
  weave in.
- **pre-stop / stop** (the agent is about to end its turn) → final drain, plus
  the decision point for whether a still-pending, foldable event should block a
  premature stop (return a "you still have pending input" nudge) or let the run
  end. Ships with the first slice (see §Resolutions).
- **session-start / notification** (optional) → seed the run with the initial
  portal-state capsule, or relay a runner-side notification.

Two directions across the boundary seam:

- **Outbound flush** (runner → daemon): the mechanism tells the daemon "a
  boundary happened," letting delivery be **event-driven** rather than
  heartbeat-polled. This is what makes mid-thought replies land promptly.
- **Inbound injection** (daemon → runner): when the mechanism supports a live
  context channel, brr carries a fresh portal-state delta (new pending events,
  delivery acks, budget shifts, the operational meta above) that the runner
  weaves into context — the INBOUND-CHECK portal becomes *automatic* instead of
  "remember to read `inbox.json`."

- **Claude Code** — per-run `.claude/settings.local.json` `hooks` block:
  `PostToolBatch` → `brr hook post-tool`, `Stop` → `brr hook stop`, and
  `SessionStart` → `brr hook session-start`. The endpoint emits **JSON on stdout
  (exit 0)**:
  - *post-tool flush + injection* — `{"hookSpecificOutput": {"hookEventName":
    "PostToolBatch", "additionalContext": "<delta>"}}`. **Non-blocking:** woven
    in alongside the completed tool batch, turn continues.
  - *stop-control* — `{"decision": "block", "reason": "<pending input>",
    "hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": "…"}}`.
    `decision: "block"` prevents the stop and feeds the reason back.
  - **Mechanism caveat:** for `PostToolBatch` / `Stop`, **plain stdout is
    debug-log only** — injection requires the JSON `additionalContext` field
    (only `UserPromptSubmit` / `UserPromptExpansion` / `SessionStart` inject bare
    stdout). So `brr hook` must speak JSON for post-tool/stop.
- **Codex CLI** — runner argv carries `-c hooks.PostToolUse=[…]`,
  `-c hooks.Stop=[…]`, and `-c hooks.SessionStart=[…]` plus
  `--dangerously-bypass-hook-trust`. The inline TOML shape omits `matcher` to
  match every supported occurrence. Codex accepts `hookSpecificOutput` for
  additional context; `Stop` maps block to `continue: false` + `stopReason`.
- **Gemini CLI** — a richer event taxonomy mapped onto the same three phases:
  `AfterTool` → post-tool (context injection ✓, block-result ✓), `AfterAgent` →
  stop (retry/halt ✓), `SessionStart` → seed (✓); `BeforeTool`/`BeforeModel`
  carry rewrite/deny if ever needed. brr can render a Gemini-shaped block
  (`decision: "deny"` + exit 2), but no Gemini config emitter is installed yet.

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
tick) and a **reverse channel**. The boundary back channel fixes exactly those:
the runner boundary mechanism flushes the just-written outbox file at the tool
boundary, and, where supported, the same seam hands back fresh state. No new
"halt-free write" primitive is needed — the back channel *is* the answer.
(Nudging the user without even a tool-call boundary would be a
streaming-stdout-tap problem; separate, not worth it now.)

## Retiring `portal wrap` and the keepalive

Two control surfaces become redundant once the back channel lands.

**`portal wrap`** was strictly dominated — **deleted 2026-06-23** (commit on
`brr/retire-portal-wrap`), now that the boundary back channel is on `main`:

- `brr portal wrap` subcommand + `cmd_portal_wrap` in `cli.py` and its three
  tests — removed.
- The `brr portal wrap` paragraph in `src/brr/docs/portals.md` and the wrapper
  line in the Run Context Bundle wording (`prompts.py`) — removed; the docs now
  describe boundary-pushed surfacing (Tier 2) with a `portal-state.json` /
  `brr portal state` pull fallback.
- The portal-grammar implementation-sequence framing of #2 — rewritten to:
  superseded by the boundary back channel, wrapper retired.

**Keep** `brr portal state` — it stays useful as the inspected text view *and* as
the source the boundary renderer reads. The retirement is the *manual wrapper*,
not the state portal.

**The `.keepalive` budget-extension control file** remains a retirement
candidate, but it did not ship with the hook unification. It stays gated on the
no-timeout-for-Tier-0/1 behaviour and a replacement budget/cost channel:

- **Tier-2 runners** carry budget/cost/quota state *bidirectionally* through the
  back channel, so the resident already knows its standing and the daemon can act
  on a live signal — there is nothing left for a one-way "please don't kill me
  yet" file to do.
- **Tier-0/1 runners** can simply *not impose* a hard daemon timeout; bounding
  the run becomes the user's responsibility (as it effectively is for a plain
  local CLI agent). That removes the other reason `.keepalive` exists.

Either way the keepalive is a one-directional liveness hack. The likely end
state is still deletion, but until the no-timeout fallback and bidirectional
budget/cost surface are wired, `.keepalive` remains the working slot-control
portal.

## Verification: hook docs promised a back channel (2026-06-22)

The load-bearing claim — hooks push fresh context *into* the running agent, not
just emit telemetry *out* — was checked against each runner's docs and then
against live firing tests where available:

- **Claude Code** ([hooks reference](https://code.claude.com/docs/en/hooks)):
  `PostToolUse` / `PostToolBatch` accept
  `hookSpecificOutput.additionalContext` (non-blocking injection); `Stop`
  accepts `decision: "block"` + `reason` / `additionalContext` (prevents the
  stop, continues the turn); `SessionStart` can seed the run. Fire-verified
  under `claude --print` once parent safe-mode leakage was stripped.
- **Codex CLI** ([codex hooks](https://developers.openai.com/codex/hooks)): same
  event set with `additionalContext` injection, `continue: false` halt, and
  `updatedInput` rewrite. Its config accepts inline TOML `hooks.<Event>` arrays;
  omitted `matcher` matches every occurrence. `PostToolUse` +
  `additionalContext` is fire-verified on codex-cli 0.141.0; `Stop` and
  `SessionStart` are wired by the same emitter and should get their own live
  smoke when the next Codex quota window makes that cheap.
- **Gemini CLI** ([gemini hooks](https://geminicli.com/docs/hooks/)):
  `SessionStart` / `BeforeAgent` / `AfterTool` inject context; `AfterAgent`,
  `AfterTool`, `BeforeTool` can block/retry; `BeforeTool`/`BeforeModel` can
  rewrite input; blocking is `decision: "deny"` + exit 2. Exact injection-field
  names are in Gemini's hooks reference page, to be pinned at implementation.

## Resolutions (firmed 2026-06-22, second review round)

What the implementation slice starts from. These are reversible (design-doc
decisions), so the slice begins from a position, not a blank page.

- **`brr hook` JSON envelope — shipped.** One transport-neutral shape; the
  profile adapter renders it into each runner's native fields:
  - *stdin (event):* `{"phase": "post-tool|stop|session-start", "run_id": …,
    "event_id": …, "tool": {…}|null, "change_token": …}` — plus the raw hook
    payload passed through for runner-native fields.
  - *stdout (result):* neutral `{"inject": "<text|null>", "block": false,
    "block_reason": null}`. Claude: `inject` → `hookSpecificOutput.additionalContext`,
    `block`+`block_reason` → `{"decision":"block","reason":…}`. Codex:
    `inject` → `hookSpecificOutput.additionalContext`, `block` →
    `continue:false`+`stopReason`. Gemini: `inject` → its injection field (TBD from the
    reference page), `block` → `decision:"deny"`.
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
  reshaped from shell-wrapper to the boundary back channel.
- [`src/brr/docs/portals.md`](../src/brr/docs/portals.md) — shipped control-file
  manual; loses the `portal wrap` paragraph on retirement and now names the
  native-hook back channel.
- [`src/brr/prompts/runners.md`](../src/brr/prompts/runners.md) — the runner
  contract preamble + profiles; declares `hooks:` for Claude/Codex and Gemini.
- [`design-co-maintainer.md`](design-co-maintainer.md) §11 — continuity and
  delivery spine the back channel serves.
