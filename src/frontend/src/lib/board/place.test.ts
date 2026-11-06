import { deepEqual, equal } from 'node:assert/strict';
import { test } from 'node:test';
import { EMPTY, landAt, paneScroll, readPlace } from './place.ts';

test('a place reads back whole, and a torn or foreign one is the empty place', () => {
	const p = {
		bead: 'w-x',
		hist: ['a', 'b'],
		sel: 'l1',
		selCol: 'alpha',
		grown: ['alpha'],
		open: ['c:w-x'],
		x: 350,
		y: { inbox: 'end', alpha: 120 }
	};
	deepEqual(readPlace(JSON.stringify(p)), p);
	deepEqual(readPlace(null), EMPTY);
	deepEqual(readPlace('{"bead":'), EMPTY);
	deepEqual(readPlace('[1,2]'), { ...EMPTY, y: {} });
	// a field of the wrong shape is dropped, not thrown
	deepEqual(readPlace(JSON.stringify({ bead: 1, hist: 'x', x: 'far', y: null })), EMPTY);
	deepEqual(readPlace(JSON.stringify({ hist: ['a', 2, null] })).hist, ['a']);
});

test('a pane within 40 px of its bottom is at the end; anywhere else is a number', () => {
	equal(paneScroll(960, 1200, 240), 'end');
	equal(paneScroll(921, 1200, 240), 'end');
	equal(paneScroll(920, 1200, 240), 920);
	equal(paneScroll(0, 1200, 240), 0);
	equal(paneScroll(12.6, 1200, 240), 13);
});

test('only a number is a place to land: end and nothing both leave the pane at its bottom', () => {
	equal(landAt(120), 120);
	equal(landAt(0), 0);
	equal(landAt('end'), null);
	equal(landAt(undefined), null);
});
