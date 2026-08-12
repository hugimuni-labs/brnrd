import assert from 'node:assert/strict';
import test from 'node:test';

import {
	isCollapsed,
	scrollClockTick,
	sectionFrameLit,
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

// scrollClockTick — the shared settle clock both the rail and the machine
// dock run through (2026-08-08, corrected 2026-08-11). Expansion is
// immediate; collapse waits `settleMs` past the *first* qualifying tick —
// a leading-edge debounce, not a trailing one.

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

test('every qualifying tick before the deadline leaves it alone — a leading-edge debounce', () => {
	// Corrected 2026-08-11 (his follow-up: the rail stayed full-size for the
	// whole scroll and only collapsed once the reader stopped — "it should
	// collapse even if the scrolling still happens"). Continuous scrolling
	// delivers a tick almost every frame; a trailing debounce that reschedules
	// on each one never actually reaches its deadline until the scroll stops,
	// which is the exact bug reported. The deadline is armed once, off the
	// *first* qualifying tick, and later ticks — same or different `now` —
	// must not move it.
	const armed = scrollClockTick(REST, true, 1_000, 300);
	assert.deepEqual(scrollClockTick(armed, true, 1_100, 300), armed);
	assert.deepEqual(scrollClockTick(armed, true, 1_250, 300), armed);
});

test('the deadline commits once reached, and only then', () => {
	const armed = scrollClockTick(REST, true, 1_000, 300);
	assert.deepEqual(scrollClockTick(armed, true, 1_299, 300), armed);
	assert.deepEqual(scrollClockTick(armed, true, 1_300, 300), { settled: true, pendingAt: null });
	assert.deepEqual(scrollClockTick(armed, true, 1_301, 300), { settled: true, pendingAt: null });
});

// sectionFrameLit — the section-under-the-reader frame, border and label as
// one state (his 2026-08-11 report: the border used to read `activeSection`
// alone and could outlive a state-driven un-collapse the label's own
// `stackCollapsed` gate already survived). Pinned per his ask: the lit set
// over a page's tracked headings is empty whenever the stack isn't
// collapsed, and never holds more than one id.

const HEADINGS = ['warp-heading', 'cloth-heading', 'corpus-heading', 'billing-heading'];

function litSet(stackCollapsed: boolean, activeSectionId: string | null): string[] {
	return HEADINGS.filter((id) => sectionFrameLit(stackCollapsed, activeSectionId, id));
}

test('the stack not collapsed: no heading is ever lit, whatever activeSection claims', () => {
	for (const activeSectionId of [null, 'warp-heading', 'cloth-heading', 'nonexistent-id']) {
		assert.deepEqual(litSet(false, activeSectionId), []);
	}
});

test('collapsed with nothing tracked: still nothing lit', () => {
	assert.deepEqual(litSet(true, null), []);
});

test('collapsed with a tracked heading: exactly that one heading lights, never a second', () => {
	for (const id of HEADINGS) {
		const lit = litSet(true, id);
		assert.deepEqual(lit, [id]);
		assert.ok(lit.length <= 1);
	}
});

test('the lit set never exceeds size 1, across every stack/activeSection combination', () => {
	for (const stackCollapsed of [false, true]) {
		for (const activeSectionId of [null, ...HEADINGS]) {
			assert.ok(litSet(stackCollapsed, activeSectionId).length <= 1);
		}
	}
});
