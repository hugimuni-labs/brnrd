/**
 * The collapse primitive — the verdicts a scroll-aware disclosure answers to,
 * shared by the rail (`controlStrip.ts` → `railIsSlim`) and the machine
 * (`machineDock.ts` → `machineTapVerdict`).
 *
 * Extracted 2026-08-03 (the rack answers everywhere). Both blocks grew the
 * same shape independently — a reader's own `open` that must outlive every
 * scroll position (#1011, THE PICKER YOU CANNOT REACH), and a scroll-driven
 * verdict that may only ever force a *compact rendering*, never touch `open`
 * itself (THE DOCK THAT TAPPED WRONG, 2026-08-03) — and were re-deriving it
 * under two different vocabularies (`condensed`/`pinnedOpen`/`expanded` vs
 * `docked`). This file names the rule once; the two call sites translate
 * their own vocabulary into it and stay thin wrappers.
 */

export interface CollapseState {
	/** The reader's own act — opened this block by hand. Outlives every
	 *  scroll position; nothing in this file may flip it. */
	open: boolean;
	/** True once the page has scrolled far enough that this block's full
	 *  form belongs off-screen — the rail's `condensed`, the machine's
	 *  `docked`. A structural fact about the viewport, not the reader's
	 *  intent. */
	scrolledPast: boolean;
	/** A second, independent way the reader keeps the full form on screen —
	 *  the rail's `pinnedOpen`. Blocks with no such override pass `false`. */
	pinnedOpen: boolean;
}

/**
 * Does this block render its compact/summary form right now?
 *
 * One rule for every caller: the reader's own `open` (and its `pinnedOpen`
 * cousin) always outrank the scroll verdict — both are equally the reader's,
 * and enumerating only one of them is exactly how THE PICKER YOU CANNOT
 * REACH shipped the first time. Only when the reader has done neither does
 * `scrolledPast` get to force the compact form.
 */
export function isCollapsed(state: CollapseState): boolean {
	return state.scrolledPast && !state.pinnedOpen && !state.open;
}

export interface TapVerdict {
	/** The reader's expansion after the tap — `null` for "do not touch it". */
	open: boolean | null;
	/** Take the reader to the block's home in the document. */
	travel: boolean;
}

/**
 * What a tap on this block's head means, once a scroll verdict is in play.
 *
 * At rest (not scrolled past) the head sits on top of its own body, so a tap
 * is an ordinary disclosure toggle. Scrolled past, the body lives elsewhere
 * in the document and the head is a pointer: the tap may open, and it may
 * travel, but it may never fold a body the reader cannot see — folding it
 * from here is exactly what made a fold look like "scrolled randomly" (the
 * lane vanishing above the reader, everything below rising by its height).
 * The reader still folds by hand, once the travel lands them where the body
 * actually is.
 */
export function tapVerdict(open: boolean, scrolledPast: boolean): TapVerdict {
	if (!scrolledPast) return { open: !open, travel: false };
	return { open: open ? null : true, travel: true };
}
