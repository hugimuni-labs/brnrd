import assert from 'node:assert/strict';
import test from 'node:test';

import { crossingCells } from './crossing.ts';
import { runFace } from './runFace.ts';

const THREADS = ['loom', 'post', 'clockwork'];

test('a strip lights the crossed threads and carries the topic hue', () => {
	const cells = crossingCells(THREADS, ['post']);
	assert.equal(cells.length, 3);
	assert.deepEqual(
		cells.map((cell) => cell.lit),
		[false, true, false]
	);
	// The hue is hashed from the canonical topic id — the same face the
	// heddle rail introduces — never from position: retiring one topic must
	// not reshuffle every other thread's color.
	assert.equal(cells[1].color, runFace('post').color);
});

test('an unwelded run draws nothing at all — not a strip of dark ticks', () => {
	assert.deepEqual(crossingCells(THREADS, []), []);
	assert.deepEqual(crossingCells(THREADS, undefined), []);
});

test('no threads, no strip', () => {
	assert.deepEqual(crossingCells([], ['loom']), []);
});
