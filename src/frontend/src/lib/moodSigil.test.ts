// The carving's invariants: deterministic, mirror-symmetric where the face
// demands it, distinct across moods, and never empty — a stone that carves
// nothing for a real mood is a blank being wearing a filed one.
import { test } from 'node:test';
import { deepEqual, equal, notDeepEqual, ok } from 'node:assert/strict';

import { moodSigil, parseFaceCore, SIGIL_COLS, SIGIL_ROWS } from './moodSigil.ts';

test('the emote grammar parses: skull stripped, eye·mouth·eye', () => {
	deepEqual(parseFaceCore('b·_·d'), { left: '·', mouth: '_', right: '·' });
	deepEqual(parseFaceCore('bo_od'), { left: 'o', mouth: '_', right: 'o' });
	deepEqual(parseFaceCore('bˋoˊd'), { left: 'ˋ', mouth: 'o', right: 'ˊ' });
	equal(parseFaceCore('??'), null);
	// A bare 3-char core without the skull still parses.
	deepEqual(parseFaceCore('o_o'), { left: 'o', mouth: '_', right: 'o' });
});

test('same frame, same carving — determinism is the vocabulary', () => {
	deepEqual(moodSigil('b·_·d'), moodSigil('b·_·d'));
});

test('different moods carve different stones', () => {
	notDeepEqual(moodSigil('b·_·d'), moodSigil('bo_od'));
	notDeepEqual(moodSigil('bˋ_ˊd'), moodSigil('b-_-d'));
	// Even two frames sharing a face core differ via the lattice hash? No —
	// same text is same carving; the lattice hashes the frame text itself,
	// so equal text must carve equal stone. Pin the converse instead: an
	// unknown mood still gets a distinct lattice from its own text.
	notDeepEqual(moodSigil('zzz-unknown-1'), moodSigil('zzz-unknown-2'));
});

test('the carving has the invader symmetry where symmetry means face', () => {
	const grid = moodSigil('bo_od');
	equal(grid.length, SIGIL_ROWS);
	for (const row of grid) equal(row.length, SIGIL_COLS);
	// Lattice rows (0, 6, 7) are mirrored by construction.
	for (const r of [0, 6, 7]) {
		for (let c = 0; c < SIGIL_COLS; c++) {
			equal(grid[r][c], grid[r][SIGIL_COLS - 1 - c], `lattice row ${r} mirrors`);
		}
	}
	// Symmetric eyes mirror across the face.
	for (const r of [1, 2]) {
		for (let c = 0; c < SIGIL_COLS; c++) {
			equal(grid[r][c], grid[r][SIGIL_COLS - 1 - c], `eye row ${r} mirrors`);
		}
	}
});

test('every mood frame carves something — a stone is never blank', () => {
	for (const frame of ['b·_·d', 'bo_od', 'bˋ_ˊd', 'b^w^d', 'bx_xd', 'total-stranger']) {
		const lit = moodSigil(frame).flat().filter(Boolean).length;
		ok(lit >= 4, `${frame} carves ${lit} cells`);
	}
});
