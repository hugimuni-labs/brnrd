/**
 * THE PICK — one object, one place, moving.
 *
 * His 2026-08-02 read of the machine block, verbatim: *"they repeat after each
 * other … I would actually rather prefer … really turn the UI around pushing a
 * run object through the stages of the execution."* Measured, one live run was
 * drawn **five** times on that page: a `from the warp · weaving` row, a NOW
 * seam cell, the unfolded node, its warp item above, its cloth line below. Five
 * drawings of one object is why no stage read as a stage — nothing moved, every
 * section re-rendered.
 *
 * The lane replaces the first two of those five with one row that changes heat.
 * A pick is the same object at every phase; only its phase, its clock, and its
 * temperature differ:
 *
 *   armed   — a scheduled wake, not yet fired. Frost thawing toward amber as
 *             the fire nears; the bar is imminence drawn as geometry.
 *   picking — a run burning right now. Amber, elapsed clock, and the warp
 *             items it lifted carried *on the row* instead of in a second list.
 *
 * Order is the fall: furthest-off armed pick at the top, then down through the
 * soonest, then the picking rows at the bottom edge — against the fell line,
 * where the next thing that happens to them is becoming cloth. A row's position
 * in the lane *is* its progress through execution, which is the whole ask.
 *
 * Value imports carry `.ts` extensions because the tests run under node's own
 * runner with no bundler in the loop — the rule `cloth.ts` and `futureShelf.ts`
 * already document.
 */

import type { LiveRun } from './liveRuns.ts';
import { liveRunDisplayName } from './liveRuns.ts';
import type { ScheduledWake } from './scheduledWakes.ts';
import type { WeavingRow } from './warp.ts';
import { futureEtaLabel, futureShelfRows } from './futureShelf.ts';
import { durationLabel } from './runLedger.ts';
import { THERMAL_STOPS, type GlowUrgency } from './statusPalette.ts';

/**
 * How many armed picks the lane draws before it starts saying "+N further".
 *
 * A schedule with thirty `every:` entries would otherwise push the burning rows
 * a screen and a half below the fold — the lane's own ordering turned against
 * it. The cap keeps the *soonest* ones, because those are the ones about to
 * become picking rows; the overflow is announced, never silently dropped.
 */
export const ARMED_ROW_CAP = 6;

/** How many picking rows draw in full before the lane folds the rest to a count. */
export const PICKING_ROW_CAP = 4;

export type PickPhase = 'armed' | 'picking';

/** One warp item this pick lifted — the weld, drawn on the pick instead of in
 *  its own list. */
export interface PickServes {
	callSign: string;
	headline: string;
}

export interface PickRow {
	/** Selection id, in the vocabulary the page's `onSelect` already speaks. */
	id: string;
	kind: 'run' | 'wake';
	phase: PickPhase;
	/** The object's own name — a run's `.name`, a wake's summary. */
	label: string;
	/** The clock: `in 42m` while armed, elapsed while picking. Null when the
	 *  wire has no honest number to draw rather than a fabricated zero. */
	clock: string | null;
	/** The scheduler's own verdict when it has one (`quota-paused`), or
	 *  `stopping…` for a run whose stop the server has acknowledged. */
	note: string | null;
	color: string;
	urgency: GlowUrgency;
	/** Imminence as geometry, 0..1. Always 1 while picking: a burning run has
	 *  arrived, and a shrinking bar under it would read as a countdown to
	 *  nothing. */
	barFraction: number;
	serves: PickServes[];
	/**
	 * Warp threads this pick crosses, for `crossing.ts` — the forward weld.
	 *
	 * An armed pick reads them from the schedule entry's `serves:` row, which
	 * the daemon publishes on the activity record's `links`. A picking one
	 * reads nothing here: the page already holds a `taken:`-built index that is
	 * authoritative for a run that exists, and two sources for one strip is
	 * exactly the duplicated fact this whole round is about. Empty means *no
	 * claim* — not "crosses nothing".
	 */
	crosses: string[];
}

/**
 * The warp threads a scheduled wake says it serves.
 *
 * Defensive about the wire on purpose: `links` is free-form JSON on the
 * activity record, so this is the one field on a `ScheduledWake` that a daemon
 * older than the forward weld simply will not send, and a hand-authored
 * `serves:` row is what fills it. Anything that is not a list of strings is no
 * claim at all.
 */
export function servesThreads(wake: ScheduledWake): string[] {
	const serves = wake.links?.serves;
	if (!Array.isArray(serves)) return [];
	return serves.filter((thread): thread is string => typeof thread === 'string' && thread !== '');
}

function elapsedClock(run: LiveRun, now: number): string | null {
	const started = run.started_at ? Date.parse(run.started_at) : Number.NaN;
	if (!Number.isFinite(started)) return null;
	return durationLabel(Math.max(0, (now - started) / 1000));
}

/**
 * The lane, ordered as the fall: armed picks furthest-first, then the picking
 * rows last — nearest the fell line.
 *
 * `weaving` is the `taken:`-live join the page already computes for the warp
 * (`weavingRows`); folding it in here is what retires the separate
 * `from the warp · weaving` list. A pick with no warp items simply carries no
 * chips — the absence is a fact, not a missing feature.
 */
export function pickRows(input: {
	liveRuns: LiveRun[] | null;
	scheduledWakes: ScheduledWake[] | null;
	weaving?: WeavingRow[];
	now: number;
}): PickRow[] {
	const { liveRuns, scheduledWakes, weaving = [], now } = input;

	const servesByRun = new Map<string, PickServes[]>();
	for (const row of weaving) {
		const list = servesByRun.get(row.liveRunId) ?? [];
		list.push({ callSign: row.callSign, headline: row.item.headline });
		servesByRun.set(row.liveRunId, list);
	}

	// `futureShelfRows` already does the honest work — unparseable instants
	// dropped, one shared horizon so the bars compare, thermal thaw toward now.
	// Reversed here because the lane falls: soonest sits closest to the fire.
	const armed: PickRow[] = futureShelfRows(scheduledWakes, now)
		.map((row) => ({
			id: row.wake.id,
			kind: 'wake' as const,
			phase: 'armed' as const,
			label: (row.wake.summary || row.wake.conversation_key || 'wake').trim(),
			clock: row.wake.status === 'quota-paused' ? null : futureEtaLabel(row.etaMs),
			note:
				row.wake.status === 'quota-paused' || row.wake.status === 'quota-paced'
					? row.wake.status
					: null,
			color: row.color,
			urgency: row.urgency,
			barFraction: row.barFraction,
			serves: [],
			crosses: servesThreads(row.wake)
		}))
		.slice(0, ARMED_ROW_CAP)
		.reverse();

	const picking: PickRow[] = (liveRuns ?? []).map((run) => {
		const id = run.run_id || run.id;
		return {
			id,
			kind: 'run' as const,
			phase: 'picking' as const,
			label: liveRunDisplayName(run) || run.repo_label || 'live run',
			clock: elapsedClock(run, now),
			note: run.stop_requested ? 'stopping…' : null,
			color: THERMAL_STOPS.amber,
			urgency: (liveRuns ?? []).length > 1 ? ('attention' as const) : ('calm' as const),
			barFraction: 1,
			serves: servesByRun.get(id) ?? [],
			// A live run's threads come from the page's `taken:` index, which is
			// authoritative for a run that exists. Nothing to add here.
			crosses: []
		};
	});

	return [...armed, ...picking];
}

/** How many armed picks the cap left off the top of the lane. */
export function armedOverflow(scheduledWakes: ScheduledWake[] | null, now: number): number {
	return Math.max(0, futureShelfRows(scheduledWakes, now).length - ARMED_ROW_CAP);
}
