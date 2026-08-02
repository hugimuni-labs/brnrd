import assert from 'node:assert/strict';
import test from 'node:test';

import {
	MACHINE_DOCK_SLACK_PX,
	RAIL_BOTTOM_PADDING_PX,
	machineBodyOnScreen,
	machineDockTop,
	machineDockVerdict,
	machineHeadFields,
	machineTapVerdict,
	railKeepsLivePick
} from './machineDock.ts';

test('parked, the head carries everything — it is the only line there is', () => {
	assert.deepEqual(machineHeadFields(false), {
		lead: true,
		clock: true,
		note: true,
		extra: true,
		armedTail: true
	});
});

test('with the lane on screen, the head drops every measurement the body draws', () => {
	// His 2026-08-02 read: "when it is expanded, it shouldn't repeat the both
	// collapsed and semi-expanded shape". The lane one line below renders the
	// lead run with its own clock, the further strands as their own rows, and
	// each armed pick with its own countdown.
	const fields = machineHeadFields(true);
	assert.equal(fields.lead, true);
	assert.equal(fields.clock, false);
	assert.equal(fields.note, false);
	assert.equal(fields.extra, false);
	assert.equal(fields.armedTail, false);
});

test('identity survives both states — the dock must say which run it is', () => {
	assert.equal(machineHeadFields(true).lead, machineHeadFields(false).lead);
});

// THE DOCK THAT TAPPED WRONG (2026-08-03). One predicate was true for two
// reasons: `open` meant both "the reader opened this" and "its body is on
// screen". Docked, the second is false while the first is true, and every
// renderer that read `open` answered the wrong question.

test('the body is on screen only when the reader opened it AND the head is not docked', () => {
	assert.equal(machineBodyOnScreen(true, false), true);
	assert.equal(machineBodyOnScreen(true, true), false);
	assert.equal(machineBodyOnScreen(false, false), false);
	assert.equal(machineBodyOnScreen(false, true), false);
});

test('docked, the head reads exactly as it does parked whether or not it is open', () => {
	// His report: "when the machine block is scrolled up it is not collapsed,
	// so pressing it the first time doesn't expand it." The docked line of an
	// *open* block used to say less than the parked line — identity only, with
	// its clock and armed tail suppressed to avoid repeating a lane that was
	// screens above and repeating nothing. Same line either way now, which is
	// also what makes it honest that a docked tap does the same thing either
	// way.
	const dockedOpen = machineHeadFields(machineBodyOnScreen(true, true));
	const dockedShut = machineHeadFields(machineBodyOnScreen(false, true));
	assert.deepEqual(dockedOpen, dockedShut);
	assert.deepEqual(dockedOpen, machineHeadFields(false));
});

test('at rest the head still folds away what the lane one line below draws', () => {
	// The original rule survives untouched where it was ever true.
	assert.deepEqual(machineHeadFields(machineBodyOnScreen(true, false)), machineHeadFields(true));
});

test('at rest the tap is a disclosure and toggles, and never moves the reader', () => {
	assert.deepEqual(machineTapVerdict(false, false), { open: true, travel: false });
	assert.deepEqual(machineTapVerdict(true, false), { open: false, travel: false });
});

test('docked and shut, the tap opens the block and takes the reader to it', () => {
	assert.deepEqual(machineTapVerdict(false, true), { open: true, travel: true });
});

test('docked and open, the tap only travels — it touches nothing', () => {
	// The bug, in one assertion. This tap used to fold: the lane vanished from
	// the document at the block's home *above* the reader, and everything below
	// rose by one lane's height. His "the menu hits scrolled randomly a bit" —
	// not random, exactly one lane tall.
	assert.deepEqual(machineTapVerdict(true, true), { open: null, travel: true });
});

test('no tap taken from a docked head can ever close what the reader opened', () => {
	// THE PICKER YOU CANNOT REACH (#1011), held one step tighter than before:
	// not only does no scroll position change `open`, no tap taken *from* a
	// scroll position closes it either. The reader folds with the lane in front
	// of them or not at all.
	for (const open of [true, false]) {
		assert.notEqual(machineTapVerdict(open, true).open, false);
	}
});

test('a fold always happens with the lane on screen', () => {
	// The general rule, stated as the invariant it is: the only verdict that
	// closes the body is one taken while the body is visible.
	for (const open of [true, false]) {
		for (const docked of [true, false]) {
			if (machineTapVerdict(open, docked).open === false) {
				assert.equal(machineBodyOnScreen(open, docked), true);
			}
		}
	}
});

test('the head docks once its home has gone a line past the parking spot', () => {
	assert.equal(machineDockVerdict({ home: 200, dockTop: 44, docked: false }), false);
	assert.equal(machineDockVerdict({ home: 44, dockTop: 44, docked: false }), false);
	assert.equal(machineDockVerdict({ home: -400, dockTop: 44, docked: false }), true);
});

test('the verdict holds a dead band — THE BOUNDARY THAT FLICKERED, same rule', () => {
	// A form change earns a dead band at least as tall as the form change
	// itself, and this one swaps the head between its pointer and disclosure
	// forms. Inside the band the verdict keeps its last answer, so a slow
	// touchpad sitting on the boundary has nothing to toggle.
	const inside = { home: 44 - MACHINE_DOCK_SLACK_PX / 2, dockTop: 44 };
	assert.equal(machineDockVerdict({ ...inside, docked: false }), false);
	assert.equal(machineDockVerdict({ ...inside, docked: true }), true);
});

test('travel terminates: landing at the block releases the dock', () => {
	// The trip scrolls the *sentinel's top* to `railHeight` from the viewport
	// top, which puts the block's home one seam (24px) below that, while the
	// head parks `RAIL_BOTTOM_PADDING_PX` above `railHeight` for as long as the
	// rail stays condensed — and it does, since it only un-condenses back at the
	// page's own top. So the reader lands clear of the parking spot, undocked,
	// and the next tap is an ordinary fold rather than a second trip to where
	// they already are.
	const railHeight = 44;
	const seam = 24;
	assert.equal(
		machineDockVerdict({
			home: railHeight + seam,
			dockTop: machineDockTop(railHeight, true),
			docked: true
		}),
		false
	);
});

test('an unmeasured geometry never claims to be docked', () => {
	// First paint, before either measurement binds. Guessing "docked" would
	// make the very first tap on a page at rest a scroll instead of an expand.
	assert.equal(machineDockVerdict({ home: Number.NaN, dockTop: 44, docked: false }), false);
	assert.equal(machineDockVerdict({ home: 0, dockTop: Number.NaN, docked: true }), false);
});

test('the dock sits flush under the rail — no gap, his "like a magnet"', () => {
	assert.equal(machineDockTop(132), 132);
});

test('the dock follows the rail as it condenses rather than pinning a constant', () => {
	assert.ok(machineDockTop(132) > machineDockTop(44));
});

test('an unmeasured rail docks at the top rather than floating off it', () => {
	// First paint, before `clientHeight` binds. Guessing high hides the head
	// behind the rail, which reads as the block having vanished — the exact
	// complaint this change answers.
	assert.equal(machineDockTop(null), 0);
	assert.equal(machineDockTop(undefined), 0);
	assert.equal(machineDockTop(Number.NaN), 0);
	assert.equal(machineDockTop(-40), 0);
});

test('the offset is whole pixels — a fractional sticky top seams against the rail', () => {
	assert.equal(machineDockTop(131.6), 132);
});

test("condensed, the dock reclaims the rail's own bottom padding", () => {
	// His read: "could we remove the space between them, almost at least, when
	// they are collapsed and on the top?" The space is not a design choice — it
	// is the rail's `pb-2`. Derived from the thing it cancels, so it stays
	// correct if that padding changes; a nudged constant would silently stop
	// matching.
	assert.equal(machineDockTop(132, true), 132 - RAIL_BOTTOM_PADDING_PX);
});

test('at rest the spacing stays — two blocks in a page, not one instrument', () => {
	assert.equal(machineDockTop(132, false), 132);
	assert.equal(machineDockTop(132), 132);
});

test('the overlap can never push the dock above the viewport', () => {
	// A rail shorter than its own padding is not a real layout, but a first
	// paint with a partial measurement is — and a negative sticky top hides the
	// head off-screen, which reads as the block having vanished.
	assert.equal(machineDockTop(4, true), 0);
	assert.equal(machineDockTop(0, true), 0);
});

test('the rail drops its live-pick row once the machine docks beneath it', () => {
	// One fact, one surface. Printing both puts the same run's name at two
	// y-positions eight pixels apart, which is the objection his own
	// correction removed: "not the collapsed rack + oneline main runner info,
	// as it is now, but a collapsed fuel + collapsed oneline machine stuck to
	// it."
	assert.equal(railKeepsLivePick(true), false);
});

test('a rail with no machine beneath it keeps the row', () => {
	assert.equal(railKeepsLivePick(false), true);
});
