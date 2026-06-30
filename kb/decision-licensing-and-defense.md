# Decision: licensing + competitive-defense posture

Status: accepted on 2026-05-26 (locked in PR #40 MR review).

brnrd defends the hosted business with three concrete moves: a split license,
supporter pricing, and deferred trademark registration. The posture protects the
managed service without making the OSS daemon hostile to use, fork, or
integrate. It sits beside [`decision-pricing-shape.md`](decision-pricing-shape.md),
[`decision-monorepo-structure.md`](decision-monorepo-structure.md), and
[`subject-managed-mode.md`](subject-managed-mode.md).

## Current Decision

1. **Split the license boundary by package.** `src/brr/` stays permissive
   (MIT). `src/brnrd/` and `src/brnrd_web/` ship as AGPLv3. The daemon is the
   integration-friendly local tool; the backend and dashboard are the managed
   service someone could otherwise rehost as a competitor.
2. **Launch with supporter pricing.** The first 200 subscribers, or subscribers
   who join within the first 12 months from public launch, whichever limit hits
   first, get $5/month or $50/year forever while their subscription stays active.
   Later subscribers pay $7/month or $70/year.
3. **Register `brr` and `brnrd` trademarks after launch.** Trademark filing is
   deferred for budget, then becomes priority at the earliest of launch+12
   months, first EUR 10K cumulative revenue, or the first credible competitor
   using a confusing name. HugiMuni SAS is the applicant; EUIPO word marks are
   the first filing path.

These choices are deliberately modest. They do not gate features behind hosted
brnrd, force self-hosters into brnrd-owned cloud accounts, require a CLA at
launch, or race price to the bottom.

## License Split

The daemon and backend have different competitive exposure:

- `src/brr/` is local software users should be able to vendor, fork, and embed
  with minimal legal friction. MIT preserves community goodwill and keeps the
  daemon easy to integrate into other agentic workflows.
- `src/brnrd/` and `src/brnrd_web/` are the hosted-product surface. AGPLv3
  blocks the realistic managed-service clone: anyone running a modified brnrd as
  a network service must share those modifications under the same license.
  Self-hosters running the published code are unaffected.

The boundary matches the monorepo package layout in
[`decision-monorepo-structure.md`](decision-monorepo-structure.md): one repo, one
shared kb, one Python package with extras, but separate source directories and
per-package license files. The root `LICENSE` remains MIT; `src/brnrd/LICENSE`
and `src/brnrd_web/LICENSE` carry AGPLv3; a top-level license overview explains
the split.

Inbound contributions use the license of the touched package. No CLA is required
at launch. Revisit only if a concrete relicensing or commercial dual-license
need appears.

## Supporter Pricing

The supporter cohort is both a launch-growth tool and a loyalty contract:

- Supporters get a real, permanent discount for arriving early.
- The public $7 price keeps the steady-state subscription below the $10 mental
  threshold while giving roughly 40% more long-tail revenue per subscriber than
  $5.
- The 200-subscriber cap is large enough to build momentum and small enough to
  feel like a meaningful early cohort. The 12-month cap prevents a stale
  "early-adopter" price from lingering if growth is slower than expected.

Stripe enforces the grandfathering mechanically: early checkout sessions use the
$5/$50 `Price` IDs, later checkout sessions use $7/$70, and existing
subscriptions keep their original `Price` while active. Cancelling and
re-subscribing after the boundary uses the then-current public price.

The pricing page can show the supporter counter during launch, for example
"Y / 200 supporter spots left." It should not promise a shrinking deadline that
the project would later be tempted to move downward.

## Trademark Trigger

Trademark is the cheapest strong defense for the names themselves, but the
pre-revenue budget cost is real. The accepted path is:

- File EUIPO word marks for `brr` and `brnrd` through a French IP lawyer, with
  HugiMuni SAS as applicant.
- Budget EUR 800-1500 for lawyer fees, class-9 / class-42 filing, and opposition
  buffer.
- Treat US, UK, or figurative-logo filings as later expansions when user base or
  brand recognition justifies them.

This protects against a fork or competitor trading on the project names. It is
more durable than defensive domain buying: a trademark plus UDRP/EURid paths
covers confusing bad-faith domains without paying yearly for every possible TLD.

## Adjacent Moats

Several defenses live elsewhere because they are broader product contracts:

- `brnrd.dev` is the official managed deployment, with verified bot identities
  and first-party release discipline. See
  [`subject-managed-mode.md`](subject-managed-mode.md).
- Integration stickiness comes from installed bots, GitHub App authorization,
  repo/channel routes, credential vault entries, audit history, and dashboard
  continuity. See [`design-brnrd-protocol.md`](design-brnrd-protocol.md).
- Security posture is part of the moat: data minimization, envelope-key
  credential storage, no-code-stored guarantees, and auditability.
- BYO credentials and BYO compute are part of the promise. Subscribers can bring
  their own cloud-platform credentials for any managed env class we ship. A
  competitor cannot easily out-open brnrd on credentials without giving up more
  revenue than a low-price subscription model can afford.

## Rejected Alternatives

- **Pure permissive licensing.** MIT/Apache everywhere maximizes goodwill but
  leaves the hosted backend defenseless against managed-service rehosting.
- **Pure AGPL.** AGPL on the daemon would raise legal friction exactly where the
  project wants integrations and downstream experimentation.
- **BUSL, ELv2, or SSPL for the backend.** These source-available licenses add
  stronger anti-SaaS restrictions, but lose OSI-approved status and signal more
  distrust than this project needs. AGPLv3 buys enough defense with much less
  community cost.
- **Hosted-only feature gating.** Self-hosted brnrd stays always-free with full
  feature parity. Hosted brnrd competes by operating the service, not by
  withholding code.
- **Locking subscribers into brnrd-owned cloud accounts.** Subscription buys the
  platform and managed fallback, not hostage control over a user's compute or
  credentials.
- **Racing down on price.** A $3 competitor should not be matched; Stripe,
  compute, support, and infra make that price structurally weak.
- **Defensive domain hoarding.** Trademark plus dispute procedures covers the
  real attack more cheaply than buying many TLDs forever.

## Open Follow-Ups

- Recheck trademark timing at month 6 after launch, using actual revenue and
  competitor signals. Accelerate if any trigger arrives early.
- Adjust the supporter cap only upward if traction is slower than expected. Do
  not close the promised cohort earlier than advertised.
- Revisit the annual supporter price only if launch conversion shows annual
  uptake needs a stronger push.
- Consider a commercial dual-license or CLA only when a real customer or large
  backend contribution creates the need.
- Add figurative-logo, US, or UK trademarks only when the brand or user base
  justifies the extra filing cost.

## Read Next

1. [`decision-pricing-shape.md`](decision-pricing-shape.md) for the broader
   pricing model and sustainability math.
2. [`decision-monorepo-structure.md`](decision-monorepo-structure.md) for the
   package boundary that carries the license split.
3. [`subject-managed-mode.md`](subject-managed-mode.md) for the hosted product
   being protected.
4. [`design-brnrd-protocol.md`](design-brnrd-protocol.md) for the security,
   credential, routing, and data-minimization surfaces that reinforce the moat.

## Lineage

- 2026-05-26 - Drafted after the "$5/month is reasonable, but what stops a
  competitor cloning the OSS and undercutting?" question. Locked the three-prong
  defense: license split, supporter pricing, deferred trademark.
- 2026-05-26 - Expanded the anti-pattern surface with "do not lock subscribers
  into brnrd's cloud"; BYO-everything-for-subscribers became part of the moat.
- 2026-06-29 - Compressed from a proposal-shaped page into current-state
  synthesis; no decision changed.
