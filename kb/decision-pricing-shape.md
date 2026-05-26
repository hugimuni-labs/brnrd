# Decision: managed-mode pricing shape

**Status: proposed, not yet accepted on 2026-05-22; reshaped
multiple times (see Lineage). Current shape on 2026-05-26
(third-wave follow-up):** unnamed paid tier ("Subscribed") at
$5/month with 300 credits included, Free tier at 3 projects /
100 events / 5 credits — subscription for the platform +
metered credits for compute. The earlier "free dispatcher +
paid managed compute" framing turned out to be self-defeating —
see "What changed and why" below. Companion to
[`subject-managed-mode.md`](subject-managed-mode.md) (the
surfaces being priced),
[`design-brnrd-protocol.md`](design-brnrd-protocol.md) (the
per-task accounting hooks the model rides on), and
[`design-billing.md`](design-billing.md) (the subscription +
wallet + Stripe mechanics that implement the cost model).

## Decision

**Two tiers at launch**, with metered compute on top of either:

| Tier | Price | Projects | Events / month | Compute included | Dashboard | Audit retention | Support |
|------|-------|----------|----------------|------------------|-----------|-----------------|---------|
| **Free** | $0 | **3** | 100 | 5 spawn-credits ($0.05) | Basic (per-project, read-only views) | 7 days | Community (Discord, GitHub issues) |
| **Subscribed** | **$5 / month** *(or $50 / year, ~17% off)* | up to **10** | 10,000 | **300 spawn-credits ($3 of compute)** | Full (cost charts, permission-prompt customisation, cross-project view, project-binding UI) | 90 days | Email |
| **Compute overage** (both tiers) | $0.01 / credit (metered) | — | — | top-up via existing wallet | — | — | — |
| **Self-hosted brnrd** | $0 | unlimited | unlimited | self-paid cloud bill | full (your deployment) | self-defined | self-supplied |

**Team / per-seat tier** is deferred to v-next; solo subscription
+ Free are the launch shapes. Per-seat ships when the first real
team asks (Linear-shape pricing, ~$5/seat on top of the
subscription base).

**On naming**: the paid tier deliberately has no marketing name
(no "Plus" / "Pro" / "Premium"). UI and docs say
"Subscribed" / "Subscriber" / "Subscription tier." Tier naming
is a marketing decision that wants market data; doing it pre-
launch locks the wording before any user has bought it.
Reverse-merging a brand name in later is trivial; un-naming a
launched tier is painful.

## What the subscription unlocks — the "why I'd pay" features

Subscribing is **not just "Free with bigger numbers."** It
unlocks specific, named features that have real implementation
cost and real user value:

| Subscriber-only feature | Why it has cost / value |
|------------------------|------------------------|
| **Bigger project headroom** (10 projects, vs 3 on Free) | The dispatcher's multi-project resolution path (chat-binding + prefix override on TG/Slack/Discord, per-installation routing on GH App) is genuinely more code, more state, more support burden the more projects an account fans out across. Free's 3-project cap handles the "side project + day-job + scratchpad" case; the jump to 10 covers serious adopters. |
| **Full dashboard** (cost charts, cross-project view, permission-prompt config, project-binding UI) | More views = more build + maintenance cost. Free gets the read-only essentials; subscribers get the operational surface. |
| **Generous compute included** (300 credits = $3 of compute) | Removes the "do I have credits?" mental overhead for common use; covers ~100 spawns/month at typical task size. Heavy users still top up; light subscribers effectively never think about credits. |
| **10K events/month** (vs 100 on Free) | High enough that real subscribers effectively never hit the ceiling. Free's 100 is a try-it cap, not a usable production cap. |
| **90-day audit retention** | Compliance signal for users who care about post-hoc forensics. Free's 7 days covers debugging; 90 days covers "what happened in March?" |
| **Email support** | Real cost (someone reads + responds). Subscribers pay for it. |

The pitch reads as "I'm buying these specific things" rather
than "I'm buying past an artificial wall," which is the
difference between sustainable pricing and rent-seeking.

## What we charge for, what we don't

A clean line drawn explicitly so it shows up on the landing page:

- We **charge a small monthly fee for the platform** — the
  always-on bot infrastructure, multi-project routing, the
  dashboard, the audit log, the permission-prompt machinery.
  These have real fixed operational cost (Upsun + postgres +
  brnrd backend); a thin subscription covers them sustainably.
- We **charge metered cents for cloud compute** — failover
  spawns on brnrd's cloud account, beyond the included grant.
  Pass-through Fly Machines cost + small platform margin. Pay
  only when you actually use it.
- We **don't charge for AI usage** — your Anthropic / OpenAI /
  Google bill is yours, paid directly to them. We just relay
  the credential to the sandbox at spawn time.
- We **don't subscription-gate the OSS** — every brr feature
  works against a self-hosted brnrd; the subscription is for
  *hosted brnrd convenience*, not for the brr daemon
  functionality itself.

This framing reads as "platform + ops as a service" rather than
"SaaS layered on top of an OSS thing," which matches the actual
architecture and avoids "rent-seeking on free software" vibes.

## Event-cap overage — soft throttle, not metered

Hitting the event cap on either tier triggers a **soft throttle
+ notify**, not a metered overage charge:

- **Free** at 100/month: subsequent events queue with a gate
  reply "monthly event cap reached — subscribe, switch to
  self-hosted brnrd, or wait until <date> for monthly reset."
  Events resume at the next month boundary.
- **Subscribed** at 10K/month: realistically never hit by solo
  users. If hit, soft-throttle to ~1 event/sec with a
  "you're at the soft limit — email us, we'll raise it"
  notification. No metered event billing — feels punitive
  for a thing that's cheap to operate on brnrd's side.

Events should *feel* free / unlimited in normal use; the caps
exist for abuse / runaway-integration protection, not as a
revenue surface. The revenue surface is the subscription
itself.

## Why this shape

Four constraints shaped the decision:

1. **Non-VC-backed.** No "burn now, monetise later" runway.
   Every tier has to be either at-or-near zero marginal cost to
   operate, or revenue-positive per unit of usage. No
   subsidised growth.
2. **Everything is OSS self-hostable.** A user who doesn't like
   the pricing can fork brnrd and run their own. The pricing
   has to be honest enough that most users prefer hosted *not*
   because they can't self-host, but because operating it isn't
   worth their time.
3. **Adopter goodwill.** Launching with a clear "Free works
   for 3 projects; subscribe at $5/month when you want bigger
   limits + the full toolkit" split avoids the bait-and-switch
   perception that "free everything now, paid later" creates.
4. **Data minimization as trust signal.** "We don't store
   your code" makes hosted brnrd defensible vs self-hosted on
   security grounds. This belongs on the pricing/landing page
   as much as on the design page.

The platform-sub + metered-compute shape satisfies all four by
matching each billing stream to its cost shape:

- **Platform cost is mostly fixed.** A webhook receiver +
  postgres + a long-poll endpoint + dashboard infra costs
  on the order of $50-200/month at launch scale. Charging a
  small monthly sub to users who depend on it (bigger project
  headroom, full dashboard, customised permission flow,
  generous compute included) covers it sustainably without
  rent-seeking — it's a fixed cost matched to a fixed price.
- **Compute cost is variable and significant.** Per-second
  Fly Machines billing is the real per-spawn cost. The
  subscription includes 300 credits ($3 of compute) every
  month, which covers ~100 spawns at typical task size for
  most users without forcing them to think about top-ups;
  heavy users meter naturally past the included grant.
- **AI compute belongs to the user.** Anthropic / OpenAI /
  Google bill the user directly; brnrd doesn't intermediate
  that relationship.

## Sustainability math

Crude back-of-envelope at $5/month base + 300 credits included
+ ~30% of subscribers exceed the included compute (the heaviest
users top up via the wallet at $0.01/credit):

| Scenario | MRR (subscription only) | Compute revenue (over included) | Compute cost (Fly pass-through) | Infra cost | Net |
|----------|------------------------|--------------------------------|---------------------------------|------------|-----|
| 50 subscribers | $250 | ~$50 | ~$120 | ~$150 | **~+$30** |
| 200 subscribers | $1,000 | ~$200 | ~$500 | ~$300 | **~+$400** |
| 500 subscribers | $2,500 | ~$500 | ~$1,250 | ~$500 | **~+$1,250** |
| 1,000 subscribers | $5,000 | ~$1,000 | ~$2,500 | ~$800 | **~+$2,700** |

Model crosses sustained-net-positive around ~80 subscribers — a
credible threshold for "this project pays for itself + a small
honorarium to its maintainer" within the first year of public
availability. At 500+ subscribers the project is comfortably
funding itself plus paying real maintainer time.

The earlier credits-only model required ~10× the user count to
hit the same net because compute alone doesn't have enough
margin density to cover the platform's fixed cost. The
**friendlier $5 / 300-credit shape compared to a $9 / 500-credit
alternative** trades thinner per-subscriber margin for higher
expected conversion (the "I'll subscribe at $5 to support a
tool I use casually" psychological threshold is far below the
$9/$10 line). At equal subscriber counts the alternatives are
revenue-similar; the bet is that $5 with 300 credits converts
materially more users than $9 with 500.

## Subscription mechanics

Implementation detail lives in
[`design-billing.md`](design-billing.md). Headline contract:

- **$5/month**, billed monthly via Stripe recurring subscription
  (separate Stripe product from the credit-wallet one-shot
  top-ups). All EU compliance work from the credit-wallet leg
  (Stripe France, HugiMuni SAS, Qonto payouts, Stripe Tax,
  OSS scheme, SCA via Checkout) applies to the subscription
  product identically — Stripe handles both subscription and
  one-shot products under the same Stripe account.
- **Annual discount** option: $50/year = ~$4.17/month effective
  (~17% off). Saves Stripe per-charge fees (12 charges/year → 1)
  and gives users a small win.
- **Cancel anytime**, takes effect at the period end. No
  proration on cancellation (it's $5/month, the math is
  trivial); no claw-back of compute credits granted that month.
- **Upgrade / downgrade mid-month**: prorated. From Free to
  Subscribed mid-month grants the included compute prorated to
  the remainder of the month; cancel → Free drops the project /
  event caps at the period boundary (no nuking your existing
  projects mid-billing-cycle; the dashboard surfaces "you have
  N projects but Free allows 3 — pick which to keep" before any
  caps bite). Compute credits granted that month stay on the
  account through the period end.
- **Subscription state is account-scope** (per
  [`design-config-layout.md`](design-config-layout.md)): the
  daemon and brnrd-side spawn both read `subscription.tier`
  from the account-scope settings store to know which caps to
  apply.

## What changed and why (the pricing reframe)

The pre-2026-05-25-third-wave shape was: free dispatcher (with
~300 free credits/month covering ~100 free spawns) + metered
credits for everything else. Reshaped after the user surfaced
that:

> "I can't see me going over the limits and ever needing to
>  pay anything. We didn't discuss how do you get over the
>  limits for events. I think the pricing in its current shape
>  won't make this project successful."

Diagnosis: the earlier shape **mixed two billing models in
one wallet** (free credits cover events + spawns together) and
made the dispatcher — the genuinely-load-bearing, fixed-cost
piece of brnrd — entirely free. Active users wouldn't hit the
compute cap; casual users wouldn't hit anything; nobody would
pay; the project would starve.

The reframe separates the two cleanly:

- **Subscription for the platform** (events + dispatcher +
  bigger project headroom + dashboard + audit retention +
  included compute) — matched to its fixed cost shape.
- **Metered credits for compute** (failover spawns over the
  included grant) — matched to its variable cost shape.

The subscription's value proposition is a **bundle of real,
named features** — bigger project headroom, full dashboard,
generous included compute, longer audit retention, email
support — each of which has actual implementation or operational
cost. The "this is real for me" line is when you want the
toolkit + the breathing room across more than a handful of
projects, which is where the subscription pays for itself in
saved time vs operating a self-hosted brnrd.

The credit wallet stays — it just becomes the surface for
metered compute on top of the included grant, not the entire
billing model. The subscription is a new billing leg in
parallel.

## BYO compute — designed, deferred (not in launch pricing)

Earlier draft included BYO failover compute (user stores their
own Fly / Modal / Daytona / etc. token on brnrd; brnrd spawns
into the user's cloud account) as a free-tier feature. Dropped
from launch on 2026-05-25 because:

- ~30% more backend surface area (per-platform credential
  storage UI, scope validation, per-platform onboarding docs,
  per-platform failure modes, dispatcher branching on platform
  selection) — disproportionate to launch user value.
- ~5% of launch users care; the other 95% would rather paste
  an AI credential and let brnrd handle the spawn.
- Per-platform maintenance burden is unbounded.

Pricing implication is small: when BYO comes back post-launch,
it lands cleanly on the subscription as a power-user feature
(the user pays the platform sub for the dispatcher; their cloud
spawns hit their own cloud bill; brnrd doesn't charge for the
spawns themselves, just for routing).

Daemon-side cloud envs (a laptop daemon fans out to the user's
cloud via a first-party env extra like `brr[fly]` or a
third-party env registered via the `brr.envs` entry point)
remain independent of managed mode entirely — brnrd isn't in
that path, nothing to price.

## Alternatives considered

### Alt 1 — Credits-only (the previous shape)

Free dispatcher tier with ~300 free credits/month covering ~100
free spawns; metered credits beyond. **Rejected on 2026-05-25**
because:

- Active users self-select into "I'll just stay free" and the
  project never sees revenue from them.
- Mixes events + compute in one credit unit, making it impossible
  to distinguish "user is hammering the dispatcher" from "user
  burned compute on one big task" for pricing purposes.
- The "compute as the only paid surface" framing under-charges
  for the genuinely-load-bearing platform infrastructure.

### Alt 2 — Subscription for the platform with NO included compute

Subscription = $5/month, just unlocks bigger project headroom +
dashboard. All compute is metered, no included credits.
Rejected because:

- Users hate "the sub doesn't include anything tangible."
  Bundling some compute makes the subscription feel like real
  value.
- Including $3 of compute in a $5 sub means subscribers
  effectively pay $2/month for the platform — well above
  marginal cost, comfortably below "rent-seeking" perception,
  and the bundled-credits framing is easier to pitch.

### Alt 3 — Subscription with unlimited compute

Flat $X/month gets you unlimited compute too. Rejected because:

- Cloud cost is per-second and bounded only by hard caps; one
  user spawning continuously could cost brnrd more than $X.
- Mismatched cost shape (fixed price for variable cost) is the
  exact recipe for under-water economics.

### Alt 4 — Per-project pricing ($X/month per project beyond Free's cap)

Free = 3 projects; $1-2/month for each additional project.
Rejected because:

- Feels coin-operated; users dislike per-project add-on
  pricing.
- The subscription already captures bigger headroom with a
  cleaner mental model and bundles the other features.

### Alt 5 — Subscription for managed gates only (early-2026-05-22 framing)

Earlier framing was "$X / month for the managed bots."
Rejected at the time because gates were thought to be zero
marginal cost, but in retrospect the platform's *fixed* cost
(dispatcher, postgres, dashboard) IS real and a small sub is
the right tool to cover it. The current shape (platform
subscription) is essentially this idea revisited with the
metered-compute lesson learned.

### Alt 6 — Hard 1-project cap on Free (multi-project as the gate)

Earlier draft within this same reframe (the unwritten "wave 3a"
shape from 2026-05-25) had Free capped at 1 project, with
multi-project as the explicit subscription gate. Rejected on
2026-05-26 in favour of 3 / 10 because:

- 1-project Free reads as "trial mode, not Free" to the
  community (HN / dev-twitter audience). The hobbyist with
  "side project + day-job + scratchpad" bounces before they
  even see the subscription value.
- Multi-project routing is implementation-real, but the cost
  difference between supporting 1 vs 3 projects per account
  is negligible (the routing tables and prefix-resolution
  paths exist either way once you cross "more than one"); the
  meaningful complexity scales with project count, and capping
  at 3 vs 10 still captures that.
- Comparable OSS-with-paid-tier projects (Plausible, Supabase,
  PostHog, Cal.com) all sit at "generous-but-bounded Free" on
  their headline limit and earned their adoption from that
  posture, not from tighter caps.

The subscription is still gated on a bundle of real things
(higher event cap, full dashboard, generous included compute,
90-day audit, email support, 7× project headroom) — just not
on the binary 1→2 cliff.

### Alt 7 — Reseller of AI compute

Brief consideration: brnrd as middleman buying bulk AI usage
and reselling at margin. Rejected because:

- Real reseller economics need scale we don't have.
- Adds reseller-of-record complexity (PCI, tax, support).
- Breaks the "we don't have your prompts" trust story.
- Provider TOS often disallow it.

Brnrd intermediates AI credentials (encrypted vault, used at
spawn time, never logged). It does *not* intermediate AI
billing.

## Open questions to settle before launch

- **Free project cap (3 vs 2 vs higher).** Currently 3, chosen
  for community reception (the "generous but bounded" pattern
  that Plausible / Supabase / PostHog / Cal.com use). The
  more-conservative-commercial alternative is 2 (still avoids
  the "trial mode" reading, slightly more pressure to
  subscribe). Revisit post-launch with actual conversion data.
- **Subscription project cap.** 10 is sketched; could be lower
  (e.g. 5) if it pushes power users to per-seat earlier, or
  higher (e.g. 25 / unlimited) for simplicity. The cap should
  be high enough that no real solo developer hits it.
- **Annual discount level.** $50/year = ~$4.17/mo effective
  (~17%); could go lower for early-adopter push (e.g. $48/year
  for the first 100 subscribers).
- **Included compute level.** 300 credits ($3) covers ~100
  spawns/month; could be tightened (e.g. 200) to push metered
  top-ups earlier, or loosened (e.g. 500) for a "feels free"
  experience at the cost of platform margin. Pre-launch
  decision; current 300-credit shape leaves $2/month
  platform-fee headroom over the included compute.
- **Permission-prompt friction vs auto-approve defaults.** The
  subscription bundles generous compute, which reduces the
  "I'll review every cost" pressure that drove `ask` as the
  default. Subscribers may want `auto-approve-under-X-credits`
  as the default. Revisit during early-subscriber onboarding
  data.
- **Subscription-tier brand name (post-launch).** Currently
  unnamed (just "Subscribed"). If user demand or marketing
  data suggests a brand name would land well, options like
  "Member" (community / OSS-aligned) or "Gear" (brand-
  cohesive with the gear logo, "geared up" energy) can be
  retro-fitted without churning the CLI verb (`brr brnrd
  subscription` / `brr brnrd subscribe` stays).
- **Self-hosted brnrd messaging.** "Always free, full feature
  parity" is the line. The pricing page should explicitly call
  this out so users don't feel coerced — the trust line is
  "we run hosted because operating brnrd isn't worth your
  time, not because we've crippled the OSS."

## Trust signals that ship with the pricing page

- "We don't have your code" — per
  [`design-brnrd-protocol.md`](design-brnrd-protocol.md)
  "Data minimization". Event content dropped after dispatch;
  responses pass through; conversation contents rendered live
  from platform APIs and git remotes (not mirrored to brnrd);
  AI credentials encrypted at rest with per-account envelope
  keys.
- "What we DO hold, named and bounded" — full table in the
  design page's "What we DO hold" subsection: account email +
  password hash; credentials (encrypted, until revoke — AI
  credentials AND, per the generalised credential vault,
  docker-registry credentials); project bindings; event
  metadata graph (30-day TTL, no body / no preview / no
  participant names — the cross-gate table of contents that
  powers failover continuity); Telegram per-chat ring buffer
  (50 msgs × 72h, the one named concession because TG's Bot
  API lacks retroactive history); audit log (metadata, 90
  days for Subscribed / 7 days for Free); spawn outcomes (12
  months for billing).
- "Self-hostable end-to-end, always free" — every server-side
  piece is OSS in the monorepo (per
  [`decision-monorepo-structure.md`](decision-monorepo-structure.md));
  hosted is convenience, not lock-in. The CLI's
  `brr brnrd connect <url>` takes any URL, defaulting to
  `brnrd.dev` — self-hosted deployments are first-class.
- "We charge for ops, not for AI usage" — the platform sub
  covers fixed costs; compute is pass-through with small
  margin; AI is paid directly by the user to the providers.
- "No card-on-file for compute by default" — credit top-ups
  via Stripe Checkout are one-shot purchases; only the
  subscription and auto-topup opt-ins have a recurring billing
  relationship.
- "Per-account audit log" — every credential read, context
  fetch (cross-gate or TG-ring-buffer read), spawn, prompt
  resolution surfaced to the user; surprises are bugs, not
  features.

## Lineage

- 2026-05-22 — drafted as part of the work-continuity reframe
  of managed mode. Three-tier shape (free dispatcher + BYO
  failover + paid managed compute + later team tier).
  Pondering provenance in
  [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §1.
- 2026-05-25 — reshaped: BYO compute deferred from launch;
  collapsed to two-tier (free dispatcher inc. 100 managed-
  compute spawns/month, plus usage-based over the cap, plus a
  deferred per-seat team tier). Free-tier spawn cap revised
  down from 200 → 100. Data-minimization trust signal
  promoted to a load-bearing pricing surface. "We charge for
  ops, not for AI usage" framing added explicitly.
- 2026-05-25 (pass 4) — credits wallet adopted as the payment
  model (1 credit = $0.01; one-shot Stripe Checkout top-ups;
  no card-on-file by default; free-tier grant ≈ 300 free
  credits/month covering ~100 spawns; paid credits never
  expire; opt-in auto-topup for heavy users; pro-rata refund
  of unused paid credits within 30 days). Full wallet
  mechanics moved out to a new
  [`design-billing.md`](design-billing.md) page.
- 2026-05-25 (pass 4 follow-up — third wave) — **major
  pricing reframe**. The "free dispatcher + paid managed
  compute" shape rejected as self-defeating: active users
  wouldn't hit the compute cap, casual users wouldn't hit
  anything, nobody would pay, project would starve. Adopted
  **subscription for the platform + metered credits for
  compute**. Initial draft of the new shape sketched the
  paid tier as "Brnrd Plus" at $9/month with 500 credits
  included and 1 project on Free.
- 2026-05-26 (third-wave follow-up) — naming + pricing
  refined. **Paid tier left unnamed** (just "Subscribed" /
  "Subscriber"); "Plus" rejected as too SaaS-upsell-tier
  branding-coded. **Price set to $5/month** ($50/year
  annual) with **300 credits included** ($3 of compute) —
  trades thinner per-subscriber margin for higher expected
  conversion at the sub-$5 psychological threshold. **Free
  tier project cap raised from 1 → 3** for community
  reception, matching the "generous but bounded Free"
  pattern Plausible / Supabase / PostHog / Cal.com use;
  subscription cap stays at 10 projects. CLI verb shape
  also reshaped: `brr brnrd subscription [status | start |
  cancel | resume | portal]` (noun-first, matches the
  existing `creds` / `policy` / `projects` namespacing) +
  `brr brnrd subscribe` shortcut. Event-overage soft-
  throttle, audit retention deltas, self-hosted-free posture,
  and metered-compute mechanics unchanged from the
  third-wave shape. Driven by the user's "I don't like
  Plus as a name or verb; $5 a month with the credits to
  make up for it; properly tweaked Free might not need the
  1-project cap" feedback.
