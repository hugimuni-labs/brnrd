# Design: portal grammar & the reconcile/projection layer

Status: **direction settled in conversation (2026-06-18), not yet built.**
Sequenced *after* [#148](https://github.com/Gurio/brr/issues/148) ships.
This page is the design seed for
[#159](https://github.com/Gurio/brr/issues/159) (*cockpit output frame
and parallel-safe run mailbox*) — written so a future wake can pick the
work up cold once #148 is dogfooded. It captures two named shapes and the
list of existing shapes that have to change to land them.

> Provenance: a multi-turn Telegram design conversation (2026-06-17 →
> 2026-06-18) that started from the "forge as a synced directory"
> idea (#117) and turned into "interrupts as portals / make the output
> *be* the surface." The full turn-by-turn arc lives in the resident
> dominion `thread-of-record.md`; this page is the promoted, settled
> synthesis. Maintainer confirmed all four decisions below on 2026-06-18.

## The four settled decisions

1. **The gate stays.** It is correctly factored as a thin transport —
   [`design-managed-delivery.md`](design-managed-delivery.md) settled
   "render daemon-side, vary only the transport" and that holds for
   Telegram-self-hosted, Telegram-cloud, and forge alike. Do **not** ditch
   or split the gate. What is unnamed is the layer one floor *above* it —
   name and shape that (see *Reconcile/projection layer* below).
2. **Portal grammar = #159's output-frame.** The resident's generated
   stream itself is the surface; portals are the marked regions in it.
   Three forms: inbound, outbound, parked. The parallel-safe run mailbox
   is the transport for *parked* portals that outlive one wake.
3. **Drop the nouns "dashboard" and "cockpit."** Both pull against #159's
   own principle that outputs should *feel like the surface* rather than
   be a fixed-slot panel a human drew and the resident pours data into.
   The replacement idiom is **scrolls and portals** — the generative
   stream and the seams in it. (Blast radius for "cockpit" is real — see
   *Shapes to change*; this is a migration, not a find-replace.)
4. **#148 ships first, unchanged.** It is the behaviour loop
   (plan→approve→execute, child runs, dwelling habits) and runs fine on
   today's dotfile protocol. The portal grammar is a later *re-skin* that
   subsumes those dotfiles, and it is better designed **after** #148 is
   lived-in, because #148 is what reveals which portals actually recur.
   Designing the slots now would be guessing.

## Reconcile/projection layer (the unnamed floor above the gate)

The correction that unlocked this: **append-log vs desired-state is
orthogonal to transport**, not "messenger vs forge." Both semantics ride
both transports:

- **append-log** — chat messages, PR/issue comments. Ordered, additive;
  you emit and it goes.
- **desired-state** — the status `.card` (one `message_id` reconciled in
  place — terraform-shaped), a PR's diff/labels, an issue's open/closed.

So the status card was *never* an append-log item; it is desired-state
riding a messenger. That is exactly why "the card edits aren't
append-only" felt wrong under the old framing. The clean factoring is
**two reconcile semantics × N transports**, with the gate a dumb pipe
under both. This layer is what's today scattered across `run_progress`,
card lifecycle, correspondent dedup, and the forge-state facet — naming
it is the refactor #159/#117 want.

Two complications fall out cleanly, neither needing new machinery:

- **Dual Telegram (self-hosted + cloud) duplication** is a *third* axis —
  fan-out/redundancy — already handled at the identity layer
  (correspondent-redundancy, shipped 2026-06-14: recognise the duplicate
  correspondent, deliver once, don't double-act → one perceived
  continuity regardless of how many pipes reach it).
- **CRDT is not earned.** CRDTs pay off only under *uncoordinated
  concurrent writers*. brr is single-flight per dominion — one writer to
  the card at a time — so last-write-wins reconcile suffices. Borrow the
  *model* (event log → projection → idempotent reconcile), skip the
  engine — the same verdict reached on Temporal. Revisit only if the
  #159 parallel-run-mailbox future ever puts two runs on one surface.

## Portal grammar (the output-frame)

A **portal** is a marked region in the resident's generated stream that
means *"here I turn to the world."* One primitive, two faces — and the
two faces are the whole trick: the same region is **what the human sees
rendered** (the live surface) *and* **the seam the daemon fills or
drains**. One artifact, both directions. That is the spell-scroll: not a
layout someone drew and the resident pours data into, but the trace of
the resident's own generation, where each turning-toward-the-world is a
glyph that both shows and channels.

- **Inbound portal** — "is anyone there?": a poll of the event mailbox at
  a natural seam. Input present → it flows through and the resident folds
  it into the continuation (the multi-response `event:` path). Absent →
  the portal closes and generation continues on its own momentum. **This
  subsumes "interrupt handling"** — there is no separate interrupt
  mechanism, just a portal the resident chose to open. Today this is the
  `inbox.json` re-read the resident must *remember* to do; as a portal it
  is *in the path of generation*.
- **Outbound portal** — emit to a surface; its *kind* carries the
  reconcile semantics from the layer above. append-log kind → a
  message/comment goes out; desired-state kind → reconcile the `.card`,
  open/refresh a PR. Portals are thus the single syntax sitting *over* the
  two-semantics model — the unification, expressed at the output-grammar
  layer, not the transport layer.
- **Parked portal** — an outbound portal that *parks the continuation*
  until something refluxes in. PLAN→approve (#148) is the canonical case:
  emit the plan, park, resume when the approval arrives. When the crossing
  outlives one thought, the parked portal becomes a **message between
  actors in the run mailbox** — precisely #159's parallel-safe half. So
  #159 splits naturally: the **portal grammar** is the language; the **run
  mailbox** is the transport for portals that outlive a wake.

It maps one-to-one onto the scattered dotfile conventions the resident
holds in working memory today:

| today's dotfile / convention | portal form |
| --- | --- |
| `inbox.json` re-read | inbound portal |
| `.card` note | outbound desired-state portal |
| `gate:` / forge handoff | outbound portal with a destination |
| PLAN→approve (#148) | parked portal (→ mailbox when cross-wake) |
| always-on injections (kb-health, pitfalls, forge dump) | resident-*summonable* portals, not firehose |

The robustness payoff is the real argument, not the aesthetics. Today the
control surface is "remember the dotfile names and their frontmatter." As
portals it moves *into the generation itself* — the resident cannot forget
to narrate or to check for input, because turning-to-the-world is just how
the stream advances. That pushes the lesson down the robustness ladder
from "remember" to "structurally in the path." It also folds in the G4
firehose cut: front-loaded injections become portals the resident summons
when relevant instead of an always-on dump.

## Shapes to change (when the portal re-skin lands, after #148)

The "how they are to be changed" the maintainer asked be noted. **None of
these should be done now** — #148 ships first, and a piecemeal rename
ahead of the grammar would be the wrong receipt.

- **Drop "dashboard"** from the cockpit/delivery framing in favour of the
  generative-stream idiom. Mostly prose:
  [`plan-resident-cockpit.md`](plan-resident-cockpit.md) G4/G5 ("slots",
  "dashboard"), and the cost-card prose in
  [`plan-cost-aware-cockpit.md`](plan-cost-aware-cockpit.md).
- **Drop "cockpit" — but mind the blast radius.** Unlike "dashboard",
  "cockpit" is *shipped surface*, not just prose: the `brr docs cockpit`
  CLI command, the bundled manual `src/brr/docs/cockpit.md`, the dominion
  `cockpit.md`, and the delivery-contract prose that points at
  `brr docs cockpit`. Renaming is a real migration with a code edge and a
  user-facing command. Fold it into the portal re-skin deliberately; do
  not rush a find-replace across a shipped command. (Open question for the
  maintainer: keep `brr docs cockpit` as the command spelling for muscle
  memory even as the *concept* becomes scrolls/portals, or migrate the
  command too?)
- **Retire the "messenger = append-log" mental bucket** wherever it
  recurs. It was the resident's own earlier conversational framing, not a
  fixed page; the correction is the orthogonality stated above (two
  semantics × N transports). When the #159 write-up lands, state the
  orthogonality explicitly in
  [`design-managed-delivery.md`](design-managed-delivery.md) /
  [`subject-managed-mode.md`](subject-managed-mode.md) so a future wake
  does not re-derive the wrong bucket.

## What a future wake picks up

After #148 is dogfooded: turn this page into the #159 design write-up
proper (or a comment on #159), having watched which portals actually
recurred in #148's lived use. The grammar's slot list should crystallise
from that evidence, not from this page's guesses. The reconcile/projection
layer naming (#159/#117) can proceed in parallel — it is a clarifying
refactor of existing scattered code, independent of the output grammar.

See also: [`design-managed-delivery.md`](design-managed-delivery.md)
(the gate transport this sits above),
[`subject-managed-mode.md`](subject-managed-mode.md) (the hub),
[`design-co-maintainer.md`](design-co-maintainer.md) §11 (the
continuity/delivery spine), and #117 (forge-as-synced-directory, the
desired-state sibling of this grammar).
