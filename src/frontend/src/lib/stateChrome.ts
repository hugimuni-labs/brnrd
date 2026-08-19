// Row/chip state chrome shared by SpoolRack and the bench's project /
// environment lists (w-68, the gauge/bench split, 2026-08-19).
//
// Two redesigns landed together, both scoped to these two components — the
// app-wide amber sweep (nine "selected" recipes across 28 sites, the
// primary CTA pixel-identical to one of them) is its own strand, not this
// one:
//
// 1. **Selection leaves the hue channel.** Argued to the maintainer and
//    signed on w-68: amber is the brand's press-me (the CTA), so a picker
//    row wearing the same hue to mean "you chose this" is indistinguishable
//    from "press this" at a glance. Selection now reads as a *shape* — a
//    left rule — at one of two brightnesses, never a colour swap. The
//    rack's two states (a one-shot next-wake request vs the standing pin)
//    keep the axis the maintainer asked for: one mark, two intensities —
//    bright for the time-boxed request, muted for the durable pin.
// 2. **Off is designed, not dimmed.** The former `DISABLED_ROW` used
//    `opacity-45` on the whole row — border, mark, and text together — which
//    reads as "broken", not "deliberately unavailable" (the standing bar:
//    "do not grey things out — design them off"). The new recipe drops the
//    opacity filter and marks the row with a dashed border instead: a socket
//    with nothing plugged in, not a faded photocopy of a live row.
//
// THIS IS NOT THE WHOLE VOCABULARY. Six of the seven amber recipes audited
// inside these two components at extraction time (2026-08-19, "clear the
// ground under the rail") converge here; a seventh — the sticky "riding
// <thread>" chip — deliberately does not, because it names a different kind
// of fact. w-68 signed that split too: "the sticky chip stops wearing the
// badges' costume — it is a live state, they are labels." A badge answers
// "which one did you pick"; the sticky chip answers "what is true right
// now, for how much longer" — so it gets its own recipe, `LIVE_CLAIM`,
// rather than a third intensity bolted onto the selection axis.

/** SpoolRack: a one-shot "next wake" request parked on this row — the
 *  brighter of the rack's two marks, since it is the more time-boxed claim
 *  ("next wake · requested" is not the same standing as the durable pin). */
export const SELECTED_REQUESTED = 'border-l-2 border-l-stone-100 bg-stone-800/50';

/** SpoolRack: the standing default/pin. Same mark as `SELECTED_REQUESTED`,
 *  deliberately dimmer — see `SpoolRack.svelte`'s own comment on why the two
 *  must never look the same. */
export const SELECTED_PINNED = 'border-l-2 border-l-stone-500 bg-stone-900/40';

/** The bench: the currently chosen option inside a picker list (project,
 *  environment). One mark, one intensity — these lists carry no second
 *  state to distinguish. */
export const SELECTED_OPTION = 'border-l-2 border-l-stone-100 bg-stone-800/40';

/** A live, time-bounded claim — SpoolRack's "riding <thread>" chip. Not a
 *  selection (nobody picked it from a list) and not the CTA (nothing to
 *  press): its own recipe, a filled dot standing in for the badges' rule. */
export const LIVE_CLAIM = 'border-stone-600/70 bg-stone-800/70 text-stone-200';

/** Shared "designed off" recipe: not a dimmed copy of a live row — a
 *  distinct, deliberate rendering. Dashed border, full-opacity ink; the row
 *  reads as "nothing plugged into this socket", not "broken". Covers every
 *  off state on the rail: SpoolRack's unavailable/unverified rows
 *  (collapsed to one bucket, see `spoolRack.ts::offerabilityOf` — the
 *  distinction between "verified unavailable" and "we don't know" does not
 *  survive to a row a reader can act on), the bench's non-dispatchable
 *  repos, and its unavailable environments. */
export const OFF_ROW = 'cursor-not-allowed border-dashed border-stone-700/70 bg-stone-950/40';

/** The off-row mark, prefixed onto a label when a row cannot be acted on. */
export const OFF_MARK = '✗ ';

/** The idle, tappable row — the direct sibling of every state above, and
 *  the branch each of their ternaries falls through to. */
export const IDLE_ROW = 'border-stone-800/60 bg-stone-900/30 hover:border-stone-600/70';
