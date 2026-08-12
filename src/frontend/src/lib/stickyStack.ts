/**
 * THE STACK THAT OWNS ITS GEOMETRY (w-48, `design-the-sticky-stack.md`).
 *
 * The home page's rail/heddle/machine chrome is one sticky container — THE
 * STACK — and this module is everything JavaScript still decides about it:
 * **booleans, never coordinates.** CSS layout owns where things paint; a
 * docked element is inside the stack or unmounted, so there is no computed
 * `top:` left to get wrong. That asymmetry is the whole design: a wrong
 * boolean mis-times a transition by pixels of scroll; the machinery this
 * replaces (three hand-stepped scroll clocks, six settled-height samples,
 * three reserve spacers, two JS-computed `top:` styles) could park a strip
 * at a wrong document coordinate where it ate taps indefinitely — four
 * regressions in ten days (#1169 · #1258 · #1325 · #1328 · the post-#1328
 * tap-eater), every one the same species.
 *
 * What feeds these verdicts is a trailing-edge-throttled scroll listener
 * reading live sentinel rects, plus one ResizeObserver on the stack itself
 * — the page owns that wiring; this module owns the decisions so they stay
 * testable without a DOM. (IntersectionObserver was driven first and
 * measured out: an instant jump teleports a sentinel from below the
 * viewport to above it with `isIntersecting` false -> false — no state
 * change, no callback — so cached crossing state lies exactly on deep
 * links and fast flings. `SCROLL_STEP_THROTTLE_MS` below is the honest
 * price instead: a handful of rect reads per window, against the old
 * machinery's per-animation-frame spend.)
 *
 * What survives unchanged, deliberately: `collapse.ts`'s reader-sovereignty
 * rules (`isCollapsed`, `tapVerdict` — #1011's law that a scroll verdict
 * never touches `open`) and its `scrollClockTick` settle clock (the felt
 * behavior: collapse commits `SCROLL_SETTLE_MS` after the first qualifying
 * signal, expansion is immediate). This module steps three of those clocks
 * together, which is #1169's own lesson — one clock, one schedule — kept.
 */

import { scrollClockTick, SCROLL_SETTLE_MS, type ScrollClock } from './collapse.ts';

/** The three limbs whose settled verdicts the stack renders from. */
export interface StackClocks {
	/** The rail's condensed form (full ↔ slim). */
	rail: ScrollClock;
	/** The heddle strip's docked copy (mounted in the stack ↔ absent). */
	heddle: ScrollClock;
	/** The machine head's docked form — pointer vs disclosure, and the
	 *  section label's gate. The head itself never mounts or moves. */
	lane: ScrollClock;
}

/**
 * The step throttle: at most one live-rect read batch per window, always
 * including one after the last event (trailing edge), so a jump's final
 * position is never missed. Well under `SCROLL_SETTLE_MS`-scale feel:
 * collapse timing stays "within a beat of the crossing".
 */
export const SCROLL_STEP_THROTTLE_MS = 100;

export function initialStackClocks(): StackClocks {
	return {
		rail: { settled: false, pendingAt: null },
		heddle: { settled: false, pendingAt: null },
		lane: { settled: false, pendingAt: null }
	};
}

/**
 * The rail's raw condense verdict — hysteresis as two *sentinel pairs*
 * rather than arithmetic (THE BOUNDARY THAT FLICKERED's rule, kept: a form
 * change earns a dead band at least as tall as the form change itself).
 *
 * `condenseAbove` reads the sentinel placed after the stack's reserve
 * spacer — flow-stable at the *at-rest* stack bottom, so the boundary is
 * "the whole full stack has scrolled past" and does not move when the rail
 * changes form. `releaseAbove` reads the sentinel before the container —
 * the stack's own flow top, stable by construction. Between the two
 * boundaries the verdict holds its last state; jitter has nothing to
 * toggle.
 */
export function railRawVerdict(state: {
	condenseAbove: boolean;
	releaseAbove: boolean;
	condensed: boolean;
}): boolean {
	return state.condensed ? state.releaseAbove : state.condenseAbove;
}

/**
 * A limb docks when its home sentinel has scrolled up past the stack's
 * *live* bottom edge — `machineDockVerdict`'s dead-band geometry, with the
 * one load-bearing difference: the boundary is the measured bottom of the
 * real stack, never a height computed from settled samples. A stale
 * `stackBottom` here shifts *when* a limb docks by pixels of scroll; it
 * can never shift *where anything paints*.
 *
 * The dead band is partly geometric now: docking the heddle strip mounts
 * it into the stack, which moves the stack's bottom down by the strip's
 * own height — so the release boundary sits a strip-height past the dock
 * boundary with nothing remembered. The constant below covers the rest
 * (the head's docked form is the same box), exactly as it did before.
 */
export const DOCK_SLACK_PX = 24;

export function limbDockVerdict(state: {
	/** The limb's home sentinel top, in viewport coordinates. */
	homeTop: number;
	/** The stack's live bottom edge, read off the container's own rect. */
	stackBottom: number;
	/** The verdict's own last answer. */
	docked: boolean;
}): boolean {
	if (!Number.isFinite(state.homeTop) || !Number.isFinite(state.stackBottom)) return false;
	if (state.docked) return state.homeTop < state.stackBottom;
	return state.homeTop < state.stackBottom - DOCK_SLACK_PX;
}

/** This instant's raw geometric verdicts, before the settle clock. */
export interface StackRaws {
	rail: boolean;
	heddle: boolean;
	lane: boolean;
}

export interface StackStep {
	clocks: StackClocks;
	/** ms epoch the earliest pending collapse commits at, or `null` —
	 *  the page's one settle timer arms against this. */
	nextDeadline: number | null;
	/** Whether any settled verdict or pending deadline moved — the page
	 *  reassigns its `$state` only when this is true (reference-identity
	 *  dirtying; the old machinery's own rule, kept). */
	changed: boolean;
}

/**
 * One tick of the whole stack: all three clocks stepped together, on the
 * same `now`, through the same rules — the single debounce. Callers step
 * on: the throttled scroll/resize step, the settle timer firing, or a rack
 * open/close.
 *
 * The rack gate rides here (#1328's rule, one place): while the rack is
 * open nothing may dock — "docked" means *stuck in the stack in place of a
 * scrolled-off form*, and an open rack means nothing is stuck to anything.
 * `raw=false` clears immediately (expansion is never debounced), so a rack
 * opening un-docks everything in the same step.
 */
export function stepStackClocks(
	clocks: StackClocks,
	raws: StackRaws,
	railOpen: boolean,
	now: number,
	settleMs: number = SCROLL_SETTLE_MS
): StackStep {
	const next: StackClocks = {
		rail: scrollClockTick(clocks.rail, !railOpen && raws.rail, now, settleMs),
		heddle: scrollClockTick(clocks.heddle, !railOpen && raws.heddle, now, settleMs),
		lane: scrollClockTick(clocks.lane, !railOpen && raws.lane, now, settleMs)
	};
	const deadlines = [next.rail.pendingAt, next.heddle.pendingAt, next.lane.pendingAt].filter(
		(deadline): deadline is number => deadline !== null
	);
	const changed = (['rail', 'heddle', 'lane'] as const).some(
		(limb) =>
			next[limb].settled !== clocks[limb].settled || next[limb].pendingAt !== clocks[limb].pendingAt
	);
	return {
		clocks: changed ? next : clocks,
		nextDeadline: deadlines.length > 0 ? Math.min(...deadlines) : null,
		changed
	};
}

/**
 * The scroll-spy half: which tracked section heading was the last to pass
 * above the stack's bottom edge — document order, exactly one or none, the
 * same answer the old scroll tick computed with four live rect reads. The
 * page keeps a per-heading `above` boolean from the same observer that
 * watches the limb sentinels; this picks the winner.
 */
export interface HeadingState {
	id: string;
	label: string;
	above: boolean;
}

export function activeSectionFrom(
	headings: readonly HeadingState[]
): { id: string; label: string } | null {
	let last: { id: string; label: string } | null = null;
	for (const heading of headings) {
		if (heading.above) last = { id: heading.id, label: heading.label };
	}
	return last;
}

/**
 * The one surviving spacer, and the design's whole risk budget: a sticky
 * container is still in flow, so the rail condensing would move the
 * document under a scrolled reader (scroll anchoring would absorb this,
 * but Safari doesn't implement it). The spacer holds the difference
 * between the stack's at-rest height and its live height — non-zero only
 * while the reader is scrolled past it. It positions nothing: a stale
 * sample means the document is briefly the wrong height and a scroll back
 * to the top may jump once, then self-heal at rest — it cannot eat a tap.
 */
export function stackReserve(restHeight: number, liveHeight: number): number {
	if (!Number.isFinite(restHeight) || !Number.isFinite(liveHeight)) return 0;
	return Math.max(0, Math.round(restHeight) - Math.round(liveHeight));
}

/**
 * Whether this instant is the stack's at-rest form — the only moment the
 * rest-height sample may be taken. Re-sampled on *every* resize event that
 * qualifies, so a transient pollution (a rack mid-close reporting tall)
 * self-heals on the next resize instead of freezing into the layout — the
 * failure mode the old six-sample machinery was one guard short of, five
 * times.
 */
export function stackAtRest(state: {
	railOpen: boolean;
	railCondensed: boolean;
	heddleDocked: boolean;
	machineDocked: boolean;
}): boolean {
	return !state.railOpen && !state.railCondensed && !state.heddleDocked && !state.machineDocked;
}
