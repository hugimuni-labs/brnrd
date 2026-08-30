import assert from 'node:assert/strict';
import test from 'node:test';
import { buildWarpGraph } from '../warpGraph.ts';
import type { LiveRun } from '../liveRuns.ts';
import {
	dailyBuoys,
	dailyIslands,
	dailyItemState,
	dailyLiveBars,
	hashItemId,
	knowledgePageCount,
	surfaceBuoys
} from './daily.ts';

const run = (id: string, parent: string | null = null): LiveRun =>
	({
		id,
		run_id: id,
		parent_run_id: parent,
		is_subspawn: parent !== null,
		name: id,
		label: '',
		stream: '',
		kind: 'daemon',
		repo_label: 'org/repo',
		started_at: null,
		last_seen: null,
		runner: {},
		phase: 'running',
		card_text: null,
		card_updated_at: null,
		course: { done: 1, total: 3, current: 'build' },
		portals: { pending: 2, oldest_at: null },
		room: { env: 'worktree', branch: `brr/${id}`, dir: id }
	}) as LiveRun;

test('daily live bars nest strands beneath their live parent', () => {
	const bars = dailyLiveBars([run('child', 'parent'), run('parent')]);
	assert.deepEqual(
		bars.map((bar) => [bar.name, bar.depth, bar.course, bar.pending]),
		[
			['parent', 0, '1/3', 2],
			['child', 1, '1/3', 2]
		]
	);
});

test('daily buoys exclude blocked, completed, and goal items', () => {
	const graph = buildWarpGraph([
		{ path: 'surface/warp/w-1.md', markdown: '# decide\ntype: decision\n' },
		{ path: 'surface/warp/w-2.md', markdown: '# act\ntype: action\nneeds: w-1\n' },
		{ path: 'surface/warp/w-3.md', markdown: '# done\ntype: action\ndone: 2026-08-30\n' },
		{ path: 'surface/warp/g-1.md', markdown: '# goal\ntype: goal\n' }
	]);
	assert.deepEqual(
		dailyBuoys(graph).map((buoy) => [buoy.item.id, buoy.mark]),
		[['w-1', '◇']]
	);
});

test('daily islands render only branch facts carried by live and cloth wires', () => {
	const islands = dailyIslands(
		[run('one')],
		[
			{
				repo_label: 'org/repo',
				external_refs: [
					{ kind: 'branch', name: 'brr/cut' },
					{ kind: 'pr', number: 12 }
				]
			} as never
		]
	);
	assert.deepEqual(islands[0].branches, [
		{ name: 'brr/one', pr: null, live: true },
		{ name: 'brr/cut', pr: 12, live: false }
	]);
	assert.equal(knowledgePageCount([{ layer: 'authored' }]), null);
	assert.equal(knowledgePageCount([{ layer: 'knowledge' }, { layer: 'knowledge' }]), 2);
});

test('surface buoys cap the field, calls first, remainder counted', () => {
	const files = [
		{ path: 'surface/warp/w-1.md', markdown: '# a1\ntype: action\n' },
		{ path: 'surface/warp/w-2.md', markdown: '# call\ntype: decision\n' },
		{ path: 'surface/warp/w-3.md', markdown: '# a2\ntype: action\n' },
		{ path: 'surface/warp/w-4.md', markdown: '# prep\ntype: preparation\n' }
	];
	const buoys = dailyBuoys(buildWarpGraph(files));
	const field = surfaceBuoys(buoys, 3);
	assert.equal(field.shown.length, 3);
	assert.equal(field.hidden, 1);
	assert.deepEqual(
		field.shown.slice(0, 2).map((b) => b.item.type),
		['decision', 'preparation']
	);
});

test('surface buoys with room to spare hide nothing', () => {
	const files = [{ path: 'surface/warp/w-1.md', markdown: '# solo\ntype: action\n' }];
	const field = surfaceBuoys(dailyBuoys(buildWarpGraph(files)));
	assert.equal(field.shown.length, 1);
	assert.equal(field.hidden, 0);
});

test('hash item id strips the leading # and blanks to null', () => {
	assert.equal(hashItemId('#w-47'), 'w-47');
	assert.equal(hashItemId('w-47'), 'w-47');
	assert.equal(hashItemId('#'), null);
	assert.equal(hashItemId(''), null);
});

test('daily item state ranks blocked over taken over ready, and reads done/retired off the lifecycle', () => {
	const graph = buildWarpGraph([
		{ path: 'surface/warp/w-1.md', markdown: '# decide\ntype: decision\n' },
		{ path: 'surface/warp/w-2.md', markdown: '# blocked\ntype: action\nneeds: w-1\n' },
		{ path: 'surface/warp/w-3.md', markdown: '# taken\ntype: action\ntaken: run-1\n' },
		{ path: 'surface/warp/w-4.md', markdown: '# ready\ntype: action\n' },
		{ path: 'surface/warp/w-5.md', markdown: '# shipped\ntype: action\ndone: 2026-08-30\n' },
		{
			path: 'surface/warp/w-6.md',
			markdown: '# dropped\ntype: action\nretired: 2026-08-30 no longer needed\n'
		}
	]);
	const state = (id: string) => dailyItemState(graph.itemById.get(id)!, graph);
	assert.equal(state('w-1'), 'ready');
	assert.equal(state('w-2'), 'blocked');
	assert.equal(state('w-3'), 'taken');
	assert.equal(state('w-4'), 'ready');
	assert.equal(state('w-5'), 'done');
	assert.equal(state('w-6'), 'retired');
});
