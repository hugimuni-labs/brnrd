import assert from 'node:assert/strict';
import test from 'node:test';
import { MAX_COLS, MIN_COLS, colsForWidth } from './asciiFieldCols.ts';

test('clamps to the floor when the box is narrower than the floor can show', () => {
	// 100px / 7.2px-per-char ≈ 13 cols — below MIN_COLS, so the floor wins.
	assert.equal(colsForWidth(100, 7.2), MIN_COLS);
});

test('clamps to the ceiling when the box is far wider than any camera needs', () => {
	// 4000px / 5px-per-char = 800 cols — above MAX_COLS, so the ceiling wins.
	assert.equal(colsForWidth(4000, 5), MAX_COLS);
});

test('passes an in-range answer through unchanged (floored, not rounded)', () => {
	// 358px / 7.2px-per-char = 49.72 — floors to 49, not 50.
	assert.equal(colsForWidth(358, 7.2), 49);
});

test('the regression this guards: a real 390px phone box never gets forced wider than it is', () => {
	// Measured live (repro/drive-fits.mjs, mocked fixtures) at 390px:
	// /ascii's own page ≈ 358px of board, /daily's narrower .field-frame
	// ≈ 308px — both at the ~7.2px char width AsciiField.svelte measures.
	// The old MIN_COLS (64) forced 64 cols (≈461px) into both; either
	// number here landing below the true avail/charWidth answer would mean
	// the floor is back to overriding a box that can't hold it.
	const asciiCols = colsForWidth(358, 7.2);
	const dailyCols = colsForWidth(308, 7.2);
	assert.ok(asciiCols * 7.2 <= 358, `/ascii: ${asciiCols} cols must fit in 358px`);
	assert.ok(dailyCols * 7.2 <= 308, `/daily: ${dailyCols} cols must fit in 308px`);
});

test('a degenerate measurement (zero/negative/NaN) falls back to the floor, not a crash', () => {
	assert.equal(colsForWidth(0, 7.2), MIN_COLS);
	assert.equal(colsForWidth(-10, 7.2), MIN_COLS);
	assert.equal(colsForWidth(358, 0), MIN_COLS);
	assert.equal(colsForWidth(NaN, 7.2), MIN_COLS);
});

test('a custom floor/ceiling pair overrides the module defaults', () => {
	assert.equal(colsForWidth(50, 7.2, 10, 20), 10);
	assert.equal(colsForWidth(5000, 7.2, 10, 20), 20);
});
