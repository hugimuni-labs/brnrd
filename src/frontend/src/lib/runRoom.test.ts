// the-overlay-that-shows-the-room: the where-the-work-happens helpers.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { edgeLine, lifecycleNotice, roomLine, runCourse } from './liveRuns.ts';
import { buildWarpGraph, runWarpAttachments } from './warpGraph.ts';

test('lifecycleNotice names AWAIT with its deadline, and stays silent on plain weaving', () => {
	assert.equal(lifecycleNotice({ lifecycle: 'weaving', await_until: null }), null);
	assert.equal(lifecycleNotice({ lifecycle: null, await_until: null }), null);
	const awaiting = lifecycleNotice({
		lifecycle: 'awaiting',
		await_until: '2026-08-25T19:00:00Z'
	});
	assert.equal(awaiting?.word, 'await');
	assert.equal(awaiting?.tone, 'awaiting');
	// The detail names the resolution contract, not just a clock: any event
	// resolves the wait — that is what separates AWAIT from a stall.
	assert.match(awaiting?.detail ?? '', /resolves on any event/);
	const closing = lifecycleNotice({ lifecycle: 'closing', await_until: null });
	assert.equal(closing?.word, 'closing');
	assert.equal(closing?.tone, 'closing');
});

test('lifecycleNotice AWAIT without a deadline still renders, without one', () => {
	const notice = lifecycleNotice({ lifecycle: 'awaiting', await_until: null });
	assert.equal(notice?.word, 'await');
	assert.doesNotMatch(notice?.detail ?? '', /, or /);
});

test('roomLine composes branch · dir, names the shared checkout for host, and stays null when absent', () => {
	assert.equal(
		roomLine({ env: 'worktree', branch: 'brr/the-room', dir: 'run-x' }),
		'brr/the-room · run-x'
	);
	assert.equal(roomLine({ env: 'host', branch: 'main', dir: null }), 'main · the shared checkout');
	assert.equal(roomLine(null), null);
	assert.equal(roomLine(undefined), null);
	assert.equal(roomLine({ env: null, branch: null, dir: null }), null);
});

test('edgeLine composes act · detail and never fabricates from an empty edge', () => {
	assert.equal(
		edgeLine({
			at: 'x',
			phase: 'post-tool',
			act: 'run',
			tools: ['Bash'],
			detail: 'pytest -q',
			out_bytes: 12,
			injected: false
		}),
		'run · pytest -q'
	);
	assert.equal(
		edgeLine({
			at: 'x',
			phase: 'stop',
			act: null,
			tools: [],
			detail: null,
			out_bytes: null,
			injected: false
		}),
		null
	);
	assert.equal(edgeLine(null), null);
});

test('runCourse reads checkbox rows off the card and points at the first open one', () => {
	const card = [
		'# a run',
		'## Now',
		'working.',
		'## Plan',
		'- [x] read the ask',
		'- [x] backend truth',
		'- [ ] wire the overlay',
		'- [ ] tests'
	].join('\n');
	assert.deepEqual(runCourse(card), { done: 2, total: 4, current: 'wire the overlay' });
	// No checkboxes at all → no course, never 0/0.
	assert.equal(runCourse('# a run\njust prose'), null);
	assert.equal(runCourse(null), null);
});

test('runWarpAttachments joins taken and done items to the run, done outranking taken', () => {
	const files = [
		{
			path: 'surface/warp/w-1.md',
			markdown: '# taken by us\ntype: action\ntaken: run-a run-b\n\nbody'
		},
		{
			path: 'surface/warp/w-2.md',
			markdown: '# resolved by us\ntype: action\ntaken: run-a\ndone: 2026-08-25 run-a\n\nbody'
		},
		{
			path: 'surface/warp/w-3.md',
			markdown: '# someone else\ntype: action\ntaken: run-z\n\nbody'
		}
	];
	const graph = buildWarpGraph(files as never);
	const attached = runWarpAttachments(graph, 'run-a');
	// done first (the receipt), then taken; the item done-by-us appears once.
	assert.deepEqual(
		attached.map((item) => [item.id, item.relation]),
		[
			['w-2', 'done'],
			['w-1', 'taken']
		]
	);
	assert.deepEqual(runWarpAttachments(graph, ''), []);
});
