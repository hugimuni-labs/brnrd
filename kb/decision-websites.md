# Decision: two websites — brr.dev (OSS landing) + brnrd.dev (hosted product)

**Status: accepted 2026-05-26** (locked in PR #40 MR review,
locking pass IV — user's call: "do we make two websites or
one (e.g. brr oss project landing and brnrd as an actual
product, or brr as a sub page? I am leaning towards the
two)" → two it is). Names the two-property web shape for
the brr-family product line, so the OSS landing surface and
the hosted-product signup surface don't collapse into a
single page that has to do two jobs poorly. Companion to
[`decision-monorepo-structure.md`](decision-monorepo-structure.md)
(the package boundary the two sites sit on top of),
[`decision-licensing-and-defense.md`](decision-licensing-and-defense.md)
(the trust-signal layer this shape backs: "we charge for
ops, not for crippled OSS"), and
[`decision-pricing-shape.md`](decision-pricing-shape.md) §
"Trust signals that ship with the pricing page" (the trust
content brnrd.dev's pricing page hosts).

## Decision

**Two distinct web properties at two distinct URLs**, each
with one clear job:

| Property | URL | What it is | Who runs it | Auth + payments |
|----------|-----|-----------|-------------|-----------------|
| **brr.dev** (OSS landing) | `https://brr.dev` (or `https://brr.run` if `.dev` is unavailable — TBD at registration time) | OSS project landing page — what brr is, docs, contributor info, "always free + self-hostable" framing, self-hosting guide for brnrd | GitHub Pages or a static-site host (Netlify / Cloudflare Pages); content lives in the monorepo under `docs/site/` or similar | None — fully public, no signup, no payments |
| **brnrd.dev** (hosted product) | `https://brnrd.dev` | Hosted-product surface: signup, pricing, dashboard, billing portal, marketing pages explaining what subscribers get | Live web app served by the `brnrd_web` component from the monorepo, deployed to whatever ASGI host brnrd runs on | Stripe-integrated signup + dashboard auth + billing portal per [`design-billing.md`](design-billing.md) |

Each site has one job; collapsing them would either
contaminate the OSS landing with payment surfaces (bad trust
signal) or hide the OSS posture under a product chrome (bad
OSS signal). Keeping them separate is the simplest way to
honour both audiences.

## Cross-linking is the trust signal

The two URLs interlink prominently, and the interlinks
themselves carry the project's "we charge for ops, not for
OSS" promise:

**brr.dev links to brnrd.dev:**

- The "don't want to host yourself?" callout in the
  quickstart points at `brnrd.dev/signup`. Matter-of-fact,
  no sales pressure: "If you'd rather we operate the
  always-on bits, see brnrd.dev — same software, hosted."
- The pricing-shape "Trust signals" section's "self-hosted
  is always free, full feature parity" line is the dominant
  framing on brr.dev; brnrd.dev is the *option*, not the
  default.

**brnrd.dev links to brr.dev:**

- The footer of every brnrd.dev page links to brr.dev with
  the phrase "Powered by the open-source brr — full feature
  parity on self-hosted."
- The pricing page on brnrd.dev cross-links to brr.dev's
  self-hosting guide with one sentence: "Don't want a card
  on file? Run brnrd yourself; the source is at
  github.com/Gurio/brr."
- The signup page mentions self-hosted as a path: "Or run
  your own at brr.dev/self-host (free, full parity, you
  handle the ops)."

This shape makes "we don't have you locked in" tangible.
Two distinct URLs, each acknowledging the other as a real
alternative, lets the trust pitch be a thing the user can
*see* rather than a thing they have to take on faith.

## What lives where

### brr.dev (OSS landing)

- **Home** — what brr is in 3 sentences + the gear-logo +
  the two CTAs ("Get started" → docs, "See it hosted at
  brnrd.dev" → cross-link).
- **Docs** — quickstart, CLI reference, env / runner /
  gate setup, kb methodology, dev guide. Generated from
  the monorepo's `src/brr/docs/` directory + the `kb/`.
- **Self-hosting brnrd** — single page covering the brnrd
  deployment-templates story per
  [`plan-daemon-deployment-templates.md`](plan-daemon-deployment-templates.md):
  "here's how to run your own brnrd if you want to
  self-host the managed-mode side too." Includes Stripe-
  alternative pointers (Lemon Squeezy, Paddle, etc.) for
  self-hosted billing.
- **Contribute** — GitHub link, contributor guide,
  AGENTS.md callout.
- **About** — HugiMuni SAS attribution, contact info.

No tracking analytics beyond the most basic (page-view
counts on a privacy-respecting tool like Plausible or
GoatCounter). No cookies. No login. No payment surfaces.

### brnrd.dev (hosted product)

- **Home** — what brnrd is in 3 sentences (one paragraph
  about brr the OSS, one paragraph about brnrd the
  managed surface, one paragraph about why both exist) +
  "Subscribe" CTA + "Try Free" CTA.
- **Pricing** — the full canonical pricing breakdown per
  [`decision-pricing-shape.md`](decision-pricing-shape.md):
  Free tier, Subscribed tier, supporter $5 → public $7
  cohort step, included compute, project caps,
  signup-bonus, BYO compute opt-in, "always free,
  full feature parity on self-hosted" trust callout
  cross-linking back to brr.dev.
- **Signup / pair** — Stripe-integrated signup; landing
  point for the `brnrd connect` browser hand-off; sends
  the OAuth-style pairing code back to the local CLI.
- **Dashboard** — the eight views per
  [`plan-brnrd-dashboard-mvp.md`](plan-brnrd-dashboard-mvp.md)
  (accounts/projects, project detail, task/event detail,
  conversation view, AI credentials, failover policy,
  audit log, allowance + usage).
- **Billing portal** — link out to Stripe Customer Portal
  for card / invoice / cancellation; per
  [`design-billing.md`](design-billing.md).
- **Status** — `status.brnrd.dev` subdomain (or
  embedded), uptime page for the managed gates + the
  compute backend.

Standard product analytics (privacy-respecting tier),
auth via the brnrd account system, payments via Stripe.

## Why not other shapes considered

| Shape | Why we passed |
|-------|---------------|
| **One site, brr.dev with brnrd.dev as a redirect** | Reads as "brnrd is just brr's commercial mode" — undercuts the "OSS is the canonical product, hosted is a service" framing. brnrd-as-service is real, distinct enough to deserve its own surface |
| **One site, brnrd.dev with brr.dev as a redirect** | Worse — reads as "OSS is the marketing funnel for the paid product." Hostile to the OSS-first trust pitch |
| **One site at a neutral URL** (e.g. `gear.dev`) | Adds a third brand surface to maintain; doesn't help either audience; competes with the existing brand recognition (brr / brnrd are the names that ship in the CLI) |
| **brr.dev as the only public site; brnrd.dev hidden behind product login** | The signup / pricing surface needs to be public-discoverable; hiding it behind a login defeats acquisition. Public signup needs a public URL |
| **One site with `/oss` and `/hosted` subpaths** | Adds path-discriminator overhead to every link; doesn't actually separate the two jobs; trust signal "two distinct URLs" weakens |

Two URLs, each with one job, with strong mutual cross-links
is the cleanest shape. Each property can evolve at its own
pace; the OSS landing can be a static-site simplicity win,
the product site can be the live-web-app complexity hub.

## Implementation slices (when these get built)

Not blocking the OSS-side launch — brr.dev can be a single
README-derived index page until the product surfaces are
real. Concrete slice shape, in landing order:

1. **brr.dev MVP** (static site). Markdown-driven landing
   page + docs index pointing to the monorepo's
   `src/brr/docs/`. One day of focused work; ~1K LOC of
   HTML/CSS + content. Lands as soon as we have a
   coherent OSS pitch (= now-ish).
2. **brnrd.dev signup + dashboard MVP**. The eight-view
   dashboard from
   [`plan-brnrd-dashboard-mvp.md`](plan-brnrd-dashboard-mvp.md)
   plus the signup + Stripe-pair surfaces. The dashboard
   plan is the load-bearing implementation story; the
   marketing pages (home, pricing) are ~2 days on top.
3. **Status subdomain / status.brnrd.dev**. Defer until
   we have customers; one of UptimeRobot / Better Stack /
   roll-our-own. Tracked separately.

## Brand consistency

- Gear logo + colour palette + typography shared across
  both sites — they're the same product family, not
  unrelated.
- Footer pattern shared: HugiMuni SAS attribution +
  cross-link to the other property + GitHub link.
- Header pattern differs deliberately — brr.dev's nav
  surfaces docs / self-host / contribute; brnrd.dev's nav
  surfaces dashboard / pricing / signup. Same brand chrome,
  different jobs.

## Read next

1. [`decision-monorepo-structure.md`](decision-monorepo-structure.md)
   for where the two sites' code lives in the monorepo
   (`src/brnrd_web/` for the live app; `docs/site/` or
   equivalent for the static landing).
2. [`decision-pricing-shape.md`](decision-pricing-shape.md)
   for the canonical pricing content brnrd.dev's pricing
   page hosts, plus the "Trust signals" content both sites
   share.
3. [`decision-licensing-and-defense.md`](decision-licensing-and-defense.md)
   for the moat shape the two-URL structure backs.
4. [`plan-brnrd-dashboard-mvp.md`](plan-brnrd-dashboard-mvp.md)
   for the load-bearing implementation story on the
   brnrd.dev side.
5. [`plan-daemon-deployment-templates.md`](plan-daemon-deployment-templates.md)
   for the brr.dev self-hosting guide's source material.

## Lineage

- 2026-05-26 (locking pass IV — drafted). User asked "do
  we make two websites or one (e.g. brr oss project landing
  and brnrd as an actual product, or brr as a sub page? I
  am leaning towards the two)." Locked the two-URL shape
  because it honours both audiences cleanly + makes the
  "we charge for ops, not for crippled OSS" trust pitch
  *visible* (two real URLs, each acknowledging the other
  as a real alternative, beats any in-page disclaimer).
  Page enumerates the per-site content responsibilities,
  the cross-linking pattern that carries the trust signal,
  the shapes considered + rejected, and a brief
  implementation-slices section so the OSS-side landing
  doesn't block on the product-side work.
