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
 *
 * Shared with the rail (2026-08-03, the rack answers everywhere): the tap
 * logic below is a thin wrapper over `collapse.tapVerdict`, which names the
 * rule generically for both blocks. Keep reading here for the reasoning; the
 * generic file states only the rule.
 *
 * ---
 *
 * THE DOCK THAT TAPPED WRONG (his 2026-08-03 report: "when the machine block
 * is scrolled up it is not collapsed, so pressing it the first time doesn't
 * expand it, and it likely should" — and its second symptom, "the menu hits
 * scrolled randomly a bit, because the top item gets collapsed at the real
 * beginning of the page").
 *
 * One cause, and it generalises well past this component: **one predicate,
 * true for two reasons.** `open` meant both *the reader opened this* and *its
 * body is on the reader's screen*. Docked and scrolled past, the first is
 * true and the second is false — the body sits at the block's home, screens
 * above — and every renderer that read `open` was answering the wrong
 * question:
 *
 *   - the head drew `▾` and dropped its measurements to avoid repeating a
 *     lane that was nowhere near it, so the docked line said *less* than the
 *     parked line for no reader's benefit;
 *   - the tap read `open` and folded, so the first press on a line that looks
 *     collapsed collapsed it further;
 *   - folding removed the lane from the document at its home *above* the
 *     reader, and everything below rose by the lane's height under their eyes.
 *     That is his "scrolled randomly", and it is not random: it is exactly one
 *     lane tall.
 *
 * Split the reasons and all three go. `machineBodyOnScreen` is what every
 * renderer here asks; `open` alone is nobody's question.
 *
 * The tap, docked, is then not a disclosure at all — it is a pointer, and a
 * pointer tapped takes you to the thing (`machineTapVerdict`). Not taken:
 * giving the dock its own expansion, the way the rail's slim bar owns
 * `pinnedOpen`. That works for the rail because the form it unfolds in place
 * is *short*; the rail's own tall form — the rack — travels to the top of the
 * page instead, which is the rule the two blocks actually share. **A form
 * that fits unfolds in place; a form that does not travels to its home.** The
 * machine's lane is armed picks, burning picks, and a run node under whichever
 * one is selected: it does not fit under a rail on a phone, and pinning it
 * there would leave its bottom unreachable — THE PICKER YOU CANNOT REACH, in
 * the shape it was first reported.
 */

import { tapVerdict } from './collapse.ts';

export interface MachineHeadFields {
	/** The lead run's identity — face and name. Always allowed: the dock has to
	 *  say *which* run is stuck to the top of the reader's screen. */
	lead: boolean;
	/** The lead's running clock. Suppressed only while the lane is on screen —
	 *  its first row is the same run with the same clock. */
	clock: boolean;
	/** The lead's note (`+2`, a phase word). Same reason. */
	note: boolean;
	/** `+N` further burning strands. The parked line is a pulse, not an
	 *  inventory; with the lane on screen, the lane *is* the inventory. */
	extra: boolean;
	/** The right-hand tail: `N armed · next in …`. The armed rows below say it
	 *  row by row, with their own clocks. */
	armedTail: boolean;
	/** The head run's mood chip (rest/blink glyph, worn beside the clock).
	 *  Suppressed with the lane on screen for the same reason `clock` is, not
	 *  because it is identity: a feeling turns between beats the same way a
	 *  clock ticks, so it belongs on this side of the identity/measurement
	 *  split, not the other. His 2026-08-05 read — "it repeats after the main
	 *  run card mood block" — is the concrete case: with a run focused or
	 *  selected, `RunNodeInline`'s own `MoodChip` already carries this run's
	 *  feeling one screen below, and the head repeating it here measured
	 *  nothing new. */
	mood: boolean;
}

/**
 * Whether the machine's body is where the reader is looking.
 *
 * The predicate every renderer in this file actually wants. `open` is the
 * reader's own act and outlives every scroll position (#1011); *this* is
 * whether the lane that act unfolded is on screen to be deduplicated against,
 * pointed at, or folded. Docked, the lane is at the block's home in the
 * document — screens above — so the answer is no however emphatically the
 * reader opened it.
 *
 * Note the direction: this reads the scroll to decide what the head *draws*,
 * never to decide what is open. `open` goes in and comes back untouched.
 *
 * "Docked" is *stuck*, not *lane off-screen*, and the two differ for about
 * fifty pixels of scroll right after the head sticks: the lane's lead row is
 * still under the head, printing the same clock the head has just resumed
 * printing. Priced and taken, for two reasons. The head has to be honest about
 * what a tap on it does, and the tap's meaning turns on *stuck* — keying the
 * fields on a second, later boundary would put the two questions back on two
 * schedules and reopen the seam this whole file is closing. And the failure the
 * suppression rule was written against — "two copies of a decaying number
 * disagree the moment one re-renders first" — cannot happen here: head and lane
 * both read one `pickRows` off one `now`, so they are one number rendered
 * twice, never two numbers. What is left is redundancy, briefly, in the one
 * state where redundancy is the dock's entire job.
 */
export function machineBodyOnScreen(open: boolean, docked: boolean): boolean {
	return open && !docked;
}

/**
 * Which fields the machine's head may render.
 *
 * Health fields (`error`, `stale`) are not in this set on purpose: a dead or
 * stale feed is a fact *about the block*, not a measurement the body repeats,
 * and suppressing it while the lane shows would hide the one thing that makes
 * the rows below untrustworthy.
 *
 * Takes `machineBodyOnScreen`, not `open`: the suppression exists to stop one
 * decaying number rendering twice on one screen, so it has to be keyed on the
 * screen and not on the reader's intent. Docked, the head is the only line
 * there is — the same line as parked, which is also what makes it honest that
 * a docked tap does the same thing whether or not the reader has opened the
 * block.
 *
 * The measured cost, since a sticky box still owns its place in flow: on a
 * narrow viewport the full line wraps and the short one does not, so an *open*
 * block's head grows 17px as it docks and the page below it moves that far.
 * Driven at 390px: 17px, once, at a boundary the reader is already scrolling
 * through — against a fold that took 638px of lane out from above them. At 900px
 * the line does not wrap and the figure is zero. Reserving the taller form in
 * flow would buy the 17px back and spend it as a permanent gap between the head
 * and the lane at rest, which is worse in the state the reader is in most.
 */
export function machineHeadFields(bodyOnScreen: boolean): MachineHeadFields {
	return {
		lead: true,
		clock: !bodyOnScreen,
		note: !bodyOnScreen,
		extra: !bodyOnScreen,
		armedTail: !bodyOnScreen,
		mood: !bodyOnScreen
	};
}

/**
 * What a tap on the machine's head means.
 *
 * `open`: the reader's expansion after the tap — `null` for "do not touch it".
 * `travel`: take the reader to the block's home in the document.
 *
 * At rest the head sits on top of its own lane, so it is a disclosure and taps
 * toggle. Docked, the lane is elsewhere and the head is a pointer: the tap
 * goes to the block, opening it on the way if the reader never had. It cannot
 * fold — folding a body the reader cannot see is the move that made his
 * "scrolled randomly", and refusing to do it is also the strongest form of
 * #1011's rule this file can hold: no scroll position, and no tap taken from a
 * scroll position, ever closes what the reader opened.
 *
 * The reader still folds by hand; travel puts them at the block first, where
 * the lane is on screen and the same head is a disclosure again. One tap to
 * arrive, one to fold — and the fold happens with the lane in front of them,
 * so nothing moves that they were not already watching.
 */
export interface MachineTapVerdict {
	open: boolean | null;
	travel: boolean;
}

export function machineTapVerdict(open: boolean, docked: boolean): MachineTapVerdict {
	return tapVerdict(open, docked);
}

/**
 * One frame, two moods (the machine block borrows the selection).
 *
 * **Pulse** — nothing selected: the head wears the lead live run's face and
 * name, exactly as it always has. **Inspection** — a run selected anywhere
 * on the page: the head wears *that* run's face and name instead, so a
 * reader scrolled down to the dock still sees which run their selection
 * holds, without scrolling back up to the lane to be reminded.
 *
 * `selectedId` is the page's own `loomSelection`, narrowed to the `'run'`
 * kind before it reaches here — a *wake* selection is not a run and leaves
 * the head in pulse mood. Selection wins whenever it names one at all;
 * `null` (deselect) falls straight back to the lead, no memory of the last
 * pick. The one-line rule generalises past this component: a reader's own
 * selection always outranks a computed default, the same ranking
 * `collapse.isCollapsed` gives `open`/`pinnedOpen` over `scrolledPast`.
 *
 * Deliberately just the id: which *fields* the identity renders with (face,
 * label) is a lookup the caller already owns — every selectable run lives in
 * the same `PickRow[]` the lead is drawn from (`selectFromLoom`'s two call
 * sites both select live runs), so the component resolves the id against
 * that array rather than this file inventing a second run-lookup surface.
 */
export function machineHeadRun(leadId: string | null, selectedId: string | null): string | null {
	return selectedId ?? leadId;
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
