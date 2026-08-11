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

/**
 * The shared scroll/settle clock (2026-08-08, his steer: "the behaviour of
 * both rails is a bit buggy because they behave differently… I just think
 * it should behave more uniformly and clearly and like collapse not
 * immediately but soon after the scroll happens so that the elements do not
 * congest").
 *
 * Corrected 2026-08-11 (his follow-up: the rail was staying full-size for
 * the *whole* scroll and only collapsing once the reader stopped, which
 * "doesn't look good" — you want to see what you're scrolling to while
 * you're still scrolling. "within a second after passing some threshold it
 * should collapse even if the scrolling still happens"). The clock below
 * used to reschedule its deadline on *every* qualifying tick — a trailing
 * debounce that only ever fires once ticks stop arriving, i.e. once the
 * reader stops scrolling. Continuous scrolling delivers a 'scroll' event
 * on nearly every frame, so the deadline was pushed forward in lockstep
 * with the scroll and never actually reached until the reader let go. The
 * fix anchors the deadline to the *first* qualifying tick (leading edge)
 * and lets later ticks fall through unchanged — the debounce still exists
 * (so a reader who taps the threshold and immediately backs off gets
 * `raw=false` and clears it before it fires), but it no longer re-arms
 * itself against a scroll that is still going.
 *
 * Unifies #1169's diagnosis: the rail's `railScrollVerdict` and the machine
 * dock's own threshold ran as two independent verdicts, on two independent
 * `$effect` reads, coupled only through `machineDockTop(railHeight, …)` — a
 * target computed off the rail's own *live* `clientHeight` binding. That
 * binding updates asynchronously (a ResizeObserver-backed reactive value)
 * relative to the scroll handler that flips `condensed` in the same
 * synchronous tick, so for at least one frame the dock's target position was
 * built from the *previous* rail height paired with the *new* condensed
 * state — a threshold that moved out from under its own reader. Both blocks
 * now compute their verdicts in the one `tick()` in `+page.svelte`, from
 * settled/cached height constants rather than the live binding, and both run
 * through *this* one clock — same rules, same timing, every time.
 *
 * The clock is deliberately generic (`raw` in, `settled` out) rather than
 * rail- or dock-specific: the rail and the dock each keep their own
 * geometric threshold (`railScrollVerdict` below; the dock's own
 * `machineDockVerdict`) because they answer genuinely different questions —
 * "has the rail scrolled past its own dead band" vs. "has the machine's own
 * section scrolled past where the rail's bottom now sits", which a travel
 * trip (tap the docked head, land at the block) can decouple even while the
 * rail stays condensed the whole time. What both share, and what used to
 * differ, is the *timing*: this function is the one clock both feed their
 * own raw verdict through.
 *
 * Expansion is immediate (`raw === false` clears the clock outright) —
 * nothing "congests" by un-collapsing sooner than asked, and #1011 (THE
 * PICKER YOU CANNOT REACH) is about a reader's `open` surviving scroll, not
 * about slowing down the reader's path back to the top. Collapsing is
 * debounced `settleMs` past the *first* qualifying tick (a leading-edge
 * debounce, armed once and left alone) so a reader who taps the threshold
 * for a single frame and backs off still gets a clean `raw=false` reset
 * before it fires — but a scroll that keeps going does not push the
 * deadline out in front of itself forever. It commits on schedule, in
 * motion or not.
 */
export interface ScrollClock {
	/** The settled, debounced verdict every renderer reads. */
	settled: boolean;
	/** ms epoch a pending collapse will commit at, or `null`: nothing is
	 *  pending (already settled, or currently on the "stay open" side of the
	 *  threshold). Caller-owned timer state — this function only computes
	 *  it, never schedules anything itself. */
	pendingAt: number | null;
}

// 300ms was tuned for the old trailing-debounce ("soon after scrolling
// stops"); the 2026-08-11 correction fires from the threshold crossing
// instead, so it reads against his own stated intuition — "within a
// second" — rather than that old tuning. Reduced to 500ms on 2026-08-11
// (his follow-up: "reduce it from one 2nd to half a second"); further
// tightened to 250ms same day (his follow-up: "let's do actually 250").
export const SCROLL_SETTLE_MS = 250;

/**
 * One tick of the shared clock. Called with this instant's raw geometric
 * verdict (`railScrollVerdict`, `machineDockVerdict` — see each) on every
 * scroll/resize tick, and again from the settle timer once the deadline
 * arrives. Both call sites read the same rules from the same place.
 */
export function scrollClockTick(
	clock: ScrollClock,
	raw: boolean,
	now: number,
	settleMs: number = SCROLL_SETTLE_MS
): ScrollClock {
	if (!raw) return { settled: false, pendingAt: null };
	if (clock.settled) return { settled: true, pendingAt: null };
	if (clock.pendingAt === null) return { settled: false, pendingAt: now + settleMs };
	if (now >= clock.pendingAt) return { settled: true, pendingAt: null };
	return clock;
}

/**
 * The rail's raw condense verdict, with hysteresis — moved here from
 * `controlStrip.ts` (2026-08-08) so the geometry and the clock that debounces
 * it live in one file, the unified verdict's home. Geometry unchanged from
 * its own measured history:
 *
 * THE BOUNDARY THAT FLICKERED (2026-08-02, his touchpad report: "it glitches
 * real hard between the collapsed and normal unless I scroll fast enough to
 * go past the head of the warp"). The old verdict was a single
 * IntersectionObserver threshold on the sentinel above the rail: one shared
 * boundary for condensing and un-condensing. A slow touchpad scroll sits *at*
 * that boundary for many frames, and every 1px of jitter toggled a ~140px
 * layout change plus a 260ms glitch reveal — the flicker was the trigger's
 * geometry, not the animation's.
 *
 * Second defect, same boundary: the spacer that holds the rail's flow
 * footprint is documented as "only ever non-zero while off-screen", but at
 * the old threshold the rail condensed the moment its *top* left the
 * viewport — inflating the spacer while the freed area was still on screen,
 * a visible blank band exactly where the rail had been.
 *
 * The rule: a form change earns a dead band at least as tall as the form
 * change itself. Condense only once the reader has scrolled past the whole
 * full rail (the freed space is then provably off-screen; the sticky slim
 * bar takes over seamlessly). Un-condense only back near the rail's natural
 * top, where the full form belongs. Between the two thresholds the verdict
 * holds its last state — jitter has nothing to toggle.
 */
export const RAIL_UNCONDENSE_SLACK_PX = 8;
export const RAIL_CONDENSE_FLOOR_PX = 48;

export function railScrollVerdict(state: {
	scrollY: number;
	railTop: number;
	railFullHeight: number;
	condensed: boolean;
}): boolean {
	const condenseAt = state.railTop + Math.max(state.railFullHeight, RAIL_CONDENSE_FLOOR_PX);
	const releaseAt = state.railTop + RAIL_UNCONDENSE_SLACK_PX;
	if (!state.condensed) return state.scrollY > condenseAt;
	return state.scrollY >= releaseAt;
}
