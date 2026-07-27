# Nine questions for counsel, with our positions

These are the nine questions from the 2026-07-24 legal review. Each section
states the reading HugiMuni has already taken, the evidence and law behind it,
and the precise decision requested. “Our position” means a provisional
engineering/legal reading for review, not professional advice.

## 1. Controller/processor split and DPA form

**Question.** Is the controller/processor split drawn by the current documents
correct, and should the DPA be a Terms annex or a standalone agreement?

**Our position.** HugiMuni is controller for account identity, authentication,
billing, acceptance records, security, support, and service telemetry because
it determines the purposes and means of that processing. HugiMuni is processor
only for customer content relayed or mirrored on the customer's instructions.
The DPA should remain a standalone document incorporated into the Terms by
reference; that keeps the Article 28 terms reviewable without confusing
HugiMuni's controller-role processing with customer-controlled content.

**Reasoning and citations.**

- GDPR Article 4(7) defines the controller and Article 4(8) the processor;
  Article 28 requires a binding processor contract and specifies its content:
  [Regulation (EU) 2016/679](https://eur-lex.europa.eu/eli/reg/2016/679/oj/eng).
- The implemented boundary is stated in
  [`docs/legal/dpa.md` §0](../dpa.md) and the public
  [`/privacy` source](../../../src/frontend/src/routes/privacy/+page.svelte).
- `SECURITY.md` describes what stays local, what managed mode receives, and
  what the dashboard mirrors:
  [`SECURITY.md`](../../../SECURITY.md).
- Article 17 erasure behavior is implemented in
  [`src/brnrd/account_deletion.py`](../../../src/brnrd/account_deletion.py).

**Decision requested.** Confirm or redraw each role by data category. Confirm
whether incorporation by reference is sufficient for Article 28, identify any
required signature/acceptance step, and list any Article 28 term missing from
the present DPA.

## 2. Liability cap, warranty disclaimer, and approval-bypassed execution

**Question.** Is the current liability wording enforceable for French B2C users
at the $5–7/month price point, including its treatment of *dol* and *faute
lourde*, given that agents may execute without per-command approval?

**Our position.** Keep the present four-part structure in Terms §13: expressly
preserve non-limitable liability and mandatory consumer rights; exclude
indirect categories only to the extent permitted; apply an aggregate cap of
the greater of twelve months' fees and EUR 100; and make the consumer carve-out
and severability explicit. The cap is principally a B2B/professional-user
protection. It should not purport to remove the essential obligation, excuse
*dol* or *faute lourde*, or create a significant imbalance against a consumer.
Approval-bypassed execution is a disclosed product risk and strengthens the
need for review-before-reliance language, but it should not be drafted as an
automatic admission that every failure is *faute lourde*.

**Reasoning and citations.**

- A clause that deprives an essential obligation of its substance is treated
  as unwritten: [Code civil
  Article 1170](https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000032041115/).
- Foreseeability limits do not apply where non-performance is due to gross or
  intentional fault: [Code civil
  Article 1231-3](https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000032010127/).
- Consumer clauses creating a significant imbalance are abusive:
  [Code de la consommation
  L212-1](https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000032890812/).
- The exact proposed wording is in
  [`src/frontend/src/routes/terms/+page.svelte` §13](../../../src/frontend/src/routes/terms/+page.svelte);
  the execution and isolation facts are in
  [`SECURITY.md`](../../../SECURITY.md).

**Decision requested.** Redline §13, set the cap and period, state separately
what survives for consumers and professionals, and say whether any exclusion
category or severability wording is likely to be struck. Confirm whether the
documented approval model changes that analysis.

## 3. Stripe merchant-of-record boundary

**Question.** Who owes the withdrawal-right presentation, VAT invoice, refund
handling, and other residual consumer duties when checkout uses Stripe Managed
Payments?

**Our position.** Stripe is the merchant of record and seller for the consumer
transaction; HugiMuni's leg is B2B to Stripe. Stripe therefore owns tax
calculation/remittance, the invoice, and the consumer-facing sale terms,
including the withdrawal presentation and any valid immediate-performance
waiver. HugiMuni retains duties arising from its own service conduct, privacy
roles, product claims, and any obligation the Stripe contract does not actually
assume. This position fails if the Managed Payments contract or live checkout
does not make Stripe the relevant trader for a particular duty.

**Reasoning and citations.**

- The checkout payload and live-probed Managed Payments constraints are in
  [`src/brnrd/stripe_api.py`](../../../src/brnrd/stripe_api.py); the client
  redirects to the Stripe-hosted URL through
  [`src/frontend/src/lib/billing.ts`](../../../src/frontend/src/lib/billing.ts).
- Terms §10 states the MoR reading without asserting what Stripe's checkout
  undertakes:
  [`src/frontend/src/routes/terms/+page.svelte`](../../../src/frontend/src/routes/terms/+page.svelte).
- The statutory 14-day rule and exceptions are
  [L221-18](https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000032226842/)
  and
  [L221-28](https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000044563170/).
- The driven repository findings and the two narrower attribution questions
  are in [`consumer-law-questions.md`](consumer-law-questions.md).

**Decision requested.** Map each pre-contractual, withdrawal, conformity,
refund, VAT/invoice, complaint, and mediation duty to Stripe or HugiMuni,
citing the contract clause needed to support the allocation. State what must
change before the controlled live payment if the current reading is wrong.

## 4. Telegram as a transport and international transfers

**Question.** Is the existing Telegram transfer story adequate, or does the
notice require more than “user-chosen channel”?

**Our position.** User choice explains why Telegram receives the message, but
does not by itself satisfy GDPR Chapter V. The notice should continue to name
Telegram, explain that the transport is optional and inherent to that channel,
and offer GitHub/self-hosted alternatives. HugiMuni still needs a defensible
Chapter V transfer mechanism or a narrowly applicable Article 49 derogation;
user choice must not be treated as blanket consent to recurring transfers.

**Reasoning and citations.**

- GDPR Article 13(1)(f) requires transfer/safeguard information; Articles 44–46
  set the general transfer framework; Article 49 contains derogations:
  [Regulation (EU) 2016/679](https://eur-lex.europa.eu/eli/reg/2016/679/oj/eng).
- The current disclosure and alternatives are in
  [`src/frontend/src/routes/privacy/+page.svelte`](../../../src/frontend/src/routes/privacy/+page.svelte)
  and the
  [`sub-processors page`](../../../src/frontend/src/routes/sub-processors/+page.svelte).
- The DPA lists Telegram and does not claim an adequacy decision:
  [`docs/legal/dpa.md` Annex III](../dpa.md).

**Decision requested.** Identify the correct Chapter V basis for each Telegram
flow, state whether Article 49(1)(b) can cover user-initiated transport and at
what frequency, and redline the privacy/DPA text if SCCs, supplementary
measures, or a different warning are required.

## 5. Product Liability Directive and Cyber Resilience Act

**Question.** Does the free MIT daemon, maintained beside a paid relay, remain
within the free/open-source exclusions under the revised Product Liability
Directive and the Cyber Resilience Act?

**Our position.** The unmonetised local MIT daemon is supplied free and remains
separate from the paid hosted control plane, so it is likely outside the PLD's
commercial-activity scope and outside the CRA manufacturer regime for
non-commercial free/open-source software. The paid relay is a separate
commercial service and should not inherit that exclusion. Because HugiMuni
supports the daemon in a business context and it is used with the monetised
service, CRA “open-source software steward” status remains plausible and should
not be dismissed.

**Reasoning and citations.**

- PLD Article 2(2) and recital 14 exclude free/open-source software developed
  or supplied outside commercial activity:
  [Directive (EU) 2024/2853](https://eur-lex.europa.eu/eli/dir/2024/2853/oj/eng).
- CRA Article 2(3), Article 3's definitions, and Article 24 distinguish
  non-commercial free/open-source software from open-source software stewards:
  [Regulation (EU) 2024/2847](https://eur-lex.europa.eu/eli/reg/2024/2847/oj/eng).
- The MIT/AGPL product boundary is documented in
  [`LICENSE-OVERVIEW.md`](../../../LICENSE-OVERVIEW.md) and
  [`LICENSE`](../../../LICENSE).
- `SECURITY.md` and the Terms distinguish local execution from the hosted
  relay.

**Decision requested.** Classify HugiMuni separately under the PLD and CRA for
the MIT daemon, AGPL backend/dashboard, and paid hosted service. Confirm whether
the adjacency to paid service makes the daemon commercial, whether HugiMuni is
an open-source software steward, and list any 2026–2027 preparation required.

## 6. AI Act role

**Question.** Is the current “neither model provider nor deployer” reading
correct for a pure orchestration relay?

**Our position.** HugiMuni is not the provider of the third-party models and
does not deploy those models for its own professional use: the customer selects
the model provider, supplies the subscription, and runs the agent locally.
The backend makes zero model calls. The residual risk is product
classification: if brnrd itself is an “AI system” placed on the market under
HugiMuni's name, HugiMuni could be its provider even though it did not train
the underlying model. Any Article 50 interaction duty should be assessed
against the GitHub App and Telegram bot disclosures rather than assumed away.

**Reasoning and citations.**

- AI Act Article 3 defines “provider” and “deployer”; Article 50 covers
  transparency for systems intended to interact directly with natural persons:
  [Regulation (EU) 2024/1689](https://eur-lex.europa.eu/eli/reg/2024/1689/oj/eng).
- Local execution and the relay-only backend are documented in
  [`SECURITY.md`](../../../SECURITY.md).
- The sub-processor page records that HugiMuni makes no model-provider API
  calls:
  [`src/frontend/src/routes/sub-processors/+page.svelte`](../../../src/frontend/src/routes/sub-processors/+page.svelte).

**Decision requested.** Classify HugiMuni, the local daemon, and brnrd.dev
under the AI Act definitions; identify any provider, deployer, importer, or
distributor role; and confirm what Article 50 disclosure, if any, must change.

## 7. Trademark strategy for “brnrd”

**Question.** What is the priority of trademark filing relative to cost and
launch?

**Our position.** Trademark registration is not a launch blocker. When budget
exists, conduct clearance and pursue an EU trade mark through EUIPO rather than
an INPI-only filing because the planned France-to-Spain conversion makes
Union-wide continuity materially more useful. The filing scope and timing
should follow counsel's clearance advice; the pack does not assume the mark is
available.

**Reasoning and citations.**

- An EU trade mark has unitary and equal effect throughout the Union:
  [Regulation (EU) 2017/1001 Article
  1(2)](https://eur-lex.europa.eu/eli/reg/2017/1001/oj/eng).
- The name is used for the product throughout the public pages and
  repository; ownership and license boundaries are in
  [`LICENSE-OVERVIEW.md`](../../../LICENSE-OVERVIEW.md).
- The planned Spanish conversion is addressed in question 9.

**Decision requested.** Advise on clearance, proprietor, classes, filing
sequence, and whether any present use creates an urgency that changes the
“not launch-blocking” position. Quote filing work separately from this review.

## 8. Terms versioning and re-acceptance

**Question.** Is the current version-bump and re-acceptance mechanism adequate
consent hygiene for material changes?

**Our position.** Yes, if used as designed: a material change receives a new
version; the user sees the exact document and affirmatively accepts it; the
record stores timestamp, version, and SHA-256 of immutable pinned text. The
general ToS gate currently consumes that state. The hosted addendum has a
point-of-use predicate but no hosted-compute surface currently invokes it;
before that feature exists, its dispatch choke point must enforce acceptance.
Continued use should remain limited to genuinely non-material changes unless
counsel says a broader mechanism is enforceable.

**Reasoning and citations.**

- General conditions bind the other party only if communicated and accepted:
  [Code civil
  Article 1119](https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000032040866/).
- The immutable pins, hash semantics, and material-change version rule are in
  [`src/brnrd/terms.py`](../../../src/brnrd/terms.py).
- Per-document status and re-consent are in
  [`src/brnrd/routers/_session.py`](../../../src/brnrd/routers/_session.py);
  the two distinct checkboxes are in the
  [`Terms route`](../../../src/frontend/src/routes/terms/+page.svelte) and
  [`Hosted-Execution route`](../../../src/frontend/src/routes/beta-hosted-execution/+page.svelte).
- Terms §15 promises 30 days' dashboard notice and re-acceptance for a material
  change.

**Decision requested.** Confirm which changes require fresh express
acceptance, whether 30 days' dashboard notice is adequate for paid consumers,
whether individual notice is required, and whether continued use can evidence
acceptance of any non-trivial change. Confirm the evidence record and hosted
point-of-use gate.

## 9. Planned France-to-Spain entity conversion

**Question.** How should the contracts and sequence be structured for an
intended cross-border conversion from a French SAS to a Spanish SL around the
end of 2026?

**Our position.** Use the EU cross-border conversion route rather than
dissolving the SAS and forming an unrelated company. The converted company
should retain legal personality, assets, liabilities, rights, obligations, and
contracts; current documents should remain portable by naming legal successors
and supporting a properly notified governing-law change. File any trademark as
an EUTM. As a risk-management sequence, complete or at least formally commence
the entity conversion before the maintainer's personal tax residency shifts,
subject to counsel's tax advice.

**Reasoning and citations.**

- Directive (EU) 2019/2121 Article 86b defines a cross-border conversion as
  retaining legal personality; Article 86r carries assets, liabilities,
  contracts, rights, and obligations to the converted company:
  [Directive (EU) 2019/2121](https://eur-lex.europa.eu/eli/dir/2019/2121/oj/eng).
- Spain transposed the structural-conversion rules through
  [Real Decreto-ley 5/2023](https://www.boe.es/buscar/act.php?id=BOE-A-2023-15135).
- EUTM unitary effect is cited in question 7.
- Terms §1 already uses “legal successors,” and §15 contains the change
  mechanism:
  [`src/frontend/src/routes/terms/+page.svelte`](../../../src/frontend/src/routes/terms/+page.svelte).

**Decision requested.** Confirm that the proposed SAS-to-SL route is available
and preserves subscriptions without novation; identify French and Spanish
corporate steps; quantify French exit-tax exposure at the current valuation;
identify any employee, creditor, Stripe, banking, GitHub, or data-protection
re-papering; redline portability defects in the four public documents; and
advise on sequencing relative to personal tax residency and effective
management.
