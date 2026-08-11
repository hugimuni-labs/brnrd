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
 * Order is a queue seen from the front: the picks being thrown at the head, the
 * ones waiting behind them, soonest first. A row's position still encodes its
 * phase — above the seam rule it is happening, below it is not yet — it just
 * encodes *queue depth* rather than a downward journey.
 *
 * The first cut ordered it as a fall (furthest-out pick at the top, descending
 * to the burning one at the bottom) so the lane would rhyme with the page's
 * sections, which are a run's life top to bottom. His read, minutes after it
 * deployed: *"I think the live run should appear on top beneath the rack?"* —
 * correct, and the diagnosis is that the sections and the lane are not the same
 * axis. Making them rhyme cost the reader the one row they open the page for.
 *
 * Value imports carry `.ts` extensions because the tests run under node's own
 * runner with no bundler in the loop — the rule `cloth.ts` and `futureShelf.ts`
 * already document.
 */

import type { LiveRun, MoodFace } from './liveRuns.ts';
import { liveRunDisplayName, moodFace } from './liveRuns.ts';
import type { ScheduledWake } from './scheduledWakes.ts';
import type { WeavingRow } from './warpGraph.ts';
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
	/**
	 * The resident's mood (#566), picking rows only — an armed wake has never
	 * run and so has never felt anything (THE FACE IN THREE TENSES, piece 1's
	 * own constraint: a future run gets no face). Same `moodFace` normalizer
	 * every other surface uses, so an unknown handle degrades to name-only
	 * here exactly as it does on the LiveRuns card — this file adds no second
	 * opinion about what a mood is.
	 */
	mood: MoodFace | null;
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
/**
 * A scheduled wake's own name, for the lane.
 *
 * The wire's `summary` is `self-scheduled thought: <entry-id>` — a shape the
 * daemon chose on purpose (#502: a schedule *body* is dominion content and the
 * managed backend gets the id, never an excerpt). That is the right thing to
 * transmit and the wrong thing to render: the reader gets a machine prefix and
 * a hyphenated slug where a name belongs, on a row they are being asked to
 * read as an object with a life.
 *
 * So the prefix is stripped and the slug is spaced. Only the *known* prefix —
 * a summary shaped some other way is left exactly as it arrived, because
 * guessing at an unrecognised format is how a renderer starts editing facts.
 */
export function wakeLabel(wake: ScheduledWake): string {
	const raw = (wake.summary || wake.conversation_key || 'wake').trim();
	const stripped = raw.startsWith(SCHEDULE_SUMMARY_PREFIX)
		? raw.slice(SCHEDULE_SUMMARY_PREFIX.length).trim()
		: raw;
	return stripped.replace(/-/g, ' ');
}

/** The daemon's own wording for a schedule row's summary (`cloud.py`). */
const SCHEDULE_SUMMARY_PREFIX = 'self-scheduled thought:';

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
 * The lane, ordered as the queue: the burning picks first, then the armed ones
 * behind them, soonest first.
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
		list.push({ callSign: row.callSign, headline: row.headline });
		servesByRun.set(row.liveRunId, list);
	}

	// `futureShelfRows` already does the honest work — unparseable instants
	// dropped, one shared horizon so the bars compare, thermal thaw toward now.
	// Its order is soonest-first, which is also the queue's order: the next one
	// to burn sits closest to the rule it is about to cross.
	const armed: PickRow[] = futureShelfRows(scheduledWakes, now)
		.map((row) => ({
			id: row.wake.id,
			kind: 'wake' as const,
			phase: 'armed' as const,
			label: wakeLabel(row.wake),
			clock: row.wake.status === 'quota-paused' ? null : futureEtaLabel(row.etaMs),
			note:
				row.wake.status === 'quota-paused' || row.wake.status === 'quota-paced'
					? row.wake.status
					: null,
			color: row.color,
			urgency: row.urgency,
			// Inverted from `futureShelfRows`' distance fraction, and the reason is
			// a disagreement the first cut shipped: the thermal colour *warms* as a
			// wake nears, while the bar *grew* with distance. Two encodings of the
			// same quantity pointing opposite ways is unreadable however correct
			// each is alone — long and warm now both mean imminent, and a wake four
			// days out recedes to a stub instead of dominating the lane.
			barFraction: 1 - row.barFraction,
			serves: [],
			crosses: servesThreads(row.wake),
			// Armed: not yet fired, nothing to have felt. See the field's own
			// doc — the future gets no face, by construction.
			mood: null
		}))
		.slice(0, ARMED_ROW_CAP);

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
			// A live run's threads: its own claimed topics off the wire
			// (`.topics` → presence heartbeat → live-runs payload), fresher
			// than any node file — the page's `taken:` index still unions in
			// at render time (`PickLane.rowTopics`), so an item-ignited run
			// and a claiming run both read true, together when both hold.
			crosses: run.topics ?? [],
			mood: moodFace(run.mood, run.mood_glyph, run.mood_pitch, run.mood_frames, run.mood_rest)
		};
	});

	return [...picking, ...armed];
}

/** How many armed picks the cap left off the top of the lane. */
export function armedOverflow(scheduledWakes: ScheduledWake[] | null, now: number): number {
	return Math.max(0, futureShelfRows(scheduledWakes, now).length - ARMED_ROW_CAP);
}
