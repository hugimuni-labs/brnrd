import assert from 'node:assert/strict';
import test from 'node:test';

import { futureEtaLabel, futureShelfRows, nextFutureWake } from './futureShelf.ts';
import { LOOM_MIN_FUTURE_HORIZON_MS, loomBarFraction } from './loomBand.ts';
import { THERMAL_STOPS } from './statusPalette.ts';
import type { ScheduledWake } from './scheduledWakes.ts';

// The rack's future shelf (the dissolution, 2026-08-02): the loom band's
// future rules, factored out rather than rewritten. These tests pin what
// travelled: soonest first, the band's sqrt bar fraction against the shared
// horizon, the thermal thaw, and the compact ETA legend.

const NOW = Date.parse('2026-08-01T12:00:00Z');
const HOUR = 60 * 60 * 1000;

function wake(over: Partial<ScheduledWake>): ScheduledWake {
	return {
		id: 'wake-1',
		kind: 'scheduled',
		source: 'schedule',
		status: 'scheduled',
		phase: 'at',
		bucket: 'schedule',
		summary: 'nightly sweep',
		repo_label: null,
		daemon_name: null,
		conversation_key: null,
		scheduled_for: null,
		reported_at: null,
		...over
	};
}

function at(offsetMs: number): string {
	return new Date(NOW + offsetMs).toISOString();
}

test('rows sort soonest first; a wake with no parseable instant makes no row', () => {
	const rows = futureShelfRows(
		[
			wake({ id: 'far', scheduled_for: at(4 * HOUR) }),
			wake({ id: 'anchoring', scheduled_for: null }),
			wake({ id: 'garbled', scheduled_for: 'not-a-date' }),
			wake({ id: 'soon', scheduled_for: at(HOUR) })
		],
		NOW
	);
	assert.deepEqual(
		rows.map((row) => row.wake.id),
		['soon', 'far'],
		'soonest first — a bar with no length is a fabrication, so no instant means no row'
	);
	assert.equal(futureShelfRows(null, NOW).length, 0, 'a feed not yet loaded shelves nothing');
});

test("bar fraction runs the band's sqrt scale against the shared horizon", () => {
	// Both wakes inside six hours: the horizon holds at the minimum.
	const near = futureShelfRows(
		[wake({ id: 'a', scheduled_for: at(HOUR) }), wake({ id: 'b', scheduled_for: at(4 * HOUR) })],
		NOW
	);
	assert.equal(near[0].barFraction, loomBarFraction(HOUR, LOOM_MIN_FUTURE_HORIZON_MS));
	assert.equal(near[1].barFraction, loomBarFraction(4 * HOUR, LOOM_MIN_FUTURE_HORIZON_MS));

	// A real later wake extends the horizon for every row — one denominator,
	// so the bars compare with each other.
	const far = futureShelfRows(
		[
			wake({ id: 'a', scheduled_for: at(3 * HOUR) }),
			wake({ id: 'b', scheduled_for: at(12 * HOUR) })
		],
		NOW
	);
	assert.equal(far[0].barFraction, loomBarFraction(3 * HOUR, 12 * HOUR));
	assert.equal(far[1].barFraction, 1, 'the furthest wake fills the track');

	// Overdue clamps to zero and rides the band's floor: visibly a bar.
	const overdue = futureShelfRows([wake({ id: 'late', scheduled_for: at(-10 * 60 * 1000) })], NOW);
	assert.equal(overdue[0].barFraction, 0.06);
});

test('colors thaw toward now; paused and overdue wakes read ash', () => {
	const [dueSoon] = futureShelfRows([wake({ scheduled_for: at(10 * 60 * 1000) })], NOW);
	assert.equal(dueSoon.color, THERMAL_STOPS.amber);
	assert.equal(dueSoon.urgency, 'attention');

	const [mid, deep] = futureShelfRows(
		[
			wake({ id: 'mid', scheduled_for: at(2 * HOUR) }),
			wake({ id: 'deep', scheduled_for: at(5 * HOUR) })
		],
		NOW
	);
	assert.equal(mid.color, THERMAL_STOPS.frost);
	assert.equal(mid.urgency, 'calm');
	assert.equal(deep.color, THERMAL_STOPS['frost-deep']);

	const [late] = futureShelfRows([wake({ scheduled_for: at(-5 * 60 * 1000) })], NOW);
	assert.equal(late.color, THERMAL_STOPS.ash, 'no honest countdown to draw');
	assert.equal(late.urgency, 'alarm');

	const [paused] = futureShelfRows(
		[wake({ status: 'quota-paused', scheduled_for: at(10 * 60 * 1000) })],
		NOW
	);
	assert.equal(paused.color, THERMAL_STOPS.ash, 'paused beats imminent — the scheduler said so');
	assert.equal(paused.urgency, 'calm');
});

test("legends: eta · summary, with the scheduler's verdict when it has one", () => {
	const [plain] = futureShelfRows([wake({ scheduled_for: at(42 * 60 * 1000) })], NOW);
	assert.equal(plain.legend, 'in 42m · nightly sweep');

	const [paced] = futureShelfRows(
		[wake({ status: 'quota-paced', scheduled_for: at(42 * 60 * 1000) })],
		NOW
	);
	assert.equal(paced.legend, 'in 42m · quota-paced · nightly sweep');

	const [paused] = futureShelfRows(
		[wake({ status: 'quota-paused', scheduled_for: at(HOUR) })],
		NOW
	);
	assert.equal(paused.legend, 'quota-paused · nightly sweep');

	const [keyed] = futureShelfRows(
		[wake({ summary: '', conversation_key: 'tg:123', scheduled_for: at(42 * 60 * 1000) })],
		NOW
	);
	assert.equal(keyed.legend, 'in 42m · tg:123', 'the conversation key speaks when no summary does');
});

test('eta labels: minutes, then hours, and overdue says so', () => {
	assert.equal(futureEtaLabel(42 * 60 * 1000), 'in 42m');
	assert.equal(futureEtaLabel(2 * HOUR + 5 * 60 * 1000), 'in 2h 5m');
	assert.equal(futureEtaLabel(-12 * 60 * 1000), '12m overdue');
});

test('the next wake is the soonest still ahead — never an overdue one', () => {
	const rows = futureShelfRows(
		[
			wake({ id: 'late', scheduled_for: at(-10 * 60 * 1000) }),
			wake({ id: 'ahead', scheduled_for: at(HOUR) })
		],
		NOW
	);
	assert.equal(nextFutureWake(rows)?.wake.id, 'ahead');
	assert.equal(nextFutureWake([]), null);
});
