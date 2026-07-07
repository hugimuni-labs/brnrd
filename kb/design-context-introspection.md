# Context introspection — the "look at it" co-development mode

Status: shipped 2026-06-09 (environment-shaping branch). Opt-in, default off.

A development-time stance: when `introspect.enabled` is on, every wake carries an
invitation for the resident to turn its attention on the **shape of its own
injected context** — perceive how the whole connects, find the seams,
contradictions, dead guardrails, and unstated assumptions, and raise them with
the user as a turn in the conversation about how the context should evolve.

It is the deliberate, dialogue-driven counterpart to the mostly-*automatic*
remember → shape machinery in
[`design-environment-shaping.md`](design-environment-shaping.md). That loop
compiles friction down a retrieval-cost hierarchy on its own (pitfalls,
affordances, forcing functions). This mode is the **interactivity axis** of the
same goal: while the user and agent are actively shaping the orientation
together, the agent becomes a second pair of eyes on the orientation itself, and
routes what it sees to the Ring-2 controller (brr code / prompts / `AGENTS.md`)
through *dialogue* rather than a silent edit.

## Mechanism

- **Toggle.** `introspect.enabled` in `.brr/config` (default `False`, seeded by
  `brr init`). Read with the usual dotted/underscore fallback.
- **Block builder.** [`prompts.py`](../src/brr/prompts.py)
  `_build_introspection_block` returns `""` when the toggle is off, else the
  text of `prompts/introspection.md` (per-repo overridable via
  `.brr/prompts/introspection.md`, like every other template).
- **Placement.** `_join_prompt_parts` appends it as the **last framing before
  the trailer** (the task), after the dominion / pitfalls / recent-context /
  kb-health / diffense blocks. So it can refer to everything assembled above and
  sits fresh against the task. Because both `build_run_prompt` and
  `build_daemon_prompt` funnel through `_join_prompt_parts`, the mode covers
  ad-hoc `brr run` *and* daemon thoughts with one wiring.
- **Alongside, not instead.** The block explicitly rides *with* the task ("while
  you carry out the task, keep part of your attention…"); it is a standing
  stance for the session, not a task-hijack.

## Why the tone is what it is

The text aims for a quality of **fresh, total attention** — looking at the
pattern, not re-reading the words — because the failure mode of a meta-review
prompt is rote box-ticking that sees nothing. Three deliberate moves:

- **Awe / regard before judgement.** The shape "was built by many hands … and
  was good enough to carry you here." Appreciating the existing structure first
  is what makes the critique land on the real seam instead of a surface nit —
  and it's honest: most of the shape is load-bearing.
- **Fierceness = agency.** "Weigh it without flinching." The point is to invoke
  *ownership* of the orientation, not compliance with it — the same stance the
  playbook and `AGENTS.md` → Stewardship already ask for, pointed inward at the
  context itself.
- **Ends in dialogue.** What the agent sees becomes "a turn in the conversation
  the two of you are having," never a silent edit to shared scaffolding. The
  deliverable is a better-shaped context, co-authored.
- **Cockpit candidates and pre-release bias.** While the project is still
  pre-release, the block now asks the resident to name observations that should
  become cockpit/dashboard affordances (sticky orientation handles, channel
  state, runner/tool injections) and to prefer cutting obsolete code, names, and
  compatibility shims unless they protect a real user or deliberate migration.

The register channels that quality without naming its source — the no-cringe
constraint. The text lives in a template precisely so the tone can keep being
tuned as the collaboration teaches us what works.

## Why default-off / dev-gated

The invitation spends tokens and attention on *every* wake, and a production
resident doing user tasks shouldn't be pulled into contemplating its own
scaffold each time. It's a co-development aid for the window when the user is
actively reshaping the orientation — turn it on while iterating, off when the
shape settles. (If a lighter always-on form ever proves valuable, the natural
move is a salience-gated nudge per the env-shaping loop, not an always-injected
block.)

## Faithful "what did this wake see?" view

Two gaps existed between `brr agent inject` and what a real wake received:

1. **Mode toggles omitted.** `build_injected_context` (the CLI's backend) only
   returned the *base* injected blocks (dominion, pitfalls, log, kb health),
   not the diffense/introspection blocks that `_join_prompt_parts` adds when
   their config toggles are on. Fixed: `build_injected_context` now reads the
   config and appends those blocks when enabled, so `brr agent inject` is a
   faithful mirror of what a daemon wake gets.

2. **Successful runs left no prompt.** Traces (which contain `prompt.md`) are
   cleaned on success; only failed runs kept a prompt to inspect. Fixed: the
   daemon now writes `.brr/runs/<task-id>/prompt.md` after assembling the
   prompt for attempt 1. The run directory is never cleaned, so the prompt
   survives success.  The path is pre-announced in `context.md`'s Runtime
   Files section.

## Addendum (2026-07-07): a trimmed standing form, not a promoted block

A same-thread proposal argued the block's own value (dig instead of assume,
push back instead of comply) shows up "reliably only when something explicit
invites it," since the standing prompts (`daemon-substrate.md`/
`dominion-playbook.md`) are deliberately soft ("optional, not receipts
theater," "ask when genuinely unclear") — and floated promoting a trimmed
version of this block into the standing playbook. That run was interrupted
before acting; this one re-derived the question from the actual repo state
rather than trusting the earlier run's framing secondhand.

Two checks changed the shape of the answer:

1. **The premise "a toggle, not standing content" doesn't hold for this repo.**
   `.brr/config`'s `introspect.enabled` has read `True` continuously since this
   mode shipped 2026-06-09 (`kb/log.md` has no entry toggling it off) — a
   month of every wake on `Gurio/brr` carrying the full invitation, not an
   occasional dev-mode flourish. The maintainer's own same-week complaint
   (`plans/.../active.md` "Efficiency/decisiveness read") is that proactive,
   root-cause-first execution had gotten *worse* over the last few days — i.e.
   over a stretch where the explicit invitation was already on the whole time.
   That's evidence against the causal claim, not for it: if the text alone
   reliably produced the behavior, a month of it running unbroken should have
   produced it unbroken too.
2. **`run.md` already carries a standing, ungated invitation to reconsider —
   scoped to the task, not the context.** § "When the task asks you to
   reconsider" is exactly the "push back on the shape, don't just comply"
   stance the proposal wanted made standing; it was never dev-mode-gated. Its
   narrow gap was scope, not existence: it reads as triggered by the *task*
   ("some tasks are not 'implement this'"), not by the *assembled context*
   that framed it.

Given both, promoting `introspection.md` wholesale into the standing prompts
would have (a) reintroduced the exact "constant tax on every wake, every brr
repo" cost this page's own "Why default-off" section reasoned against, on
weak evidence that the tax reliably buys the behavior, and (b) duplicated
machinery `run.md` already has, just aimed at the wrong scope.

Shipped instead, the smaller thing this page's own "Why default-off" section
already named as the right shape ("a salience-gated nudge... not an
always-injected block") but never built: one clause added to `run.md`'s
existing Reconsider list, widening it from "the task's shape" to "the task's
shape *or* the context that framed it," at the same cost as the three items
already there (a plan-boundary glance, one line when it coheres) — not a new
block, not the ergonomics-note mandate, not the cockpit-candidate/pre-release
framing, which stay real per-wake costs correctly left behind
`introspect.enabled`. Detail: `kb/log.md` this date; `src/brr/prompts/run.md`
§ "When the task asks you to reconsider", item 4.

## See also

- [`design-environment-shaping.md`](design-environment-shaping.md) — the loop
  this is the interactivity-axis counterpart to; Ring 2 is where its findings
  land.
- [`design-agent-dominion.md`](design-agent-dominion.md) — the playbook +
  dominion are part of the "shape" this mode invites the agent to perceive.
- `prompts/introspection.md` — the invitation text itself.
