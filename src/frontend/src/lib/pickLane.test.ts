import assert from 'node:assert/strict';
import test from 'node:test';

import { ARMED_ROW_CAP, armedOverflow, pickRows } from './pickLane.ts';
import type { LiveRun } from './liveRuns.ts';
import type { ScheduledWake } from './scheduledWakes.ts';
import type { WeavingRow } from './warp.ts';

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

test('the lane falls: armed picks furthest-first, burning ones last', () => {
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
		['later', 'soon', 'burning']
	);
	assert.deepEqual(
		rows.map((row) => row.phase),
		['armed', 'armed', 'picking']
	);
});

test('a picking row carries the warp items its run lifted — the weld, on the object', () => {
	const weaving = [
		{
			callSign: 'the-loom',
			path: 'surface/layers/the-loom.md',
			item: { headline: 'THE MACHINE: the frontend fuse shipped' },
			liveRunId: 'burning'
		},
		{
			callSign: 'the-post',
			path: 'surface/layers/the-post.md',
			item: { headline: 'THE GRAVEYARD' },
			liveRunId: 'other'
		}
	] as unknown as WeavingRow[];
	const rows = pickRows({
		liveRuns: [run({ id: 'burning', run_id: 'burning' })],
		scheduledWakes: null,
		weaving,
		now: NOW
	});
	assert.equal(rows.length, 1);
	assert.deepEqual(
		rows[0].serves.map((s) => s.callSign),
		['the-loom']
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
	// Furthest-first within what was kept, and what was kept is the near end.
	assert.equal(rows[rows.length - 1].id, 'w0');
	assert.equal(rows[0].id, `w${ARMED_ROW_CAP - 1}`);
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
