# Consumer-law attribution questions

These two questions were added after the original nine because paid Stripe
checkout code merged on 2026-07-21. The repository does not ignore either
issue: the Terms source records both as `LAWYER` questions and deliberately
does not draft obligations on Stripe's behalf. Our position is that Stripe,
as merchant of record, is the consumer-facing seller; the point of review is
to confirm or break that attribution.

## Search performed

A repository-wide, case-insensitive search covered `CGV`, *conditions
générales de vente*, billing terms, withdrawal, *rétractation*, waiver,
L221-18, L221-28, mediation, *médiateur*, L612-1, and L616-1. Checkout UI,
client calls, API routes, and the Stripe Checkout payload were then read
directly.

Findings:

- There is no dedicated CGV route or file named as *conditions générales de
  vente*. Paid-plan, cancellation, refund, and statutory-right language is in
  Terms §10:
  [`src/frontend/src/routes/terms/+page.svelte`](../../../src/frontend/src/routes/terms/+page.svelte).
- Terms §10's source comment expressly names Code de la consommation
  L221-18/L221-28, states the Stripe-MoR attribution question, and says the
  pricing-to-checkout flow must change if Stripe does not render the required
  presentation. That comment is drafting metadata, not text visible to a
  customer.
- The brnrd client sends only `cadence` when it asks the backend to start a
  subscription checkout:
  [`src/frontend/src/lib/billing.ts`](../../../src/frontend/src/lib/billing.ts).
  The backend creates a Stripe-hosted session with the customer, price,
  quantity, promotion-code flag, and return URLs:
  [`src/brnrd/stripe_api.py`](../../../src/brnrd/stripe_api.py). There is no
  brnrd-rendered withdrawal waiver or consent control in that flow.
- The Stripe integration records live-probed Managed Payments behavior:
  Stripe rejects `automatic_tax` and `tax_id_collection` because Stripe is
  acting as merchant of record. The repository cannot show what the
  Stripe-hosted checkout page renders in production.
- No consumer-mediation body is named in any public legal page or checkout
  code. Terms §16's source comment expressly records that none is designated.

## A. Withdrawal right and digital-service waiver

**Question.** For the Stripe Managed Payments transaction, who must present the
14-day withdrawal right and, if immediate performance is intended, obtain the
consumer's express request/consent and acknowledgment of any loss of that
right?

**Our position.** Stripe is the merchant of record and seller to the consumer,
while HugiMuni's commercial leg is B2B to Stripe. On that reading, Stripe owns
the consumer-facing information and any valid waiver in its hosted checkout.
HugiMuni should not duplicate or contradict that presentation in its own Terms.
If the Stripe contract or actual checkout does not carry those duties, the
reading fails and the brnrd pricing-to-checkout flow must present them before
payment.

**Reasoning and citations.**

- A consumer normally has 14 days to withdraw from a distance service
  contract: [Code de la consommation
  L221-18](https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000032226842/).
- If a paid service starts during that period, L221-25 requires an express
  request and addresses proportional payment before full performance:
  [L221-25](https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000044563179/).
- The loss-of-right conditions for fully performed services and digital
  content require prior express agreement/consent and acknowledgment; the
  digital-content limb also requires confirmation:
  [L221-28](https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000044563170/).
- The repository identifies Stripe as merchant of record and shows the
  Stripe-hosted session boundary in
  [`src/brnrd/stripe_api.py`](../../../src/brnrd/stripe_api.py) and Terms §10.
- Whether Stripe contractually accepts and operationally performs the
  consumer-facing duty is outside version control; it is therefore also a
  blank question in [`open-facts.md`](open-facts.md).

**Decision requested.** Confirm whether the consumer's distance contract is
with Stripe for these purposes and classify the subscription as a service,
digital service, or digital content for L221-25/L221-28. If Stripe is not the
responsible professional, identify HugiMuni's exact
pre-contractual notice, withdrawal form, immediate-performance request,
acknowledgment, confirmation, refund, and recordkeeping duties, and state
whether those belong in a CGV, the existing Terms, or the checkout UI.

## B. Médiateur de la consommation

**Question.** Must HugiMuni designate and publish a consumer-mediation body
when Stripe is the merchant of record for the consumer sale?

**Our position.** The designation and disclosure duty attaches to the
professional who is the consumer's seller/trader for the dispute. On the same
MoR reading, that is Stripe for the checkout transaction, so HugiMuni should
not invent a mediator clause unless it is independently the relevant
professional. There is currently no named mediator in the repository.

**Reasoning and citations.**

- A professional must guarantee consumers effective access to consumer
  mediation:
  [Code de la consommation
  L612-1](https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000032224805/).
- The professional must communicate the competent mediator's coordinates:
  [L616-1](https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000032224762/).
- Terms §16 records the attribution problem and the absence of a designation:
  [`src/frontend/src/routes/terms/+page.svelte`](../../../src/frontend/src/routes/terms/+page.svelte).

**Decision requested.** Confirm whether HugiMuni is a “professionnel” owing
L612-1/L616-1 duties to subscribers despite Stripe's MoR role. If yes, identify
the competent mediation category and where its coordinates must appear. If no,
state what evidence from Stripe's contract or checkout HugiMuni should retain
to support that conclusion.
