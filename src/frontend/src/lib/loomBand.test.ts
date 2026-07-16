import assert from 'node:assert/strict';
import test from 'node:test';

import {
	LOOM_MIN_FUTURE_HORIZON_MS,
	LOOM_PAST_WINDOW_MS,
	loomFutureHorizon,
	loomFuturePosition,
	loomFutureStop,
	loomPastPosition,
	loomPastStop
} from './loomBand.ts';

const NOW = Date.parse('2026-07-16T18:00:00Z');

test('past mapping pins NOW and 24h while compressing age logarithmically', () => {
	assert.equal(loomPastPosition(0), 1);
	assert.equal(loomPastPosition(LOOM_PAST_WINDOW_MS), 0);
	assert.equal(loomPastPosition(LOOM_PAST_WINDOW_MS * 2), 0);
	assert.ok(loomPastPosition(60 * 60 * 1000) > loomPastPosition(6 * 60 * 60 * 1000));
	assert.ok(loomPastPosition(60 * 60 * 1000) < 0.5, 'the log curve gives recent age room');
});

test('future horizon holds six hours and extends to the furthest real wake', () => {
	assert.equal(loomFutureHorizon([], NOW), LOOM_MIN_FUTURE_HORIZON_MS);
	assert.equal(
		loomFutureHorizon(['2026-07-16T18:20:00Z', null, 'not-a-date'], NOW),
		LOOM_MIN_FUTURE_HORIZON_MS
	);
	assert.equal(
		loomFutureHorizon(['2026-07-17T06:00:00Z', '2026-07-16T20:00:00Z'], NOW),
		12 * 60 * 60 * 1000
	);
});

test('future mapping pins overdue at NOW and the furthest wake at the edge', () => {
	const horizon = 12 * 60 * 60 * 1000;
	assert.equal(loomFuturePosition(-1, horizon), 0);
	assert.equal(loomFuturePosition(0, horizon), 0);
	assert.equal(loomFuturePosition(horizon, horizon), 1);
	assert.equal(loomFuturePosition(horizon * 2, horizon), 1);
	assert.ok(loomFuturePosition(60 * 60 * 1000, horizon) > 0);
	assert.ok(loomFuturePosition(60 * 60 * 1000, horizon) < 1);
});

test('thermal positions step rather than interpolate', () => {
	assert.equal(loomPastStop(60_000), 'amber');
	assert.equal(loomPastStop(8 * 60 * 60 * 1000), 'ember-ash');
	assert.equal(loomPastStop(20 * 60 * 60 * 1000), 'ash');
	assert.equal(loomFutureStop(5 * 60 * 1000, LOOM_MIN_FUTURE_HORIZON_MS), 'amber');
	assert.equal(loomFutureStop(2 * 60 * 60 * 1000, LOOM_MIN_FUTURE_HORIZON_MS), 'frost');
	assert.equal(loomFutureStop(5 * 60 * 60 * 1000, LOOM_MIN_FUTURE_HORIZON_MS), 'frost-deep');
});
