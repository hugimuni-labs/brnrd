import assert from 'node:assert/strict';
import test from 'node:test';

import {
	DOCK_SLACK_PX,
	activeSectionFrom,
	initialStackClocks,
	limbDockVerdict,
	railRawVerdict,
	stackAtRest,
	stackReserve,
	stepStackClocks,
	type StackRaws
} from './stickyStack.ts';
import { SCROLL_SETTLE_MS } from './collapse.ts';

// railRawVerdict — THE BOUNDARY THAT FLICKERED's dead band, as a sentinel
// pair: condensing and un-condensing are different boundaries, and between
// them the verdict holds whatever it last said. The old `railScrollVerdict`
// tests carry over here in sentinel vocabulary.

test('the rail does not condense while any of the full stack is still on screen', () => {
	assert.equal(
		railRawVerdict({ condenseAbove: false, releaseAbove: true, condensed: false }),
		false
	);
});

test('the rail condenses once the condense sentinel has scrolled past the top', () => {
	assert.equal(railRawVerdict({ condenseAbove: true, releaseAbove: true, condensed: false }), true);
});

test('a condensed rail stays condensed through the dead band — no flicker on the way up', () => {
	// The condense sentinel is back on screen (the stack shrank under it),
	// but the release sentinel has not returned: hold.
	assert.equal(railRawVerdict({ condenseAbove: false, releaseAbove: true, condensed: true }), true);
});

test('the rail un-condenses only when its own top returns', () => {
	assert.equal(
		railRawVerdict({ condenseAbove: false, releaseAbove: false, condensed: true }),
		false
	);
});

// limbDockVerdict — the old `machineDockVerdict` dead band, with the
// boundary now the stack's measured live bottom instead of a computed
// offset. Same geometry, same tests.

test('a limb docks once its home has gone a slack past the stack bottom', () => {
	assert.equal(limbDockVerdict({ homeTop: 200, stackBottom: 44, docked: false }), false);
	assert.equal(limbDockVerdict({ homeTop: 44, stackBottom: 44, docked: false }), false);
	assert.equal(limbDockVerdict({ homeTop: -400, stackBottom: 44, docked: false }), true);
});

test('the verdict holds a dead band — inside it, the last answer stands', () => {
	const inside = { homeTop: 44 - DOCK_SLACK_PX / 2, stackBottom: 44 };
	assert.equal(limbDockVerdict({ ...inside, docked: false }), false);
	assert.equal(limbDockVerdict({ ...inside, docked: true }), true);
});

test('an unmeasured geometry never claims to be docked', () => {
	assert.equal(limbDockVerdict({ homeTop: Number.NaN, stackBottom: 44, docked: false }), false);
	assert.equal(limbDockVerdict({ homeTop: 0, stackBottom: Number.NaN, docked: true }), false);
});

// stepStackClocks — the single debounce: three clocks, one `now`, one
// earliest deadline; the rack gate forces every raw false while open.

const ALL_RAW: StackRaws = { rail: true, heddle: true, lane: true };
const NO_RAW: StackRaws = { rail: false, heddle: false, lane: false };

test('one qualifying step arms every limb on the same deadline', () => {
	const step = stepStackClocks(initialStackClocks(), ALL_RAW, false, 1_000);
	assert.equal(step.changed, true);
	assert.equal(step.nextDeadline, 1_000 + SCROLL_SETTLE_MS);
	assert.equal(step.clocks.rail.settled, false);
	assert.equal(step.clocks.rail.pendingAt, 1_000 + SCROLL_SETTLE_MS);
});

test('the deadline commits all pending limbs at once — one schedule, never three', () => {
	const armed = stepStackClocks(initialStackClocks(), ALL_RAW, false, 1_000).clocks;
	const done = stepStackClocks(armed, ALL_RAW, false, 1_000 + SCROLL_SETTLE_MS);
	assert.equal(done.clocks.rail.settled, true);
	assert.equal(done.clocks.heddle.settled, true);
	assert.equal(done.clocks.lane.settled, true);
	assert.equal(done.nextDeadline, null);
});

test('an open rack un-docks everything immediately — never debounced (#1328)', () => {
	const armed = stepStackClocks(initialStackClocks(), ALL_RAW, false, 1_000).clocks;
	const settled = stepStackClocks(armed, ALL_RAW, false, 1_000 + SCROLL_SETTLE_MS).clocks;
	const opened = stepStackClocks(settled, ALL_RAW, true, 1_100);
	assert.equal(opened.clocks.rail.settled, false);
	assert.equal(opened.clocks.heddle.settled, false);
	assert.equal(opened.clocks.lane.settled, false);
	assert.equal(opened.nextDeadline, null);
});

test('raw=false clears a pending collapse before it commits', () => {
	const armed = stepStackClocks(initialStackClocks(), ALL_RAW, false, 1_000).clocks;
	const cleared = stepStackClocks(armed, NO_RAW, false, 1_020);
	assert.equal(cleared.clocks.rail.pendingAt, null);
	assert.equal(cleared.nextDeadline, null);
});

test('an unchanged step returns the same clocks object — reference identity for $state', () => {
	const start = initialStackClocks();
	const step = stepStackClocks(start, NO_RAW, false, 1_000);
	assert.equal(step.changed, false);
	assert.equal(step.clocks, start);
});

test('limbs settle independently — the earliest pending deadline wins', () => {
	const first = stepStackClocks(
		initialStackClocks(),
		{ rail: true, heddle: false, lane: false },
		false,
		1_000
	).clocks;
	const second = stepStackClocks(first, { rail: true, heddle: true, lane: false }, false, 1_020);
	assert.equal(second.clocks.rail.pendingAt, 1_000 + SCROLL_SETTLE_MS);
	assert.equal(second.clocks.heddle.pendingAt, 1_020 + SCROLL_SETTLE_MS);
	assert.equal(second.nextDeadline, 1_000 + SCROLL_SETTLE_MS);
});

// activeSectionFrom — the scroll-spy winner: last heading above the stack
// bottom, document order, exactly one or none.

const HEADINGS = (above: boolean[]) =>
	['warp-heading', 'cloth-heading', 'corpus-heading', 'billing-heading'].map((id, i) => ({
		id,
		label: id.replace('-heading', ''),
		above: above[i]
	}));

test('no heading passed: the bar says nothing', () => {
	assert.equal(activeSectionFrom(HEADINGS([false, false, false, false])), null);
});

test('the last passed heading wins, in document order', () => {
	assert.equal(activeSectionFrom(HEADINGS([true, false, false, false]))?.id, 'warp-heading');
	assert.equal(activeSectionFrom(HEADINGS([true, true, false, false]))?.id, 'cloth-heading');
	assert.equal(activeSectionFrom(HEADINGS([true, true, true, true]))?.id, 'billing-heading');
});

// stackReserve — the one surviving spacer: rest − live, clamped, whole
// pixels, and never a number when the inputs are not.

test('the spacer holds exactly the missing height, and only ever a positive one', () => {
	assert.equal(stackReserve(204, 140), 64);
	assert.equal(stackReserve(204, 204), 0);
	assert.equal(stackReserve(140, 204), 0);
	assert.equal(stackReserve(Number.NaN, 100), 0);
	assert.equal(stackReserve(100, Number.NaN), 0);
});

// stackAtRest — the only moment the rest-height sample may be taken.

test('any active form — condensed, open, docked — disqualifies the sample', () => {
	const rest = { railOpen: false, railCondensed: false, heddleDocked: false, machineDocked: false };
	assert.equal(stackAtRest(rest), true);
	assert.equal(stackAtRest({ ...rest, railOpen: true }), false);
	assert.equal(stackAtRest({ ...rest, railCondensed: true }), false);
	assert.equal(stackAtRest({ ...rest, heddleDocked: true }), false);
	assert.equal(stackAtRest({ ...rest, machineDocked: true }), false);
});
