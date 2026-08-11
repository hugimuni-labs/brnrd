import assert from 'node:assert/strict';
import test from 'node:test';

import { openReducer } from './warpGraphOpen.ts';

test('toggle opens a closed item alone', () => {
	const next = openReducer(new Set(), { type: 'toggle', id: 'w-1' });
	assert.deepEqual([...next], ['w-1']);
});

test('toggle on the sole open item closes it', () => {
	const next = openReducer(new Set(['w-1']), { type: 'toggle', id: 'w-1' });
	assert.deepEqual([...next], []);
});

test('toggle on any other item collapses to that item alone — even with several open', () => {
	const next = openReducer(new Set(['w-1', 'w-2', 'w-3']), { type: 'toggle', id: 'w-2' });
	assert.deepEqual([...next], ['w-2']);
});

test('toggle on a closed item while others are open still collapses to it alone', () => {
	const next = openReducer(new Set(['w-1']), { type: 'toggle', id: 'w-9' });
	assert.deepEqual([...next], ['w-9']);
});

test('follow adds the target and keeps the source (and anything else) open', () => {
	const next = openReducer(new Set(['w-1']), { type: 'follow', id: 'w-2' });
	assert.deepEqual([...next].sort(), ['w-1', 'w-2']);
});

test('follow from nothing open is the same as opening just the target', () => {
	const next = openReducer(new Set(), { type: 'follow', id: 'w-2' });
	assert.deepEqual([...next], ['w-2']);
});

test('follow onto an already-open target is a no-op (same identity, no needless render)', () => {
	const current = new Set(['w-1', 'w-2']);
	const next = openReducer(current, { type: 'follow', id: 'w-2' });
	assert.equal(next, current);
});

test('toggle never mutates the set passed in', () => {
	const current = new Set(['w-1']);
	openReducer(current, { type: 'toggle', id: 'w-9' });
	assert.deepEqual([...current], ['w-1']);
});

test('follow never mutates the set passed in', () => {
	const current = new Set(['w-1']);
	openReducer(current, { type: 'follow', id: 'w-2' });
	assert.deepEqual([...current], ['w-1']);
});
