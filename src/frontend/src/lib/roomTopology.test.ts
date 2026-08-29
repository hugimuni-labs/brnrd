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
	// THE PROPERTY, unchanged by the fold: a shared prefix is one node, and
	// both paths hang off it. `src/frontend` is a fork — two directory
	// children — so it survives as a place.
	assert.ok(topo.nodes[dirId(REPO, ['src', 'frontend'])]);
	assert.equal(
		topo.nodes[dirId(REPO, ['src', 'frontend', 'tests'])].parentId,
		dirId(REPO, ['src', 'frontend'])
	);
	assert.equal(
		topo.nodes[dirId(REPO, ['src', 'frontend', 'src', 'lib'])].parentId,
		dirId(REPO, ['src', 'frontend'])
	);
	// and it is still a structural directory, not something else
	assert.equal(topo.nodes[dirId(REPO, ['src', 'frontend'])].kind, 'directory');

	// THE FOLD: a chain of single-child directories is punctuation, not
	// terrain. `src` had one child and is gone into it; so is
	// `src/frontend/src`, and all of `docs/legal`. The surviving node keeps
	// the *deep* id — `dirId` still addresses the chamber a caller names —
	// and wears the whole folded path as its label.
	for (const gone of [
		dirId(REPO, ['src']),
		dirId(REPO, ['src', 'frontend', 'src']),
		dirId(REPO, ['docs']),
		dirId(REPO, ['docs', 'legal'])
	]) {
		assert.equal(topo.nodes[gone], undefined, `${gone} should have folded`);
	}
	assert.equal(topo.nodes[dirId(REPO, ['src', 'frontend'])].label, 'src/frontend');
	assert.equal(topo.nodes[dirId(REPO, ['src', 'frontend', 'src', 'lib'])].label, 'src/lib');
	assert.equal(
		topo.nodes[dirId(REPO, ['docs', 'legal', 'export'])].label,
		'docs/legal/export'
	);
	assert.equal(
		topo.nodes[dirId(REPO, ['docs', 'legal', 'export'])].parentId,
		islandRootId(REPO)
	);

	// Nine segment-nodes became four places. The count is the whole point of
	// the change (maintainer, 2026-08-29: "not like too flat, as it currently
	// is") and a fold that quietly stopped folding would still pass every
	// assertion above.
	const dirs = Object.values(topo.nodes).filter((n) => n.kind === 'directory');
	assert.equal(dirs.length, 4);
});

test('a fork is a place: folding chains makes the trie branch, it does not flatten it', () => {
	// The rule has two halves and this is the half that is easy to lose: a
	// directory with two or more directory children is a fork in the terrain,
	// and a fork is the structure a map exists to show. Collapse those too and
	// you get a flat list of full paths — legible, and not a place.
	const trails: Record<string, TrailStep[]> = {
		r1: [
			{ dir: 'src/a/deep', act: 'mutate', at: '2026-08-27T10:01:00Z' },
			{ dir: 'src/b/deep', act: 'mutate', at: '2026-08-27T10:02:00Z' }
		]
	};
	const topo = topoFor([liveRun({ run_id: 'r1' })], trails);
	// `src` forks, so it stays — and it did NOT absorb either branch
	assert.ok(topo.nodes[dirId(REPO, ['src'])]);
	assert.equal(topo.nodes[dirId(REPO, ['src'])].label, 'src');
	// each single-child branch below it folds to one node
	assert.equal(topo.nodes[dirId(REPO, ['src', 'a', 'deep'])].label, 'a/deep');
	assert.equal(topo.nodes[dirId(REPO, ['src', 'b', 'deep'])].label, 'b/deep');
	assert.equal(topo.nodes[dirId(REPO, ['src', 'a'])], undefined);
	for (const seg of ['a', 'b']) {
		assert.equal(
			topo.nodes[dirId(REPO, ['src', seg, 'deep'])].parentId,
			dirId(REPO, ['src'])
		);
	}
});

test('an actor standing in a pass-through directory pins it — the fold never moves a hand', () => {
	// The fold runs *after* actor resolution for exactly this reason.
	// `resolveActorPlace` looks a chamber up by `dirId` and falls back to the
	// camp when the node is missing, so folding first would silently demote an
	// actor standing on a scaffolding node — a move nobody made, rendered as a
	// move, which is worse than not drawing it.
	const trails: Record<string, TrailStep[]> = {
		r1: [{ dir: 'src/frontend/src/lib', act: 'mutate', at: '2026-08-27T10:01:00Z' }]
	};
	const graph = compileRoomGraph(
		liveWire([
			liveRun({
				run_id: 'r1',
				edge: {
					at: '2026-08-27T10:03:00Z',
					phase: 'post-tool',
					act: 'orient',
					tools: ['Bash'],
					detail: 'ls',
					out_bytes: 1,
					injected: false,
					dir: 'src/frontend'
				}
			})
		]),
		null,
		trails
	);
	const topo = compileTopology(graph);
	const stood = topo.actorPlaces.r1;
	assert.ok(topo.nodes[stood], 'the actor stands on a node that exists');
	if (stood === dirId(REPO, ['src', 'frontend'])) {
		assert.equal(
			topo.nodes[dirId(REPO, ['src', 'frontend'])].label,
			'src/frontend',
			'pinned by the actor, and it kept its own place rather than folding away'
		);
	}
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
		r1: [
			{ dir: 'src/frontend/tests', act: 'probe', at: '2026-08-27T10:01:00Z' },
			// a second branch, so `src/frontend` is a fork and survives the
			// fold: with one path the whole chain folds to a single node and
			// this test would pass by having nothing structural left to check
			{ dir: 'src/frontend/src', act: 'probe', at: '2026-08-27T10:02:00Z' }
		]
	};
	const graph = compileRoomGraph(liveWire([liveRun({ run_id: 'r1' })]), null, trails);
	const topo = compileTopology(graph);
	// `src/frontend` exists structurally — it forks, so the fold keeps it…
	assert.ok(topo.nodes[dirId(REPO, ['src', 'frontend'])]);
	// …but the graph's chamber list (first-touch data) has only the observed dir
	const chambers = graph.islands[0].camps[0].chambers.map((c) => c.dir);
	assert.deepEqual(chambers, ['src/frontend/tests', 'src/frontend/src']);
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
	// THE PROPERTY: up to the lowest common ancestor, then down. The LCA is
	// still `src/frontend` — a fork, so the fold keeps it — and the walk is
	// one hop shorter because `src/frontend/src` was a single-child chain and
	// folded into `src/lib`. A shorter route through the *same* ancestor is
	// the fold working; a route that skipped the ancestor would be a bug.
	assert.deepEqual(route, [
		dirId(REPO, ['src', 'frontend', 'src', 'lib']),
		dirId(REPO, ['src', 'frontend']),
		dirId(REPO, ['src', 'frontend', 'tests'])
	]);
	assert.ok(
		route!.includes(dirId(REPO, ['src', 'frontend'])),
		'the LCA is on the path — the fold shortens a route, it never bypasses one'
	);
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
