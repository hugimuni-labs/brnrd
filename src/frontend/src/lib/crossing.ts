/**
 * THE CROSSING — the loom picture, at row scale.
 *
 * His 2026-08-02 read: *"is it the best possible visual representation of a run
 * going through different stages … from a conception out of forks and ideas on
 * the layers"*, and then the sharper version: *"temporal repeating instead of
 * referencing."* On a real loom the warp threads stand the length of the frame
 * and every pick crosses them; which threads a pick lifted is the whole record
 * of what it did. The page had that relationship as prose — a
 * `from the warp · weaving` list here, a warp item there, a cloth line
 * somewhere else — and never as geometry.
 *
 * This is the geometry, and it needs no new wire: `taken:` is already written
 * onto warp items at ignition (THE WELD, #972), and it keeps the run id after
 * the run finishes. So one index over the authored layers answers, for any run
 * in any tense, which threads it crossed. Rendered as a fixed strip of ticks in
 * the warp's own layer order, one *alphabet* travels between every surface that
 * draws it: same threads, same order, **same hue**, so a strip on a burning
 * pick and a strip on the cloth line it becomes are legibly the same statement
 * about the same object — the reference drawn instead of the fact repeated.
 *
 * Hue is what makes a lit tick *identifiable* rather than merely countable. The
 * first cut carried identity in position alone and the maintainer read it
 * exactly right: "nice to see which one(s) is / are being worked … but the
 * current version doesn't convey that correctly." Position keeps the order;
 * colour carries the name; the warp header's legend is where the two are
 * introduced to each other.
 *
 * The alphabet is the claim, not the x. Inside the pick lane the strips do also
 * land at the same offset (the rows share a lead slot and a padding box), and
 * that is worth having. Across the cloth they cannot: those rows wrap, so their
 * strip sits where the row's own content puts it. Making it otherwise would take
 * a grid shared by three components, which is a much larger change than the one
 * this buys.
 *
 * An *armed* pick's crossing arrives by the other door: `schedule.md`'s
 * `serves:` row, published on the activity record's `links` (THE FORWARD WELD).
 * `taken:` is written at ignition and so can only describe a run that already
 * started, which is why the future tense needed its own sentence. A run the warp
 * never welded still draws nothing at all — see `crossingCells`.
 *
 * Value imports carry `.ts` extensions because the tests run under node's own
 * runner with no bundler in the loop.
 */

import type { WarpLayer } from './warp.ts';
import { threadColorFor } from './statusPalette.ts';

/** The threads, in the warp's own authored order — the column positions every
 *  crossing strip on the page shares. */
export function crossingThreads(layers: WarpLayer[]): string[] {
	return layers.map((layer) => layer.callSign);
}

/**
 * run id → the layer call signs that run crossed.
 *
 * Built over every item of every layer, not only the live ones: a cloth line
 * from three days ago still names the threads its run lifted, which is the
 * whole point of drawing the strip in both tenses. Duplicate crossings within
 * one layer collapse — a run that took two items on `the-loom` crossed that
 * thread once.
 */
export function buildCrossingIndex(layers: WarpLayer[]): Map<string, string[]> {
	const order = new Map(crossingThreads(layers).map((callSign, index) => [callSign, index]));
	const seen = new Map<string, Set<string>>();
	for (const layer of layers) {
		for (const item of layer.items) {
			for (const runId of item.taken) {
				if (!runId) continue;
				const set = seen.get(runId) ?? new Set<string>();
				set.add(layer.callSign);
				seen.set(runId, set);
			}
		}
	}
	const index = new Map<string, string[]>();
	for (const [runId, set] of seen) {
		index.set(
			runId,
			[...set].sort((a, b) => (order.get(a) ?? 0) - (order.get(b) ?? 0))
		);
	}
	return index;
}

export interface CrossingCell {
	callSign: string;
	lit: boolean;
	/** This thread's identity hue (`statusPalette.threadColorFor`), keyed on
	 *  the **call sign**, never on its place in the authored order (#1029):
	 *  adding, splitting, merging or reordering layers must not repaint the
	 *  ones that did not change. Carried on the cell rather than looked up by
	 *  the renderer so every surface that draws a strip agrees, and so the
	 *  legend and the strips cannot drift apart. */
	color: string;
}

/**
 * The strip for one run: every thread, in order, each either crossed or not.
 *
 * Returns `[]` when there are no threads *or* when this run crossed none —
 * an empty strip and a strip of all-dark ticks say different things, and only
 * the first one is honest about a run the warp has never heard of. A run that
 * genuinely lifted nothing is indistinguishable from an unwelded one at this
 * layer, and pretending otherwise would be drawing a fact the wire cannot
 * support.
 */
export function crossingCells(
	threads: string[],
	crossed: readonly string[] | undefined
): CrossingCell[] {
	if (threads.length === 0 || !crossed || crossed.length === 0) return [];
	const set = new Set(crossed);
	return threads.map((callSign) => ({
		callSign,
		lit: set.has(callSign),
		color: threadColorFor(callSign)
	}));
}
