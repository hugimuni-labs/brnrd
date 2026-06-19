# Plan: the resident's cockpit — runner control & a dwelling that feels live

Status: active on 2026-06-17 (G4 firehose cut + G5 shipped; G1.1 medium
surfacing, opt-in diffense defaults, and repo-level conversational
dwelling framing shipped via the cost braid; G1.2–G3 and the dominion-side
G4 dwelling habits still proposed). The cost/notification braid is split out into
[`plan-cost-aware-cockpit.md`](plan-cost-aware-cockpit.md).

> This page began on 2026-06-16 from a tight, token-budgeted wake that
> landed *after* its predecessor died on runner-medium
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
**run decomposition / delayed execution**, and the **cockpit reframe**
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
1. **Surface the medium in the wake context.** *(G1.1 shipped 2026-06-17
   on `brr/cost-aware-cockpit`.)* The Mode block now carries a read-only
   `Runner: <medium>` line so a thought can see which runner/model it
   runs on. The first A2 ingress shipped 2026-06-18: a conservative
   quota snapshot can now ride the same line as
   `Runner: <medium> (<quota posture>)`; provider-specific collectors
   are the remaining pickup. The cost lens this enables is detailed in
   [`plan-cost-aware-cockpit.md`](plan-cost-aware-cockpit.md).
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
  proposal (decomposition, chosen media, historical cost pre-analysis
  from comparable past runs, never a projected dollar promise) the human
  can approve with a short reply. **(Tier A — shipped 2026-06-19** on
  `brr/plan-shape-148-tierA`: `src/brr/docs/portals.md` now defines the
  five-part PLAN message under *The PLAN message — the parked portal's
  shape*, and choreography step 3 points at it. This is the one concrete
  gap part 1 had on today's dotfile protocol — `portals.md` called
  PLAN→approve "the canonical parked portal" but never said what a PLAN
  looked like. No daemon machinery; ships independent of #128.)
- The approval reply wakes a run **scoped to that plan** — the plan rides
  in as context so execution doesn't rebuild from zero. **(Tier B — still
  open.)** Today the plan rides into the approval wake via the woven
  conversation turns + gate history + dominion (a *convention*, good
  enough to be useful now). First-class *scoping* — a marker the daemon
  threads from the plan event to the execution event — is the run/event
  model (#128) plus a small piece of daemon state. It sits on the
  behavioural slice (per-run claim + `defer_until`, Q1–Q4) and is best
  designed **after** #148 is dogfooded (the portal grammar reveals which
  parked portals actually recur — see
  [`design-portal-grammar.md`](design-portal-grammar.md) decision 4).

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
synthesis line — total branches, branches with unpushed commits, dirty
branches, current branches — and surface only the branches that are
*this run*, dirty, or have unpushed commits. This is the §4.2
firehose-vs-synthesis principle applied to the forge facet; §5 shipped
the facet but not its compression. Same medicine the kb-health and
recent-log blocks already take.

**Status:** the worktree half shipped on 2026-06-17. The wake prompt and
generated run context now summarize branch inventory and omit clean
pushed branches, while still listing the current/dirty/unpushed branches
that need attention. The issue/PR thread half remains uncompressed
because it is already small and the facet is network-free, so it does
not know live open/closed PR status.

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
**cockpit cheatsheet** so the control protocol is *looked up*, not
*memorized* — pushing it down the robustness ladder from "remember the
dotfile names" to "glance at the panel."

> **Correction (2026-06-16):** the cheatsheet's home is the **repo**, not
> the dominion (where this section first put it). See §G5 — generic
> cockpit knowledge belongs in a bundled prompt doc every adopter
> inherits; only *per-resident* state (the "what I'm carrying" note, my
> own habits) stays in the dominion.

**Home:** [`design-agent-dominion.md`](design-agent-dominion.md),
[`design-agent-ergonomics.md`](design-agent-ergonomics.md),
[`design-co-maintainer.md`](design-co-maintainer.md) §8.

## G5 — Unify the injection layer; ship the manuals as bundled, *inspected* docs

**Source.** Maintainer, 2026-06-16, on merging the cockpit framing:
"some things we should move out of your dominion to the repo, so that
other brr-managed repos can follow through this environment… we need some
actual how-to manuals of an average task execution workflow… maybe not
injected but inspected, maybe injected, it is your call… a braided
framing that makes everything meaningful." This is the cockpit's *prompt
side* — what gets folded into a wake and how — as opposed to G1–G4's
runtime and dashboard sides.

**The shape today.** A daemon wake is already assembled from layers
(`prompts.py` → `build_daemon_prompt` / `_join_prompt_parts`):
`run.md` + `daemon-substrate.md` (preamble) → dominion digest
(playbook + self-inject) → matched pitfalls → recent-log tail → kb-health
→ mode toggles (diffense, introspection) → the Run Context Bundle (the
per-run delivery contract). The layering is real and mostly clean
(`plan-agent-orientation-layering.md` did the first pass). Two problems
remain:

1. **The same mechanics are re-explained in three voices.** The
   outbox / keepalive / `.card` / `gate:` / `schedule.md` protocol is
   narrated in `daemon-substrate.md`, again in the Run Context Bundle's
   delivery contract, and gestured at in the playbook. Each wake pays for
   all three. That's the "layer to unify."
2. **There is no average-workflow manual at all.** Nothing tells a wake
   the *shape of a normal daemon run* — receive an event → orient → decide
   plan-vs-execute → (if plan) emit a PLAN and schedule the approval
   wake → (if execute) do the work, narrate via `.card`, deliver →
   decompose / defer the rest via `schedule.md`. The protocol primitives
   exist; the *choreography* is folk knowledge re-derived each wake.

**Direction (the calls I'd make, pending the nod):**

- **Generic cockpit knowledge → the repo, not the dominion.** A new
  bundled prompt doc — working name `prompts/cockpit.md` — is the
  one-screen cheatsheet + the average-workflow choreography, written
  laconic and agent-facing. Because it's bundled, every adopter's
  resident inherits it on `brr init`; a fresh dominion no longer has to
  re-grow the same habits. The dominion keeps only what is *this
  resident's*: the "what I'm carrying" note, accreted pitfalls, personal
  habits.
- **Inspected, not injected — with a one-line pointer injected.** The
  manual must *not* ride into every wake in full; that is exactly the
  firehose G4 cuts. Instead: surface it the way tool docs already are
  (`brr docs` / a `brr agent …` view), and inject a single pointer line
  ("the cockpit manual is at `brr docs portals` / `prompts/cockpit.md`;
  read it when the run's shape is unfamiliar"). Glance at the panel;
  don't memorize it. This keeps the robustness ladder honest: the *live
  state* (medium, quota, this run's branches) is injected; the *manual*
  is one glance away.
- **Deduplicate the protocol to one canonical home.** The delivery
  mechanics get *one* authoritative description — the Run Context Bundle
  stays the per-run *values* (paths, budget, this event's ids), and the
  *protocol prose* collapses into the cockpit doc that the bundle points
  at. `daemon-substrate.md` keeps only the substrate facts that aren't
  per-run (single-flight, capture net, schedule semantics). The win is
  fewer tokens and one voice instead of three.

**Why bundled-and-inspected is the braided answer.** The maintainer's
"neuromancer in ascii" / Talos-Principle instinct is that the wrapping
layers should feel like *instrument panels of one cockpit*, each
meaningful, none a wall of noise. A manual you inject in full is a wall;
a manual you can *summon* is a panel. Bundling it in the repo is what
makes the cockpit a shared environment other brr-managed repos inhabit,
not a private apartment each resident furnishes alone.

**Home:** `prompts/cockpit.md` (new bundled doc),
`prompts/daemon-substrate.md` + `prompts.py` (dedup + pointer injection),
[`plan-agent-orientation-layering.md`](plan-agent-orientation-layering.md)
(the layering this extends),
[`design-co-maintainer.md`](design-co-maintainer.md) §4.2 (firehose vs.
synthesis — the same principle, applied to the manual).

**Status:** shipped on 2026-06-16 (maintainer nod: "implement the whole
5"). What landed, all three calls:

- **`docs/cockpit.md`** — the bundled, agent-facing cockpit manual: a
  control-file cheatsheet (outbox replies, `event:` / `gate:` sends,
  `.keepalive`, `.card`, `inbox.json`, `schedule.md`) plus the
  average-run choreography (receive → orient → decide plan-vs-execute →
  narrate → deliver → decompose/defer). It lives in **`docs/`, not
  `prompts/`** — the page's "working name `prompts/cockpit.md`" was
  tentative; *inspected, not injected* means the docs system, which
  already exists for exactly this (bundled + per-repo overridable).
- **`brr docs` CLI re-introduced.** Implementing "inspected via `brr
  docs`" surfaced standing drift: the command was swept away in the
  2026-05-01 "remove agent commands from git" batch, yet
  `decision-bundled-docs.md`, `index.md` ("Run `brr docs` to list it"),
  and the docs module all still assumed it existed. Re-added `cmd_docs`
  (list + read a topic); the docs module needed no change.
- **One-line pointer injected; protocol deduped.** `daemon-substrate.md`
  now closes with a pointer to `brr docs portals` instead of the
  protocol being re-narrated; the Run Context Bundle's delivery contract
  was compressed to its per-run *values* + operative rules with a single
  "full protocol lives in `brr docs portals`" line. The bundle stays the
  per-run authority; the manual is the one conceptual home.

Remaining third voice: the **dominion playbook** still re-narrates the
same protocol (it's the resident's own memory, a `brr-home` commit, not
this repo branch) — trimming it to a pointer is the resident's follow-up.

## Prioritized sequence (token- and pain-aware)

Shipped:

- **G5 — `brr docs portals` + protocol dedup.** Cheap, high-leverage,
  adopter-facing: one bundled doc holds the cheatsheet + average-workflow
  choreography, inspected instead of injected.
- **G4 firehose cut — collapse the forge-state branch dump.** Saves
  tokens on every wake by replacing stale clean-branch listings with a
  summary plus only attention-worthy branches.

Next:

1. **G1.1 — surface the medium + quota in the wake bundle.** One line,
   read-only, enables everything else in G1.
2. **G1.2/1.3 — fallback chain + quota-aware deferral.** The fix that
   stops live runs from dying on exhaustion. Highest *pain* leverage;
   slightly more machinery, so second.
3. **G2 — plan→approve loop.** Tier A (PLAN message shape) shipped
   2026-06-19; Tier B (daemon-threaded plan→execution scoping) wants
   #128's run/event behavioural slice and the post-#148 portal grammar.
4. **G3 — decomposition via child events.** Defer behind #128.
5. **G4 dominion-side dwelling habits — keep a richer dominion surface
   and trim the resident playbook's remaining protocol re-narration now
   that repo prompts/docs frame `.card` and outbox use as normal
   substantial-work behaviour.**

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
