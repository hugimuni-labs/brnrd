/**
 * The machine's dock — the magnet, and the head that stops repeating itself.
 *
 * Two of his 2026-08-02 reads, one component:
 *
 *   "the now scrolls under the rack, the fuel — I think the idea is that they
 *    stay together, collapsed rack and collapsed fuel, really closely stacked
 *    like a magnet"
 *
 *   "I actually really like both expanded and collapsed run view. But when it
 *    is expanded, it shouldn't repeat the both collapsed and semi-expanded
 *    shape"
 *
 * The magnet half is layout and lives in the page (`sticky`, offset by the
 * rail's live height, one z-layer under it). The half worth a pure function is
 * the second: **what the head may say while the body is open.**
 *
 * The rule, and it generalises past this component: a header above an open
 * body may carry *identity* — which thing you are looking at — and must not
 * carry *measurements* the body draws in full one line below. Identity does
 * not decay; a clock does, and two copies of a decaying number on one screen
 * disagree the moment one of them re-renders first.
 *
 * Deliberately NOT here: any scroll verdict that changes `open`. That is THE
 * PICKER YOU CANNOT REACH (#1011) — the rail once let a scroll position
 * destroy a panel the reader had opened by hand. The dock is visual only; the
 * reader's expansion survives every scroll position, and the body it opened
 * stays exactly where the document put it.
 */

export interface MachineHeadFields {
	/** The lead run's identity — face and name. Always allowed: the dock has to
	 *  say *which* run is stuck to the top of the reader's screen. */
	lead: boolean;
	/** The lead's running clock. Suppressed while open — the lane's first row
	 *  is the same run with the same clock. */
	clock: boolean;
	/** The lead's note (`+2`, a phase word). Same reason. */
	note: boolean;
	/** `+N` further burning strands. The parked line is a pulse, not an
	 *  inventory; open, the lane *is* the inventory. */
	extra: boolean;
	/** The right-hand tail: `N armed · next in …`. The armed rows below say it
	 *  row by row, with their own clocks. */
	armedTail: boolean;
}

/**
 * Which fields the machine's head may render.
 *
 * Health fields (`error`, `stale`) are not in this set on purpose: a dead or
 * stale feed is a fact *about the block*, not a measurement the body repeats,
 * and suppressing it while open would hide the one thing that makes the rows
 * below untrustworthy.
 */
export function machineHeadFields(open: boolean): MachineHeadFields {
	return {
		lead: true,
		clock: !open,
		note: !open,
		extra: !open,
		armedTail: !open
	};
}

/**
 * Where the machine's head docks: directly under the rail, no gap — his
 * "really closely stacked to it, like a magnet".
 *
 * The rail is `sticky top-0` and *changes height* as it condenses, so the
 * offset is read live rather than pinned to a constant. A negative or absent
 * measurement (first paint, before `clientHeight` binds) docks at 0 rather
 * than floating the head off the top of the viewport — the failure mode of
 * guessing here is a header that hides behind the rail, which reads as the
 * block having vanished.
 */
export function machineDockTop(railHeight: number | null | undefined): number {
	if (typeof railHeight !== 'number' || !Number.isFinite(railHeight)) return 0;
	return Math.max(0, Math.round(railHeight));
}

/**
 * Whether the condensed rail should still draw its own live-pick row.
 *
 * It should not, once the machine docks beneath it. That row existed because
 * the machine block scrolled away and took the only "what is burning" with
 * it; a dock replaces it with the real thing — face, frame, armed tail — so
 * keeping both would print the same run's name at two y-positions eight
 * pixels apart. His 2026-08-02 correction is what made the two-dock shape
 * strictly better than one: *"not the collapsed rack + oneline main runner
 * info, as it is now, but a collapsed fuel + collapsed oneline machine stuck
 * to it."*
 *
 * A function rather than a deleted branch because the rail keeps the row when
 * there is no machine dock to hand it to — an embed, a narrower surface, any
 * caller that renders the rail alone.
 */
export function railKeepsLivePick(machineDocks: boolean): boolean {
	return !machineDocks;
}
