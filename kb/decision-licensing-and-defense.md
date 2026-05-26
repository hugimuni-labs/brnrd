# Decision: licensing + competitive-defense posture

**Status: proposed, not yet accepted on 2026-05-26.** Names the
three concrete moves that protect the brnrd hosted business
from the "anyone can clone our OSS and undercut us" failure
mode, while keeping the OSS posture honest. Lives at the
intersection of [`decision-pricing-shape.md`](decision-pricing-shape.md)
(subscription as the revenue surface to defend),
[`decision-monorepo-structure.md`](decision-monorepo-structure.md)
(package boundary aligns with the license boundary), and
[`subject-managed-mode.md`](subject-managed-mode.md) (the
hosted product being protected).

## Decision

Three concrete moves, in priority + sequencing order:

1. **License split**: `src/brr/` (the daemon) stays
   permissive (MIT). `src/brnrd/` + `src/brnrd_web/` (the
   hosted backend + dashboard) ship as **AGPLv3**. Daemon
   ergonomics + community goodwill are preserved; the backend
   gets a real moat against "Big Cloud rebrands our OSS as
   managed service" attacks (the MongoDB / Elastic / Redis
   pattern). Self-hosters are unaffected — they always could
   and still can run their own brnrd. Implement at the same
   time as the monorepo restructuring.
2. **Early-adopter pricing**: launch subscription is **$5 /
   month ($50 / year)** for the first **200 subscribers**;
   their price is grandfathered forever (Stripe-stable on
   their original `Price` ID). New joiners after subscriber
   #200 (or after 12 months from public launch, whichever
   first) pay **$7 / month ($70 / year)**. Early-adopter
   pricing creates a self-selecting "supporter cohort" with
   genuine skin-in-the-game loyalty; the $7 steady-state gives
   ~40% more revenue per subscriber for the long tail without
   moving past the sub-$10 psychological line. Annual discount
   stays ~17% in both phases.
3. **Trademark registration of `brr` + `brnrd`**:
   **deferred** at launch (no available budget); becomes
   **launch+12-month or first-€10K-revenue priority**,
   whichever comes first. Filed with EUIPO via a French IP
   lawyer (HugiMuni SAS as the applicant), estimated total
   cost **€800-1500** (lawyer fee + class-9 / class-42
   filing fees + buffer for opposition response). Once
   registered, no competitor can ship a service under the
   `brr` or `brnrd` names; forks must rebrand. This is the
   single highest-leverage defensive move per euro spent;
   the only reason it's #3 here is the budget timing.

These three together neutralise ~95% of the realistic
competitive threats at this project's scale. None of them
gate the OSS, race the price to the bottom, or break the
"self-hosted brnrd stays always-free with full feature
parity" promise.

## Move 1 — License split (MIT daemon + AGPL backend)

### Why a split, not one license

The daemon and the backend have different competitive
exposures:

- **Daemon (`src/brr/`)** runs on the user's laptop. Forking
  it doesn't compete with brnrd's business — brnrd's business
  is the hosted *service*, not the daemon code. Maximum
  community goodwill is the right posture here: anyone can
  fork the daemon, vendor it, ship derivatives, integrate it
  into commercial products without sharing changes.
- **Backend (`src/brnrd/` + `src/brnrd_web/`)** is the *thing
  someone could host as a competing service*. A permissive
  license here is what enables the "AWS rebrands MongoDB as
  DocumentDB" attack. The defensive answer is **copyleft**:
  ship the backend as **AGPLv3**, which means anyone running
  it as a network service must share their modifications under
  the same license. Self-hosters running unmodified brnrd are
  unaffected (no obligation to share anything if they're not
  modifying). A competing managed service must either share
  their changes (which makes brnrd better) or stop offering
  the service.

The license boundary lines up cleanly with the package
boundary already proposed in
[`decision-monorepo-structure.md`](decision-monorepo-structure.md):
`src/brr/` is one package; `src/brnrd/` and `src/brnrd_web/`
are separate packages that ship under different extras
(`pip install brr[backend]`). One repo, two licenses,
zero ambiguity about which file is which.

### Mechanics

- **Daemon license file**: keep the existing repo-root
  `LICENSE` (MIT) as the default. Stays exactly as today.
- **Backend license files**: add `src/brnrd/LICENSE`
  (AGPLv3) and `src/brnrd_web/LICENSE` (AGPLv3) alongside
  the per-package source.
- **`pyproject.toml` per-package metadata**: declare the
  per-extra license in the optional-dependencies metadata.
  Each extra's `License` classifier matches its package's
  actual license.
- **Top-level `LICENSE-OVERVIEW.md`** (or appended to
  README) explains the split: "the brr daemon (`src/brr/`)
  is MIT-licensed; the brnrd backend (`src/brnrd/`) and
  dashboard (`src/brnrd_web/`) are AGPLv3-licensed."
- **Contribution policy** for the AGPLv3 portions: standard
  AGPL inbound = inbound + AGPL terms; no CLA at launch
  (CLAs are a community-goodwill cost; revisit only if a
  re-license becomes operationally needed, which it won't
  for a long time).
- **`kb/`** stays under the daemon's permissive license; the
  knowledge base is documentation, not the protected
  business asset. (Re-evaluate if the kb starts shipping
  unique structured-content tooling worth protecting; not at
  launch.)

### Why AGPLv3 specifically (vs alternatives)

| License | Self-host friction | Competing-SaaS friction | Community signal | Notes |
|---------|-------------------|------------------------|------------------|-------|
| **AGPLv3** (chosen) | Zero | High (must open-source mods) | OSI-approved, neutral | The Goldilocks zone. Sentry, Plausible, Grafana Labs (some products), Mastodon — all use this exact pattern. |
| BUSL (Business Source License) | Zero on intended-use | Very high (explicit commercial-use restriction; can't run as SaaS) | Source-available, not OSI-approved | HashiCorp's choice. Stronger, but signals "we don't trust you"; some companies refuse to depend on it. |
| Elastic License v2 (ELv2) | Zero on intended-use | Very high (no managed-service of) | Source-available, not OSI-approved | Elastic's choice post-2021. Similar trade-offs to BUSL. |
| SSPL (Server Side Public License) | Zero | Extreme (requires open-sourcing the entire service infra) | Not OSI-approved; broadly distrusted | MongoDB's choice. Overreaches; cloud providers explicitly avoid it; bad community signal. |
| MIT / Apache 2.0 | Zero | Zero | Maximum goodwill | The default. Indefensible against Big Cloud rehosting. |

AGPL is the lightest-touch defense that actually works. It
preserves OSI-approved status (matters for some downstream
distribution paths), preserves self-hoster ergonomics
(running unmodified brnrd has no AGPL obligations beyond
copyright notice + source availability — which we publish
ourselves), and forces the specific attack we want to
prevent (commercial managed-service competitors) to either
contribute back or stop. Stronger source-available licenses
(BUSL / ELv2 / SSPL) add restrictions that hurt community
adoption far more than they add protection at this scale.

### Open questions

- **Dual-license arrangement**? Some projects offer a paid
  commercial license of the AGPL backend for companies who
  want to use it embedded in non-AGPL software. Defer; only
  worth setting up if a real customer asks. Cost to set up
  later is ~one-week of legal review.
- **Kb licensing**: currently inherits the daemon's MIT.
  Could move to CC-BY-SA for the prose specifically. Not
  blocking; revisit if the kb itself becomes a moat.
- **Pre-existing contributors**: brr has had external
  contributions under MIT. Anything that moves to
  `src/brnrd/` and goes AGPL must either be (a) written
  fresh, (b) covered by the contributor's MIT contribution
  (which is forward-compatible with AGPL relicensing of the
  combined work — MIT-into-AGPL is a one-way ratchet that's
  explicitly allowed), or (c) re-contributed under AGPL. At
  current contributor count this is a manageable inventory
  check, not a blocker.

## Move 2 — Early-adopter pricing ($5 → $7 step)

### Why early-adopter lock-in

Two effects compound:

1. **Self-selecting supporter cohort.** Users who subscribe
   in the first weeks of launch are explicitly the
   highest-trust, lowest-churn segment. Giving them a forever-
   low price ($5 vs $7) creates real loyalty — they paid
   in early, they stay because the deal is genuinely good,
   they evangelise. Linear, Notion, Plausible, Beeper, and
   many others have used this exact pattern; conversion +
   retention numbers from supporter cohorts run materially
   higher than baseline.
2. **Long-tail pricing headroom.** The steady-state $7 gives
   ~40% more revenue per subscriber than $5 without crossing
   the sub-$10 psychological line where users start
   comparison-shopping seriously ("is this worth $9?"). At
   200 subscribers the difference is small ($1000 vs $1400
   MRR); at 1000 subscribers it's $2000 / month, which is
   real maintainer time.

The "5 for early adopters / 7 for the afterparty" phrasing
is the user's framing; the page locks the framing in canonical
language.

### Mechanics — how the step actually ships

- **Stripe**: one `Product` ("Brnrd Subscription"), two
  monthly `Price` IDs ($5 and $7), two annual `Price` IDs
  ($50 and $70). Early-adopter checkout sessions point at
  the $5 / $50 prices; post-cohort checkout sessions point
  at $7 / $70. Existing subscribers stay on their original
  `Price` forever (Stripe never auto-migrates subscriptions
  to a new price; the subscription object holds the price
  reference).
- **Cohort boundary**: **first 200 subscribers OR
  12 months from public launch date**, whichever comes
  first. Atomic counter on the brnrd backend gates which
  price the checkout endpoint emits.
- **Switching guard**: at the boundary, the dashboard /
  CLI "subscribe" flow flips to the $7 price atomically
  on the 201st subscription start (or on the launch+12-mo
  date), with a public announcement ("first 200 supporters,
  thank you; new joiners are $7/mo from today"). No grand-
  fathered-cohort UI is needed — existing subs are
  automatically grandfathered by virtue of Stripe holding
  their original `Price` reference.
- **Annual subscribers on the $50 price** retain it through
  every renewal as long as they don't cancel (Stripe
  semantic). If they cancel and re-subscribe later, the new
  subscription uses the then-current price ($70 if past the
  boundary).
- **Documentation surface**: the pricing page shows both
  prices side-by-side during the cohort phase ("$5 supporter
  price, available for the first 200 subscribers — Y / 200
  spots left"). Live counter drives urgency without feeling
  scammy.

### Why 200 specifically (vs other thresholds)

- 200 subs × $5/mo = $1000 MRR on the supporter cohort,
  enough to credibly say "this project is sustainable"
  but small enough to feel limited.
- At typical OSS-with-managed-tier conversion rates
  (1-3% of GitHub-star traffic), 200 subs is reachable in
  the first 3-6 months post-launch — fast enough that the
  scarcity feels real, slow enough that it doesn't gate
  out actual early adopters.
- 12-month cap as the alternate trigger covers the "we
  hit 200 fast" AND the "we don't hit 200 in a year"
  cases — either way the price step lands within 12 months.
- Round-number psychology: 200 is mentally available as a
  goal ("we're at 73 / 200") in a way 167 isn't.

### Open questions

- **Reset / extension if 200 is hit very quickly** (say,
  within 2 weeks)? Probably keep the boundary firm — the
  supporter cohort isn't "anyone who arrived early," it's
  "anyone who arrived during the supporter window." A
  hit-200-fast outcome is the best signal and shouldn't be
  diluted by reopening.
- **Lifetime / one-time perks for the supporter cohort
  beyond price**? Possibly: early access to upcoming features,
  a "supporter" badge in the dashboard, a Discord role.
  Nice-to-haves; defer past launch.
- **What about the $50 annual supporter price specifically
  — re-bundle with extra credits as an upgrade enticement
  later**? Don't overcomplicate at launch. The $5/$50 split
  is plenty.

## Move 3 — Trademark registration (deferred but prioritized)

### Decision

Register `brr` and `brnrd` as EU trademarks with the EUIPO
(European Union Intellectual Property Office), via a French
IP lawyer (HugiMuni SAS is the applicant). **Deferred at
launch** for budget reasons. Becomes priority work when
**either** trigger fires:

- 12 months from public launch, OR
- First €10K of cumulative revenue, OR
- First credible competitor attempt observed,

whichever comes first.

### Why trademark at all

Trademark protection is the only defensive move that:

- Is one-shot (file once, valid 10 years, renewable
  cheaply) — not a recurring cost or operational burden;
- Is enforceable against existing competitors after the
  fact (license changes only protect against *future*
  code use; trademark protects against *anyone* using
  the name commercially);
- Is cheap relative to the protection it provides
  (€800-1500 vs the alternative of seeing "Brnrd Pro by
  Some Other Company" appear in your launch week);
- Does NOT require the OSS to be encumbered in any way
  (you can MIT/Apache the daemon AND own the trademark
  on the name — these are independent).

The specific attack it neutralises: someone forks brr,
runs it on their own infra, calls it "Brnrd" (or "brr Pro",
"brnrd.io", etc.), and competes for the same brand
recognition you've been building. Without trademark, this
is legal and effective. With trademark, this is grounds for
a cease-and-desist that any decent IP lawyer can serve for
~€500 and that the competitor will obey because losing the
name is fatal to their product.

### Why deferred (not now)

- **Budget**: €800-1500 is real money pre-revenue. Lawyer
  fee runs ~€400-700; EUIPO filing fee is €850 base
  (€1000 if filing in two classes — which we should, for
  software + SaaS). Add buffer for opposition response
  (if any third party challenges) at €300-500.
- **Risk profile is small at zero users**: the realistic
  competitive threat scales with brand visibility; pre-
  launch and early-launch there's nothing meaningful to
  protect yet.
- **Application timing**: filing before public launch is
  *ideal* (no prior third-party use to challenge);
  filing within the first 6-12 months is *acceptable*
  (anyone who picks up the name during the window is
  unlikely to have established meaningful prior use, and
  registration is retroactive in its protective effect on
  *future* commercial use). Filing beyond 18-24 months
  starts to risk genuine third-party use claims.

The "12 months / €10K revenue / first competitor" trigger
covers all three risk axes: time-to-visibility, financial
capacity, and observed-threat. Pre-launch budget reservation
of €1500 in the post-launch ops budget; first revenue tranche
above operating cost flows to the trademark fund.

### Concrete pre-launch task

Add **"register brr + brnrd trademarks"** to the post-launch
priority list. When triggered, the path is:

1. Brief lawyer (~30 min phone, ~€100 retainer or rolled
   into filing fee). Specify EU coverage; classes 9 (software)
   + 42 (SaaS, hosting). Provide brand assets: word marks
   ("brr" and "brnrd"); optional figurative mark on the gear
   logo if budget permits (additional €850 per mark).
2. File via lawyer (lawyer handles EUIPO portal + initial
   examination response). Typical timeline: 4-6 months to
   registration if no opposition; 12-18 months if opposed.
3. Receive registration; updated `LICENSE-OVERVIEW.md` /
   README footer notes "brr® and brnrd® are registered
   trademarks of HugiMuni SAS."

Optional later additions:
- US trademark via USPTO if user base shifts US-heavy
  (~$1500 one-shot via a US attorney; less defensible
  filing alone).
- UK trademark via IPO (post-Brexit; €200-400; only if
  meaningful UK user base).

### Open questions

- **Defensive registration of look-alikes** ("brrrun",
  "barnard", "brrcloud", etc.)? Generally not worth the
  cost; one strong registration on the canonical names is
  enough. Revisit only if specific squatters appear.
- **Trademark vs domain strategy**: brnrd.dev is owned;
  brnrd.com / brnrd.io are not (~€5K and ~€30 respectively
  per the user's domain research). Trademark registration
  is independent of domain ownership — owning the trademark
  lets you challenge bad-faith domain registrations via
  UDRP / EURid procedures cheaply if it ever matters.
  Don't pre-buy defensive domains; rely on trademark.

## Adjacent moves (already covered elsewhere, listed for completeness)

These are part of the defense posture too, but live in
other pages because they're load-bearing for other
decisions:

- **`brnrd.dev` is the "official" deployment.** First to
  ship features (1 release ahead of the OSS docs page),
  has the verified bot accounts (`@brnrdbot` on TG,
  `Brnrd` GH App), runs the official Discord. Operational
  discipline more than a separate decision; tracked in
  [`subject-managed-mode.md`](subject-managed-mode.md).
- **Integration stickiness as the soft moat.** Installed
  bots in user chats, authorized GH Apps, configured project
  bindings, AI credentials in the vault, audit history —
  migrating to a clone means re-installing every integration.
  Per the data-minimization principle in
  [`design-brnrd-protocol.md`](design-brnrd-protocol.md), we
  hold very little, but the few things we hold are sticky.
  No work required; emerges from the architecture.
- **Brand + content + community.** Founder-attached project,
  blog, Discord, short demo videos, the EU-resident /
  French-legal-entity / privacy-first angle. Tracked in
  [`notes-pondering-fleet.md`](notes-pondering-fleet.md) §1
  pre-launch breadcrumbs and the upcoming launch-marketing
  plan (not yet written).
- **Security posture as a moat**: per-account envelope-key
  encryption, no-code-stored guarantee, full audit log. A
  competing fork would have to match this OR less, and
  users compare trust scores. Already documented in
  [`design-brnrd-protocol.md`](design-brnrd-protocol.md) →
  "Credential security model" + "Data minimization."
- **Pricing AT the floor.** $5/$7 is low enough that a
  rational competitor can't undercut it materially without
  losing money per sub. Documented in
  [`decision-pricing-shape.md`](decision-pricing-shape.md).
- **BYO-everything-for-subscribers** as an "open and honest"
  signal that doubles as a moat. By design, paying customers
  can bring their own Fly token (and, in the future, their
  own connector OAuth tokens for Google / Linear / etc.)
  instead of routing through brnrd-side credentials. A
  competing fork can't easily out-open us — being more open
  on credentials means giving up more revenue, which only
  works if their model already runs at higher per-customer
  revenue (e.g. paid usage / per-seat / etc.); ours runs at
  $5/$7. Documented in
  [`decision-pricing-shape.md`](decision-pricing-shape.md)
  § "Compute: managed vs BYO" and
  [`design-brnrd-protocol.md`](design-brnrd-protocol.md)
  § "BYO compute".

## What we explicitly do NOT do

- **Don't go BUSL / ELv2 / SSPL.** Source-available licenses
  signal "we don't trust the community"; they actively poison
  contribution + integration. The defense premium they buy
  over AGPL isn't worth the goodwill cost at our scale.
  Revisit only if AGPL fails to prevent a real attack (which
  it won't, because the realistic attackers are not Big
  Cloud — they're indie hackers, who AGPL stops cold).
- **Don't gate any feature behind the hosted-only fence.**
  The promise is "self-hosted brnrd stays always-free with
  full feature parity." Breaking that promise is the single
  fastest way to torch the community-trust moat. Hosted-
  brnrd's advantages must come from running it for the user
  (uptime, scale, integrations, support), not from withholding
  code.
- **Don't lock subscribers into brnrd's cloud account when
  their env class supports BYO.** The subscription buys access
  to the *platform* (the always-on bots, the multi-project
  dispatcher, the dashboard, the audit log, the curated
  managed-compute fallback) — it does not buy exclusive use
  of brnrd's compute. Subscribers can bring their own Fly /
  Modal / Daytona / etc. token for any cloud env we ship
  managed (per
  [`decision-pricing-shape.md`](decision-pricing-shape.md)
  § "Compute: managed vs BYO" and
  [`design-brnrd-protocol.md`](design-brnrd-protocol.md)
  § "BYO compute"). Same principle pre-applies to future
  agentic-secretary connectors. This is the "open and honest"
  posture that distinguishes brnrd from rent-seeking
  reseller-shaped competitors AND strengthens the community-
  trust moat — subscribers stay because the platform is worth
  the sub, not because their data / credentials / spawns are
  hostage to our cloud account.
- **Don't race to the bottom on price.** If a $3 competitor
  appears, do not match. Their math doesn't work
  (Stripe + compute + infra eats their margin); they will
  either raise prices or fold. Matching them turns a non-
  competitor into a competitor.
- **Don't pre-buy defensive domains** (brnrd.com / .io /
  .tech / etc.). Trademark registration achieves the same
  protection at a fraction of the cost via UDRP if a domain
  squatter ever appears.
- **Don't require a CLA at launch.** CLAs add contribution
  friction in exchange for re-licensing flexibility we
  don't need. Add only if a specific business reason
  emerges (e.g. a real customer asks for a commercial dual-
  license arrangement).

## Alternatives considered

### Alt 1 — Pure permissive (MIT / Apache everything)

The default. Maximum community goodwill. Indefensible
against Big Cloud rehosting (Mongo / Elastic / Redis story).
Rejected because the asymmetric daemon-vs-backend exposure
makes the AGPL-backend / MIT-daemon split a strictly better
trade-off (preserves goodwill where it matters; adds defense
where it matters).

### Alt 2 — Pure AGPL (daemon AND backend)

Stronger defense; loses the "anyone can vendor the daemon
into their own product without strings" property. AGPL on
the daemon would discourage integrations (the daemon's
purpose is to BE integrated into agentic workflows; the
fewer license-question conversations users have to have with
their legal teams, the better). The split-license posture
captures the best of both.

### Alt 3 — BUSL / ELv2 for the backend (instead of AGPL)

Stronger defense than AGPL (explicit anti-managed-service
clause; doesn't depend on the AGPL network-distribution
trigger). Loses OSI-approved status — some downstream paths
(Linux distribution packagers, university adoption, some
corporate procurement) explicitly reject non-OSI licenses.
Also signals "we're the kind of project that might pull
the rug" which damages community trust. AGPL captures
~95% of the protection at far lower community-trust cost.
Revisit only if AGPL is observed to fail in practice.

### Alt 4 — SSPL for the backend

MongoDB's choice. Over-broad (requires open-sourcing the
*entire* service infrastructure surrounding the SSPL
software, not just modifications). Cloud providers
explicitly avoid SSPL software. Broadly perceived as a
bad-faith move. Rejected.

### Alt 5 — Trademark from day one (don't defer)

Best protection earliest. Rejected at launch only because
of €800-1500 budget pressure; the post-launch trigger
ensures registration lands well within the realistic
risk window. If €1500 becomes available pre-launch (e.g.
from a sponsor or pre-launch sales), promote trademark
to a same-day move — there's no downside to filing
earlier.

### Alt 6 — Defensive domains instead of trademark

Pre-register every plausible look-alike domain. Costs
~€500-1000 per year in registration fees; covers nothing
of substance (anyone can register a new domain on any TLD
you didn't pre-buy). Trademark + UDRP procedure covers
the actual attack pattern at much lower ongoing cost.
Rejected.

### Alt 7 — Single-price launch (no early-adopter step)

Launch at $5 forever, no step. Simpler. Loses the
supporter-cohort loyalty effect AND leaves long-tail
revenue on the table. Reverse direction (launch at $7,
drop to $5 later for promo) reads as a bait-and-switch
the other way. The "early-adopter price, public price"
pattern is well-established and reads as honest
("supporters got in first; new users pay the going
rate").

### Alt 8 — Launch at $7 directly (no $5 phase)

Simpler. Loses the launch-momentum signal that the $5
"supporter price" creates. Conversion data from comparable
launches (Plausible's 50%-off-for-life supporter run,
Linear's $5/$10 step, Beeper's beta-tester forever-cheap
tier) strongly suggests the supporter phase is materially
worth the foregone $2/sub × 200 = $400/mo in lifetime
revenue ($4800 / year — non-trivial but well-bounded).
The launch-week momentum from "founding supporters" is
worth more than that.

## Open questions

- **Exact post-launch trademark trigger calibration**: 12
  months / €10K revenue / first competitor — whichever
  first. Revisit at month 6 with actual revenue + competitor
  data; if any of those signals lands earlier, accelerate.
- **Supporter cohort cap (200 vs 100 vs 500)**: 200 is the
  current pick. If launch traction is meaningfully faster
  or slower than expected, adjust at month 3 with actual
  signup-rate data — but only adjust *upward* if appropriate
  (extending the cohort), never downward (closing supporters
  earlier than promised).
- **Annual supporter discount level ($50 vs $48 vs $45)**:
  Currently $50 (~17% off monthly). Could go more aggressive
  ($45 = 25% off) to push annual specifically. Defer; one
  knob at a time.
- **Trademark scope expansion**: filing as EU + word marks
  only at the initial registration is the budget-friendly
  path. Adding figurative-mark (gear logo) registration is
  ~€850 extra and worthwhile when the logo becomes a
  recognisable brand asset (post-launch). US + UK extensions
  only if user base shifts meaningfully to those geographies.
- **AGPL contribution-policy edge cases**: large external
  contributions to `src/brnrd/` may eventually need a
  contributor agreement to keep relicensing flexibility.
  Defer until the first such contribution lands.

## Read next

1. [`decision-pricing-shape.md`](decision-pricing-shape.md) —
   the pricing model the $5 / $7 step lives within (tier
   structure, included compute, sustainability math).
2. [`decision-monorepo-structure.md`](decision-monorepo-structure.md) —
   the package boundary the license split aligns with
   (`src/brr/` MIT, `src/brnrd/` + `src/brnrd_web/` AGPL).
3. [`subject-managed-mode.md`](subject-managed-mode.md) —
   the hosted product being defended; the adjacent moats
   (verified bot accounts, official deployment posture,
   integration stickiness, security posture).
4. [`design-brnrd-protocol.md`](design-brnrd-protocol.md) —
   the data-minimization + credential security posture
   that's itself part of the trust-as-moat strategy.

## Lineage

- 2026-05-26 — drafted as a follow-up to the "$5/month is
  reasonable, but what stops a competitor cloning the OSS
  and undercutting?" question. Three-prong decision (license
  split + early-adopter pricing + deferred-but-prioritized
  trademark) captures the user's "5 for early adopters, six
  seven for the afterparty, license is the right thing,
  trademark is post-launch priority" framing in canonical
  form. Pondering provenance in
  [`notes-pondering-fleet.md`](notes-pondering-fleet.md)
  §1 latest breadcrumb.
- 2026-05-26 (locking pass — BYO posture) — "Don't lock
  subscribers into brnrd's cloud" promoted to a load-bearing
  anti-pattern alongside the existing "don't gate features
  behind hosted-only" line. Adjacent moves grew a fifth entry
  for BYO-everything-for-subscribers as a moat amplifier
  ("open and honest" signal that's hard for a competing fork
  to beat without giving up more revenue than their model can
  bear). Driven by the user's "we can actually not defend
  ourself too much and allow byo everything on top of that"
  framing.
