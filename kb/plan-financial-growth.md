# Plan: financial growth — no-investor, duo-run, greedy-but-honest

Status: proposed, not yet accepted

Owner constraints this plan is built around: **no investors, full control**,
HugiMuni SAS (operator + spouse running business operations), the resident
agent as the third pair of hands, and a target of **thousands of €/month
within months, growing** — funding bigger ambitions (voice-centric realtime
agent, own CLI, own models) from revenue, not from a raise.

Inputs: the locked business substrate
([pricing](decision-pricing-shape.md), [billing](design-billing.md),
[licensing/defense](decision-licensing-and-defense.md),
[websites](decision-websites.md)), the shipped-vs-designed state of
[managed mode](subject-managed-mode.md), the
[managed-gates launch plan](plan-managed-gates-launch.md), and the GitHub
tracker (release readiness #23, post-release roadmap #69).

## The honest math first

The accepted launch pricing is deliberately floor-priced, not sacred:
$5 (supporter cohort) → $7/month plus metered credits. Because brr/brnrd
often sits on top of a user's existing agentic CLI subscription (or their
free arrangement with one), the base tier should stay easy to add until
real usage says otherwise. Under the current shape, "thousands per month"
means **300–500 subscribers** — per the sustainability table, ~$1,850/mo
net at 500 subs, ~$4,300/mo at 1,000. Subscriptions alone cannot produce
thousands in the *next few* months because the thing being subscribed to
(brnrd managed gates + billing + minimal dashboard) is accepted-but-not-
started, ~2–3 months of critical-path work.

So the plan stacks **three revenue streams with different time constants**
instead of waiting on the slowest one:

1. **Bridge revenue (weeks, not months)** — high-ticket, low-volume,
   no product dependency: concierge setup + sponsorship + founding
   pre-orders. Target: first €1–3K while brnrd is being built.
2. **The subscription engine (months 2–4)** — the locked $5/$7 + credits
   shape, exactly as accepted. Target: 200-supporter cohort filled →
   ~$1K MRR floor, growing toward 500.
3. **Premium expansion (months 4+)** — a higher-priced solo/power-user
   tier *above* the launch shape (see "Tension surfaced" below). This is
   where "greedy" actually lives; $5/$7 is a funnel, not a business
   ceiling.

## Stream 1 — bridge revenue (start immediately)

- **Concierge resident installation** — "We move a resident agent into
  your repo: daemon, playbook, kb seeding, Telegram remote, one week of
  tuning." Flat €250–500 per repo, invoiced by HugiMuni SAS (B2B services
  invoice — no Stripe dependency, no product dependency, cash in days).
  The dogfooding *is* the qualification; every gig produces a testimonial
  and a case study video. Cap at a few per month so it funds, not
  consumes, the product work.
- **GitHub Sponsors + founding pre-orders.** Open Sponsors on day one of
  the OSS launch. Then sell the supporter cohort *forward*: a €50
  founding annual pass (the already-locked $50 annual supporter price,
  prepaid) that converts to a brnrd subscription at launch. 50–100
  pre-orders = €2.5–5K cash months before the platform charges its first
  metered credit, and it pressure-tests demand with real money.
- **What this stream is not**: a consulting company. It exists to bridge
  and to harvest stories; it sunsets as MRR crosses it.

## Stream 2 — the subscription engine

Ship the already-accepted critical path, in this order, resident-driven:

1. **#52 Stripe France + Qonto + Stripe Tax + OSS scheme** (operator-side,
   zero code, do it *now* — it gates the first euro and has admin lead
   times).
2. **Managed gates Slice 1** (GitHub App adapter + brnrd backend) →
   **Slice 2** (Telegram bot) per [the launch plan](plan-managed-gates-launch.md).
3. **Stripe integration** (#53) + credit ledger (#54) per
   [billing](design-billing.md).
4. **Minimal dashboard** (the eight-view MVP, already substrate-started
   with the brnrd web UI work).
5. **brnrd.dev** signup + pricing page (#60), brr.dev static landing (#32).

The 200-supporter scarcity mechanic is the launch story: count it down
publicly ("173 of 200 founding seats left") — it's already designed for
exactly this.

## Stream 3 — premium expansion (solo-first greedy part)

**Tension surfaced, per stewardship:** the accepted pricing decision prices
at the floor *on purpose* (defense: hard to undercut), and the operator's
feedback refines the constraint: $5/$7 is not a grail, but raising the base
too early taxes users who likely already pay for the agent CLI brr rides on.
A fast-greedy goal still pulls against a floor-priced single tier. The
resolution is **not** to make the launch tier expensive by default — it is
to keep the base tier adoption-friendly until metrics say otherwise, then
open a premium layer above it once the engine runs:

- **Solo Pro / Resident tier (~$19–49/mo, exact price later)**: positioned
  for the solo developer with multiple repos/residents or heavier cadence —
  priority compute, a larger included credit grant, longer audit/dominion
  retention, the agentic-secretary behaviours as they ship, and early access
  to voice/realtime experiments. Prices the recurring relationship, not the
  dispatcher.
- **Duo/team layer**: promising, but intentionally not the next product
  promise. It needs real multi-human routing, permissions, shared audit
  views, seat/account billing, and UX decisions before the pricing can be
  more than hand-waving. Keep it as a later decision, not something this
  plan silently smuggles into the solo-first launch.

This needs its own decision page when the time comes; this plan only
stakes the claim that the path to "thousands, growing" probably runs
through higher ARPA once solo value is proven, not through more $7 seats
alone.

## The marketing engine

The positioning research already names the wedge; what's missing is
production cadence. The single strongest asset this project owns is the
**meta-story: brr is built by its own resident.** PRs on the repo are
authored by the agent; the human reviews and merges. That is the viral
hook — show it, don't claim it.

- **The hero demo (60–90s, phone-first)**: Telegram message → live
  progress card updating → PR link → merged. Film vertical for
  Shorts/TikTok/X, horizontal cut for the landing page. This is the
  highest-leverage single artifact (per positioning research: "10×
  landing impact").
- **The series**: weekly "my repo has a resident" clips from *real*
  dogfooding moments — the resident fixing its own daemon, scheduling
  its own wakes, reviewing a PR via diffense. Authenticity beats
  production value; bold wording over polish: "Stop prompting. Start
  delegating." / "I texted my repo. It shipped."
- **Channels, in order**: Show HN (once README #27 + demo are ready —
  one shot, don't waste it), X build-in-public thread cadence,
  r/selfhosted + r/LocalLLaMA (the self-hosted angle is the wedge
  audience), lobste.rs, YouTube Shorts. Discord (#31) as the catch
  basin.
- **Launch order matters**: OSS launch *first* (free, builds the
  funnel and the star count that drives the 1–3% conversion
  assumption), brnrd launch *into* that audience 6–10 weeks later with
  the founding-seat countdown.

## Division of labour (the duo-programming loop, made explicit)

**Operator (irreducibly human):**
- Week 0–2: #52 (Stripe/Qonto/Tax/OSS scheme), register brnrd.dev,
  GitHub App + brr-bot account (#66), org decision (#34).
- Film the demos (the phone-in-hand shot can't be delegated); be the
  face on HN/X; answer early adopters fast.
- Review and merge resident PRs **daily** — merge latency is the loop's
  heartbeat; a resident whose PRs sit for a week is a copilot again.
- Sell and deliver the concierge gigs (with resident assistance).

**Operations (spouse / HugiMuni):**
- Qonto, invoicing for bridge revenue, deferred-revenue bookkeeping
  (per billing design), quarterly OSS VAT returns, trademark trigger
  watch (12mo / €10K / first competitor — per licensing decision).

**Resident (me):**
- Ship the critical-path slices as scheduled work: managed gates →
  Stripe → dashboard. The single-flight loop is the constraint; the
  operator's merge cadence is the throughput lever.
- Draft every launch artifact: README rewrite (#27), landing copy,
  HN post, X threads, demo scripts/storyboards, docs.
- A **weekly metrics wake** (schedule.md entry): stars, signups, MRR,
  cohort count, churn, credit consumption — one Telegram card, trends
  not snapshots, flagging the pricing-tuning metrics the billing design
  says to instrument from day one.
- Issue triage via the GitHub `opened` trigger; keep #23 release
  tracker current.

## 90-day shape

- **Weeks 1–2**: README + hero demo + brr.dev landing; Stripe/Qonto
  filings start; Sponsors open. → first bridge €.
- **Weeks 2–3**: Show HN + channel push. Concierge offer live.
- **Weeks 3–8**: managed gates Slices 1–2 + Stripe + ledger; founding
  pre-orders open mid-window once GitHub App pairing demos end-to-end.
- **Weeks 8–12**: brnrd.dev live, founding-seat countdown, weekly video
  cadence holds. Target at day 90: **€2–4K cumulative bridge + pre-order
  revenue, 50–150 paying founders, MRR engine switched on.**
- **Months 4–6**: fill the 200 cohort (~$1K MRR floor), open the Solo
  Pro / Duo decision from measured usage, push toward 500 subs (~$3K+/mo
  net) — the level where the bigger ambitions start funding themselves.

## Risks worth naming

- **Single-channel launch risk**: if Show HN fizzles, the funnel
  assumption (1–3% of star traffic) starves. Mitigation: the video
  series is the durable channel; HN is a spike, not the plan.
- **Builder's trap**: 2–3 months of brnrd plumbing with zero public
  output. Mitigation: the launch order above front-loads the free
  audience build; the resident ships while the operator markets.
- **Pre-order trust**: charging before the platform exists requires
  explicit "converts at launch, refundable until then" wording —
  operations owns the refund policy.
- **Floor-price lock-in**: if the premium solo/duo layer never
  materialises, revenue asymptotes near ~$5K/mo at 1,000 subs — healthy,
  but not the stated ambition. The premium layer is load-bearing, not
  optional, for "growing into voice agents and own models."
