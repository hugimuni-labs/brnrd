import assert from 'node:assert/strict';
import test from 'node:test';
import { buildWarpGraph } from '../warpGraph.ts';
import type { LiveRun } from '../liveRuns.ts';
import { dailyBuoys, dailyIslands, dailyLiveBars, knowledgePageCount } from './daily.ts';

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
		{ path: 'surface/warp/w-2.md', markdown: '# act\ntype: action\nneeds: w-3\n' },
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
