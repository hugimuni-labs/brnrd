# Plan: the resident's cockpit — runner control & a dwelling that feels live

> Status: proposed (2026-06-16). Written from a tight, token-budgeted
> wake that landed *after* its predecessor died on runner-medium
> exhaustion (Codex weekly quota empty → manual reroute to Claude → that
> too returned an operational error). The pain is the source: this page
> names what would have stopped the predecessor from dying silently, and
> what would make the resident's control surface feel like a workshop
> rather than a pile of dotfile conventions.

This is **not** a competing roadmap to
[`design-co-maintainer.md`](design-co-maintainer.md) §11 — that page owns
the continuity/delivery spine and most of it has shipped. This page adds
the dimensions the maintainer raised on 2026-06-16 that §11 doesn't
cover: **runner-medium selection**, a **plan→approve→execute loop**,
**task decomposition / delayed execution**, and the **cockpit reframe**
(reduce runner-sourced firehose, weave the dwelling/dashboard/terminal
into one legible surface). Each gap below is grounded in a lived symptom,
paired with the *smallest* fix, and pointed at its design home.

## G1 — Runner-medium selection & quota-aware fallback (the live wound)

**Symptom.** The previous two runs on this thread died operationally:
Codex's *weekly* agentic bucket hit 0% (5-hour bucket was 99% free — a
sub-quota of the weekly, not additive), then the Claude reroute returned
its own provider error. Recovery was *manual*: the human noticed,
re-routed the medium, re-sent the message. The daemon had no idea a
medium was exhausted, couldn't fall back, and couldn't defer to the known
reset (Codex resets Jun 17 01:29). A run just failed and a human paid the
latency.

**This is not [`plan-failover-compute.md`](plan-failover-compute.md).**
That page is *compute-host* failover — brnrd spawns when the user's
laptop daemon is offline. This is *medium* failover — the daemon is up,
but the chosen model/runner is out of quota or erroring. Different axis,
no design home yet. That gap is itself the finding.

**Smallest fix, in order of leverage:**
1. **Surface the medium in the wake context.** I should be able to see
   which runner/model this thought is running on (and ideally its
   remaining quota if the provider exposes it). Today the bundle never
   says. One line in the Mode block.
2. **A fallback chain.** Config a `runner_media: [codex, claude, …]`
   order; on an *operational* failure (the §6 `failed` signal with
   `runner_error`/quota class), the daemon retries the same event on the
   next medium before surfacing failure — turning a manual reroute into
   an automatic one.
3. **Quota-aware deferral.** When a provider reports a hard window reset
   (weekly bucket empty, resets at T), the daemon defers the event to T
   (a `defer_until`, which #128 already introduces) instead of burning a
   retry that will also fail. The human gets "deferred to Jun 17 01:29,"
   not a dead card.

**Home:** new short design page `design-runner-media.md` (or a § in
[`design-run-event-model.md`](design-run-event-model.md), since
`defer_until` and the per-run claim already live there).

## G2 — Plan → approve → execute (the duo loop)

**Symptom.** The maintainer wants "duo programming, not copilot." Today
the loop is implicit: I reply with a direction, the human reads it, sends
a *fresh* event that re-establishes context, and only then do I execute.
The handshake works but it's lossy — each turn rebuilds context from
cold, and there's no first-class "here is the plan, approve or edit it"
object.

**Smallest fix (convention-light, no heavy machinery):**
- A recognized **PLAN message shape** I emit to the outbox: a structured
  proposal (decomposition, chosen media, cost estimate) the human can
  approve with a short reply.
- The approval reply wakes a run **scoped to that plan** — the plan rides
  in as context so execution doesn't rebuild from zero. Mechanically this
  is the run/event model (#128) plus a marker the daemon threads from the
  plan event to the execution event. Mostly prompt + a small piece of
  daemon state, not new infrastructure.

**Home:** [`design-co-maintainer.md`](design-co-maintainer.md) §6/§11
(run↔reply decoupling) + [`design-run-event-model.md`](design-run-event-model.md).

## G3 — Task decomposition & delayed execution

**Symptom.** A big request ("holistic review + financial plan") is one
run that does everything or nothing. I can *self-schedule* (dominion
`schedule.md`) and #128 gives `defer_until`, but I can't decompose a
request into a tracked *set* of child runs, each possibly on its own
medium, executed on a cadence I choose.

**Smallest fix:** lean on what exists before building new. `schedule.md`
already lets me spawn future thoughts that thread together; #128 lets a
run read the whole inbox and decide what to tackle/fold/postpone. The
missing primitive is a run *enqueuing child events* for itself (a
decomposition writing N scheduled entries, each a sub-task). That's a
thin extension of self-scheduling, not a new subsystem. **Defer until
#128 lands** — it's the substrate; building decomposition first would
fight the run/event rename.

**Home:** [`design-run-event-model.md`](design-run-event-model.md) +
dominion `schedule.md`.

## G4 — The cockpit: less firehose, more dwelling

The maintainer's strongest framing: make the environment feel like a
*cybernetic entity's dwelling, workshop, and terminal* — not a wall of
injected runner state. Two halves.

**Cut the firehose (cheap, every-wake win).** The single biggest
injected-state offender in *this* wake's bundle is the **forge-state
branch dump**: ~38 worktrees/branches printed in full, almost all stale,
costing tokens on every wake for near-zero signal. Collapse it to a
synthesis line — "N branches, M with unpushed work, K with open PRs" —
and surface only the few that are *this task's* or have unpushed work.
This is the §4.2 firehose-vs-synthesis principle applied to the forge
facet; §5 shipped the facet but not its compression. Same medicine the
kb-health and recent-log blocks already take.

**Weave the dwelling (the "living" piece).** The pieces of a cockpit all
exist but are disconnected:
- the **dominion** is the dwelling/workshop (durable, mine) — but thin (4
  files) and I rarely *act from* it mid-task;
- the **`.card` seam** is the dashboard-back-to-human — but I almost
  never compose it, so the human sees daemon scaffolding, not me;
- the **outbox/`gate:`** is the terminal-out — powerful but a lot of
  dotfile+frontmatter convention to hold in working memory.

The fix is less "new feature," more **habit + legibility**: (a) compose
the `.card` as a matter of course so the human always sees a live,
self-authored status; (b) keep a richer dominion surface I read and write
each wake (a standing "what I'm carrying" note); (c) a one-screen
**cockpit cheatsheet** in the dominion so the control protocol is
*looked up*, not *memorized* — pushing it down the robustness ladder from
"remember the dotfile names" to "glance at the panel."

**Home:** [`design-agent-dominion.md`](design-agent-dominion.md),
[`design-agent-ergonomics.md`](design-agent-ergonomics.md),
[`design-co-maintainer.md`](design-co-maintainer.md) §8.

## Prioritized sequence (token- and pain-aware)

1. **G1.1 — surface the medium + quota in the wake bundle.** One line,
   read-only, enables everything else in G1. Ship first.
2. **G4 firehose cut — collapse the forge-state dump.** Cheap, saves
   tokens on *every* wake including this class of reflection. Pure win.
3. **G1.2/1.3 — fallback chain + quota-aware deferral.** The fix that
   stops live runs from dying on exhaustion. Highest *pain* leverage;
   slightly more machinery, so second.
4. **G2 — plan→approve loop.** Convention-light; unlocks the duo cadence
   the maintainer wants. Wants #128's run/event threading.
5. **G3 — decomposition via child events.** Defer behind #128.
6. **G4 dwelling habits.** Continuous, not a milestone — start now (this
   wake composes a `.card`; the cockpit cheatsheet lands in the dominion).

## Read next

- [`design-co-maintainer.md`](design-co-maintainer.md) — continuity &
  delivery spine; §11 is the master roadmap this page extends.
- [`design-run-event-model.md`](design-run-event-model.md) — the
  `defer_until` / per-run-claim substrate G1.3, G2, and G3 all lean on.
- [`plan-failover-compute.md`](plan-failover-compute.md) — *compute-host*
  failover; explicitly the sibling axis to G1's *medium* failover, not
  the same thing.
- [`design-agent-dominion.md`](design-agent-dominion.md),
  [`design-agent-ergonomics.md`](design-agent-ergonomics.md) — the
  dwelling/back-channel G4 weaves together.
