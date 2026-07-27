import assert from 'node:assert/strict';
import test from 'node:test';

import { withheldCopy, type WithheldLane } from './withheld.ts';

test('unrecorded consent explains that legacy repos were never asked', () => {
	const withheld: WithheldLane = {
		lane: 'activity',
		unrecorded: ['hugimuni-labs/brnrd'],
		opted_out: []
	};
	assert.equal(
		withheldCopy(withheld),
		'paused — these repos were connected before the publish consent existed and have never been asked: hugimuni-labs/brnrd'
	);
});

test('an explicit none is described as the choice it was', () => {
	const withheld: WithheldLane = {
		lane: 'live_runs',
		unrecorded: [],
		opted_out: ['hugimuni-labs/brnrd']
	};
	assert.equal(
		withheldCopy(withheld),
		"off — you set this repo's publish scope to nothing: hugimuni-labs/brnrd"
	);
});

test('mixed causes stay distinct in one line', () => {
	const withheld: WithheldLane = {
		lane: 'quota',
		unrecorded: ['org/legacy'],
		opted_out: ['org/off-a', 'org/off-b']
	};
	assert.equal(
		withheldCopy(withheld),
		"paused — these repos were connected before the publish consent existed and have never been asked: org/legacy · off — you set these repos' publish scope to nothing: org/off-a, org/off-b"
	);
});

test('a recorded subset that excludes the lane still gets an honest explanation', () => {
	const withheld: WithheldLane = {
		lane: 'quota',
		unrecorded: [],
		opted_out: []
	};
	assert.equal(
		withheldCopy(withheld),
		'off — no connected repo includes this lane in its publish scope'
	);
});
