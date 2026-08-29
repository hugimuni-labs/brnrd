import assert from 'node:assert/strict';
import test from 'node:test';

import { compileRoomGraph, type TrailStep } from './roomGraph.ts';
import { compileTopology, campId, dirId, islandRootId } from './roomTopology.ts';
import { MAX_DIR_LABEL_CHARS, emptyAtlas, layoutRoom, terminalRequest } from './roomLayout.ts';
import { LABOUR_FLOOR, TERRAIN_TOP, inDistrict } from './roomRegions.ts';
import type { LiveRun, LiveRunsResponse } from './liveRuns.ts';

function liveRun(over: Partial<LiveRun> & { run_id: string }): LiveRun {
	return {
		id: over.run_id,
		kind: 'daemon',
		stream: 'cloud:telegram:1:',
		label: '',
		name: '',
		repo_label: 'hugimuni-labs/brnrd',
		started_at: '2026-08-27T10:00:00Z',
		last_seen: '2026-08-27T10:20:00Z',
		parent_run_id: null,
		is_subspawn: false,
		runner: { name: 'claude-fable', shell: 'claude', core: 'fable' },
		phase: 'running',
		card_text: null,
		card_updated_at: null,
		relics_counts: null,
		portals: { pending: 0, oldest_at: null },
		room: { env: 'host', branch: 'brr/layout', dir: null },
		edge: null,
		daemon_stale: false,
		...over
	} as LiveRun;
}

function liveWire(runs: LiveRun[]): LiveRunsResponse {
	return {
		generated_at: '2026-08-27T10:20:00Z',
		runs,
		stale: false,
		reported_at: '2026-08-27T10:20:00Z',
		spawn_max_concurrent: 3
	};
}

const REPO = 'hugimuni-labs/brnrd';

function topoWith(trailDirs: string[], runs?: LiveRun[]) {
	const trails: Record<string, TrailStep[]> = {
		r1: trailDirs.map((dir, i) => ({
			dir,
			act: 'orient',
			at: `2026-08-27T10:0${i}:00Z`
		}))
	};
	return compileTopology(
		compileRoomGraph(liveWire(runs ?? [liveRun({ run_id: 'r1' })]), null, trails)
	);
}

test('the root sits at its island origin; depth advances a constant tree(1) indent', () => {
	// Two branches on purpose. Since the trie fold (2026-08-29) a single
	// chain collapses to one node, and a depth test on a one-node trie would
	// pass by having no depth to advance through.
	const topo = topoWith(['src/frontend/src/lib', 'src/frontend/tests']);
	const { layout } = layoutRoom(topo);
	const root = layout.nodes[islandRootId(REPO)];
	assert.deepEqual(root, { x: 0, y: 0 });
	const frontend = layout.nodes[dirId(REPO, ['src', 'frontend'])];
	const lib = layout.nodes[dirId(REPO, ['src', 'frontend', 'src', 'lib'])];
	// The advance no longer clears the parent's painted label, because no
	// child shares its parent's row any more. It is `tree(1)`'s indent: two
	// world units, four characters at island scale — whatever the label says.
	// This is the assertion that fails if the label-aware advance comes back:
	// `src/frontend/` is 13 characters, so the old rule owed ≥ 7 units here.
	assert.equal(lib.x - frontend.x, 2, 'a constant indent, not a label-aware stride');
	assert.ok(lib.y !== frontend.y, 'and the row is what separates them');
	// deterministic: the same topology lays out to the same coordinates
	assert.deepEqual(layoutRoom(topo).layout.nodes, layout.nodes);
});

test('no two nodes share a row their painted labels would both reach', () => {
	// The layout-side half of the label garble. The camera clips label
	// against label within a row; that guard only ever hid the collision.
	// Here the allocator refuses to produce one.
	const topo = topoWith([
		'src/frontend/src/lib',
		'src/frontend/src/routes/ascii',
		'src/frontend/repro',
		'src/frontend/tests',
		'src/brnrd/brr/gates',
		'src/brnrd/brr/prompts',
		'docs/src/content/docs'
	]);
	const { layout } = layoutRoom(topo);
	const spans = Object.values(topo.nodes)
		.filter((n) => n.kind === 'directory' || n.kind === 'file')
		.map((n) => {
			const p = layout.nodes[n.id];
			// the camera paints `label/` east from the node's own cell
			const w = Math.ceil(Math.min(n.label.length + 1, MAX_DIR_LABEL_CHARS) / 2);
			return { id: n.id, y: p.y, lo: p.x, hi: p.x + w };
		});
	for (const a of spans) {
		for (const b of spans) {
			if (a.id === b.id || a.y !== b.y) continue;
			assert.ok(a.hi <= b.lo || b.hi <= a.lo, `${a.id} and ${b.id} overlap on row ${a.y}`);
		}
	}
});

test('the tree walks depth-first downward — a subtree is a contiguous block', () => {
	const topo = topoWith(['src/a', 'src/b', 'src/c']);
	const { layout } = layoutRoom(topo);
	const src = layout.nodes[dirId(REPO, ['src'])];
	const a = layout.nodes[dirId(REPO, ['src', 'a'])];
	const b = layout.nodes[dirId(REPO, ['src', 'b'])];
	const c = layout.nodes[dirId(REPO, ['src', 'c'])];
	// `tree(1)`: every entry gets its own line, children below their parent,
	// siblings in observation order. Before 2026-08-29 the first child
	// continued its parent's lane and the next two claimed ±4 — which is what
	// forced every level to advance past the parent's label.
	assert.equal(a.y, src.y + 1);
	assert.equal(b.y, src.y + 2);
	assert.equal(c.y, src.y + 3);
	assert.ok(a.x === b.x && b.x === c.x, 'siblings share a column');
	assert.equal(a.x, src.x + 2, 'one indent east of the parent');
});

test('a subtree occupies the rows between its parent and the next sibling', () => {
	// `one` needs two children or the fold (#1695) collapses the chain and
	// there is no subtree left to be contiguous.
	const topo = topoWith(['src/one/x', 'src/one/y', 'src/two']);
	const { layout } = layoutRoom(topo);
	const one = layout.nodes[dirId(REPO, ['src', 'one'])];
	const x = layout.nodes[dirId(REPO, ['src', 'one', 'x'])];
	const y = layout.nodes[dirId(REPO, ['src', 'one', 'y'])];
	const two = layout.nodes[dirId(REPO, ['src', 'two'])];
	assert.ok(x.y > one.y && y.y > x.y, 'children descend below their parent');
	assert.ok(two.y > y.y, "and the parent's next sibling is below the whole block");
});

test('adding a new observed path does not move any existing node', () => {
	const topo1 = topoWith(['src/frontend', 'docs']);
	const first = layoutRoom(topo1);
	const topo2 = topoWith(['src/frontend', 'docs', 'src/routes/new', 'scripts']);
	const second = layoutRoom(topo2, first.memory);
	for (const [id, p] of Object.entries(first.layout.nodes)) {
		assert.deepEqual(second.layout.nodes[id], p, `${id} moved`);
	}
	// and the new terrain exists somewhere real
	assert.ok(second.layout.nodes[dirId(REPO, ['src', 'routes', 'new'])]);
	assert.ok(second.layout.nodes[dirId(REPO, ['scripts'])]);
});

test('the same topology + atlas memory yields the same coordinates (renderer independence)', () => {
	const topo = topoWith(['src/frontend/src/lib', 'src/frontend/tests']);
	const a = layoutRoom(topo, emptyAtlas());
	const b = layoutRoom(topo, emptyAtlas());
	assert.deepEqual(a.layout.nodes, b.layout.nodes);
	assert.deepEqual(a.layout.edgeRoutes, b.layout.edgeRoutes);
	// a snapshot round-trip through the memory changes nothing either
	const c = layoutRoom(topo, a.memory);
	assert.deepEqual(c.layout.nodes, a.layout.nodes);
});

test('file leaves occupy a small stable offset from their directory', () => {
	const trails: Record<string, TrailStep[]> = {
		r1: [{ dir: 'src/lib', act: 'mutate', at: '2026-08-27T10:01:00Z', file: 'a.ts' }]
	};
	const topo = compileTopology(
		compileRoomGraph(liveWire([liveRun({ run_id: 'r1' })]), null, trails)
	);
	const { layout } = layoutRoom(topo);
	const dir = layout.nodes[dirId(REPO, ['src', 'lib'])];
	const file = layout.nodes[`${dirId(REPO, ['src', 'lib'])}#file:a.ts`];
	assert.ok(Math.abs(file.x - dir.x) <= 5 && Math.abs(file.y - dir.y) <= 4);
});

test('camps anchor on the west shore with their stations around them', () => {
	const topo = topoWith(['src']);
	const { layout } = layoutRoom(topo);
	const root = layout.nodes[islandRootId(REPO)];
	const camp = layout.nodes[campId(REPO, { branch: 'brr/layout', dir: null })];
	assert.ok(camp.x < root.x, 'camp west of the root');
	const chart = layout.nodes[`${campId(REPO, { branch: 'brr/layout', dir: null })}#chart-table`];
	assert.ok(Math.abs(chart.x - camp.x) <= 5 && Math.abs(chart.y - camp.y) <= 4);
});

test('a second island claims its own origin south, and a third never moves the second', () => {
	const runs2 = [
		liveRun({ run_id: 'r1' }),
		liveRun({
			run_id: 'r2',
			repo_label: 'org/two',
			room: { env: 'worktree', branch: 'brr/t', dir: 'wt' }
		})
	];
	const topo2 = compileTopology(compileRoomGraph(liveWire(runs2), null));
	const first = layoutRoom(topo2);
	const one = first.layout.nodes[islandRootId(REPO)];
	const two = first.layout.nodes[islandRootId('org/two')];
	assert.ok(two.y > one.y, 'second island south of the first');

	const runs3 = [
		...runs2,
		liveRun({
			run_id: 'r3',
			repo_label: 'org/three',
			room: { env: 'worktree', branch: 'brr/u', dir: 'wu' }
		})
	];
	const topo3 = compileTopology(compileRoomGraph(liveWire(runs3), null));
	const second = layoutRoom(topo3, first.memory);
	assert.deepEqual(second.layout.nodes[islandRootId('org/two')], two);
	assert.ok(second.layout.nodes[islandRootId('org/three')].y > two.y);
});

test('the world is unbounded, and the camera is a window onto it', () => {
	// Forks at every level: since the fold (#1695) a chain collapses to one
	// node, so a chain would measure the fold rather than the world's size.
	const paths: string[] = [];
	for (let i = 0; i < 3; i++)
		for (let j = 0; j < 3; j++) for (let k = 0; k < 3; k++) paths.push(`pkg/a${i}/b${j}/c${k}`);
	const topo = topoWith(paths);
	const { layout } = layoutRoom(topo);
	const b = layout.worldBounds;
	// Where the unboundedness *lives* moved on 2026-08-29. The old board grew
	// east at ten to twenty-eight characters a level and this assertion read
	// `maxX - minX > 38`; one node per row spends depth on rows instead, the
	// same trade `tree(1)` makes, so the tall axis is the honest one to
	// assert on. Width is still unbounded — it is just no longer the axis a
	// forty-node tree exceeds a window on first.
	assert.ok(b.maxY - b.minY > 24, 'taller than a 24-row window');
	assert.ok(b.minX < 0, 'home and the shores extend west of the origin');
	assert.ok(b.maxX > 0, 'terrain extends east of it');
	// and it keeps growing: more observation, more world, nothing moved
	const more = layoutRoom(topoWith([...paths, 'other/deep/one', 'other/deep/two']));
	assert.ok(more.layout.worldBounds.maxY >= b.maxY);
});

test('the terminal gets allocated ground, and terrain can never reach it', () => {
	// The defect, as geometry. Before this, `asciiCamera` derived a 50x7 box
	// from the camp's point and painted it with claiming writes while the
	// tree grew east underneath — two owners, one cell, paint order deciding.
	const topo = topoWith(Array.from({ length: 40 }, (_, i) => `pkg/mod${i}`));
	const req = terminalRequest('r1', 50, 7);
	const { layout } = layoutRoom(topo, emptyAtlas(), [req]);
	const rect = layout.regions[req.id];
	assert.ok(rect, 'the request was allocated');
	assert.equal(rect.w, 25, '50 characters at 2 chars/unit');
	assert.equal(rect.h, 7);
	for (const [id, p] of Object.entries(layout.nodes)) {
		const inside = p.x >= rect.x && p.x < rect.x + rect.w && p.y >= rect.y && p.y < rect.y + rect.h;
		assert.ok(!inside, `${id} landed inside the terminal's rectangle`);
	}
	// and the band itself is what guarantees it, not the current node count
	const root = layout.nodes[islandRootId(REPO)];
	assert.ok(rect.y + rect.h - 1 <= root.y + LABOUR_FLOOR, 'the window hangs from the labour floor');
	for (const [id, node] of Object.entries(topo.nodes)) {
		if (node.kind !== 'directory' && node.kind !== 'file') continue;
		assert.ok(
			layout.nodes[id].y >= root.y + TERRAIN_TOP,
			`${id} claimed a row north of the terrain district`
		);
	}
});

test('an allocated rectangle survives a reload and never moves', () => {
	const req = terminalRequest('r1', 50, 7);
	const first = layoutRoom(topoWith(['src/a']), emptyAtlas(), [req]);
	const rect = first.layout.regions[req.id];
	// more terrain arrives, and the same memory is handed back
	const second = layoutRoom(topoWith(['src/a', 'src/b/c', 'docs']), first.memory, [req]);
	assert.deepEqual(second.layout.regions[req.id], rect, 'the window moved');
	assert.ok(first.memory.regions?.[req.id], 'the allocation is in the memory the caller persists');
});

test('the districts an island owns are disjoint', () => {
	const topo = topoWith(['src/a']);
	const { layout } = layoutRoom(topo);
	const root = islandRootId(REPO);
	for (const name of ['terrain', 'camp', 'labour', 'forge'] as const) {
		assert.ok(layout.districts[`${root}#${name}`], `${name} district is published`);
	}
	const origin = layout.nodes[root];
	const names = ['terrain', 'camp', 'labour', 'forge'] as const;
	for (let x = -20; x <= 20; x++) {
		for (let y = -12; y <= 12; y++) {
			const hits = names.filter((n) => inDistrict(n, origin, { x: origin.x + x, y: origin.y + y }));
			assert.ok(hits.length <= 1, `(${x},${y}) claimed by ${hits.join(' + ')}`);
		}
	}
});

test("a route leaving terrain turns at its own row, not down the tree's trunk", () => {
	// The same cross-district claim the terminal's box was, one layer down.
	// The shore rail from the root to the forge dock used to run four rows
	// straight down the tree's own trunk column before turning west — visible
	// on the live board as a `║` cutting through `lib/` and its files.
	const topo = topoWith(['src/a', 'src/b', 'docs']);
	const { layout } = layoutRoom(topo);
	const rootId = islandRootId(REPO);
	const origin = layout.nodes[rootId];
	const route = layout.edgeRoutes[`${rootId}->${rootId}#forge-dock`];
	assert.ok(route, 'the shore edge is routed');
	// walk the polyline cell by cell: past the origin itself, no cell of this
	// route may sit in the terrain district
	for (let i = 0; i + 1 < route.length; i++) {
		const [p, q] = [route[i], route[i + 1]];
		const steps = Math.max(Math.abs(q.x - p.x), Math.abs(q.y - p.y));
		for (let k = 1; k <= steps; k++) {
			const cell = {
				x: p.x + Math.sign(q.x - p.x) * Math.min(k, Math.abs(q.x - p.x)),
				y: p.y + Math.sign(q.y - p.y) * Math.min(k, Math.abs(q.y - p.y))
			};
			assert.ok(
				!inDistrict('terrain', origin, cell),
				`the shore rail crosses terrain at (${cell.x}, ${cell.y})`
			);
		}
	}
	// and the tree's own edges keep the vertical-first turn: there the
	// vertical run *is* the parent's trunk, which is what a tree looks like
	const trunk = layout.edgeRoutes[`${dirId(REPO, ['src'])}->${dirId(REPO, ['src', 'b'])}`];
	assert.equal(trunk[1].x, layout.nodes[dirId(REPO, ['src'])].x, 'tree edges turn at the parent');
});

test('no two terrain nodes share a character row, however their labels fall', () => {
	// Measured on the deployed board 2026-08-29, with two actors exploring
	// different parts of one repo:
	//
	//     |  tests/--· prodshot.mjs        two nodes, one row
	//     |  brr/└---· .prodshot.mjs       two junctions, one row
	//
	// The first allocator let a node share a row whenever the two painted
	// *extents* missed each other. On one subtree that is invisible; with
	// interleaved discoveries it garbles into a path that does not exist.
	// `tree(1)` never shares a row and this is the reason.
	//
	// Two subtrees at very different depths on purpose: a shallow node and a
	// deep one are exactly the pair whose extents miss.
	const topo = topoWith([
		'src/frontend/tests',
		'src/frontend/tests/repro/deep/deeper',
		'src/brnrd/brr/gates',
		'src/brnrd/brr/prompts',
		'docs'
	]);
	const { layout } = layoutRoom(topo);
	const rows = new Map<number, string[]>();
	for (const node of Object.values(topo.nodes)) {
		if (node.kind !== 'directory' && node.kind !== 'file') continue;
		const y = layout.nodes[node.id].y;
		(rows.get(y) ?? rows.set(y, []).get(y)!).push(node.id);
	}
	for (const [y, ids] of rows)
		assert.equal(ids.length, 1, `row ${y} holds ${ids.length} nodes: ${ids.join(' + ')}`);
});
