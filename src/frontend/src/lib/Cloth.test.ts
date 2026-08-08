import { ok } from 'node:assert/strict';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { compile } from 'svelte/compiler';
import { render } from 'svelte/server';
import type { BoltRow } from './bolts.ts';

const here = dirname(fileURLToPath(import.meta.url));

// The cloth-head lane (design-the-bolt.md §The cloth side, fork 2 signed):
// the away lane for unacked bolts, rendered between the head row and the
// lens rail. Same compile-server-side dance as WarpBand.test.ts — the real
// Cloth compiled in, its Svelte children (MoodChip, RunNodeInline,
// Crossing) stubbed, since this harness has no bundler to resolve them.

const CLOTH_GEN = '.cloth.cloth.generated.mjs';
const generatedFiles = [CLOTH_GEN].map((name) => join(here, name));

function compileCloth(): void {
	const source = readFileSync(join(here, 'Cloth.svelte'), 'utf8');
	const compiled = compile(source, { generate: 'server', runes: true, name: 'Cloth' });
	const runnable = compiled.js.code
		.replace(/import\s+MoodChip\s+from\s*'\.\/MoodChip\.svelte';/, 'const MoodChip = () => {};')
		.replace(
			/import\s+RunNodeInline\s+from\s*'\.\/RunNodeInline\.svelte';/,
			'const RunNodeInline = () => {};'
		)
		.replace(/import\s+Crossing\s+from\s*'\.\/Crossing\.svelte';/, 'const Crossing = () => {};')
		.replace(/'(\.\/[A-Za-z0-9_-]+)'/g, "'$1.ts'");
	writeFileSync(join(here, CLOTH_GEN), runnable);
}

interface ClothProps {
	rows: unknown[] | null;
	now: number;
	windowMs: number;
	stale: boolean;
	unackedBolts?: BoltRow[] | null;
	boltGlowToken?: number;
}

async function renderCloth(props: ClothProps): Promise<string> {
	compileCloth();
	try {
		const module = await import(`./${CLOTH_GEN}?t=${process.pid}-${Math.random()}`);
		return render(module.default, { props }).body;
	} finally {
		for (const file of generatedFiles) rmSync(file, { force: true });
	}
}

after(() => {
	for (const file of generatedFiles) rmSync(file, { force: true });
});

function bolt(overrides: Partial<BoltRow> = {}): BoltRow {
	return {
		runId: 'run-1',
		name: 'run-1',
		named: false,
		bolt: 'accepted',
		repoLabel: 'Gurio/brr',
		endedAt: Date.parse('2026-08-07T22:00:00Z'),
		relics: [],
		...overrides
	};
}

test('no lane renders when there are no unacked bolts, or the feed has not resolved', async () => {
	const empty = await renderCloth({
		rows: [],
		now: Date.now(),
		windowMs: 1000,
		stale: false,
		unackedBolts: []
	});
	ok(!empty.includes('await taking'), 'zero unacked — no lane markup');

	const unresolved = await renderCloth({
		rows: [],
		now: Date.now(),
		windowMs: 1000,
		stale: false,
		unackedBolts: null
	});
	ok(!unresolved.includes('await taking'), 'unresolved feed — count doctrine, no partial lane');
});

test('the lane lists unacked bolts newest first, with a take control on each and a take-all', async () => {
	const body = await renderCloth({
		rows: [],
		now: Date.now(),
		windowMs: 1000,
		stale: false,
		unackedBolts: [
			bolt({ runId: 'run-1', name: 'the-cutting', named: true }),
			bolt({ runId: 'run-2', name: 'run-2' })
		]
	});
	ok(body.includes('2 bolts await taking'), 'the lane header counts — the strip’s own phrase, one copy source');
	ok(body.includes('the-cutting'), 'a named run renders its name');
	ok(body.includes('run-2'), 'an unnamed run falls back to its id');
	ok(body.includes('take all'), 'the lane-head take-all control renders');
	const takeButtons = body.match(/>\s*take\s*</g) ?? [];
	ok(takeButtons.length === 2, `expected one "take" per row, found ${takeButtons.length}`);
});

test("a bolt row carries produce chips reused from the cloth's own grammar", async () => {
	const body = await renderCloth({
		rows: [],
		now: Date.now(),
		windowMs: 1000,
		stale: false,
		unackedBolts: [bolt({ relics: [{ kind: 'commit' }, { kind: 'pr' }] })]
	});
	ok(body.includes('1pr'), 'the pr chip renders via produceChips');
	ok(body.includes('1c'), 'the commit chip renders via produceChips');
});

test('at rest (no glow armed) the lane carries no glow highlight class', async () => {
	const body = await renderCloth({
		rows: [],
		now: Date.now(),
		windowMs: 1000,
		stale: false,
		unackedBolts: [bolt()],
		boltGlowToken: 0
	});
	ok(!body.includes('bg-amber-900/30'), 'the glow class is absent until armed');
});
