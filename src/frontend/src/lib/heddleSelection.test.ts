import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { toggleHeddleSelection } from './heddleSelection.ts';

const ALL = ['loom', 'post', 'mail'];

describe('toggleHeddleSelection', () => {
	it('all-lit → press makes the pressed topic the sole active filter', () => {
		const next = toggleHeddleSelection(null, 'post', ALL);
		assert.deepEqual([...(next ?? [])], ['post']);
	});

	it('filtered → pressing an unlit topic adds it (plain membership)', () => {
		const next = toggleHeddleSelection(new Set(['post']), 'mail', ALL);
		assert.deepEqual([...(next ?? [])].sort(), ['mail', 'post']);
	});

	it('filtered → pressing a lit topic removes it', () => {
		const next = toggleHeddleSelection(new Set(['post', 'mail']), 'mail', ALL);
		assert.deepEqual([...(next ?? [])], ['post']);
	});

	it('removing the last lit topic returns to all-lit (null), never an empty filter', () => {
		const next = toggleHeddleSelection(new Set(['post']), 'post', ALL);
		assert.equal(next, null);
	});

	it('relighting every topic by hand collapses back to all-lit (null)', () => {
		const next = toggleHeddleSelection(new Set(['post', 'mail']), 'loom', ALL);
		assert.equal(next, null);
	});

	it('is pure — never mutates the selection it was handed', () => {
		const original = new Set(['post']);
		toggleHeddleSelection(original, 'mail', ALL);
		assert.deepEqual([...original], ['post']);
	});
});
