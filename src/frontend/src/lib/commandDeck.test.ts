import assert from 'node:assert/strict';
import test from 'node:test';
import { commandQueue, missionRun, parseDeckCommand, unlockCount } from './commandDeck.ts';
import { buildWarpGraph } from './warpGraph.ts';
import { referenceFrames } from './referenceTrace.ts';

test('decision queue respects dependencies and live ownership without hiding abandoned assignments', () => {
	const runs = referenceFrames()[1];
	const graph = buildWarpGraph([
		{ path: 'surface/warp/w-1.md', markdown: '# Sign the scope\n\ntype: decision\n' },
		{ path: 'surface/warp/w-2.md', markdown: '# Build the scope\n\ntype: action\nneeds: w-1\n' },
		{ path: 'surface/warp/w-3.md', markdown: '# Blocked choice\n\ntype: decision\nneeds: w-2\n' },
		{
			path: 'surface/warp/w-4.md',
			markdown: '# Abandoned prep\n\ntype: preparation\ntaken: old-run\n'
		},
		{
			path: 'surface/warp/w-5.md',
			markdown: `# Active prep\n\ntype: preparation\ntaken: ${runs[0].run_id}\n`
		},
		{ path: 'surface/warp/g-1.md', markdown: '# Goal\n\ntype: goal\n' }
	]);
	assert.deepEqual(
		commandQueue(graph, runs).map((item) => item.id),
		['w-1', 'w-4']
	);
	assert.equal(unlockCount('w-1', graph), 1);
});

test('crew selection falls back when a selected worker disappears', () => {
	const runs = referenceFrames().find((frame) => frame.length > 1)!;
	const worker = runs.find((run) => run.is_subspawn)!;
	assert.equal(missionRun(runs, worker.id)?.id, worker.id);
	assert.equal(
		missionRun(
			runs.filter((run) => run.id !== worker.id),
			worker.id
		)?.is_subspawn,
		false
	);
	assert.equal(missionRun([], null), null);
});

test('command grammar names only implemented navigation and rejects accidental execution', () => {
	assert.deepEqual(parseDeckCommand(' OPEN w-42 '), { kind: 'open', id: 'w-42' });
	assert.deepEqual(parseDeckCommand('crew 2'), { kind: 'crew', index: 1 });
	assert.deepEqual(parseDeckCommand('log'), { kind: 'section', section: 'history' });
	for (const input of [
		'crew 0',
		'crew -1',
		'north',
		'merge main',
		'open w-1; rm',
		'constructor',
		'toString'
	]) {
		assert.equal(parseDeckCommand(input).kind, 'unknown');
	}
});
