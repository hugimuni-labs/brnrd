import assert from 'node:assert/strict';
import test from 'node:test';

import {
	CROSSING_TICKS,
	REACH_TICKS,
	CARRY_TICKS,
	advanceCrossings,
	crossingFrames,
	crossingsFor
} from './roomCrossing.ts';
import type { RoomLayout } from './roomLayout.ts';

// A two-node board: HOME on the left, one actor's place to the right.
const layout = {
	nodes: { home: { x: 0, y: 0 }, 'isl/x': { x: 12, y: 0 } },
	edgeRoutes: {}
} as unknown as RoomLayout;
const places = { r1: 'isl/x' };

function mint(rows: { actorRunId: string; at: string }[], seen = new Set<string>()) {
	return crossingsFor(rows, seen, 'home', places, layout);
}

test('a ceremony is minted from an attested crossing and never from a tick', () => {
	// Movement doctrine, inherited whole from #1652: a change may animate
	// only when canonical input attests it. The input here is one published
	// boundary that carried an injection.
	const seen = new Set<string>();
	const first = mint([{ actorRunId: 'r1', at: '10:00:00Z' }], seen);
	assert.equal(first.length, 1);
	assert.equal(first[0].at, '10:00:00Z');
	assert.ok(first[0].points.length >= 2, 'a delivery has a path, not a position');
});

test('the republished tail mints each ceremony exactly once', () => {
	// The wire publishes a bounded tail (8 rows) and republishes the *same*
	// rows every poll. Without identity on the boundary's own `at`, every
	// poll would re-deliver every letter still in the tail — a claw firing on
	// a 2s clock, which is precisely the fabricated motion the doctrine
	// forbids and precisely what the old sampler looked like.
	const seen = new Set<string>();
	const rows = [
		{ actorRunId: 'r1', at: '10:00:00Z' },
		{ actorRunId: 'r1', at: '10:00:01Z' }
	];
	assert.equal(mint(rows, seen).length, 2);
	assert.equal(mint(rows, seen).length, 0, 'the same tail, again, delivers nothing');
	assert.equal(
		mint([...rows, { actorRunId: 'r1', at: '10:00:02Z' }], seen).length,
		1,
		'and only the genuinely new row rides'
	);
});

test('an unplaceable actor is marked seen rather than owed', () => {
	// Retrying it every tick would mint a ceremony the moment the camera
	// happened to place the actor — motion caused by a layout change instead
	// of by an attested event.
	const seen = new Set<string>();
	assert.equal(mint([{ actorRunId: 'ghost', at: '10:00:00Z' }], seen).length, 0);
	assert.ok(seen.has('ghost@10:00:00Z'));
	assert.equal(mint([{ actorRunId: 'ghost', at: '10:00:00Z' }], seen).length, 0);
});

test('a claw with no source is not a claw', () => {
	assert.deepEqual(
		crossingsFor([{ actorRunId: 'r1', at: 'x' }], new Set(), null, places, layout),
		[]
	);
});

test('the three beats: reach empty-handed, carry outward, settle', () => {
	let live = mint([{ actorRunId: 'r1', at: '10:00:00Z' }]);
	const seenLetters: number[] = [];
	let reachedFull = false;
	let settled = false;

	for (let t = 0; t < CROSSING_TICKS; t++) {
		const [frame] = crossingFrames(live);
		assert.ok(frame, `a frame at tick ${t}`);
		if (t < REACH_TICKS) {
			// The reach carries nothing — the claw is going to fetch, and an
			// empty reach is a real state, not a missing letter.
			assert.equal(frame.letter, null, `reach beat ${t} carries nothing`);
			assert.equal(frame.settling, false);
			if (frame.arm.length === live[0].points.length) reachedFull = true;
		} else if (t < REACH_TICKS + CARRY_TICKS) {
			assert.ok(frame.letter, `carry beat ${t} carries the letter`);
			assert.equal(
				frame.arm.length,
				live[0].points.length,
				'the whole path stays drawn while the letter travels — the direction is the point'
			);
			seenLetters.push(frame.letter!.x);
		} else {
			assert.equal(frame.settling, true, `settle beat ${t}`);
			assert.equal(frame.letter, null, 'delivered');
			settled = true;
		}
		live = advanceCrossings(live);
	}

	assert.ok(reachedFull, 'the arm reaches the actor before anything is carried');
	assert.ok(settled, 'and withdraws after');
	assert.equal(live.length, 0, 'the ceremony ends rather than looping');
	// The letter travels *outward*, from the source to the actor: a delivery
	// with no direction is a blink, and the direction is the whole argument
	// for the claw having a source at all.
	assert.deepEqual(
		[...seenLetters].sort((a, b) => a - b),
		seenLetters,
		'the letter never travels backwards'
	);
	assert.ok(seenLetters[seenLetters.length - 1] > seenLetters[0], 'and it does travel');
});

test('the arm withdraws from the source end, not the actor end', () => {
	// The letter has arrived; what recedes is the reach, back toward HOME.
	// Withdrawing from the far end would read as the message being taken
	// away again.
	let live = mint([{ actorRunId: 'r1', at: '10:00:00Z' }]);
	for (let t = 0; t < REACH_TICKS + CARRY_TICKS; t++) live = advanceCrossings(live);
	const first = crossingFrames(live)[0].arm;
	live = advanceCrossings(live);
	const later = crossingFrames(live)[0].arm;
	assert.ok(later.length < first.length, 'the arm shortens');
	assert.equal(
		later[later.length - 1].x,
		first[first.length - 1].x,
		'the far end — the actor — stays put'
	);
});
