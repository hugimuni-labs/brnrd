import assert from 'node:assert/strict';
import test from 'node:test';

import { liveRunDisplayName } from './liveRuns.ts';

test('live run display prefers the resident-authored name', () => {
	assert.equal(liveRunDisplayName({ name: 'run naming', label: 'waking message', kind: 'daemon' }), 'run naming');
});

test('live run display falls back to the waking-message excerpt', () => {
	assert.equal(liveRunDisplayName({ name: '', label: 'waking message', kind: 'daemon' }), 'waking message');
});
