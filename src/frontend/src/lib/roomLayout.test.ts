import assert from 'node:assert/strict';
import test from 'node:test';

import { compileRoomGraph, type TrailStep } from './roomGraph.ts';
import { compileTopology, campId, dirId, islandRootId } from './roomTopology.ts';
import { emptyAtlas, layoutRoom } from './roomLayout.ts';
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

test('the repository root sits at its island origin; depth advances just past the parent label', () => {
	const topo = topoWith(['src/frontend/src/lib']);
	const { layout } = layoutRoom(topo);
	const root = layout.nodes[islandRootId(REPO)];
	assert.deepEqual(root, { x: 0, y: 0 });
	const src = layout.nodes[dirId(REPO, ['src'])];
	const frontend = layout.nodes[dirId(REPO, ['src', 'frontend'])];
	const src2 = layout.nodes[dirId(REPO, ['src', 'frontend', 'src'])];
	// the advance is label-aware (2026-08-27, the width fold): each child
	// clears its parent's painted label at island scale (2 chars/unit) plus
	// a short corridor — never the old fixed 11-unit stride
	assert.ok(src.x > root.x);
	assert.ok((frontend.x - src.x) * 2 >= 'src/'.length + 2, 'clears the parent label');
	assert.ok((src2.x - frontend.x) * 2 >= 'frontend/'.length + 2, 'clears the longer label');
	assert.ok(frontend.x - src.x <= 14, 'and never sprawls past the cap');
	// deterministic: the same topology lays out to the same coordinates
	assert.deepEqual(layoutRoom(topo).layout.nodes, layout.nodes);
});

test('the first child continues its parent lane; siblings claim the stable alternation', () => {
	const topo = topoWith(['src/a', 'src/b', 'src/c']);
	const { layout } = layoutRoom(topo);
	const src = layout.nodes[dirId(REPO, ['src'])];
	const a = layout.nodes[dirId(REPO, ['src', 'a'])];
	const b = layout.nodes[dirId(REPO, ['src', 'b'])];
	const c = layout.nodes[dirId(REPO, ['src', 'c'])];
	assert.equal(a.y, src.y); // first child continues the lane
	assert.equal(b.y, src.y - 4); // then -4
	assert.equal(c.y, src.y + 4); // then +4
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

test('world bounds can exceed any viewport in every direction', () => {
	const topo = topoWith(['src/a/b/c/d/e/f/g/h']);
	const { layout } = layoutRoom(topo);
	const b = layout.worldBounds;
	assert.ok(b.maxX - b.minX > 76 / 2, 'wider than a 76-col window at 2 chars/unit');
	assert.ok(b.minX < 0, 'home/camps extend west of the origin');
});
