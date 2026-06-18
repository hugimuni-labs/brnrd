# Plan: cost-aware execution & an operator-legible control loop

Status: active on 2026-06-17. First slices shipped on
`brr/cost-aware-cockpit`; opt-in review defaults and conversational
prompt framing shipped on `brr/cost-aware-conversation`. This page is the
**cost/notification braid**
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

## Maintainer steer — 2026-06-17 (refines this plan)

A follow-up on the same thread, sent on a deliberately tight (~$1) budget,
sharpened the direction. Five points, each folded into the slices below.

1. **Historical pre-analysis, never a forward cost *estimate*.** Quoting a
   projected dollar cost to a user is dangerous — a wrong money number
   erodes trust and reads as a promise the run never made. *Historical*
   data is categorically different and safe: "runs of this shape have
   cost ≈ X / consumed ≈ Y tokens" is a fact about the past, not a
   guarantee about this run. So everywhere this plan said "cost estimate"
   (A3, C1, C2), read **historical cost pre-analysis**: surface what
   comparable past runs actually consumed, framed explicitly as history,
   with no projected total presented as if it were owed. This is a hard
   product guardrail, not a presentation preference.

2. **No PR by default; cost-awareness is situational, not boilerplate.**
   `diffense.create_pr` defaulting on — and other *token-ignorant*
   defaults (always emit a review pack, always run the full publish
   choreography) — spend tokens regardless of whether the run warrants
   them. Flip the posture: PR / review-pack emission is opt-in or
   situational, decided from the run's shape and the cost frame, not done
   reflexively. More broadly the resident's cost-awareness should be
   *obvious and situational* — surfaced when it bites, not carried as
   standing ceremony on every wake. The prompt/config slice now defaults
   both `diffense.emit_pack` and `diffense.create_pr` off; repos opt in
   when the richer review surface is worth the prompt and forge work.

3. **Conversational & concurrent, not one-shot.** The single-flight
   *execution* model stays — it's mechanical truth — but the *framing*
   the resident reads should stop implying "one task → one terminal reply
   → silence." Lean into proactive, frequent communication: live
   status-card updates, mid-thought outbox sends, folding in queued
   events at plan boundaries, more than one message per wake. The aim is
   duo-programming texture, not a request/response servant. Concretely
   this means *softening* the "aim at one isolated run execution" wording in the
   delivery contract and dwelling habits toward "stay in the
   conversation." → amends Loop C and the cockpit dwelling habits.

4. **Temporal — evaluated, borrow the patterns not the engine** (below).

5. **Runner quota/pricing feasibility — confirmed plausible.** The
   maintainer reads these stats off the provider billing/usage pages, so
   A2/A3 have a real data source; the open question is API/CLI access,
   not existence. Concrete per-provider shape below.

### Temporal — durable-execution patterns, not the engine

The maintainer asked whether [Temporal](https://github.com/temporalio/temporal)
fits brr's IO layer — the gates (chat + the terraform-esque forge/GitHub
access) — and could reconcile them under one model.

What Temporal is: a *durable-execution* engine. You write workflows +
activities as ordinary code; Temporal persists every step, so a workflow
survives process death, retries failed activities with backoff, runs
durable timers (sleep for days), and resumes exactly where it left off.
It is, essentially, **the run/event model (#128) plus Loop B's
fallback/deferral, sold as a managed product.**

Where it's genuinely adjacent: Loop B (quota-aware retry / fallback /
deferral), `defer_until`, the per-run claim, surviving a kill mid-run —
all textbook durable execution. The conceptual fit is real and worth
learning from.

Where it does *not* fit brr's constraints:
- **Operational weight.** Temporal needs a server cluster (or Temporal
  Cloud — a paid third-party dependency). brr's ethos is no-investor,
  full-control, dependency-light, runs-on-a-laptop-or-one-fly-machine.
  Adopting it trades that for a stateful service to run, secure, and pay
  for.
- **Wrong layer / granularity.** brr's gates are thin IO adapters (poll
  Telegram, poll GitHub, write files). Temporal orchestrates long,
  multi-step *business workflows*; a gate poll isn't one. The
  "terraform-esque" forge reconcile is closer to a small control loop
  than a workflow DAG.
- **Single-flight is deliberate.** brr is intentionally serial per
  dominion (society-of-mind on shared memory). Temporal's value shines
  with high-concurrency orchestration — not brr's bottleneck.

Verdict: **borrow the patterns, not the dependency.** When #128's
run/event model needs durable retries, timers, and resumable state,
design it *as* a minimal durable-execution log (it already resembles
one) rather than importing Temporal. Re-evaluate only if brr ever runs a
fleet large enough that hand-rolled durability becomes the bottleneck —
then Temporal (or a lighter SQLite-backed state machine) re-enters as a
build-vs-buy question. Filed as a note, not a plan.

### Runner quota/pricing — where the data lives

A2/A3 need per-provider quota + spend; the maintainer confirms it's
visible on billing/usage pages, so this is about API/CLI access:
- **Codex / OpenAI** — the agentic weekly + 5h buckets the runner already
  hits; rate-limit state surfaces on 429s, and a usage endpoint holds
  historicals. Probe: parse the runner's own rate-limit error/headers
  (already in the §6 `failed` signal) for live quota; the usage endpoint
  for history.
- **Claude / Anthropic** — responses carry `anthropic-ratelimit-*`
  headers (requests/tokens remaining + reset); the Console exposes
  usage/cost. Probe: read headers off each call for live quota; the
  usage/cost endpoint for historical spend.
- **Gemini** — live quota via the Cloud quotas API; usage via Cloud
  billing.

Common shape: **live quota** comes cheap off response headers / error
bodies the runner already sees (Loop A2, vantage-clean as a host fact);
**historical spend** comes from each provider's usage/billing endpoint,
polled out-of-band and cached (this is the data behind the *historical
pre-analysis* of point 1). Neither needs a forward estimate. Next step is
a small per-provider spike to confirm the endpoints — not a design change.

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
- **A3 — a per-run *historical* spend analysis** (not a forward
  estimate; see the maintainer steer above). Coarse is fine — tokens
  in/out × the medium's published rate, or the runner's own usage line —
  but it is surfaced as *what this run actually consumed* and *what runs
  of this shape have historically cost*, never as a projected total. Lets
  the resident reason about cost, not just time. Feeds Loop C.

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
  include the **cost frame** when the bundle exposes one: which medium,
  quota posture, whether the work is being chunked for cost/resilience,
  and historical spend facts when A3 provides them. Never present a
  projected dollar total as a promise. The user sees a live,
  self-authored status instead of daemon scaffolding. Cheapest,
  highest-frequency win.
- **C2 — a plan→approve handshake (G2) with a historical cost
  pre-analysis.** Before a big or budget-risky run, emit a structured
  PLAN to the outbox: decomposition, chosen medium, **what comparable
  past runs have cost** (history, never a forward dollar promise — see
  the steer above), and what each child run will do. The human approves/edits with a short reply; the approval
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
   first minutes means the next wake picks up, not restarts. The first
   `brr/cost-aware-cockpit` slice was the worked example.
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

## Shipped slices

- **This plan page** — the resumable spine.
- **A1** — surface the runner medium in the Mode block (read-only).
- **Diffense de-firehose** — move the heavy *Publish-from-the-pack*
  plumbing out of the always-injected review-pack block into an
  inspected `brr docs` topic, leaving a compact operative summary +
  pointer. This is the same G5 "inspect, don't inject" medicine the
  maintainer asked for by name ("help yourself rid of it … my delivery
  contract still carries the full Publish-from-the-pack plumbing
  block"): it's manual-style choreography paid for on *every* diffense
  wake regardless of whether the run is review-worthy.
- **Opt-in review defaults** — `diffense.emit_pack` and
  `diffense.create_pr` now default off, so routine wakes do not pay the
  prompt / pack / forge tax unless the repo deliberately enables that
  review surface.
- **Conversational delivery framing** — the daemon substrate, Run
  Context Bundle, and cockpit manual now frame single-flight as an
  execution mechanic, not a one-shot reply contract: substantial work
  should keep the live card honest and use mid-thought replies when they
  help.
- **A2 prompt/snapshot ingress** — the daemon now has a conservative
  runner-quota snapshot contract (`.brr/runner-quota.json`,
  `runner.quota.*`, or `BRR_RUNNER_QUOTA_*`) and threads a proven summary
  into the Mode block as `Runner: <medium> (<quota posture>)`. This ships
  the run-visible surface without pretending provider-specific collectors
  exist yet: when no quota signal is present, the line stays compact.
- **Run-facing bundle language** — the generated prompt, recovery context,
  and bundled operator docs now frame the live unit as a daemon run/wake
  (`Run Context Bundle`, `Run ID`). The follow-up #128 rename slice now
  removes the legacy `task-...` id string and `.brr/tasks/` storage in
  favour of `run-*` ids and `.brr/runs/<run-id>/run.md` manifests.

## Sequence after this run (pickup list)

1. **A2 provider collectors** — populate the shipped quota snapshot from
   real Codex / Claude / Gemini signals (headers, errors, usage APIs, or
   a host wrapper) without adding slow network work to prompt assembly.
2. **C1 — `.card` cost frame habit in resident memory** (repo prompt
   framing shipped; the dominion playbook still needs the same trim).
3. **C3 — operator notification + `brr docs operator` doc** (the
   user-facing acknowledge contract; the maintainer's explicit ask).
4. **B1/B2 — fallback chain + quota deferral** (highest reliability
   leverage; wants #128's run/event substrate).
5. **C2 — plan→approve with a historical cost pre-analysis** (the
   duo-loop primitive; wants #128 threading).
6. **A3 — per-run historical spend analysis** (feeds C1/C2; coarse first).
7. **Finish the one-shot framing cleanup in the dominion playbook** —
   repo prompts/docs now say "stay in the conversation"; the resident's
   own playbook still carries some older protocol re-narration and should
   be trimmed to point at `brr docs cockpit`.

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
