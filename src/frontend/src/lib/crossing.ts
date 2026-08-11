/**
 * THE CROSSING — the loom picture, at row scale.
 *
 * On a real loom the warp threads stand the length of the frame and every
 * pick crosses them; which threads a pick lifted is the whole record of
 * what it did. Here the threads are **topics** (2026-08-11: the runes
 * transitioned from run ids to topic ids — the durable axis is the topic,
 * runs hang off it). A strip of ticks in topic order, one alphabet
 * travelling every surface that draws it: same threads, same order, same
 * hue, so a strip on a burning pick and a strip on the cloth line it
 * becomes are legibly the same statement about the same object.
 *
 * The hue is hashed from the canonical topic id (`runFace` — the same
 * derivation runs used to wear), never from position: an index-based hue
 * reshuffles the whole page the day one topic retires.
 *
 * The join behind the strip is `warpGraph.runTopicIndex` — run id → the
 * canonical topic ids of the items that run took or completed. `taken:`
 * is written at ignition and keeps the run id after the run finishes, so
 * one index answers for any run in any tense. An *armed* pick's crossing
 * arrives by the other door: `schedule.md`'s `serves:` row (THE FORWARD
 * WELD), because `taken:` can only describe a run that already started.
 *
 * Value imports carry `.ts` extensions because the tests run under node's
 * own runner with no bundler in the loop.
 */

import { runFace } from './runFace.ts';

export interface CrossingCell {
	callSign: string;
	lit: boolean;
	/** The thread's identity hue — `runFace(canonical topic id)`, carried on
	 *  the cell rather than looked up by the renderer so every surface that
	 *  draws a strip agrees, and the rail and the strips cannot drift. */
	color: string;
}

/**
 * The strip for one run: every thread, in order, each either crossed or not.
 *
 * Returns `[]` when there are no threads *or* when this run crossed none —
 * an empty strip and a strip of all-dark ticks say different things, and
 * only the first is honest about a run the warp has never heard of.
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
		color: runFace(callSign).color
	}));
}
