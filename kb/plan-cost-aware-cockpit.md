# Plan: cost-aware execution & an operator-legible control loop

Status: active on 2026-06-17. First slices shipping on
`brr/cost-aware-cockpit`. This page is the **cost/notification braid**
of [`plan-resident-cockpit.md`](plan-resident-cockpit.md) — it does not
replace that page's G1–G5; it threads them through one lens the
maintainer articulated on 2026-06-17: *keep the user extremely aware of
plan, execution flow, and costs, and give them operational control,
while the resident learns to chunk its own work under a hard budget.*

> **Source pain (the grounding).** Two consecutive wakes on this thread
> died operationally: Codex's weekly agentic bucket hit 0%, the manual
> Claude reroute returned its own error, and a human had to notice,
> re-route, and re-send. Compounding it: the resident is **blind to its
> own cost** mid-run (no medium, no quota, no spend in the wake bundle),
> the **user is blind to the plan and the running cost** until a reply
> lands, and the **inbox/acknowledge loop is undocumented to the user**
> — they don't reliably know there *is* an inbox to act on, where it is,
> or how to acknowledge. The maintainer frames the fix not as a feature
> bolt-on but as *enablement of the resident's inner constitution*:
> seeing cost the way the maintainer sees it, and structuring work to
> survive a tight budget without compromising the work itself.

## The shape: three coupled loops

The ask decomposes into three loops that share one substrate (the
run/event model + self-scheduling + the outbox/`.card` seam). None is
new infrastructure; each is a thin, legible layer on what exists.

### Loop A — Cost self-awareness (the resident sees what it spends)

The resident cannot chunk work to a budget it cannot see. Today the
wake bundle carries a wall-clock `Budget:` line but **nothing about the
compute medium or its quota/spend**. Slices:

- **A1 (ship now) — surface the medium in the Mode block.** One
  read-only line: `Runner: <medium>` (e.g. `claude`/`codex`/`gemini`).
  `runner_name` is already resolved at prompt-build time
  (`daemon.py` → `resolve_runner`, emitted in `run_started`); it just
  isn't threaded into `build_daemon_prompt`. Smallest possible step,
  enables everything below. *(= plan-resident-cockpit G1.1.)*
- **A2 — surface remaining quota / reset window when the provider
  exposes it.** Codex publishes weekly + 5h buckets; Claude/Anthropic
  expose rate headers. A best-effort probe (vantage-rule clean: it's a
  host fact the sandboxed agent can't see) adds
  `Runner: codex (weekly 0% — resets 2026-06-17T01:29Z)`. When a bucket
  is near-empty, that line *is* the signal to chunk smaller or defer.
- **A3 — a per-run spend estimate.** Even coarse (tokens in/out × the
  medium's published rate, or the runner's own usage line if it emits
  one) lets the resident reason about cost, not just time. Feeds Loop C
  and the user-facing card.

### Loop B — Quota-aware survival (runs stop dying on exhaustion)

This is plan-resident-cockpit **G1.2/G1.3**, restated as the
load-bearing reliability slice:

- **B1 — fallback chain.** `runner_media: [codex, claude, …]` in config;
  on an *operational* failure classed as quota/runner-error (the §6
  `failed` signal), the daemon retries the same event on the next medium
  before surfacing failure. Turns the manual reroute into an automatic
  one.
- **B2 — quota-aware deferral.** When a provider reports a hard window
  reset, defer the event to T (`defer_until`, introduced by #128) rather
  than burning a retry that will also fail. The user sees "deferred to
  T," not a dead card.

Depends on the run/event model (#128) for `defer_until` and the per-run
claim. Until #128 lands, A1/A2 + the user-facing notification (Loop C)
already cut most of the pain by making exhaustion *visible early*.

### Loop C — Operator legibility & control (the user is never in the dark)

The maintainer wants the loop to feel like *duo programming*, with the
human holding operational control over plan, flow, and cost. Three
seams, all already present, under-used:

- **C1 — the `.card` as a standing cost+plan dashboard.** Compose it as
  a matter of course (plan-resident-cockpit G4 dwelling habit), and
  include the **cost frame**: which medium, rough spend/quota posture,
  and what phase. The user sees a live, self-authored status instead of
  daemon scaffolding. Cheapest, highest-frequency win.
- **C2 — a plan→approve handshake (G2) with a cost estimate.** Before a
  big or budget-risky run, emit a structured PLAN to the outbox:
  decomposition, chosen medium, **cost estimate**, and what each child
  run will do. The human approves/edits with a short reply; the approval
  wakes a run scoped to that plan (no cold-rebuild). This is the
  operational-control primitive — the human gates spend *before* it
  happens.
- **C3 — the notification & acknowledge contract (the missing manual).**
  The user does not have a clear, durable explanation of: *there is an
  inbox; here is where it lives; here is how to acknowledge / approve /
  redirect a pending plan or deferral.* Today the outbox/`inbox.json`
  machinery is agent-facing only. The fix is a **user-facing surface**:
  (a) a short notification the gate delivers when the resident is
  waiting on the user ("plan ready — approve, edit, or redirect; reply
  to this message"), and (b) a one-screen *operator* doc (sibling to the
  agent-facing `brr docs cockpit`) the notification points at. This is
  what makes the control real rather than implied.

## Budget-aware chunking — the resident's own discipline

This is the introspective core: a standing habit (encoded here and,
once stable, promoted into the cockpit manual + dominion playbook) for
executing under a tight budget without compromising the work:

1. **Read the cost frame first** (Loop A lines) at wake. If the medium
   is near a quota wall or the wall-clock budget is short, *plan to
   chunk* before starting.
2. **Commit-early, push-early** is the resilience primitive: the diff is
   the receipt that survives a kill mid-run. A plan page committed in the
   first minutes means the next wake picks up, not restarts. (This very
   run is the worked example.)
3. **Decompose into resumable slices**, each its own commit, sequenced
   so the *most resilient / highest-leverage* lands first (durable plan
   → read-only surfacing → reversible code).
4. **Defer the rest explicitly** — a `schedule.md` entry or a PLAN to
   the user — rather than racing the budget to do everything badly.
5. **Narrate the chunking in the `.card`** so the user sees *why* the
   work is staged, not a silent partial.

Not compromising: chunking is about *sequencing and resumability*, never
about shipping a thinner answer to beat the clock. The forcing function
is "what would the next wake be glad to find committed," not "what's the
least I can get away with."

## What this run ships (first slices, committed incrementally)

- **This plan page** — the resumable spine (committed + pushed first).
- **A1** — surface the runner medium in the Mode block (read-only).
- **Diffense de-firehose** — move the heavy *Publish-from-the-pack*
  plumbing out of the always-injected review-pack block into an
  inspected `brr docs` topic, leaving a compact operative summary +
  pointer. This is the same G5 "inspect, don't inject" medicine the
  maintainer asked for by name ("help yourself rid of it … my delivery
  contract still carries the full Publish-from-the-pack plumbing
  block"): it's manual-style choreography paid for on *every* diffense
  wake regardless of whether the run is review-worthy.

## Sequence after this run (pickup list)

1. **A2 — quota/reset probe** (vantage-rule clean; needs per-provider
   bucket parsing).
2. **C1 — `.card` cost frame habit** (cheap; partly a dominion-playbook
   change).
3. **C3 — operator notification + `brr docs operator` doc** (the
   user-facing acknowledge contract; the maintainer's explicit ask).
4. **B1/B2 — fallback chain + quota deferral** (highest reliability
   leverage; wants #128's run/event substrate).
5. **C2 — plan→approve with cost estimate** (the duo-loop primitive;
   wants #128 threading).
6. **A3 — per-run spend estimate** (feeds C1/C2; coarse first).

## Read next

- [`plan-resident-cockpit.md`](plan-resident-cockpit.md) — the parent
  cockpit plan; G1 (medium failover), G2 (plan→approve), G4 (dwelling)
  are the loops this page costs-and-notifications.
- [`design-run-event-model.md`](design-run-event-model.md) — #128's
  `defer_until` + per-run claim, the substrate B1/B2/C2 lean on.
- [`design-co-maintainer.md`](design-co-maintainer.md) — §11 continuity
  spine, §4.2 firehose-vs-synthesis (the diffense de-firehose applies it).
- [`plan-failover-compute.md`](plan-failover-compute.md) — *compute-host*
  failover, the sibling axis to Loop B's *medium* failover.
