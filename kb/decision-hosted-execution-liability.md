# Decision: hosted-mode execution liability posture

Status: accepted on 2026-07-04 (telegram, "Yes let's add the tos") — posture
**(1) ToS / beta disclaimer floor**, ships now. Posture (2) technical
containment tracked as a parallel engineering track, scoped once #80's
`docker.isolation=clone` lands (see "Recommendation" below, now executed
rather than proposed).

## Why this needs its own page

Raised twice in the same thread and under-served both times: named in a GH
#53 comment as a "separately, not this ticket" aside with no shape behind
it, then flagged by the maintainer as glossed over ("why mention that in
this ticket, rather than expanding the shape behind it for a user?"). This
page is that expansion. It is **not** [`decision-runtime-dependencies.md`
#80](https://github.com/Gurio/brr/issues/80) — #80 documents the *local*
Docker trust model (self-hosted user runs brr against their own repo, host
UID, RW bind-mount — "dependency + network isolation, not a containment/
credential boundary," stated plainly rather than hidden). This page is the
**hosted** case: brnrd's own infra runs a *remote user's* Claude/Codex
session in yolo-exec mode, on hardware brnrd operates and is answerable
for.

## The exposure

Hosted brnrd (per [`design-billing.md`](design-billing.md),
[`decision-account-centered-daemon.md`](decision-account-centered-daemon.md))
lets a subscriber's agent run with tool access — file writes, shell exec,
network calls — against their repo, on brnrd-operated compute. If that
session is compromised (prompt injection, a malicious dependency, a
supply-chain hit in the executed repo itself) the blast radius is brnrd's
infrastructure and brnrd's legal exposure, not just the user's laptop the
way #80's local model is. Nothing in the current design doc set states a
posture for this; it is genuinely unscoped, not quietly decided.

## Three postures (not mutually exclusive)

1. **ToS / beta disclaimer floor.** User accepts, in writing, that hosted
   execution is at their own risk, brnrd provides no execution sandbox
   guarantee beyond stated defaults, and liability is capped/excluded to
   the extent the applicable law (French SAS = HugiMuni, so French/EU
   consumer-protection floors apply and can't be contracted around for
   individual users) allows. Cheap, ships before any technical work,
   blocks nothing architecturally.
2. **Technical containment for hosted specifically.** Stronger defaults
   than #80's local posture *because* it's brnrd's own infra on the line:
   network egress allowlists, ephemeral per-run containers, no persistent
   credential mount beyond the run's lifetime, resource/time caps enforced
   server-side rather than left to the user's own Docker flags. Reuses
   #80's designed `docker.isolation=clone` sub-mode as the mechanism, just
   defaults it *on* for hosted rather than opt-in.
3. **Both.** Disclaimer as the legal floor, containment as the actual risk
   reduction under it — what most agentic-exec SaaS platforms do, because
   a disclaimer alone doesn't stop a compromised session from touching
   brnrd's own infra, only shifts who's liable after it does.

## Resolved 2026-07-04

Maintainer picked (1) — ToS/beta disclaimer floor — as the immediate
ship, unqualified ("Yes let's add the tos"). Not stated: whether (2)
technical containment is *also* wanted now or genuinely deferred; read
against the recommendation below (ship (1) now, track (2) once #80 lands)
as the maintainer accepting the recommendation as a whole, not just its
first clause — the two remaining open questions below are downstream
execution detail, not further posture forks.

## Open questions (execution detail, not posture)

- Who drafts the ToS/disclaimer language — HugiMuni's counsel, or does this
  wait on that relationship existing? Blocks actually shipping the text,
  not the decision to ship it.
- Does (2)'s hosted-default containment change the shared-UID assumptions
  in [`decision-account-centered-daemon.md`](decision-account-centered-daemon.md)
  enough to need its own design pass, or is it a config default flip on
  top of #80's already-designed mechanism? Relevant once #80 lands, not
  before.

## Recommendation (accepted 2026-07-04)

Ship (1) before any hosted user gets remote exec — it's cheap, it's a
legal/product move not an engineering one, and it doesn't block the
weeks-scale timeline. Track (2) as a parallel engineering track scoped
once #80's `docker.isolation=clone` ships, defaulted on for hosted rather
than built twice.

## Read next

- [`decision-runtime-dependencies.md`](decision-runtime-dependencies.md) —
  the local trust-model counterpart (#80).
- [`design-billing.md`](design-billing.md) — hosted compute cost model this
  exposure sits underneath.
- [`decision-account-centered-daemon.md`](decision-account-centered-daemon.md) —
  shared-UID daemon architecture this posture would sit inside.
