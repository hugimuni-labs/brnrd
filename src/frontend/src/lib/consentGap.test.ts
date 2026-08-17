import assert from 'node:assert/strict';
import test from 'node:test';

import { consentGapRepos, laneForWithheld, laneShareClause } from './consentGap.ts';
import { PUBLISH_LANES } from './publishScope.ts';
import type { WithheldLane } from './withheld.ts';

test('zips unrecorded names against their ids, in order', () => {
	const withheld: WithheldLane = {
		lane: 'corpus',
		unrecorded: ['Gurio/BeCenter', 'Gurio/other-repo'],
		unrecorded_ids: ['repo-1', 'repo-2']
	};
	assert.deepEqual(consentGapRepos(withheld), [
		{ id: 'repo-1', name: 'Gurio/BeCenter', reason: 'unrecorded' },
		{ id: 'repo-2', name: 'Gurio/other-repo', reason: 'unrecorded' }
	]);
});

test('zips opted-out names against their ids, tagged distinctly from unrecorded', () => {
	const withheld: WithheldLane = {
		lane: 'corpus',
		opted_out: ['Gurio/off'],
		opted_out_ids: ['repo-9']
	};
	assert.deepEqual(consentGapRepos(withheld), [
		{ id: 'repo-9', name: 'Gurio/off', reason: 'opted_out' }
	]);
});

test('both lists present: unrecorded rows first, then opted-out', () => {
	const withheld: WithheldLane = {
		lane: 'corpus',
		unrecorded: ['Gurio/legacy'],
		unrecorded_ids: ['repo-1'],
		opted_out: ['Gurio/off'],
		opted_out_ids: ['repo-2']
	};
	assert.deepEqual(consentGapRepos(withheld), [
		{ id: 'repo-1', name: 'Gurio/legacy', reason: 'unrecorded' },
		{ id: 'repo-2', name: 'Gurio/off', reason: 'opted_out' }
	]);
});

test('a name with no id twin is dropped, not guessed', () => {
	const withheld: WithheldLane = {
		lane: 'corpus',
		unrecorded: ['Gurio/legacy'],
		unrecorded_ids: []
	};
	assert.deepEqual(consentGapRepos(withheld), []);
});

test('no lists at all yields no rows', () => {
	assert.deepEqual(consentGapRepos({ lane: 'corpus' }), []);
});

test('laneForWithheld resolves the matching PUBLISH_LANES entry', () => {
	const lane = laneForWithheld({ lane: 'corpus' });
	assert.equal(
		lane,
		PUBLISH_LANES.find((l) => l.value === 'corpus')
	);
});

test('laneForWithheld returns null for an unknown lane token', () => {
	assert.equal(laneForWithheld({ lane: 'not-a-real-lane' }), null);
});

test('laneShareClause splits the label after the em dash', () => {
	const corpus = PUBLISH_LANES.find((l) => l.value === 'corpus')!;
	assert.equal(laneShareClause(corpus), 'authored pages, kb, run bodies');
});

test('laneShareClause falls back to the whole label when there is no dash', () => {
	assert.equal(laneShareClause({ value: 'x', label: 'no dash here' }), 'no dash here');
});
