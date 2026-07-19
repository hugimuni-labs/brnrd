import assert from 'node:assert/strict';
import test from 'node:test';

import {
	LOOM_MIN_FUTURE_HORIZON_MS,
	loomBarFraction,
	loomCellClickSelects,
	loomFutureHorizon,
	loomFutureStop,
	loomPastStop,
	loomPastWindowLabel
} from './loomBand.ts';

const NOW = Date.parse('2026-07-16T18:00:00Z');

test('bar fraction floors at a visible sliver and caps at the full track', () => {
	assert.equal(loomBarFraction(0, 100), 0.06);
	assert.equal(loomBarFraction(-5, 100), 0.06);
	assert.equal(loomBarFraction(50, 0), 0.06);
	assert.equal(loomBarFraction(100, 100), 1);
	assert.equal(loomBarFraction(200, 100), 1, 'over-max clamps rather than overflows');
});

test('bar fraction spreads a long tail instead of flattening it', () => {
	// sqrt scaling: a run at 1/4 of max still gets half the track — one
	// marathon run must not shrink every other bar into a sliver.
	const quarter = loomBarFraction(25, 100);
	assert.ok(quarter > 0.5 && quarter < 0.6, `sqrt spread, got ${quarter}`);
	assert.ok(loomBarFraction(25, 100) > 25 / 100 + 0.06);
});

test('past window labels are legible units', () => {
	assert.equal(loomPastWindowLabel(6 * 60 * 60 * 1000), '6h');
	assert.equal(loomPastWindowLabel(24 * 60 * 60 * 1000), '24h');
	assert.equal(loomPastWindowLabel(7 * 24 * 60 * 60 * 1000), '7d');
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

test('thermal positions step rather than interpolate', () => {
	assert.equal(loomPastStop(60_000), 'amber');
	assert.equal(loomPastStop(8 * 60 * 60 * 1000), 'ember-ash');
	assert.equal(loomPastStop(20 * 60 * 60 * 1000), 'ash');
	assert.equal(loomFutureStop(5 * 60 * 1000, LOOM_MIN_FUTURE_HORIZON_MS), 'amber');
	assert.equal(loomFutureStop(2 * 60 * 60 * 1000, LOOM_MIN_FUTURE_HORIZON_MS), 'frost');
	assert.equal(loomFutureStop(5 * 60 * 60 * 1000, LOOM_MIN_FUTURE_HORIZON_MS), 'frost-deep');
});

// The #478/#482 collision: a shelf cell is a real anchor *and* the loom is the
// spine. Only the unmodified primary click may be intercepted — every gesture a
// reader uses to mean "open this elsewhere" has to survive untouched, or the
// fix trades one broken affordance for another.
test('a plain left click selects; every modified click still follows the link', () => {
	assert.equal(loomCellClickSelects({ button: 0 }), true);
	assert.equal(loomCellClickSelects({}), true, 'a bare synthetic click is primary');

	assert.equal(loomCellClickSelects({ button: 1 }), false, 'middle-click opens a tab');
	assert.equal(loomCellClickSelects({ button: 2 }), false, 'right-click opens the menu');
	assert.equal(loomCellClickSelects({ button: 0, metaKey: true }), false);
	assert.equal(loomCellClickSelects({ button: 0, ctrlKey: true }), false);
	assert.equal(loomCellClickSelects({ button: 0, shiftKey: true }), false);
	assert.equal(loomCellClickSelects({ button: 0, altKey: true }), false, 'alt-click downloads');

	assert.equal(
		loomCellClickSelects({ button: 0, defaultPrevented: true }),
		false,
		'something upstream already handled it'
	);
});
