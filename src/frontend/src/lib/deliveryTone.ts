// The delivery axis — where a run's reply ended up — and the one place it is
// coloured.
//
// Two defects fixed together, because they are one defect: this map lived
// byte-identical in `RunNode.svelte` and `RunNodeInline.svelte`, and both
// copies painted `delivered`/`collected` in `emerald`, a hue
// `statusPalette.ts` excludes by name — *"a direct frost→amber lerp crosses an
// unintended green"*. A fact stored twice is repaired once; a palette rule
// that lives only in a comment is a rule nothing enforces.
//
// The axis is not thermal, so it borrows the thermal tones rather than the
// tiers:
//
// - `pending` stays amber. It is the warm state — still in flight, still
//   owed, the one row here that wants attention now.
// - `delivered` / `collected` move to frost. Settled, not spent: the reply
//   landed and nothing is owed. `collected` is the same tone at lower light
//   because it is the weaker claim — captured, not handed to a reader.
// - `undeliverable` stays red, which honours the palette's own reservation
//   rather than bending it: *"red is reserved for a broken contract."*
//   Nothing took the reply is exactly one.
//
// Ash was the other candidate for `delivered` and was rejected: `STATUS_SPENT`
// reads as exhausted, and a delivered reply is the opposite of that.

export type DeliveryTone = 'delivered' | 'collected' | 'pending' | 'undeliverable' | 'unknown';

/** Tailwind classes only — these render inside markup, never onto a canvas. */
export const DELIVERY_TONE_CLASS: Record<string, string> = {
	delivered: 'text-sky-200/90',
	collected: 'text-sky-200/60',
	pending: 'text-amber-400',
	undeliverable: 'text-red-400',
	unknown: 'text-ink-quiet'
};

export function deliveryToneClass(tone: string | null | undefined): string {
	return DELIVERY_TONE_CLASS[tone ?? ''] ?? DELIVERY_TONE_CLASS.unknown;
}
