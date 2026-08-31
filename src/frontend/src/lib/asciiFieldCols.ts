// The pure half of AsciiField.svelte's `measureCols()` — the clamp that
// turns "how many pixels of board box do we actually have" into "how many
// columns do we ask the camera for". Extracted so it's testable without a
// DOM.
//
// The failure this guards (#1652 follow-up, "the room fits the reader",
// 2026-09-01): `MIN_COLS` used to be 64, a floor with no relation to any
// box the component actually measured. Below ~500px of real board width
// (any phone, and `/daily`'s narrower `.field-frame` well past that) the
// floor forced more columns than the box had, and CSS quietly cut the
// excess — `/ascii` overflowed the *document* at every width tested, and
// `/daily`'s map lost its east edge to `.field-frame`'s `overflow: hidden`
// with no scrollbar to say so. Driven at 390px (`repro/drive-fits.mjs`,
// mocked fixtures, one live run): the honest avail/charWidth answer is
// ~42 columns inside `/daily`'s frame and ~49 inside `/ascii`'s own page —
// both comfortably above the new floor, so it never actually engages at
// any width the spec requires (390/768/1024/1440). It exists only to keep
// a momentarily-bad measurement (0-width before layout settles, say) from
// collapsing the board to a handful of columns.
export const MIN_COLS = 32;
export const MAX_COLS = 220;

/**
 * `avail / charWidth`, floored and clamped to `[MIN_COLS, MAX_COLS]`.
 * Never asks for more columns than the box can show, down to the floor —
 * callers own making sure the floor stays below what any supported
 * viewport actually measures (see the module comment).
 */
export function colsForWidth(
	availPx: number,
	charWidthPx: number,
	minCols = MIN_COLS,
	maxCols = MAX_COLS
): number {
	if (!(availPx > 0) || !(charWidthPx > 0)) return minCols;
	return Math.max(minCols, Math.min(maxCols, Math.floor(availPx / charWidthPx)));
}
