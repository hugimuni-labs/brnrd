import assert from 'node:assert/strict';
import test from 'node:test';

import {
	isCollapsed,
	railScrollVerdict,
	scrollClockTick,
	tapVerdict,
	type ScrollClock
} from './collapse.ts';

// isCollapsed — the rule both the rail and the machine answer to: a reader's
// own `open` (and `pinnedOpen`) always outrank the scroll verdict.

test('at rest (not scrolled past) a block is never collapsed, whatever the reader opened', () => {
	for (const pinnedOpen of [false, true]) {
		for (const open of [false, true]) {
			assert.equal(isCollapsed({ open, pinnedOpen, scrolledPast: false }), false);
		}
	}
});

test('scrolled past, an untouched block collapses to its compact form', () => {
	assert.equal(isCollapsed({ open: false, pinnedOpen: false, scrolledPast: true }), true);
});

test('scrolled past, the reader opening it survives the scroll verdict', () => {
	assert.equal(isCollapsed({ open: true, pinnedOpen: false, scrolledPast: true }), false);
});

test('scrolled past, pinning it open survives the scroll verdict too', () => {
	assert.equal(isCollapsed({ open: false, pinnedOpen: true, scrolledPast: true }), false);
});

// tapVerdict — a tap on a block whose head may be scrolled away from its body.

test('at rest a tap is an ordinary disclosure toggle and never travels', () => {
	assert.deepEqual(tapVerdict(false, false), { open: true, travel: false });
	assert.deepEqual(tapVerdict(true, false), { open: false, travel: false });
});

test('scrolled past and shut, a tap opens the block and travels to it', () => {
	assert.deepEqual(tapVerdict(false, true), { open: true, travel: true });
});

test('scrolled past and open, a tap only travels — it never folds a body it cannot see', () => {
	assert.deepEqual(tapVerdict(true, true), { open: null, travel: true });
});

test('no tap taken past the scroll verdict can ever close what the reader opened', () => {
	for (const open of [true, false]) {
		assert.notEqual(tapVerdict(open, true).open, false);
	}
});

// railScrollVerdict — moved from controlStrip.test.ts (2026-08-08, the
// unified scroll/settle clock): the geometry is unchanged, THE BOUNDARY THAT
// FLICKERED's dead band has the same asymmetric thresholds as always.
// Condensing waits for the whole full rail to scroll past; un-condensing
// waits for the return to the rail's natural top. A reader parked anywhere
// between them (which is where a slow touchpad scroll lives) must see no
// form change in either direction.
const RAIL = { railTop: 100, railFullHeight: 180 };

test('the rail does not condense while any of its full form is still on screen', () => {
	// Old trigger fired at scrollY > railTop (101). That inflated the spacer
	// while the freed band was still visible, and 1px of jitter toggled it.
	assert.equal(railScrollVerdict({ ...RAIL, scrollY: 101, condensed: false }), false);
	assert.equal(railScrollVerdict({ ...RAIL, scrollY: 279, condensed: false }), false);
});

test('the rail condenses once the reader has scrolled past the whole of it', () => {
	assert.equal(railScrollVerdict({ ...RAIL, scrollY: 281, condensed: true }), true);
	assert.equal(railScrollVerdict({ ...RAIL, scrollY: 281, condensed: false }), true);
});

test('a condensed rail stays condensed through the dead band — no flicker on the way up', () => {
	// Same scroll positions as the first test, opposite prior state: the
	// verdict must hold, not toggle. This pair IS the hysteresis.
	assert.equal(railScrollVerdict({ ...RAIL, scrollY: 279, condensed: true }), true);
	assert.equal(railScrollVerdict({ ...RAIL, scrollY: 120, condensed: true }), true);
});

test('the rail un-condenses only back at its natural top', () => {
	assert.equal(railScrollVerdict({ ...RAIL, scrollY: 107, condensed: true }), false);
	assert.equal(railScrollVerdict({ ...RAIL, scrollY: 0, condensed: true }), false);
});

test('an unmeasured full height still gets a minimum dead band, not a zero one', () => {
	// Before the first measurement railFullHeight is 0; a zero band would
	// reintroduce the single shared boundary this function exists to remove.
	assert.equal(
		railScrollVerdict({ railTop: 100, railFullHeight: 0, scrollY: 120, condensed: false }),
		false
	);
	assert.equal(
		railScrollVerdict({ railTop: 100, railFullHeight: 0, scrollY: 148, condensed: false }),
		false
	);
	assert.equal(
		railScrollVerdict({ railTop: 100, railFullHeight: 0, scrollY: 149, condensed: false }),
		true
	);
});

// scrollClockTick — the shared settle clock both the rail and the machine
// dock now run through (2026-08-08). Expansion is immediate; collapse waits
// `settleMs` past the last qualifying tick.

const REST: ScrollClock = { settled: false, pendingAt: null };

test('raw=false clears the clock outright — expansion is never debounced', () => {
	assert.deepEqual(scrollClockTick(REST, false, 1_000), REST);
	assert.deepEqual(scrollClockTick({ settled: true, pendingAt: null }, false, 1_000), REST);
	assert.deepEqual(scrollClockTick({ settled: false, pendingAt: 1_500 }, false, 1_000), REST);
});

test('already settled, the clock holds — no re-arming while still raw', () => {
	const settled: ScrollClock = { settled: true, pendingAt: null };
	assert.deepEqual(scrollClockTick(settled, true, 1_000), settled);
});

test('a fresh raw=true tick arms a deadline settleMs out, not settled yet', () => {
	assert.deepEqual(scrollClockTick(REST, true, 1_000, 300), { settled: false, pendingAt: 1_300 });
});

test('every qualifying tick before the deadline reschedules it — a trailing debounce', () => {
	// Scrolling continuously re-arms the deadline from the *last* tick, not
	// the first — his "collapse … soon after the scroll happens" reads as
	// "after it stops", not "300ms after it started".
	const armed = scrollClockTick(REST, true, 1_000, 300);
	assert.deepEqual(scrollClockTick(armed, true, 1_100, 300), { settled: false, pendingAt: 1_400 });
	assert.deepEqual(scrollClockTick(armed, true, 1_250, 300), { settled: false, pendingAt: 1_550 });
});

test('the deadline commits once reached, and only then', () => {
	const armed = scrollClockTick(REST, true, 1_000, 300);
	assert.deepEqual(scrollClockTick(armed, true, 1_299, 300), {
		settled: false,
		pendingAt: 1_599
	});
	assert.deepEqual(scrollClockTick(armed, true, 1_300, 300), { settled: true, pendingAt: null });
	assert.deepEqual(scrollClockTick(armed, true, 1_301, 300), { settled: true, pendingAt: null });
});
