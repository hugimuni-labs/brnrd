# Document manifest

This pack does not duplicate the legal pages. Each page remains in its
canonical repository location, while this manifest pins the exact bytes
reviewed with SHA-256. Copies were avoided because the accepted Terms already
demonstrated the drift risk: the current pin is
`src/brnrd/legal/tos-2026-07-24-r3.txt`, the second repin after the rendered
page changed wording without changing its version label (the maintainer's
2026-08-05 edits — see Question 2's update below — decided the acceptance
population was empty of third parties, so no re-acceptance was owed).

Hash snapshot:

- Date: 2026-08-09
- Source revision (parent of this export-only change):
  `0f4abad833c7008f6211a67e9df0d94c662272b9`
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
| Terms of Service — rendered source | [`src/frontend/src/routes/terms/+page.svelte`](../../../src/frontend/src/routes/terms/+page.svelte) | `7495dd077ee533cf29159e825a859a1a2ba9b30f5f93263c9bd727eb68352d7a` |
| Terms of Service — exact current acceptance pin | [`src/brnrd/legal/tos-2026-07-24-r3.txt`](../../../src/brnrd/legal/tos-2026-07-24-r3.txt) | `3a752022d307f241f07a3cc0ff8401b0f66d06124e1bc9d50e9d003db311760b` |
| Hosted-Execution Beta Terms — rendered source | [`src/frontend/src/routes/beta-hosted-execution/+page.svelte`](../../../src/frontend/src/routes/beta-hosted-execution/+page.svelte) | `c86191bde58adb66cea09c90f0632e899a1bd9ff9f484954f70a3fda4b79a9f1` |
| Hosted-Execution Beta Terms — exact current acceptance pin | [`src/brnrd/legal/hosted-execution-2026-07-08.txt`](../../../src/brnrd/legal/hosted-execution-2026-07-08.txt) | `4c6b86ba299b9f37b5b1ec5eabe1632919f8a359fea7b6e83408bad70260d94e` |
| Privacy Notice | [`src/frontend/src/routes/privacy/+page.svelte`](../../../src/frontend/src/routes/privacy/+page.svelte) | `5f32045b90ecdc6b674af82a9f268c172613caafb66b6492be577b7970c7f87b` |
| Legal Notice / *mentions légales* | [`src/frontend/src/routes/legal-notice/+page.svelte`](../../../src/frontend/src/routes/legal-notice/+page.svelte) | `cbbfae6ecb231148d5fa63828c592cc81d005d1472ff2ec795667b5c899c28c6` |
| K-bis-backed company and host facts rendered by the Legal Notice | [`src/frontend/src/lib/legalNotice.ts`](../../../src/frontend/src/lib/legalNotice.ts) | `e2fc3b4c0c956e35f151c6bd52cc5f256b1f123e2b50574a6794280c0b1d6d20` |
| Sub-processor list | [`src/frontend/src/routes/sub-processors/+page.svelte`](../../../src/frontend/src/routes/sub-processors/+page.svelte) | `22108856edeec8982213147926d678b6237bc5073257cfce0b86e4813436930e` |
| Data Processing Agreement | [`docs/legal/dpa.md`](../dpa.md) | `ee366853701826282da0326092fc4ff504aab99f726e6dfcf234f4d9854f43fd` |
| Article 30 record of processing | [`docs/legal/art-30-record.md`](../art-30-record.md) | `3d23e2289ce10bbb59f9fa1c235dc6f207bfd5b2c6e596c99b1f6ae0768d88b3` |
| Driven data-flow and trust inventory | [`SECURITY.md`](../../../SECURITY.md) | `2883c0835519ffd0aafed284983b8e5bbe9ef6662aaaa3da476701f0446645a5` |
| Pricing and offer page | [`src/frontend/src/routes/pricing/+page.svelte`](../../../src/frontend/src/routes/pricing/+page.svelte) | `d5e9feede83d088461ae5873be558a2f37d7a6c6de39dc36a18ce3ffdc7ad242` |
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
| [`src/brnrd/stripe_api.py`](../../../src/brnrd/stripe_api.py) | Stripe Checkout payload and Managed Payments / merchant-of-record behavior | `ed443ac26c9655462bbdf8bc7af5e5a10f60e3f36dd44a0449da2a57a77dd8b8` |
| [`src/brnrd/terms.py`](../../../src/brnrd/terms.py) | Immutable legal-text pins, versions, and SHA-256 acceptance evidence | `3196627add4fd431e74eafa88ba1a67a6ff5c35fa98f00a910185e015ed60526` |
| [`src/brnrd/routers/_session.py`](../../../src/brnrd/routers/_session.py) | Per-document acceptance status, re-consent predicate, and mirror deletion on last disconnect | `c5a2c508781c8fdf8a6b0ab259d788922dce4875bd5039a1d83b9beb577ce4e8` |

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
