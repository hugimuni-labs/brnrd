import assert from 'node:assert/strict';
import test from 'node:test';

import { compileRoomReef, REEF_RENDER_MAX } from './roomReef.ts';
import type { RoomGraph, ClothRow } from './roomGraph.ts';

// ── fixtures ─────────────────────────────────────────────────────────────

function clothRow(over: Partial<ClothRow> & { runId: string }): ClothRow {
	return {
		name: over.runId,
		tense: 'cut',
		glyph: null,
		endedAt: null,
		wallSeconds: null,
		usd: null,
		counts: {},
		course: null,
		childOf: null,
		kbPages: [],
		...over
	} as ClothRow;
}

/** A minimal RoomGraph — `compileRoomReef` reads only `.cloth`, so the rest
 * of the shape is filler that satisfies the type without meaning anything. */
function graphOf(cloth: ClothRow[]): RoomGraph {
	return {
		generatedAt: null,
		islands: [],
		actors: [],
		cloth,
		pendingLetters: 0,
		slots: { active: 0, max: null },
		crossings: [],
		clockwork: [],
		garage: [],
		watch: [],
		daemonMood: null,
		stale: false
	};
}

// ── the join: page → the run(s) that cited it ───────────────────────────────

test('one page cited by two rows becomes one outcrop with two citations', () => {
	const reef = compileRoomReef(
		graphOf([
			clothRow({
				runId: 'r1',
				tense: 'live',
				glyph: '@',
				kbPages: [{ path: 'design-the-water-line.md', url: 'https://x/water' }]
			}),
			clothRow({
				runId: 'r2',
				tense: 'cut',
				endedAt: '2026-08-20T10:00:00Z',
				kbPages: [{ path: 'design-the-water-line.md', url: null }]
			})
		])
	);
	assert.equal(reef.outcrops.length, 1);
	const [outcrop] = reef.outcrops;
	assert.equal(outcrop.path, 'design-the-water-line.md');
	assert.equal(outcrop.url, 'https://x/water');
	assert.deepEqual(
		outcrop.citations.map((c) => c.runId),
		['r1', 'r2']
	);
});

// ── absence stays absence: no zero-outcrop placeholder ──────────────────────
// MUTATION CHECKED: replaced `if (!page.path) continue;` with an unconditional
// push (a row with kbPages: [] never reaches the loop body at all, so the
// mutation that matters is skipping the emptiness — simulated by pushing a
// synthetic `{path: '', ...}` outcrop unconditionally per row). The test
// below failed (`reef.outcrops.length` became 1, an outcrop with an empty
// path, instead of 0) until the mutation was reverted. See report-reef.md.
test('a run with no kb produce contributes no outcrop — never a placeholder', () => {
	const reef = compileRoomReef(
		graphOf([clothRow({ runId: 'r1', kbPages: [] }), clothRow({ runId: 'r2' })])
	);
	assert.deepEqual(reef.outcrops, []);
	assert.equal(reef.droppedOlder, 0);
});

// ── depth: older = deeper, from the *earliest* citation, not the latest ─────
// MUTATION CHECKED: flipped `applyCitation`'s comparison from `<` (keep the
// earliest) to `>` (keep the latest). The assertion on `depthAt` below
// failed (read back the later run's `endedAt` instead of the earlier one's)
// until the mutation was reverted. See report-reef.md.
test('depth anchors to the earliest citing row, not the most recent', () => {
	const reef = compileRoomReef(
		graphOf([
			clothRow({
				runId: 'later',
				endedAt: '2026-08-25T10:00:00Z',
				kbPages: [{ path: 'p.md', url: null }]
			}),
			clothRow({
				runId: 'earlier',
				endedAt: '2026-08-01T10:00:00Z',
				kbPages: [{ path: 'p.md', url: null }]
			})
		])
	);
	assert.equal(reef.outcrops[0].depthAt, '2026-08-01T10:00:00Z');
});

test('dated outcrops sink oldest-last; undated (live-only) outcrops stay shallowest', () => {
	const reef = compileRoomReef(
		graphOf([
			clothRow({
				runId: 'live-only',
				tense: 'live',
				kbPages: [{ path: 'undated.md', url: null }]
			}),
			clothRow({
				runId: 'old',
				endedAt: '2026-07-01T00:00:00Z',
				kbPages: [{ path: 'old.md', url: null }]
			}),
			clothRow({
				runId: 'new',
				endedAt: '2026-08-30T00:00:00Z',
				kbPages: [{ path: 'new.md', url: null }]
			})
		])
	);
	assert.deepEqual(
		reef.outcrops.map((o) => o.path),
		['undated.md', 'new.md', 'old.md']
	);
});

// ── only grows: a bound on what's *rendered*, never on what's known ─────────
// MUTATION CHECKED: changed `droppedOlder` to always report `0` regardless of
// `all.length - outcrops.length`. The assertion below (`droppedOlder === 2`)
// failed (read `0`) until the mutation was reverted. See report-reef.md.
test('the render bound cuts the list but always states the count dropped', () => {
	const rows = Array.from({ length: REEF_RENDER_MAX + 2 }, (_, i) =>
		clothRow({
			runId: `r${i}`,
			endedAt: `2026-01-${String(1 + (i % 28)).padStart(2, '0')}T00:00:00Z`,
			kbPages: [{ path: `page-${i}.md`, url: null }]
		})
	);
	const reef = compileRoomReef(graphOf(rows));
	assert.equal(reef.outcrops.length, REEF_RENDER_MAX);
	assert.equal(reef.droppedOlder, 2);
});

test('pure and deterministic: the same graph compiles to the same reef twice', () => {
	const rows = [
		clothRow({
			runId: 'r1',
			endedAt: '2026-08-01T00:00:00Z',
			kbPages: [{ path: 'a.md', url: null }]
		})
	];
	const first = compileRoomReef(graphOf(rows));
	const second = compileRoomReef(graphOf(rows));
	assert.deepEqual(first, second);
});
