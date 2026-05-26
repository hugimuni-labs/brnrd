# Decision: managed-mode pricing shape

**Status: proposed, not yet accepted on 2026-05-22; reshaped
multiple times (see Lineage). Current shape on 2026-05-26
(third-wave follow-up, locked):** unnamed paid tier
("Subscribed") at **$5/month for the first 200 supporters
(grandfathered forever) → $7/month for new joiners after**,
with 300 credits / month included and a **25-project cap that
unlocks to unlimited after $10 of cumulative credit
purchases**. Free tier at 3 projects / 100 events / **10-credit
one-time signup bonus** (no recurring grant) — subscription
for the platform + metered credits for compute. The earlier
"free dispatcher + paid
managed compute" framing turned out to be self-defeating —
see "What changed and why" below. The two-step pricing
(supporter $5 → public $7) is the launch-cohort defensive
move documented in
[`decision-licensing-and-defense.md`](decision-licensing-and-defense.md).
Companion to
[`subject-managed-mode.md`](subject-managed-mode.md) (the
surfaces being priced),
[`design-brnrd-protocol.md`](design-brnrd-protocol.md) (the
per-task accounting hooks the model rides on),
[`design-billing.md`](design-billing.md) (the subscription +
wallet + Stripe mechanics that implement the cost model), and
[`decision-licensing-and-defense.md`](decision-licensing-and-defense.md)
(the moat the pricing is part of: license split + early-
adopter step + deferred trademark).

## Decision

**Two tiers at launch**, with metered compute on top of either:

| Tier | Price | Projects | Events / month | Compute included | Dashboard | Audit retention | Support |
|------|-------|----------|----------------|------------------|-----------|-----------------|---------|
| **Free** | $0 | **3** | 100 | **10-credit one-time signup bonus ($0.10)**, no recurring grant | Basic (per-project, read-only views + allowance gauges) | 7 days | Community (Discord, GitHub issues) |
| **Subscribed — supporter price** *(first 200 subscribers, then locked forever)* | **$5 / month** *(or $50 / year, ~17% off)* | **25** (unlimited after $10 cumulative top-ups) | 10,000 | **300 spawn-credits ($3 of compute) / month** | Full (cost charts, permission-prompt customisation, cross-project view, project-binding UI, allowance gauges) | 90 days | Email |
| **Subscribed — public price** *(joiners after supporter cohort closes)* | **$7 / month** *(or $70 / year, ~17% off)* | **25** (unlimited after $10 cumulative top-ups) | 10,000 | **300 spawn-credits ($3 of compute) / month** | Full | 90 days | Email |
| **Compute overage** (all tiers) | $0.01 / credit (metered) | — | — | top-up via existing wallet | — | — | — |
| **Self-hosted brnrd** | $0 | unlimited | unlimited | self-paid cloud bill | full (your deployment) | self-defined | self-supplied |

Supporter-price cohort closes on **first 200 subscribers OR
12 months from public launch, whichever first**. Existing
supporters keep $5/$50 forever (Stripe-stable on their
original `Price` ID; no auto-migration). Rationale in
[`decision-licensing-and-defense.md`](decision-licensing-and-defense.md)
§ "Move 2 — Early-adopter pricing".

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
| **Bigger project headroom** (25 projects default, unlimited after $10 of cumulative top-ups; vs 3 on Free) | The dispatcher's multi-project resolution path (chat-binding + prefix override on TG/Slack/Discord, per-installation routing on GH App) is genuinely more code, more state, more support burden the more projects an account fans out across. Free's 3-project cap handles the "side project + day-job + scratchpad" case; the jump to 25 covers serious adopters; the spend-gated unlock to unlimited rewards demonstrated real usage without putting a hard cap on heavy users. |
| **Full dashboard** (cost charts, cross-project view, permission-prompt config, project-binding UI) | More views = more build + maintenance cost. Free gets the read-only essentials; subscribers get the operational surface. |
| **300 credits / month of managed compute included** | Bundled grant covers ~100 spawns/month at typical task size. The $5 platform fee buys the platform AND a $3 grant of managed compute on the house. Light subscribers effectively never think about credits; heavy users top up at $0.01/credit. |
| **BYO cloud compute** (subscribers can bring their own Fly / Modal / etc. token instead of using managed compute) | "If we ship it managed, you can BYO it." Subscribers who prefer to keep their cloud spend on accounts they already own (or who want to skip the managed-compute margin) bring a credential to the vault; dispatcher routes spawns to the user's cloud account. The sub is the gate; cloud envs available depend on which envs we've shipped managed. See "Compute: managed vs BYO" below. |
| **10K events/month** (vs 100 on Free) | High enough that real subscribers effectively never hit the ceiling. Free's 100 is a try-it cap, not a usable production cap. |
| **90-day audit retention** | Compliance signal for users who care about post-hoc forensics. Free's 7 days covers debugging; 90 days covers "what happened in March?" |
| **Email support** | Real cost (someone reads + responds). Subscribers pay for it. |

The pitch reads as "I'm buying these specific things" rather
than "I'm buying past an artificial wall," which is the
difference between sustainable pricing and rent-seeking.

**Framing nuance on the included compute grant:** the $5
platform fee is the platform fee. The 300 credits aren't a
"reimbursement" that effectively makes the platform cost
$2 — they're a bundled grant of managed compute on the
house. Mental model: "I pay $5 for the platform, and I get
$3 of compute included." Not "I pay $2 net and the $3 is a
refund." Subscribers who BYO or self-host their compute
still pay $5 for the platform; the grant lapses unused (or
covers an unbrought env). This framing matters for both the
sell ("the platform is worth $5; the compute is free on
top") and for our own unit economics tracking (the $5 is
clean revenue; the $3 grant is a controllable cost-of-goods,
not a price reduction).

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

## Compute: managed vs BYO (subscriber choice)

Subscribers see one of two compute flows. **Free stays
managed-only on purpose** (rationale below).

| | **Managed compute (default)** | **BYO compute (subscriber opt-in)** |
|---|------|------|
| **Who** | Free + Subscribed | Subscribed only |
| **Where spawns run** | brnrd's cloud account (Fly Machines at launch) | Subscriber's own cloud account |
| **Credentials** | brnrd-side, invisible to the user | Subscriber stores a cloud-platform credential in the vault (`brr brnrd creds add cloud-platform --provider fly --token …`) |
| **Who pays the cloud** | brnrd, recovered via credit debits ($0.01/credit, 300 credits included with sub) | Subscriber, directly to the cloud provider |
| **brnrd revenue per spawn** | $5/$7 platform sub + small compute margin on overages | $5/$7 platform sub, full stop (zero compute markup) |
| **Setup friction** | Zero (works out of the box) | One-time vault upload of a cloud-platform token + scope check |
| **Best for** | "I want this to work, don't care where it runs" | "I already have a Fly account / company billing routes through one cloud / I want to skip the managed-compute margin" |

**One rule covers all clouds we ship**: if brnrd ships managed
support for a cloud env (Fly at launch; Modal / Daytona /
Codespaces / etc. later), subscribers can BYO that cloud the
same day the managed support ships. "BYO-only" doesn't exist —
the cloud-platform credential vault entry is plumbed through
the same env class that powers managed mode (per the "Caller
axis" pattern in
[`research-cloud-envs.md`](research-cloud-envs.md): same env
class, two callers).

The 300 included credits **stay granted regardless of BYO
choice**. A subscriber who BYOs Fly might let the credits
lapse unused that month, or spend them on a different env
they didn't bring (e.g. managed Modal once shipped). The
grant is "bundled compute on the house," not "refundable
unused budget."

### Why BYO is subscriber-only (not on Free)

Three reasons the policy gate on `subscription.tier ==
subscribed`:

1. **Free's whole purpose is "try this without setup
   friction."** Adding "first, configure your Fly token,
   verify scopes, debug per-platform onboarding edge cases"
   defeats that role.
2. **BYO is structurally a cost-saving feature; subscribing
   is the cost-saving move.** Users who care enough about
   compute cost to BYO should already be subscribers.
   Otherwise we'd create a strict-better-than-paid Free path
   (Free + BYO compute + managed bots) and undercut our own
   revenue.
3. **Implementation cleanliness.** Vault unlock and dispatcher
   credential lookup gate on a single condition
   (`subscription.tier == "subscribed"`); one code path, one
   support story.

The subscription itself is the per-paying-customer gate;
the BYO posture flows naturally from "subscribers are
already paying for the platform — they shouldn't be locked
into our cloud account on top of that."

### Cloud envs available for BYO at launch vs over time

- **Launch**: BYO Fly Machines only (we're shipping Fly
  managed; the same env class invoked with the subscriber's
  token is a small incremental on top of managed).
- **Post-launch**: each cloud env we add managed support for
  (Modal, Daytona, Codespaces, …) unlocks BYO for that env
  in the same release. No deferred-forever promises; BYO
  shipping cadence follows managed-support shipping cadence
  one-for-one.

This avoids the BYO-explodes-the-launch-surface problem from
the earlier draft (~30% backend surface per cloud platform:
credential storage UI, scope validation, onboarding docs,
per-platform failure modes, dispatcher branching) while
keeping the promise honest. Launch ships one cloud; BYO
parallel-ships with managed for every cloud after.

### Same principle applies to future agentic-secretary connectors

When the agentic-secretary layer lands (per
[`decision-connectors-layering.md`](decision-connectors-layering.md))
and brings hosted Google / Linear / Notion / etc.
connectors, the same BYO-for-subscribers rule applies:
subscribers can bring their own OAuth credentials for any
connector we ship managed; Free gets the managed-only path.
The rule is platform-wide ("BYO available for any
subscriber-only feature we ship managed"), not cloud-env-
specific.

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
itself. **Throttling is always surfaced to the user** —
silent throttling is the actually-mean version; explicit
"you're being throttled, here's why, here's how to lift it"
is honest and fixes itself when the user understands the
situation. See "Dashboard nudges + transparency" below for
where throttle states surface to the user.

## Free compute grant — one-time signup bonus, not recurring

Free's compute allowance reshaped from "5 spawn-credits per
month (activity-gated)" to **"10-credit one-time signup
bonus" on 2026-05-26 (locking pass II)**. Rationale: the
"start stingy, relax later" principle is structurally better
than the reverse for both unit economics AND community
perception. Tightening reads as betrayal; loosening reads as
"we're winning, here's more on the house."

### Mechanics

- New Free account gets **10 credits** in a `free_signup_bonus`
  ledger sub-bucket on account creation.
- Bonus **expires 30 days from account creation** OR upon full
  consumption, whichever first. Activation grant, not a
  savings account.
- After the bonus, Free users top up at $0.01/credit if they
  want failover compute, OR subscribe for the recurring
  300-credit subscriber grant.
- **No monthly grant on Free**. The activity-gating logic from
  the previous shape is removed entirely; there's no dormant-
  account cost line to worry about.

### Math at scale

| Free accounts | One-time bonus cost (total, not per year) |
|---------------|------------------------------------------|
| 1,000 | $100 |
| 10,000 | $1,000 |
| 100,000 | $10,000 |
| 1,000,000 | $100,000 |

Bounded by signup count, not by retention. Viral-growth
scenarios don't bleed compute indefinitely. Compare to the
previous 5/month recurring shape: 10K Free × 5 × 12 =
$6K/year; 100K Free × 5 × 12 = $60K/year — same magnitude as
the one-time cost at 6× the user count, with no upper bound.

### Optics

The narrative shifts from "you get $0.05 of free compute
every month" to "you get the managed dispatcher genuinely
free, plus a starter pack to try failover compute." The
dispatcher (gates + multi-project routing + permission
prompts + 7-day audit) is the load-bearing free thing; it's
free regardless of any compute grant. Selling Free as
"$0.05/mo of free compute" was muddling the value prop;
selling Free as "the managed dispatcher, free" is honest.

The failover path is opt-in and rare-path (laptop online =
no failover needed). Free users who never enable failover
never notice the absence of a monthly grant. Free users who
do enable failover get 3 tries to validate the path before
the credit moment ("top up at $0.01/credit or subscribe").

### What this gives up

The "Free user can live on the platform forever paying
nothing" narrative becomes sharper: with recurring 5/mo, a
hobbyist could process gates + 1-2 failover spawns/month
indefinitely; with the signup-bonus shape, the same user can
still process unlimited gates indefinitely (the load-bearing
common path), but failover beyond the bonus requires a
top-up or sub. Acceptable trade because failover is the rare
path, not the common one.

## Subscriber project cap — 25 default, unlimited after $10 of cumulative top-ups

The flat "10 projects on Subscribed" cap reshaped on 2026-05-26
(locking pass II) into a **tiered cap**:

- **Subscribed (default)**: up to **25 projects**.
- **Subscribed + ≥$10 cumulative top-ups (ever)**: **unlimited
  projects**.

### Mechanics

- The account tracks `cumulative_purchased_usd_lifetime` — a
  never-decreasing counter incremented on every successful
  Stripe top-up. Refunds don't decrement (the spend happened;
  refunds are tracked separately).
- The derived flag `project_cap_unlocked` is set when
  `cumulative_purchased_usd_lifetime >= 10`. **Once set,
  permanent on the account** (survives subscription cancel +
  re-subscribe; if the user re-Frees, they're capped at 3
  projects per Free's cap, but the unlock flag persists for
  any future re-subscription).
- Project-creation endpoint enforces the effective cap on each
  attempt: `effective_cap = unlimited if (subscribed AND
  unlocked) else 25 if subscribed else 3`.
- $10 threshold = two typical top-ups (most common Stripe
  Checkout amount is $5; second is $20). Signals "real
  user with sustained usage" without being punitive.

### Rationale

- **25 is high enough that almost no real solo developer hits
  it.** Most solo devs work across 3-10 projects actively;
  serious indie hackers maybe 15-20. 25 covers the long tail.
- **The unlock is a trust signal, not a paywall escape.** "You've
  shown sustained usage by purchasing compute; the cap is no
  longer relevant." Removes a friction point for power users
  who'd otherwise feel the 25-cap as artificial.
- **Spend-gated, not status-gated.** "Subscriber level X" tiers
  are the rent-seeking pattern; a spend-gated unlock is
  matter-of-fact: you've contributed enough revenue that the
  marginal multi-project routing cost is paid for.
- **Doesn't bind the team-tier discussion.** v-next per-seat
  pricing is its own thing; this unlock is per-account.

### What this gives up

A small possibility of abuse: a subscriber tops up $10 in
credits, gets the unlock, then cancels the subscription but
spawns 100 projects on a re-subscribe later. Mitigated by:
the binding-uniqueness rule (a GitHub repo / TG chat can only
be bound to one account at a time — so the "100 projects" are
mostly toy projects without managed-gate routing), and by the
fact that the unlock only matters while subscribed (the $5
sub is the per-paying-customer gate).

## Multi-account abuse mitigation: binding uniqueness, not fingerprinting

Naive concern: "what stops a Free user from making 10 accounts
to chain together 100 signup bonuses + 30 projects?"

Mitigation at launch is **resource-binding uniqueness**, NOT
identity fingerprinting or IP-based velocity controls:

- **GitHub repo binding is unique per repo.** If `myorg/foo`
  is already bound to account A, account B trying to bind the
  same repo gets "this repo is already bound to another
  account; have the original owner unbind first."
- **Telegram chat binding is unique per chat.** Same shape:
  one chat → one (account, project) pair, enforced server-
  side at bind time.
- **Slack / Discord / future-platform chat bindings** follow
  the same rule.

This is enforced anyway for **routing correctness** — you
can't dispatch two projects to the same chat without
collision — so framing it as abuse-mitigation gives us 95%
of the value at zero incremental cost.

**What about projects with no bindings?** A Free user could
create 3 projects per account × 10 accounts = 30 unbound
"projects" in our database. These have **zero managed-gate
routing value** (no chat, no repo to receive events from).
They can only be used via the local daemon's gates, which
the user could have set up without brnrd at all. Abuse
leverage = approximately zero.

**What about the 10 × 10-credit signup bonuses (= 100
credits = $1 of compute)?** Bounded by signup velocity per
email / OAuth identity (Stripe and email verification on
account creation handle the common case); the cost of 10
duplicate accounts is at most $1 of compute, which is below
the cost of investigating abuse cases. Accept as immaterial.

What we **don't** add at launch: device fingerprinting, IP
velocity limits beyond standard DDoS protection,
"suspicious account" flagging, ML anti-abuse. All
overengineering at our scale; revisit if abuse signal
appears in real data (which it won't, because the leverage
is too small).

## Dashboard nudges + transparency

The dashboard surfaces **usage relative to allowance** as a
first-class read view, and nudges toward subscribe / top-up
when the user approaches or crosses an allowance line. Honest
nudges, not dark patterns.

### What the dashboard shows (per account)

- **Events bar**: `87 / 100 events this month` (Free) or
  `2,341 / 10,000 events this month` (Subscribed). Resets at
  the month boundary; bar colour grades from green → yellow
  (≥75%) → orange (≥90%) → red (≥100%, throttling active).
- **Credits bar** with bucket breakdown on hover:
  - Free: `3 / 10 signup bonus credits remaining, expires
    May 15` (red when 0 remaining); below: `0 purchased
    credits — top up at $0.01/credit`.
  - Subscribed: `145 / 300 monthly grant + 200 purchased
    credits available`.
- **Projects bar**: `2 / 3 projects` (Free) or `8 / 25
  projects (unlimited after $10 of cumulative top-ups —
  $4.50 to go)` (Subscribed, pre-unlock) or `8 projects
  (unlimited)` (Subscribed, post-unlock).
- **Spend chart**: month-by-month credits consumed (last 6
  months) for subscribers; current-month-only for Free.
  Already in the dashboard MVP.

### Nudge triggers + content

Banners appear at the top of the dashboard, dismissible per
session, never modal:

| Trigger | Banner | Action |
|---------|--------|--------|
| Free user crosses 80 events / month | "You're at 80% of your free event allowance this month." | Link: "Subscribe for 10,000 events / month →" |
| Free user hits event cap (events being throttled) | "Events are being throttled — you've hit the 100 / month Free cap. Throttle clears <next month boundary>." | Link: "Subscribe to lift the throttle now →" |
| Free user's signup bonus fully consumed | "Free signup bonus consumed. Top up or subscribe for ongoing failover compute." | Two links: "Top up at $0.01/credit" / "Subscribe for 300 credits / month →" |
| Free user's signup bonus expires unused at day 30 | "Your signup bonus expired. Top up or subscribe to use failover compute." | Same two links. |
| Free user tries to create a 4th project | (form-side error) "Free supports up to 3 projects. Subscribe for 25 (unlimited after $10 of credit spending)." | CLI / dashboard returns the same message with a subscribe URL. |
| Subscriber crosses 80% of credit grant | "You've used 80% of this month's 300 included credits." | Link: "Top up at $0.01/credit (covers ~33 spawns per $1)." |
| Subscriber at 25-project cap, not unlocked | (form-side error on 26th project creation) "Subscriber accounts support up to 25 projects by default — unlock unlimited after $10 of cumulative top-ups ($X.XX to go)." | Link: "Top up now →" |
| Subscriber crosses 80% of event cap (≥8K events) | "You're at 80% of your monthly event cap." | Link: "Email us — we'll raise the cap." |

### What we don't do (anti-patterns avoided)

- **No modals that block work.** Banners are inline at the top
  of the dashboard, never overlay.
- **No "are you sure you want to cancel?" friction** — cancel
  flow goes straight to Stripe Customer Portal.
- **No tiny "no thanks" buttons / huge "subscribe" buttons** —
  dismissal is equal-weight with the action.
- **No countdown timers** or "limited-time discount" pressure
  on the nudge.
- **No hidden hard caps** — every throttle is signposted; the
  user always knows why a request was slowed / queued.
- **No nudge spam** — at most one event-cap banner per
  threshold crossing per session; gate notifications about
  throttling fire at most once per throttle event, not on
  every queued event.

### Gate-side nudge (one-liner footer)

When a gate (TG / GH / Slack) replies to the user with a
throttle / out-of-credit / cap-hit message, the reply includes
a single-line footer:

```
[ this task was queued — Free event cap reached.
  subscribe at brnrd.dev/subscribe → ]
```

Never more than one line. Never adds the footer to a
successful response. The user sees the nudge only when it's
relevant to the action that just happened.

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

Crude back-of-envelope at supporter-cohort $5/mo or public-
cohort $7/mo + 300 credits included + ~30% of subscribers
exceed the included compute (heaviest users top up at
$0.01/credit). Blended ARPU below assumes the first 200 are
supporter-priced ($5), every subscriber after is public-
priced ($7):

| Scenario | Subscriber mix | Blended MRR (sub only) | Compute revenue (over included) | Compute cost (Fly pass-through) | Infra cost | Net |
|----------|---------------|------------------------|--------------------------------|---------------------------------|------------|-----|
| 50 supporters | 50 × $5 | $250 | ~$50 | ~$120 | ~$150 | **~+$30** |
| 200 supporters (cohort full) | 200 × $5 | $1,000 | ~$200 | ~$500 | ~$300 | **~+$400** |
| 500 subs (200 supporter + 300 public) | 200 × $5 + 300 × $7 | $1,000 + $2,100 = $3,100 | ~$500 | ~$1,250 | ~$500 | **~+$1,850** |
| 1,000 subs (200 supporter + 800 public) | 200 × $5 + 800 × $7 | $1,000 + $5,600 = $6,600 | ~$1,000 | ~$2,500 | ~$800 | **~+$4,300** |

Model crosses sustained-net-positive around ~80 subscribers
(at the all-supporter price) — a credible threshold for "this
project pays for itself + a small honorarium to its maintainer"
within the first year of public availability. At 500+
subscribers the project is comfortably funding itself plus
paying real maintainer time. The $5 → $7 step adds **~$600 /
month** of long-tail headroom at 500 subs (300 public-priced
subs × +$2) and **~$1,600 / month** at 1,000 subs (800 public-
priced subs × +$2) vs an all-supporter-price universe —
meaningful maintainer-time funding from a small price
difference.

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

## Credit buckets and expiry (per-source policy)

Credits are tracked in a **bucketed ledger** with per-source
expiry — the standard "credit pools / entitlement buckets"
shape used by OpenAI / Anthropic / AWS / GCP / most metered
SaaS billing systems. The exact ledger schema, debit
priority, and Stripe wiring live in
[`design-billing.md`](design-billing.md) §
"Credit buckets and expiry policy"; the headline shape:

| Bucket | Granted on | Expires | Rolls over | Refundable |
|--------|-----------|---------|-----------|------------|
| `free_signup_bonus` | One-time on Free account creation | 30 days from creation OR on full consumption | No | No |
| `subscriber_monthly` | Subscription start + every renewal | End of current billing cycle | **No** | No |
| `purchased` | Stripe Checkout top-up confirmed | **Never** (dormancy-bounded) | Yes | Pro-rata within 30 days |
| `promotional` *(future)* | Referral / support goodwill / campaign | Specified at grant time (30-90 days typical) | No | No |

Debit priority on spend: `free_signup_bonus` →
`subscriber_monthly` → `promotional` (soonest-expiring first)
→ `purchased` (oldest-first FIFO). Users' purchased balance
is always
preserved last — the grant gets consumed first.

**Why monthly grants expire end-of-cycle:** mobile-plan /
cloud-quota intuition. "This month's allowance" is the
universally-understood frame; "use it or lose it" reads as
the allowance refreshing, not as the platform being stingy.
Rolling grants would let Free users stockpile $0.60 / year
of compute and subscribers stockpile $36 / year — small money,
but it muddies the "$3 of optional bundled compute" framing
and creates accounting tail on what should be zero-liability
grants.

**Why purchased credits don't expire:** "I paid you $10, my
1,000 credits should still exist next year" is the strongest
consumer-protection expectation and the EU-friendly posture
(France has no hard expiry rule on pre-paid digital balances
but industry convention is 12+ months; "never" is a cheap
moat-strengthening move over OpenAI / Anthropic who default
to 1 year). Dormant-account liability is bounded by an
**account-dormancy policy** (see
[`design-billing.md`](design-billing.md)):
24 months inactive → pause services, credits remain valid;
36+ months → dashboard prompt for reactivation; deletion
only on explicit user request or GDPR right-to-erasure.

**Free signup bonus (no recurring grant on Free):** the
earlier "5 credits/mo activity-gated" shape was revisited
2026-05-26 (locking pass II) and replaced with a **10-credit
one-time signup bonus** that expires 30 days from account
creation OR on full consumption. Bounded by signup count
rather than active-user count — see "Free compute grant —
one-time signup bonus, not recurring" above for math + optics.
Subscriber grants refresh unconditionally on every monthly
billing cycle — the subscription itself is the activity signal.

**Dashboard language:** the dashboard never says "your
credits expired"; it says "your monthly allowance refreshes
on <date>" / "your monthly allowance reset on <date>, new
balance: 300 credits." Same mechanic, opposite emotional
valence. The bucket UI labels the user's balance as a single
number with "X credits this month + Y purchased" breakdown
only on hover / details.

## Early-adopter price step ($5 supporter → $7 public)

Launch ships **two `Price` variants** of the same Stripe
subscription product:

- **Supporter price**: $5 / month, $50 / year. Available
  for the first 200 subscribers OR until 12 months from
  public launch, whichever first. **Grandfathered forever
  on Stripe** — existing supporters keep paying $5/$50 at
  every renewal because Stripe never auto-migrates a
  subscription off its original `Price`.
- **Public price**: $7 / month, $70 / year. Default for
  every new checkout session after the supporter cohort
  closes. Same product, same features, same included
  compute — just $2 / month more (~40% revenue uplift per
  subscriber on the long tail).

The supporter / public step is the launch-cohort loyalty
mechanism documented in
[`decision-licensing-and-defense.md`](decision-licensing-and-defense.md)
§ "Move 2 — Early-adopter pricing", which also covers the
why-200 sizing, the Stripe `Price`-ID grandfathering
contract, the cohort-counter mechanic, and the alternatives
considered. This page only locks the numbers and the
visible mechanics.

**What the dashboard shows during the supporter window:**
both prices side by side ("$5 supporter price, Y / 200
spots remaining" + "$7 standard price after the supporter
cohort"). Live counter so the scarcity is honest.

**What flips at the boundary:**

- The `brr brnrd subscribe` CLI verb and the dashboard
  checkout flow swap the emitted `Price` ID atomically on
  the 201st subscription start (or on the launch+12-month
  date), and announce the change publicly the same day.
- Existing supporters see no change — same price, same
  invoice, same renewals. The grandfathering is
  Stripe-native.
- Annual supporters who let their subscription lapse and
  re-subscribe re-enter at the then-current price ($70 if
  past the boundary). Documented in the cancel flow.

## Subscription mechanics

Implementation detail lives in
[`design-billing.md`](design-billing.md). Headline contract:

- **$5/month (supporter) → $7/month (public, post-cohort)**,
  billed monthly via Stripe recurring subscription
  (separate Stripe product from the credit-wallet one-shot
  top-ups). All EU compliance work from the credit-wallet leg
  (Stripe France, HugiMuni SAS, Qonto payouts, Stripe Tax,
  OSS scheme, SCA via Checkout) applies to the subscription
  product identically — Stripe handles both subscription and
  one-shot products under the same Stripe account.
- **Annual discount** option: $50/year (supporter) or
  $70/year (public) = ~$4.17/mo or ~$5.83/mo effective
  (~17% off either tier). Saves Stripe per-charge fees
  (12 charges/year → 1) and gives users a small win.
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

## BYO compute — subscriber feature, parallel-shipped with managed

Reframed on 2026-05-26 from the earlier "designed, deferred"
posture. Earlier draft framed BYO as a Free-tier feature
dropped from launch entirely because of the per-platform
backend cost (~30%: credential storage UI, scope validation,
onboarding docs, per-platform failure modes, dispatcher
branching). The current framing keeps the per-platform cost
real but reshapes around it:

- **Policy**: BYO is a **subscriber feature** for whichever
  cloud envs ship managed. Free stays managed-only by design.
- **Launch scope**: BYO Fly Machines ships at launch alongside
  managed Fly (the same env class invoked with the user's
  token is a small incremental over managed; no separate cloud
  to onboard).
- **Post-launch scope**: BYO availability for each cloud
  follows managed support for that cloud one-for-one. Adding
  managed Modal also adds BYO Modal in the same release.

Pricing implication: subscribers who BYO contribute pure
$5/$7 subscription revenue per month with **zero compute
markup**. Subscribers who use managed contribute $5/$7 plus
small per-spawn margin on overages. Both are
revenue-positive; BYO trades a slim margin stream for a
cleaner trust posture (subscribers aren't locked into
brnrd's cloud account, they're choosing managed for the
convenience).

Daemon-side cloud envs (a laptop daemon fans out to the
user's cloud via a first-party env extra like `brr[fly]` or
a third-party env registered via the `brr.envs` entry
point) remain independent of managed mode entirely — brnrd
isn't in that path, nothing to price. The "BYO at the brnrd
layer" we're describing here is specifically about the
**managed-dispatcher routing spawns to the user's cloud
account** — the dispatcher and the credential vault are
brnrd-side; only the compute target shifts.

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
- **Subscription project cap unlock threshold.** $10 of
  cumulative top-ups is the proposed default. Could be lower
  ($5 = one typical top-up; faster unlock; weaker spend
  signal) or higher ($25 / $50; slower unlock; stronger
  spend signal). Default $10 covers two top-ups, signals real
  usage, doesn't gate too aggressively. Revisit if early
  subscriber data shows it's binding too often.
- **Annual discount level.** $50/year (supporter) and
  $70/year (public) sit at ~17% off monthly. Could go more
  aggressive on annual (e.g. $45/year supporter, $60/year
  public = 25% off) to push annual specifically. Defer; one
  pricing knob at a time, and the supporter step is already
  the launch's annual-conversion lever.
- **Included compute level.** 300 credits ($3) covers ~100
  spawns/month; could be tightened (e.g. 200) to push metered
  top-ups earlier, or loosened (e.g. 500) for a "feels free"
  experience at the cost of platform margin. Pre-launch
  decision; current 300-credit shape leaves $2/month
  platform-fee headroom over the included compute.
- **Free signup bonus size.** 10 credits one-time is the
  current proposal — bounded by signup count (not by
  retention), so the math is clean: 100K Free signups
  total = $10K of compute total (not / year). Could go
  lower (5 credits, $5K at 100K signups) for tighter cost
  bounds, or higher (20 credits, $20K at 100K signups) for
  more generous activation. 10 covers ~3 failover spawns
  at typical task size — enough to validate the path. The
  earlier "5 credits/month recurring + activity-gated"
  shape was revisited 2026-05-26 (locking pass II) per
  "start stingy, relax later" — the one-time-bonus shape
  is both simpler to reason about AND structurally bounded
  by signup count rather than active-user count.
- **Account-dormancy timing.** 24 months pause / 36 months
  prompt is the proposed default; could be longer (36/48,
  more user-friendly, higher dormancy tail) or shorter
  (18/24, more aggressive cleanup, more friction risk).
  Revisit if dormant-account count crosses 10% of total
  accounts.
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
- 2026-05-26 (locking pass — $5 → $7 step + license note).
  Pricing locked at **two `Price` variants**: $5/mo (or
  $50/yr) for the first 200 supporters, grandfathered
  forever on Stripe; $7/mo (or $70/yr) for new joiners after
  the supporter cohort closes (200 subs OR 12 months from
  public launch, whichever first). Same product, same
  features, same included compute — only the price differs.
  Adds ~$600/mo (at 500 subs) to ~$1,600/mo (at 1,000 subs)
  of long-tail headroom over an all-supporter-price universe.
  Full rationale (why-200, why-$7, Stripe `Price`-ID
  grandfathering contract, cohort-counter mechanic, the
  whole moat / OSS-defense story) moved to the new
  [`decision-licensing-and-defense.md`](decision-licensing-and-defense.md);
  this page only locks the numbers + visible mechanics +
  blended sustainability math. Driven by the user's "$5 for
  early adopters, $6/$7 for the afterparty — license is
  the right thing, trademark is a post-launch priority"
  framing.
- 2026-05-26 (compute model + credit-bucket lock-in).
  **BYO compute reframed from "deferred forever" to
  "subscriber feature, parallel-shipped with managed."**
  Policy: if brnrd ships managed support for a cloud env,
  subscribers can BYO that env in the same release. Launch
  ships BYO Fly Machines (alongside managed Fly); other
  clouds get BYO when they get managed. Free stays managed-
  only on purpose (the sub is the gate; BYO is cost-saving,
  subscribing is the cost-saving move). Same BYO-for-
  subscribers principle applies to future agentic-secretary
  connectors. New "Compute: managed vs BYO" section codifies
  the two-flow shape. **Credit buckets formalised** with
  per-source expiry policy: `free_monthly` *(later renamed
  `free_signup_bonus` in locking pass II)* /
  `subscriber_monthly` (use-it-or-lose-it end-of-cycle),
  `purchased` (never expires, dormancy-bounded), `promotional`
  (future, expires per grant). Debit priority is grants
  first, purchased last (FIFO within bucket); preserves the
  user's paid balance. **Activity-gated Free monthly grants**:
  the 5 credits/mo only refresh if the Free account had any
  prior-month activity — bounds the long-tail cost of dormant
  one-time-signup accounts at zero. **Reimbursement framing
  rejected** in favour of "$5 platform fee + $3 of bundled
  compute on the house" — the platform fee is the platform
  fee, the credits aren't a refund. Dashboard never says
  "credits expired"; says "monthly allowance refreshes on
  &lt;date&gt;." Full ledger / debit / Stripe wiring lives
  in [`design-billing.md`](design-billing.md); this page
  carries the policy summary. Driven by the user's "we
  probably also gonna have to expire granted credits
  somehow, unless you think it would be perceived
  negatively, what's the right shape?" + "agree on no BYO
  for Free + per-paying-customer language."
- 2026-05-26 (locking pass II — Free signup bonus, subscriber
  cap unlock, dashboard nudges). **Free monthly compute grant
  reshaped from "5 credits/month activity-gated" to "10-credit
  one-time signup bonus, 30-day expiry."** Bounded by signup
  count rather than active-user count — math caps at $10K of
  compute at 100K signups total, vs $60K/year at 100K active
  Free users on the previous shape. Removes the activity-
  gating logic entirely (no longer needed). New "Free compute
  grant — one-time signup bonus, not recurring" section
  codifies mechanics + math + optics. Driven by the user's
  "start a bit stingier and relax as we go" principle.
  **Subscriber project cap reshaped from flat 10 to tiered
  25 (default) / unlimited (after $10 cumulative top-ups).**
  New `cumulative_purchased_usd_lifetime` account state +
  derived `project_cap_unlocked` flag (permanent once set,
  survives subscription cancel + re-subscribe). New
  "Subscriber project cap — 25 default, unlimited after $10
  spent" section. Driven by the user's "capped at smth high
  like 25, unlimited as soon as they spent smth small but
  reasonable on credits." **Multi-account abuse mitigation
  via binding uniqueness** (GitHub repo + TG/Slack/Discord
  chat bindings unique per resource) framed as both routing-
  correctness AND abuse-mitigation; no fingerprinting / IP
  velocity / "suspicious account" flagging at launch
  (overengineering at our scale). New "Multi-account abuse
  mitigation: binding uniqueness, not fingerprinting"
  section. Driven by the user's "we maybe need to implement
  project ownership, so a user wouldn't go creating multiple
  accounts." **Dashboard nudges + transparency section**
  codifies the honest-nudge UX: dismissible inline banners,
  no modals, every throttle is signposted, gate-side replies
  include a single-line subscribe footer when (and only
  when) the user just hit a throttle / cap / out-of-credit.
  Anti-patterns named explicitly (no dark-pattern friction
  on cancel, no countdown timers, no nudge spam). Driven by
  the user's "a dashboard to show the allowance consumption
  in events and credits, and a nudge to go subscribe if
  anything got above the allowance — that's not too mean,
  right?" + "throttling is a good idea, like it."
