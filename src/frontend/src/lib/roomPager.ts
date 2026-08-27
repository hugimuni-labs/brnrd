// The pager — the accumulated boundary-injection feed (#1654, the pager
// ceremony). The name is the mechanism, not a metaphor: the daemon can only
// reach an actor at tool boundaries, so between them, pages pile up in a
// buffer the actor reads when it next looks down. One-way, accumulating,
// read-on-glance.
//
// Wire honesty (the fence this module renders *around*, never through):
// the live wire attests THAT an injection landed (`edge.injected`), when,
// and which boundary carried it — never what it said. The injected text
// lives in `boundaries.jsonl`, which deliberately never leaves the machine.
// So a page names its carrier, not its content — and the feed is what the
// wire attested while this reader watched: a page a poll never saw is
// absent, not invented.
//
// The reading ceremony follows the room's movement doctrine (#1652): a
// change may animate only when canonical input attests it. One attested
// injection produces one bounded reading — the actor stays exactly where it
// stands (injection never teleports the actor; traffic comes *to* it),
// drops into reading frames for a fixed number of display ticks, and
// settles. No decorative loop.

import type { LiveRun } from './liveRuns.ts';

/** One page: an attested injection, named by its carrying boundary. */
export interface PagerPage {
	/** Boundary timestamp (ISO) that carried the page — the dedupe key. */
	at: string;
	runId: string;
	/** The actor's glyph at mint time, so the strip can name the reader. */
	glyph: string;
	/** The act the page rode in on. */
	act: string | null;
	/** The carrier boundary's already-redacted detail. */
	detail: string | null;
}

/** Pages kept per run — enough feed to scroll a thought, not an archive. */
export const PAGER_CAP = 40;

/**
 * Accumulate pages from injected edges into `store` (mutated, caller owns
 * persistence — same pattern as the trails). Returns the pages newly minted
 * this poll: the ceremony triggers.
 */
export function recordPages(
	runs: Pick<LiveRun, 'run_id' | 'edge'>[],
	store: Record<string, PagerPage[]>,
	glyphByRun?: Record<string, string>
): PagerPage[] {
	const fresh: PagerPage[] = [];
	for (const run of runs) {
		const edge = run.edge;
		const at = edge?.at ?? null;
		if (!edge || !at || !edge.injected) continue;
		const feed = (store[run.run_id] ??= []);
		if (feed.some((p) => p.at === at)) continue;
		const page: PagerPage = {
			at,
			runId: run.run_id,
			glyph: glyphByRun?.[run.run_id] ?? '@',
			act: edge.act ?? null,
			detail: edge.detail ?? null
		};
		feed.push(page);
		if (feed.length > PAGER_CAP) feed.splice(0, feed.length - PAGER_CAP);
		fresh.push(page);
	}
	return fresh;
}

/** All pages across actors, newest first — the strip's reading order. */
export function pagerFeed(store: Record<string, PagerPage[]>): PagerPage[] {
	return Object.values(store)
		.flat()
		.sort((a, b) => b.at.localeCompare(a.at));
}

/** A reading in progress: presentation state minted from one attested
 *  injection. Pure data — the caller owns time, exactly like a Walk. */
export interface Reading {
	actorRunId: string;
	/** Display ticks remaining; the ceremony ends at 0 and is dropped. */
	ticksLeft: number;
}

/** ~2.4s at the page's 160ms ticker — a beat, not a cutscene. */
export const READING_TICKS = 15;

/** Mint readings for freshly-arrived pages; an actor already reading
 *  restarts its ceremony (two pages in one glance is still one glance). */
export function readingsFor(fresh: PagerPage[], existing: Reading[]): Reading[] {
	if (fresh.length === 0) return existing;
	const started = new Set(fresh.map((p) => p.runId));
	const kept = existing.filter((r) => !started.has(r.actorRunId));
	return [...kept, ...[...started].map((id) => ({ actorRunId: id, ticksLeft: READING_TICKS }))];
}

/** Advance every reading one display tick; finished readings are dropped. */
export function advanceReadings(readings: Reading[]): Reading[] {
	return readings.map((r) => ({ ...r, ticksLeft: r.ticksLeft - 1 })).filter((r) => r.ticksLeft > 0);
}

/** Display phase per actor for the tether animation, keyed by run id. */
export function readingPhases(readings: Reading[]): Record<string, number> {
	const out: Record<string, number> = {};
	for (const r of readings) out[r.actorRunId] = r.ticksLeft;
	return out;
}
