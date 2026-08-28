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
	/** The carrier boundary's already-redacted detail — the *command* the
	 *  page rode in on. Kept: the action log is genuinely useful and the
	 *  maintainer said so ("that is not to say the action log is bad it is
	 *  actually good, it is just not what you get injected"). */
	detail: string | null;
	/** THE BLOCK ITSELF — what the daemon actually injected at this boundary.
	 *
	 *  This is the field the pager exists to show, and until 2026-08-28 it
	 *  could not: `boundaries.jsonl` stored the injection verbatim, the
	 *  publisher wrote `bool(...)`, and the page had nothing left to render
	 *  but `detail`. So the pager showed commands, was reported as wrong four
	 *  separate times, and was four times correctly described as "implemented
	 *  and not rendered".
	 *
	 *  `null` on a daemon predating the wire field — which is why the row
	 *  falls back to the carrier rather than rendering an empty page. An
	 *  absent block is not an empty one. */
	injection: string | null;
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
			detail: edge.detail ?? null,
			injection: edge.injection ?? null
		};
		feed.push(page);
		if (feed.length > PAGER_CAP) feed.splice(0, feed.length - PAGER_CAP);
		fresh.push(page);
	}
	return fresh;
}

/** Pages newest first — the strip's reading order.
 *
 *  `liveRunIds` scopes the feed to runs that still exist. Without it the
 *  store is every page from up to 24 run ids × `PAGER_CAP`, so a quiet
 *  account read `✉×152 read · nothing waiting` — a number that is neither
 *  wrong nor about anything the reader can act on, since 149 of those rode
 *  runs that ended days ago (reported 2026-08-28: "which is quite a useless
 *  info"). The store deliberately keeps them, because a page is how a
 *  finished run's traffic stays inspectable; what changes is that the
 *  *strip* is a condition readout, and a condition is about now.
 *
 *  Omitted (the default) the feed is unscoped, which is what the trail
 *  history wants. Passing an empty set is a real, different answer: no runs
 *  are live, so nothing is current. */
export function pagerFeed(
	store: Record<string, PagerPage[]>,
	liveRunIds?: ReadonlySet<string>
): PagerPage[] {
	const entries = Object.entries(store).filter(
		([runId]) => liveRunIds === undefined || liveRunIds.has(runId)
	);
	return entries.flatMap(([, pages]) => pages).sort((a, b) => b.at.localeCompare(a.at));
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
