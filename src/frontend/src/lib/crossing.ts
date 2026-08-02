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
 * the warp's own layer order, the same column positions appear on the pick that
 * is burning and on the cloth line it becomes — the reference drawn instead of
 * the fact repeated.
 *
 * What it deliberately cannot draw: an *armed* pick's crossing. `serves:` (a
 * schedule entry naming the warp items it will lift) does not exist daemon-side
 * yet, so a scheduled wake has nothing honest to say about which threads it is
 * coming for, and it renders blank rather than guessing.
 *
 * Value imports carry `.ts` extensions because the tests run under node's own
 * runner with no bundler in the loop.
 */

import type { WarpLayer } from './warp.ts';

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
	return threads.map((callSign) => ({ callSign, lit: set.has(callSign) }));
}
