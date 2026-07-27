# Document manifest

This pack does not duplicate the legal pages. Each page remains in its
canonical repository location, while this manifest pins the exact bytes
reviewed with SHA-256. Copies were avoided because the accepted Terms already
demonstrated the drift risk: the current pin is
`src/brnrd/legal/tos-2026-07-24-r2.txt`, created after the rendered page changed
without changing its version label.

Hash snapshot:

- Date: 2026-07-27
- Source revision (parent of this export-only change):
  `e1ff7a271a9bb82b59276a7fdec01abf36ed56fc`
- Algorithm: SHA-256 over raw repository file bytes
- Machine-readable list: [`SHA256SUMS`](SHA256SUMS)
- Verification from the repository root:
  `sha256sum --check docs/legal/export/SHA256SUMS`

## Reader-facing pages

These URLs are the non-engineering reading surface. The source paths and
hashes below remain authoritative for the snapshot if a deployed page changes.

| Document | Public page |
|---|---|
| Terms of Service | [brnrd.dev/terms](https://brnrd.dev/terms) |
| Hosted-Execution Beta Terms | [brnrd.dev/beta-hosted-execution](https://brnrd.dev/beta-hosted-execution) |
| Privacy Notice | [brnrd.dev/privacy](https://brnrd.dev/privacy) |
| Legal Notice / *mentions légales* | [brnrd.dev/legal-notice](https://brnrd.dev/legal-notice) |
| Sub-processors | [brnrd.dev/sub-processors](https://brnrd.dev/sub-processors) |
| Pricing | [brnrd.dev/pricing](https://brnrd.dev/pricing) |

## Documents for substantive review

| Document | Canonical source | SHA-256 |
|---|---|---|
| Terms of Service — rendered source | [`src/frontend/src/routes/terms/+page.svelte`](../../../src/frontend/src/routes/terms/+page.svelte) | `7fe647c72fd63391cd82c7a99ed0d69f31e8c8164371906d4c4259e1209bdff2` |
| Terms of Service — exact current acceptance pin | [`src/brnrd/legal/tos-2026-07-24-r2.txt`](../../../src/brnrd/legal/tos-2026-07-24-r2.txt) | `f45b8fb473d3f5f8808b653acf7cb1563ca3ad0d8e640fc928049363421cd5d1` |
| Hosted-Execution Beta Terms — rendered source | [`src/frontend/src/routes/beta-hosted-execution/+page.svelte`](../../../src/frontend/src/routes/beta-hosted-execution/+page.svelte) | `c86191bde58adb66cea09c90f0632e899a1bd9ff9f484954f70a3fda4b79a9f1` |
| Hosted-Execution Beta Terms — exact current acceptance pin | [`src/brnrd/legal/hosted-execution-2026-07-08.txt`](../../../src/brnrd/legal/hosted-execution-2026-07-08.txt) | `4c6b86ba299b9f37b5b1ec5eabe1632919f8a359fea7b6e83408bad70260d94e` |
| Privacy Notice | [`src/frontend/src/routes/privacy/+page.svelte`](../../../src/frontend/src/routes/privacy/+page.svelte) | `9ab027aeaf8d22b1280c1eb436251057b0f1fb4516dba94d419119669edfe62e` |
| Legal Notice / *mentions légales* | [`src/frontend/src/routes/legal-notice/+page.svelte`](../../../src/frontend/src/routes/legal-notice/+page.svelte) | `cbbfae6ecb231148d5fa63828c592cc81d005d1472ff2ec795667b5c899c28c6` |
| K-bis-backed company and host facts rendered by the Legal Notice | [`src/frontend/src/lib/legalNotice.ts`](../../../src/frontend/src/lib/legalNotice.ts) | `e37706f6a5d06de289260bdbf01c3a583cc08abfb59b254193a55264948b41c6` |
| Sub-processor list | [`src/frontend/src/routes/sub-processors/+page.svelte`](../../../src/frontend/src/routes/sub-processors/+page.svelte) | `03300ddceccd24225aab06a8f34a5ab04a02e2cb065bfd8385b548728f4e04cb` |
| Data Processing Agreement | [`docs/legal/dpa.md`](../dpa.md) | `e063649e390e5390db55675bf8fd31fece71699f05998b0e5fc7634cdbe79c4f` |
| Article 30 record of processing | [`docs/legal/art-30-record.md`](../art-30-record.md) | `a946b7944433df591b388075f6c3265737fffb7a61df2b1ecc22f77eb7f42e7c` |
| Driven data-flow and trust inventory | [`SECURITY.md`](../../../SECURITY.md) | `d338c665601cc2d8b4bd7c15915d874d90bf9a480238c4c7fd54dfadf485fc89` |
| Pricing and offer page | [`src/frontend/src/routes/pricing/+page.svelte`](../../../src/frontend/src/routes/pricing/+page.svelte) | `b3ec5368e2d5452d6238ee0cb768b6f4a863c94b23f0370bf71911effc900ab4` |
| MIT license | [`LICENSE`](../../../LICENSE) | `3101d42b24f94e634de450ea11eca86b144900590896948376f67936acf02d92` |
| License boundary overview | [`LICENSE-OVERVIEW.md`](../../../LICENSE-OVERVIEW.md) | `37ed3bb589c5b2a46bcb4a674548d49d433efdda573c003332f7c13405ada0d8` |

## Load-bearing implementation evidence

These are not documents for counsel to redraft. They are the evidence behind
factual statements in the pack.

| Evidence | What it establishes | SHA-256 |
|---|---|---|
| [`src/brnrd/account_deletion.py`](../../../src/brnrd/account_deletion.py) | Article 17 deletion/anonymisation sweep, subscription cancellation, retained billing ledger | `947521f87bfd20bfc4a3018978b4ab7f2091ad49c8517c819845245ee866ae6a` |
| [`src/frontend/src/lib/billing.ts`](../../../src/frontend/src/lib/billing.ts) | Browser sends cadence and follows a Stripe-hosted checkout URL; no brnrd waiver control | `bf459f1629d0c059485cf9f319a3c3ed85abbe5358cd4ab26e5a2f3447898c8a` |
| [`src/brnrd/routers/billing.py`](../../../src/brnrd/routers/billing.py) | Authenticated checkout endpoint and server-side cohort/price selection | `ed05f548821e11bbee302632ff126e62451621a5529db6bef734d3928b1f474a` |
| [`src/brnrd/stripe_api.py`](../../../src/brnrd/stripe_api.py) | Stripe Checkout payload and Managed Payments / merchant-of-record behavior | `610d013698d3a3da86f75306c449a5b68dcc6b6428c06bc5c7b20aab34d0e24b` |
| [`src/brnrd/terms.py`](../../../src/brnrd/terms.py) | Immutable legal-text pins, versions, and SHA-256 acceptance evidence | `739eeef85ca3b771c50e0024e2233bb27fe4365039d47eb2b988186b3934dea8` |
| [`src/brnrd/routers/_session.py`](../../../src/brnrd/routers/_session.py) | Per-document acceptance status, re-consent predicate, and mirror deletion on last disconnect | `178d864361fd26c38531825c171325922fc106f352db643aa6f26e1df4bc64e9` |

## External context pointers

These sources are mutable tracker/knowledge pages and are therefore linked
rather than copied or assigned a file hash:

- [Issue #672 — engagement scope and both maintainer comments](https://github.com/hugimuni-labs/brnrd/issues/672)
- [Issue #569 — legal document structure and resident review](https://github.com/hugimuni-labs/brnrd/issues/569)
- [Issue #23 — release contract and gate 4](https://github.com/hugimuni-labs/brnrd/issues/23)
- [Engineering legal review — hosted service vs OSS](https://github.com/hugimuni-labs/brnrd-knowledge/blob/main/repos/hugimuni-labs__brnrd/research-legal-review-hosted-vs-oss-2026-07-24.md)
  (maintainer knowledge repository; access may need to be granted)

The nine questions and current positions have been extracted into this pack so
the knowledge-repository link is not required for counsel to begin the review.
