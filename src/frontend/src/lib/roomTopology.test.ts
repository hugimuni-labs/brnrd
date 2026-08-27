import assert from 'node:assert/strict';
import test from 'node:test';

import { compileRoomGraph, type TrailStep } from './roomGraph.ts';
import {
	compileTopology,
	dirId,
	islandRootId,
	pathSegments,
	routeBetween
} from './roomTopology.ts';
import type { LiveRun, LiveRunsResponse } from './liveRuns.ts';

// ── fixtures — the wire's shape ─────────────────────────────────────────────

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
		room: { env: 'host', branch: 'brr/topo', dir: null },
		edge: null,
		daemon_stale: false,
		...over
	} as LiveRun;
}

function edge(act: string, detail: string, over: Partial<NonNullable<LiveRun['edge']>> = {}) {
	return {
		at: '2026-08-27T10:10:00Z',
		phase: 'PostToolUse',
		act,
		tools: ['Bash'],
		detail,
		out_bytes: 100,
		injected: false,
		dir: '.',
		...over
	};
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

function topoFor(runs: LiveRun[], trails?: Record<string, TrailStep[]>) {
	return compileTopology(compileRoomGraph(liveWire(runs), null, trails));
}

// ── the trie ────────────────────────────────────────────────────────────────

test('two observed paths with a shared prefix share the same structural corridor', () => {
	const trails: Record<string, TrailStep[]> = {
		r1: [
			{ dir: 'src/frontend/src/lib', act: 'mutate', at: '2026-08-27T10:01:00Z' },
			{ dir: 'src/frontend/tests', act: 'probe', at: '2026-08-27T10:02:00Z' },
			{ dir: 'docs/legal/export', act: 'orient', at: '2026-08-27T10:03:00Z' }
		]
	};
	const topo = topoFor([liveRun({ run_id: 'r1' })], trails);
	// shared prefixes exist once each
	for (const id of [
		dirId(REPO, ['src']),
		dirId(REPO, ['src', 'frontend']),
		dirId(REPO, ['src', 'frontend', 'src']),
		dirId(REPO, ['src', 'frontend', 'src', 'lib']),
		dirId(REPO, ['src', 'frontend', 'tests']),
		dirId(REPO, ['docs']),
		dirId(REPO, ['docs', 'legal']),
		dirId(REPO, ['docs', 'legal', 'export'])
	]) {
		assert.ok(topo.nodes[id], `missing ${id}`);
	}
	// both full paths hang off the one `src/frontend` node
	assert.equal(
		topo.nodes[dirId(REPO, ['src', 'frontend', 'tests'])].parentId,
		dirId(REPO, ['src', 'frontend'])
	);
	assert.equal(
		topo.nodes[dirId(REPO, ['src', 'frontend', 'src'])].parentId,
		dirId(REPO, ['src', 'frontend'])
	);
	// intermediate prefixes are structural directories, not something else
	assert.equal(topo.nodes[dirId(REPO, ['src'])].kind, 'directory');
	assert.equal(topo.nodes[dirId(REPO, ['src'])].depth, 1);
});

test('ids are stable: repo identity + normalized path prefix', () => {
	assert.equal(dirId(REPO, pathSegments('src/frontend')), `repo:${REPO}/src/frontend`);
	assert.equal(dirId(REPO, pathSegments('./src//frontend/')), `repo:${REPO}/src/frontend`);
	assert.equal(dirId(REPO, []), islandRootId(REPO));
});

test('a parsed file attaches as a leaf of its terminal directory', () => {
	const trails: Record<string, TrailStep[]> = {
		r1: [
			{
				dir: 'src/frontend/src/lib',
				act: 'mutate',
				at: '2026-08-27T10:01:00Z',
				file: 'asciiRoom.ts'
			}
		]
	};
	const topo = topoFor([liveRun({ run_id: 'r1' })], trails);
	const leaf = `${dirId(REPO, ['src', 'frontend', 'src', 'lib'])}#file:asciiRoom.ts`;
	assert.equal(topo.nodes[leaf]?.kind, 'file');
	assert.equal(topo.nodes[leaf]?.parentId, dirId(REPO, ['src', 'frontend', 'src', 'lib']));
});

test('multiple camps inhabit one repository island without duplicating it', () => {
	const topo = topoFor([
		liveRun({ run_id: 'r1', room: { env: 'host', branch: 'brr/a', dir: null } }),
		liveRun({
			run_id: 'r2',
			is_subspawn: true,
			parent_run_id: 'r1',
			room: { env: 'worktree', branch: 'brr/b', dir: 'brr-wt-b' }
		})
	]);
	assert.equal(topo.islandRoots.length, 1);
	const camps = Object.values(topo.nodes).filter((n) => n.kind === 'camp');
	assert.equal(camps.length, 2);
	assert.ok(camps.every((c) => c.parentId === islandRootId(REPO)));
});

test('a cross-repo strand occupies another island while the parent stays put', () => {
	const topo = topoFor([
		liveRun({ run_id: 'parent', edge: edge('orient', 'Read x.ts', { dir: 'src' }) }),
		liveRun({
			run_id: 'kid',
			is_subspawn: true,
			parent_run_id: 'parent',
			repo_label: 'hugimuni-labs/brnrd-knowledge',
			room: { env: 'worktree', branch: 'brr/kb', dir: 'brr-wt-kid' },
			edge: edge('orient', 'Read design.md', { dir: 'design' })
		})
	]);
	assert.equal(topo.islandRoots.length, 2);
	assert.equal(topo.actorPlaces['parent'], dirId(REPO, ['src']));
	assert.equal(topo.actorPlaces['kid'], dirId('hugimuni-labs/brnrd-knowledge', ['design']));
});

// ── actor place resolution ──────────────────────────────────────────────────

test('a desk visit resolves to the camp portal rack; .card to the chart table', () => {
	const topoA = topoFor([liveRun({ run_id: 'r1', edge: edge('orient', 'Read inbox.json') })]);
	assert.ok(topoA.actorPlaces['r1'].endsWith('#portal-rack'));
	const topoB = topoFor([liveRun({ run_id: 'r1', edge: edge('mutate', 'Write .card') })]);
	assert.ok(topoB.actorPlaces['r1'].endsWith('#chart-table'));
});

test('the local rig attaches to the directory actually probed', () => {
	const trails: Record<string, TrailStep[]> = {
		r1: [{ dir: 'tests', act: 'probe', at: '2026-08-27T10:01:00Z' }]
	};
	const topo = topoFor(
		[liveRun({ run_id: 'r1', edge: edge('probe', 'pytest', { dir: 'tests' }) })],
		trails
	);
	const rig = `${dirId(REPO, ['tests'])}#rig`;
	assert.equal(topo.actorPlaces['r1'], rig);
	assert.equal(topo.nodes[rig].parentId, dirId(REPO, ['tests']));
});

test('publish stands at the island forge dock; closing at the cut loom', () => {
	const topoA = topoFor([
		liveRun({ run_id: 'r1', edge: edge('publish', 'git push origin brr/x') })
	]);
	assert.equal(topoA.actorPlaces['r1'], `${islandRootId(REPO)}#forge-dock`);
	const topoB = topoFor([liveRun({ run_id: 'r1', lifecycle: 'closing' })]);
	assert.ok(topoB.actorPlaces['r1'].endsWith('#cut-loom'));
});

test('a node existing does not imply the actor acted there — structural prefixes carry no touch data', () => {
	const trails: Record<string, TrailStep[]> = {
		r1: [{ dir: 'src/frontend/tests', act: 'probe', at: '2026-08-27T10:01:00Z' }]
	};
	const graph = compileRoomGraph(liveWire([liveRun({ run_id: 'r1' })]), null, trails);
	const topo = compileTopology(graph);
	// `src` and `src/frontend` exist structurally…
	assert.ok(topo.nodes[dirId(REPO, ['src'])]);
	// …but the graph's chamber list (first-touch data) has only the observed dir
	const chambers = graph.islands[0].camps[0].chambers.map((c) => c.dir);
	assert.deepEqual(chambers, ['src/frontend/tests']);
});

// ── routing ─────────────────────────────────────────────────────────────────

test('a directory-to-directory route follows the trie through the lowest common ancestor', () => {
	const trails: Record<string, TrailStep[]> = {
		r1: [
			{ dir: 'src/frontend/src/lib', act: 'mutate', at: '2026-08-27T10:01:00Z' },
			{ dir: 'src/frontend/tests', act: 'probe', at: '2026-08-27T10:02:00Z' }
		]
	};
	const topo = topoFor([liveRun({ run_id: 'r1' })], trails);
	const route = routeBetween(
		topo,
		dirId(REPO, ['src', 'frontend', 'src', 'lib']),
		dirId(REPO, ['src', 'frontend', 'tests'])
	);
	assert.deepEqual(route, [
		dirId(REPO, ['src', 'frontend', 'src', 'lib']),
		dirId(REPO, ['src', 'frontend', 'src']),
		dirId(REPO, ['src', 'frontend']),
		dirId(REPO, ['src', 'frontend', 'tests'])
	]);
});

test('a same-place route is the single place; unknown ends are null', () => {
	const topo = topoFor([liveRun({ run_id: 'r1' })]);
	const camp = topo.actorPlaces['r1'];
	assert.deepEqual(routeBetween(topo, camp, camp), [camp]);
	assert.equal(routeBetween(topo, camp, 'repo:not/a-place'), null);
});

test('cross-island routes travel the sea lanes through HOME', () => {
	const topo = topoFor([
		liveRun({ run_id: 'r1', edge: edge('orient', 'Read x', { dir: 'src' }) }),
		liveRun({
			run_id: 'kid',
			is_subspawn: true,
			parent_run_id: 'r1',
			repo_label: 'hugimuni-labs/brnrd-knowledge',
			room: { env: 'worktree', branch: 'brr/kb', dir: 'wt' },
			edge: edge('orient', 'Read d.md', { dir: 'design' })
		})
	]);
	const route = routeBetween(
		topo,
		islandRootId(REPO),
		islandRootId('hugimuni-labs/brnrd-knowledge')
	);
	assert.deepEqual(route, [
		islandRootId(REPO),
		topo.homeId,
		islandRootId('hugimuni-labs/brnrd-knowledge')
	]);
});
