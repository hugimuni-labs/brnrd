import assert from 'node:assert/strict';
import test from 'node:test';

import { GLYPH_DEBT, blockOf, unsafeGlyphs } from './glyphSubset.ts';
import { compileRoomGraph } from './roomGraph.ts';
import { compileTopology } from './roomTopology.ts';
import { emptyAtlas, layoutRoom } from './roomLayout.ts';
import { renderWorld, LEGEND, type Camera } from './asciiCamera.ts';
import { referenceFrames } from './referenceTrace.ts';
import type { LiveRun, LiveRunsResponse } from './liveRuns.ts';

function wire(runs: LiveRun[]): LiveRunsResponse {
	return {
		generated_at: '2026-08-27T10:20:00Z',
		runs,
		stale: false,
		reported_at: '2026-08-27T10:20:00Z',
		spawn_max_concurrent: 3
	};
}

/** Every board the reference journey produces, at two camera widths. */
function boards(): string[] {
	const out: string[] = [];
	let memory = emptyAtlas();
	for (const frame of referenceFrames()) {
		const graph = compileRoomGraph(wire(frame), null);
		const topo = compileTopology(graph);
		const placed = layoutRoom(topo, memory);
		memory = placed.memory;
		for (const cols of [80, 160]) {
			const cam: Camera = { center: { x: 0, y: 0 }, cols, rows: 26, level: 'island' };
			out.push(renderWorld(topo, placed.layout, graph, cam, {}));
		}
	}
	return out;
}

test('no glyph reaches the board from outside the safe blocks, undeclared', () => {
	// The board is a *character grid*: a glyph the reader's font lacks does
	// not fail loudly, it substitutes — usually at the wrong width, so the
	// grid shears. Measured on a real screen: ⛁ arrived as ⊕, ✉ as ≫, and ⌁
	// as a tilde, which is why the maintainer asked three separate times for
	// a boundary status line that was already built and already rendering.
	//
	// This test cannot make the existing debt safe. What it does is stop the
	// set from growing without a decision.
	for (const board of boards()) {
		const bad = unsafeGlyphs(board);
		assert.deepEqual(
			bad,
			[],
			`undeclared glyph(s) on the board: ${bad.map((c) => `${c} U+${(c.codePointAt(0) ?? 0).toString(16).toUpperCase()}`).join(', ')} — ` +
				`either use a glyph from a block a terminal font is built to cover, or add it to GLYPH_DEBT with the argument for keeping it`
		);
	}
});

test('the legend is held to the same rule as the board it explains', () => {
	// A legend that cannot render is worse than none: it is the one surface a
	// reader turns to *because* a mark was unclear.
	assert.deepEqual(unsafeGlyphs(LEGEND), []);
});

test('the debt ledger is an argument, not a permission list', () => {
	// Every entry names its codepoint, its block, and what it is for. An
	// entry that says only "keep this" would let the ledger become the thing
	// it exists to prevent — a list that grows because adding to it is easier
	// than deciding.
	for (const [glyph, reason] of Object.entries(GLYPH_DEBT)) {
		assert.match(reason, /U\+[0-9A-F]{4}/u, `${glyph} names its codepoint`);
		assert.match(reason, / · /u, `${glyph} names its block and its purpose`);
		assert.equal(blockOf(glyph), null, `${glyph} would not be in the ledger if it were safe`);
	}
});

test('the three observed substitutions are recorded as observed, not suspected', () => {
	// The difference matters: these are not a guess about font coverage, they
	// are what a specific reader saw. A later reader deciding whether to spend
	// a redesign on them should know which is which.
	for (const glyph of ['⛁', '✉', '⌁']) {
		assert.match(GLYPH_DEBT[glyph], /SUBSTITUTED/u, `${glyph} was seen falling back`);
	}
});
