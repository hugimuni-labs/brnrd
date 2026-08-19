import assert from 'node:assert/strict';
import test from 'node:test';

import { ARMED_ROW_CAP, armedOverflow, pickRows } from './pickLane.ts';
import type { LiveRun } from './liveRuns.ts';
import type { ScheduledWake } from './scheduledWakes.ts';
import type { WeavingRow } from './warpGraph.ts';

const NOW = Date.parse('2026-08-02T18:00:00Z');
const MINUTE = 60_000;

function at(offsetMs: number): string {
	return new Date(NOW + offsetMs).toISOString();
}

function wake(overrides: Partial<ScheduledWake> = {}): ScheduledWake {
	return {
		id: 'wake-1',
		kind: 'scheduled',
		source: 'schedule',
		status: 'scheduled',
		phase: 'at',
		bucket: 'default',
		summary: 'nightly sweep',
		repo_label: 'hugimuni-labs/brnrd',
		daemon_name: 'brnrd',
		conversation_key: null,
		scheduled_for: at(30 * MINUTE),
		reported_at: null,
		...overrides
	};
}

function run(overrides: Partial<LiveRun> = {}): LiveRun {
	return {
		id: 'run-a',
		run_id: 'run-a',
		repo_label: 'hugimuni-labs/brnrd',
		started_at: at(-7 * MINUTE),
		...overrides
	} as LiveRun;
}

test('the lane is a queue from the front: burning picks first, then soonest-first', () => {
	const rows = pickRows({
		liveRuns: [run({ id: 'burning', run_id: 'burning' })],
		scheduledWakes: [
			wake({ id: 'soon', scheduled_for: at(5 * MINUTE) }),
			wake({ id: 'later', scheduled_for: at(3 * 60 * MINUTE) })
		],
		now: NOW
	});
	assert.deepEqual(
		rows.map((row) => row.id),
		['burning', 'soon', 'later']
	);
	assert.deepEqual(
		rows.map((row) => row.phase),
		['picking', 'armed', 'armed']
	);
});

test('a picking row carries the warp items its run lifted — the weld, on the object', () => {
	const weaving: WeavingRow[] = [
		{
			callSign: 'loom',
			headline: 'THE MACHINE: the frontend fuse shipped',
			itemId: 'w-1',
			liveRunId: 'burning'
		},
		{
			callSign: 'post',
			headline: 'THE GRAVEYARD',
			itemId: 'w-2',
			liveRunId: 'other'
		}
	];
	const rows = pickRows({
		liveRuns: [run({ id: 'burning', run_id: 'burning' })],
		scheduledWakes: null,
		weaving,
		now: NOW
	});
	assert.equal(rows.length, 1);
	assert.deepEqual(
		rows[0].serves.map((s) => s.callSign),
		['loom']
	);
});

test('a pick with no warp items carries no chips — absence is a fact, not a gap', () => {
	const rows = pickRows({
		liveRuns: [run()],
		scheduledWakes: null,
		weaving: [],
		now: NOW
	});
	assert.deepEqual(rows[0].serves, []);
});

test('a burning pick reads elapsed, never a countdown bar', () => {
	const rows = pickRows({ liveRuns: [run()], scheduledWakes: null, now: NOW });
	assert.equal(rows[0].barFraction, 1);
	assert.equal(rows[0].clock, '7m 00s');
});

test('a run whose stop the server acknowledged says so instead of its clock', () => {
	const rows = pickRows({
		liveRuns: [run({ stop_requested: true } as Partial<LiveRun>)],
		scheduledWakes: null,
		now: NOW
	});
	assert.equal(rows[0].note, 'stopping…');
});

test('a run with no parseable start draws no clock rather than a fabricated zero', () => {
	const rows = pickRows({
		liveRuns: [run({ started_at: null })],
		scheduledWakes: null,
		now: NOW
	});
	assert.equal(rows[0].clock, null);
});

test('the cap keeps the soonest armed picks and announces what it dropped', () => {
	const wakes = Array.from({ length: ARMED_ROW_CAP + 3 }, (_, index) =>
		wake({ id: `w${index}`, scheduled_for: at((index + 1) * 30 * MINUTE) })
	);
	const rows = pickRows({ liveRuns: [], scheduledWakes: wakes, now: NOW });
	assert.equal(rows.length, ARMED_ROW_CAP);
	// Soonest-first, and what was kept is the near end of the schedule.
	assert.equal(rows[0].id, 'w0');
	assert.equal(rows[rows.length - 1].id, `w${ARMED_ROW_CAP - 1}`);
	assert.equal(armedOverflow(wakes, NOW), 3);
});

test('a quota-paused wake draws its verdict, not a countdown it cannot honour', () => {
	const rows = pickRows({
		liveRuns: [],
		scheduledWakes: [wake({ status: 'quota-paused' })],
		now: NOW
	});
	assert.equal(rows[0].clock, null);
	assert.equal(rows[0].note, 'quota-paused');
});

test('an empty machine is an empty lane, not a fabricated row', () => {
	assert.deepEqual(pickRows({ liveRuns: [], scheduledWakes: [], now: NOW }), []);
	assert.equal(armedOverflow([], NOW), 0);
	assert.equal(armedOverflow(null, NOW), 0);
});

// THE FORWARD WELD (2026-08-02): `serves:` is the future-tense half of
// `taken:`. Without it an armed pick is the one row in the lane with a blank
// where its crossing belongs.

test('an armed pick crosses the threads its schedule entry serves', () => {
	const rows = pickRows({
		liveRuns: [],
		scheduledWakes: [wake({ links: { serves: ['the-loom', 'the-post'] } })],
		now: NOW
	});
	assert.deepEqual(rows[0].crosses, ['the-loom', 'the-post']);
});

test('an entry that never stated serves makes no claim about threads', () => {
	// Not "crosses nothing" — no claim. `crossingCells` draws neither as a
	// strip, and only this distinction keeps a pre-weld daemon honest.
	assert.deepEqual(pickRows({ liveRuns: [], scheduledWakes: [wake()], now: NOW })[0].crosses, []);
});

test('a malformed links payload is no claim, not a crash', () => {
	// `links` is free-form JSON on the activity record; a daemon older than the
	// weld sends no key at all, and nothing validates the shape server-side.
	for (const links of [null, {}, { serves: 'the-loom' }, { serves: [1, null, ''] }]) {
		const rows = pickRows({
			liveRuns: [],
			scheduledWakes: [wake({ links } as Partial<ScheduledWake>)],
			now: NOW
		});
		assert.deepEqual(rows[0].crosses, []);
	}
});

test('a burning pick takes its threads from the taken: index, never from serves', () => {
	// Two sources for one strip is the duplicated fact this whole round is
	// about. `taken:` is authoritative for a run that exists.
	const rows = pickRows({ liveRuns: [run()], scheduledWakes: null, now: NOW });
	assert.deepEqual(rows[0].crosses, []);
});

// The armed row's two readings must agree in direction, and the label must be a
// name rather than the wire's machine prefix.

test('the bar fills as the fire nears, agreeing with the thermal colour', () => {
	const rows = pickRows({
		liveRuns: [],
		scheduledWakes: [
			wake({ id: 'soon', scheduled_for: at(5 * MINUTE) }),
			wake({ id: 'far', scheduled_for: at(90 * 60 * MINUTE) })
		],
		now: NOW
	});
	const soon = rows.find((row) => row.id === 'soon')?.barFraction ?? 0;
	const far = rows.find((row) => row.id === 'far')?.barFraction ?? 0;
	assert.ok(soon > far, `imminent should draw longer: ${soon} vs ${far}`);
});

test('a wake is labelled by its name, not by the daemon prefix the wire carries', () => {
	const rows = pickRows({
		liveRuns: [],
		scheduledWakes: [wake({ summary: 'self-scheduled thought: the-co-maintainer-tick' })],
		now: NOW
	});
	assert.equal(rows[0].label, 'the co maintainer tick');
});

test('a summary shaped some other way is left as it arrived', () => {
	// Guessing at an unrecognised format is how a renderer starts editing facts.
	const rows = pickRows({
		liveRuns: [],
		scheduledWakes: [wake({ summary: 'forge review needed' })],
		now: NOW
	});
	assert.equal(rows[0].label, 'forge review needed');
});

// #1510 ("the mood of a dead run"): `_live_runs_views` merges by `run_id`
// across every daemon on the account and sorts the result ascending by
// `started_at`, so `RunBlock.svelte`'s `burning[0]` is whichever picking row
// arrives first here. A run reported by a daemon that then retires
// merge-survives indefinitely, frozen at its last-reported `started_at` — an
// old one, which sorts *first* in ascending order and would otherwise win
// "the machine" head's face, name, and mood forever over a run that is
// actually burning right now.
test('a stale merge-survivor is dropped from the picking lane, never leads the machine head', () => {
	const rows = pickRows({
		liveRuns: [
			run({
				id: 'zombie',
				run_id: 'zombie',
				started_at: at(-3 * 60 * MINUTE),
				daemon_stale: true
			} as Partial<LiveRun>),
			run({ id: 'fresh', run_id: 'fresh', started_at: at(-1 * MINUTE) })
		],
		scheduledWakes: null,
		now: NOW
	});
	assert.deepEqual(
		rows.map((row) => row.id),
		['fresh']
	);
});

test('every picking run stale ⇒ nothing burning, not a dead one shown as live', () => {
	const rows = pickRows({
		liveRuns: [run({ id: 'zombie', run_id: 'zombie', daemon_stale: true } as Partial<LiveRun>)],
		scheduledWakes: null,
		now: NOW
	});
	assert.deepEqual(rows, []);
});

test("a burning run's live topic claim rides the row's crosses (the-run-that-claims-its-thread)", () => {
	const rows = pickRows({
		liveRuns: [run({ topics: ['the-loom', 'the-post'] }), run({ id: 'bare', run_id: 'bare' })],
		scheduledWakes: null,
		now: NOW
	});
	assert.deepEqual(rows[0].crosses, ['the-loom', 'the-post']);
	// No claim ⇒ empty, never null/undefined — the lens filters on it directly.
	assert.deepEqual(rows[1].crosses, []);
});
