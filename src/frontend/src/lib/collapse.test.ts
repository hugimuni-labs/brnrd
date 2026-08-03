import assert from 'node:assert/strict';
import test from 'node:test';

import { isCollapsed, tapVerdict } from './collapse.ts';

// isCollapsed — the rule both the rail and the machine answer to: a reader's
// own `open` (and `pinnedOpen`) always outrank the scroll verdict.

test('at rest (not scrolled past) a block is never collapsed, whatever the reader opened', () => {
	for (const pinnedOpen of [false, true]) {
		for (const open of [false, true]) {
			assert.equal(isCollapsed({ open, pinnedOpen, scrolledPast: false }), false);
		}
	}
});

test('scrolled past, an untouched block collapses to its compact form', () => {
	assert.equal(isCollapsed({ open: false, pinnedOpen: false, scrolledPast: true }), true);
});

test('scrolled past, the reader opening it survives the scroll verdict', () => {
	assert.equal(isCollapsed({ open: true, pinnedOpen: false, scrolledPast: true }), false);
});

test('scrolled past, pinning it open survives the scroll verdict too', () => {
	assert.equal(isCollapsed({ open: false, pinnedOpen: true, scrolledPast: true }), false);
});

// tapVerdict — a tap on a block whose head may be scrolled away from its body.

test('at rest a tap is an ordinary disclosure toggle and never travels', () => {
	assert.deepEqual(tapVerdict(false, false), { open: true, travel: false });
	assert.deepEqual(tapVerdict(true, false), { open: false, travel: false });
});

test('scrolled past and shut, a tap opens the block and travels to it', () => {
	assert.deepEqual(tapVerdict(false, true), { open: true, travel: true });
});

test('scrolled past and open, a tap only travels — it never folds a body it cannot see', () => {
	assert.deepEqual(tapVerdict(true, true), { open: null, travel: true });
});

test('no tap taken past the scroll verdict can ever close what the reader opened', () => {
	for (const open of [true, false]) {
		assert.notEqual(tapVerdict(open, true).open, false);
	}
});
