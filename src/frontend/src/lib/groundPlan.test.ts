import assert from 'node:assert/strict';
import test from 'node:test';

import {
	districtFor,
	fogOf,
	growNodes,
	markAtlas,
	planGround,
	LIT_MS,
	type District,
	type GroundNode
} from './groundPlan.ts';

const AREA = { x: 0.4, y: 0.4, w: 9, d: 7 };

const NODES: GroundNode[] = [
	{ path: 'src/brr', weight: 40 },
	{ path: 'src/frontend', weight: 30 },
	{ path: 'tests', weight: 14 },
	{ path: 'docs', weight: 8 },
	{ path: 'packaging', weight: 4 },
	{ path: 'scripts', weight: 2 }
];

// ── the generator ────────────────────────────────────────────────────────

test('the map is dealt, not rolled: same nodes, same seed, same districts', () => {
	const a = planGround(NODES, AREA, 'hugimuni-labs/brnrd');
	const b = planGround([...NODES].reverse(), AREA, 'hugimuni-labs/brnrd');
	assert.deepEqual(a, b, 'input order must not matter — the sort is part of the contract');
	assert.equal(a.length, NODES.length, 'every node gets a district');
});

test('a different seed deals a different map of the same rooms', () => {
	const a = planGround(NODES, AREA, 'repo-one');
	const b = planGround(NODES, AREA, 'repo-two');
	assert.deepEqual(a.map((t) => t.path).sort(), b.map((t) => t.path).sort());
	assert.notDeepEqual(a, b, 'the jitter is seeded by the repo, so siblings differ');
});

test('districts stay inside the area and do not overlap', () => {
	const districts = planGround(NODES, AREA, 'hugimuni-labs/brnrd');
	for (const t of districts) {
		assert.ok(t.x >= AREA.x - 1e-9 && t.y >= AREA.y - 1e-9, `${t.path} starts inside`);
		assert.ok(
			t.x + t.w <= AREA.x + AREA.w + 1e-9 && t.y + t.d <= AREA.y + AREA.d + 1e-9,
			`${t.path} ends inside`
		);
	}
	for (let i = 0; i < districts.length; i++) {
		for (let j = i + 1; j < districts.length; j++) {
			const a = districts[i];
			const b = districts[j];
			const overlap = a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.d && b.y < a.y + a.d;
			assert.ok(!overlap, `${a.path} and ${b.path} share floor`);
		}
	}
});

test('weight buys floor: the heaviest district is larger than the lightest', () => {
	const districts = planGround(NODES, AREA, 'hugimuni-labs/brnrd');
	const area = (t: District) => t.w * t.d;
	const heavy = districts.find((t) => t.path === 'src/brr')!;
	const light = districts.find((t) => t.path === 'scripts')!;
	assert.ok(area(heavy) > area(light) * 2, 'src/brr dwarfs scripts');
});

// ── where a dir stands ───────────────────────────────────────────────────

test('districtFor picks the deepest prefix on / boundaries; the root is the plaza', () => {
	const districts = planGround(NODES, AREA, 'x');
	assert.equal(districtFor(districts, 'src/frontend/src/lib')?.path, 'src/frontend');
	assert.equal(districtFor(districts, 'src/brr')?.path, 'src/brr');
	assert.equal(districtFor(districts, 'tests')?.path, 'tests');
	// 'src/frontendish' must NOT match 'src/frontend' — boundary, not substring.
	assert.equal(districtFor(districts, 'src/frontendish'), null);
	assert.equal(districtFor(districts, '.'), null);
	assert.equal(districtFor(districts, ''), null);
	assert.equal(districtFor(districts, null), null);
});

// ── fog ──────────────────────────────────────────────────────────────────

test('fog: void until seen, lit while worked, explored after the light moves on', () => {
	const districts = planGround(NODES, AREA, 'x');
	const t = districts.find((d) => d.path === 'docs')!;
	const now = 1_000_000_000;
	assert.equal(fogOf(t, {}, now), 'void');
	let atlas = markAtlas({}, districts, 'docs/legal', now);
	assert.equal(fogOf(t, atlas, now), 'lit');
	assert.equal(fogOf(t, atlas, now + LIT_MS + 1), 'explored', 'memory outlasts the light');
});

test('markAtlas returns the same reference when nothing changed', () => {
	const districts = planGround(NODES, AREA, 'x');
	const atlas = { docs: 5 };
	assert.equal(markAtlas(atlas, districts, '.', 6), atlas, 'the plaza marks nothing');
	assert.equal(markAtlas(atlas, districts, 'no/such/place', 6), atlas);
});

// ── the live floor grows from observation ────────────────────────────────

test('growNodes accretes observed dirs coarsely and weights repeats', () => {
	let nodes: GroundNode[] = [];
	nodes = growNodes(nodes, 'src/frontend/src/lib');
	nodes = growNodes(nodes, 'src/frontend');
	nodes = growNodes(nodes, 'tests');
	nodes = growNodes(nodes, '.');
	assert.deepEqual(
		nodes.map((n) => `${n.path}:${n.weight}`),
		['src/frontend:2', 'tests:1'],
		'src groups one level deeper; the plaza grows nothing'
	);
});
