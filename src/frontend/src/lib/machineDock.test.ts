import assert from 'node:assert/strict';
import test from 'node:test';

import {
	RAIL_BOTTOM_PADDING_PX,
	machineDockTop,
	machineHeadFields,
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

test('open, the head keeps identity and drops every measurement the body draws', () => {
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

test('condensed, the dock reclaims the rail\'s own bottom padding', () => {
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
