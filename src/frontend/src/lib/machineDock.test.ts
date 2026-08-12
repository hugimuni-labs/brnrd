import assert from 'node:assert/strict';
import test from 'node:test';

import {
	machineBodyOnScreen,
	machineHeadFields,
	machineHeadRun,
	machineTapVerdict,
	railKeepsLivePick
} from './machineDock.ts';
import { tapVerdict } from './collapse.ts';

test('parked, the head carries everything — it is the only line there is', () => {
	assert.deepEqual(machineHeadFields(false), {
		lead: true,
		clock: true,
		note: true,
		extra: true,
		armedTail: true,
		mood: true
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
	assert.equal(fields.mood, false);
});

test('the mood chip is a measurement, not identity — it drops exactly when the clock does', () => {
	// His 2026-08-05 read: the head's mood glyph repeats the run card's own
	// `MoodChip` once the lane (or a selected run's node) is on screen. A
	// feeling turns between beats the same way a clock ticks, so it is
	// suppressed on the same predicate as `clock`, never on `lead`'s.
	assert.equal(machineHeadFields(true).mood, machineHeadFields(true).clock);
	assert.equal(machineHeadFields(false).mood, machineHeadFields(false).clock);
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

// `machineTapVerdict` is a thin wrapper over the shared `collapse.tapVerdict`
// (2026-08-03, the rack answers everywhere) — pin the delegation itself, not
// just the observed shape, so a future edit that re-derives this locally
// instead of delegating shows up as a diff.
test("machineTapVerdict is exactly tapVerdict under the machine's own vocabulary", () => {
	for (const open of [false, true]) {
		for (const docked of [false, true]) {
			assert.deepEqual(machineTapVerdict(open, docked), tapVerdict(open, docked));
		}
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

// One frame, two moods: the machine block borrows the selection.

test('nothing selected — pulse — the head wears the lead, exactly as before', () => {
	assert.equal(machineHeadRun('run-a', null), 'run-a');
	assert.equal(machineHeadRun(null, null), null);
});

test('a run selected anywhere on the page — inspection — the head wears it, not the lead', () => {
	assert.equal(machineHeadRun('run-a', 'run-b'), 'run-b');
});

test('selecting the lead itself is not a distinct mood — same id either way', () => {
	assert.equal(machineHeadRun('run-a', 'run-a'), 'run-a');
});

test('a selection outlives the lead — nothing burning, a run still picked from elsewhere', () => {
	// Not reachable from a live run today (`selectFromLoom`'s two call sites
	// both name a live id), but the ranking holds regardless: a selection
	// this file is handed always outranks an absent lead.
	assert.equal(machineHeadRun(null, 'run-b'), 'run-b');
});

test('deselect falls straight back to pulse — no memory of the last pick', () => {
	assert.equal(machineHeadRun('run-a', null), machineHeadRun('run-a', null));
});
