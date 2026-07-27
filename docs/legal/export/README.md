# Avocat review pack

Prepared on 2026-07-27 for a fixed-scope review by a French avocat. This is an
engineering brief, not a legal opinion, and none of the positions in it should
be described as professionally reviewed until counsel has answered them.
English is used for this pack; a French version can be provided on request.

## Who we are

The service is operated by **HugiMuni SAS**, a French *société par actions
simplifiée*, with share capital of €500, registered at RCS Tarascon under SIREN
104 156 260, registered office at 6 rue de la Verdière, 13200 Arles, France,
and VAT number FR 73 104 156 260. These values come from the K-bis-backed
  source of truth in
[`src/frontend/src/lib/legalNotice.ts`](../../../src/frontend/src/lib/legalNotice.ts);
they have not been reconstructed from issue text or guessed.

## What the service does — five sentences

1. brnrd routes work from services such as GitHub and Telegram to AI coding agents that assist with software projects.
2. The agents execute on the customer's own machine, with the customer's own repository access, credentials, and model subscription.
3. brnrd.dev provides the hosted control plane: sign-in, message ingress, a dashboard mirror, routing, and a managed GitHub identity.
4. The hosted backend relays work but makes no model call and does not run the customer's agent; the data boundary is described in [`SECURITY.md`](../../../SECURITY.md) and the [DPA](../dpa.md).
5. The open-source local daemon can be used without connecting an account, in which case the dashboard-publishing lanes do not exist and the customer's execution stays local.

## What we ask counsel to deliver

The engagement has four outputs:

1. Review and finalize the public Terms of Service, Hosted-Execution Beta
   Terms, Privacy Notice, and Legal Notice against the driven data inventory
   in `SECURITY.md`.
2. Review and finalize the Article 28 GDPR Data Processing Agreement, including
   whether it should remain standalone and incorporated by reference or become
   a Terms annex.
3. Answer the nine questions in [`questions.md`](questions.md), correcting the
   position supplied with each question rather than beginning from a blank
   brief. The two additional consumer-law attribution questions are in
   [`consumer-law-questions.md`](consumer-law-questions.md).
4. Give a written go/no-go for gate 4 of
   [release-readiness issue #23](https://github.com/hugimuni-labs/brnrd/issues/23):
   one controlled live payment through checkout, webhook, invoice/VAT,
   cancellation/resumption, refund handling, and the consumer-law presentation
   that applies at checkout.

The review is deliberately limited to the present French entity and first paid
release. Hosted compute beyond the existing beta addendum, organisation/B2B
contracts, non-EU jurisdictions, and trademark filing work are outside scope
unless separately commissioned.

## Pack index

- [`questions.md`](questions.md) — all nine legal-review questions, each with
  our current position, sources, and the decision requested from counsel.
- [`consumer-law-questions.md`](consumer-law-questions.md) — the two
  post-checkout questions: withdrawal/waiver and consumer mediation, including
  what the repository search did and did not find.
- [`document-manifest.md`](document-manifest.md) — exact repository pointers
  and SHA-256 hashes for the legal documents and their load-bearing evidence.
- [`SHA256SUMS`](SHA256SUMS) — machine-checkable form of the manifest hashes,
  with paths relative to the repository root.
- [`open-facts.md`](open-facts.md) — operational questions that cannot be
  answered from version control. The maintainer must complete these before the
  pack is sent; they are not delegated to counsel.

## Known source caveats

No existing document was edited while assembling this pack. The following
source defects should be visible to the reviewer:

- `docs/legal/dpa.md` still says in non-rendered comments that the Terms,
  Privacy Notice, and Legal Notice are on an unmerged sibling branch
  (`lines 8–14`, `50`, and `458–464`). They are merged. The public DPA body is
  unaffected, but its drafting metadata is stale.
- DPA lines `54–58` first say it is incorporated into the Terms by reference
  and then qualify that with “until the Terms formally cross-reference it.”
  Current Terms §9 says a DPA exists and is available on request but provides
  no direct DPA link. Counsel should decide whether incorporation is effective
  or the two documents must be made explicit.
- The DPA's mirror-deletion row cites
  `src/brnrd/routers/_session.py:392-399`; the last-repository check spans
  `393–400`, with `surface_updated_at` on the omitted final line. The stated
  behavior remains implemented, but the coordinate is incomplete.
- The research source and the Terms drafting comment at
  `src/frontend/src/routes/terms/+page.svelte:112` call Spain's transposition
  instrument “Ley 5/2023”. The official instrument is
  [Real Decreto-ley 5/2023](https://www.boe.es/buscar/act.php?id=BOE-A-2023-15135).
- The research source names the former `_needs_hosted_terms` helper. Current
  code uses the per-document `_needs_terms` predicate in
  [`src/brnrd/routers/_session.py`](../../../src/brnrd/routers/_session.py);
  the underlying question about material-change re-acceptance remains live.

## Before sending

Complete every blank in `open-facts.md`, verify the manifest hashes against the
revision being sent, and include the repository revision or archive in the
email. A completed-looking pack is not a reviewed pack: counsel's answers and
redlines remain required before meaningful paid consumer exposure.
