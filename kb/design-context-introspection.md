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

## See also

- [`design-environment-shaping.md`](design-environment-shaping.md) — the loop
  this is the interactivity-axis counterpart to; Ring 2 is where its findings
  land.
- [`design-agent-dominion.md`](design-agent-dominion.md) — the playbook +
  dominion are part of the "shape" this mode invites the agent to perceive.
- `prompts/introspection.md` — the invitation text itself.
