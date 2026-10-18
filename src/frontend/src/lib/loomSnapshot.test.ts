import assert from 'node:assert/strict';
import test from 'node:test';

import {
	LoomAuthError,
	ago,
	bodyChoices,
	defaultBody,
	fetchLoom,
	filterCounts,
	fuelPromoted,
	isUnread,
	latestReceipt,
	matchesFilter,
	newestAt,
	pickedBody,
	originLabel,
	planFraction,
	postLetter,
	resetsIn,
	shellLow,
	snapshotAge,
	sortThreads,
	sortedLog,
	type LoomSnapshot,
	type LoomThread
} from './loomSnapshot.ts';

const answer = (status: number, body?: unknown) =>
	(async () =>
		new Response(body === undefined ? null : JSON.stringify(body), {
			status
		})) as unknown as typeof fetch;

test('fetchLoom: 401 is signed out, 404/204/200 {} are no snapshot', async () => {
	assert.equal(await fetchLoom(answer(401)), 'anon');
	assert.equal(await fetchLoom(answer(404)), 'none');
	assert.equal(await fetchLoom(answer(204)), 'none');
	assert.equal(await fetchLoom(answer(200, {})), 'none');
	assert.equal(await fetchLoom(answer(200, { v: 2, threads: [] })), 'none');
	const snap = await fetchLoom(answer(200, { v: 1, stale_s: 4, threads: [] }));
	assert.equal(typeof snap, 'object');
	assert.equal((snap as LoomSnapshot).stale_s, 4);
	await assert.rejects(fetchLoom(answer(502)), /loom fetch failed: 502/);
});

test('postLetter: sends body only when chosen; 202 is queued; 401 throws', async () => {
	const sent: unknown[] = [];
	const capture = (async (_url: string, init: RequestInit) => {
		sent.push(JSON.parse(String(init.body)));
		return new Response(JSON.stringify({ ok: true, event_id: 'evt-1' }), { status: 202 });
	}) as unknown as typeof fetch;
	const queued = await postLetter({ thread: 'inbox', text: 'hi' }, capture);
	assert.equal(queued.event_id, 'evt-1');
	await postLetter({ thread: 'inbox', text: 'hi', body: 'claude/opus' }, capture);
	assert.deepEqual(sent, [
		{ thread: 'inbox', text: 'hi' },
		{ thread: 'inbox', text: 'hi', body: 'claude/opus' }
	]);
	await assert.rejects(postLetter({ thread: 'inbox', text: 'x' }, answer(401)), LoomAuthError);
});

const thread = (over: Partial<LoomThread>): LoomThread => ({ id: 't', ...over });

test('sortThreads: yours first, then mine, blocked, none; settled last; newest within', () => {
	const sorted = sortThreads([
		thread({ id: 'none', turn: 'none', last_at: '2026-10-19T00:10:00Z' }),
		thread({ id: 'settled', turn: 'yours', status: 'settled', last_at: '2026-10-19T00:30:00Z' }),
		thread({ id: 'mine-old', turn: 'mine', last_at: '2026-10-19T00:01:00Z' }),
		thread({ id: 'blocked', turn: 'blocked' }),
		thread({ id: 'mine-new', turn: 'mine', last_at: '2026-10-19T00:20:00Z' }),
		thread({ id: 'yours', turn: 'yours', last_at: '2026-10-18T00:00:00Z' })
	]);
	assert.deepEqual(
		sorted.map((t) => t.id),
		['yours', 'mine-new', 'mine-old', 'blocked', 'none', 'settled']
	);
});

test('filters: yours, working, delivered and their counts', () => {
	const threads = [
		thread({ id: 'a', turn: 'yours' }),
		thread({ id: 'b', strands: [{ id: 's1', state: 'working' }] }),
		thread({ id: 'c', strands: [{ id: 's2', state: 'idle' }], done: [{ kind: 'pr', ref: 'u' }] })
	];
	assert.equal(matchesFilter(threads[0], 'yours'), true);
	assert.equal(matchesFilter(threads[2], 'working'), false);
	assert.deepEqual(filterCounts(threads), { all: 3, yours: 1, working: 1, delivered: 1 });
});

test('originLabel, planFraction, latestReceipt', () => {
	assert.equal(originLabel('person:arseni'), 'by you');
	assert.equal(originLabel('thread:inbox'), 'by inbox');
	assert.equal(originLabel('loom'), 'by loom');
	assert.equal(originLabel(undefined), null);
	assert.equal(planFraction(thread({ plan: { done: 6, total: 8 } })), 0.75);
	// absent stays absent: no plan is not an empty plan
	assert.equal(planFraction(thread({})), null);
	assert.equal(planFraction(thread({ plan: { done: 0, total: 0 } })), null);
	const receipt = latestReceipt(
		thread({ done: [{ line: 'a', ref: 'u1' }, { line: 'b', ref: 'u2' }, { line: 'no ref' }] })
	);
	assert.equal(receipt?.ref, 'u2');
});

test('fuel: low at ≤15% left or dry, never from a stale reading; promoted when any shell is low', () => {
	const healthy = { shell: 'claude', gauges: [{ window: 'session', left_pct: 82 }] };
	const low = { shell: 'codex', gauges: [{ window: 'week', left_pct: 15 }] };
	const parked = { shell: 'grok', state: 'dry', gauges: [{ window: 'week', left_pct: 90 }] };
	const stale = { shell: 'codex', state: 'stale', gauges: [{ window: 'session', left_pct: 3 }] };
	assert.equal(shellLow(healthy), false);
	assert.equal(shellLow(low), true);
	assert.equal(shellLow(parked), true);
	assert.equal(shellLow(stale), false);
	// an absent gauge reading is unknown, not empty
	assert.equal(shellLow({ shell: 'x', gauges: [{ window: 'week' }] }), false);
	assert.equal(fuelPromoted([healthy]), false);
	assert.equal(fuelPromoted([healthy, low]), true);
	assert.equal(fuelPromoted(undefined), false);
});

test('bodies: declared choices win; else shell/core; default falls back to presence', () => {
	const snap: LoomSnapshot = {
		v: 1,
		fuel: [{ shell: 'claude', cores: ['opus', 'sonnet'] }],
		presence: { body: 'claude/opus' }
	};
	assert.deepEqual(bodyChoices(snap), ['claude/opus', 'claude/sonnet']);
	assert.equal(defaultBody(snap), 'claude/opus');
	snap.bodies = { default: 'claude/sonnet', choices: ['claude/sonnet'] };
	assert.deepEqual(bodyChoices(snap), ['claude/sonnet']);
	assert.equal(defaultBody(snap), 'claude/sonnet');
});

test('pickedBody: only a kept pick the loom still offers; nothing picked is not the default', () => {
	const snap: LoomSnapshot = {
		v: 1,
		bodies: { default: 'claude/opus', choices: ['claude/opus', 'codex/gpt-6.1-sol'] }
	};
	assert.equal(pickedBody(snap, null), null);
	assert.equal(pickedBody(snap, 'codex/gpt-6.1-sol'), 'codex/gpt-6.1-sol');
	assert.equal(pickedBody(snap, 'grok/grok-4.7'), null);
});

test('postLetter: no body picked sends no body field', async () => {
	const sent: unknown[] = [];
	const fetchImpl = (async (_url: string, init: RequestInit) => {
		sent.push(JSON.parse(String(init.body)));
		return new Response(JSON.stringify({ ok: true }), { status: 202 });
	}) as typeof fetch;
	await postLetter({ thread: 'inbox', text: 'hi', body: undefined }, fetchImpl);
	assert.deepEqual(sent, [{ thread: 'inbox', text: 'hi' }]);
});

test('time: ago, resetsIn, snapshotAge', () => {
	const now = Date.parse('2026-10-19T12:00:00Z');
	assert.equal(ago('2026-10-19T11:59:50Z', now), 'now');
	assert.equal(ago('2026-10-19T11:56:00Z', now), '4m ago');
	assert.equal(ago('2026-10-19T09:46:00Z', now), '2h 14m ago');
	assert.equal(ago('2026-10-16T12:00:00Z', now), '3d ago');
	assert.equal(ago('garbage', now), null);
	assert.equal(resetsIn('2026-10-19T14:14:00Z', now), 'in 2h 14m');
	assert.equal(resetsIn('2026-10-19T11:00:00Z', now), 'now');
	assert.equal(resetsIn(undefined, now), null);
	assert.equal(snapshotAge(30), null);
	assert.equal(snapshotAge(600), '10m');
});

test('log: newest first, filtered by thread; unread is newer than seen', () => {
	const log = [
		{ id: 'a', at: '2026-10-19T00:01:00Z', thread: 'inbox' },
		{ id: 'b', at: '2026-10-19T00:03:00Z', thread: 'x' },
		{ id: 'c', at: '2026-10-19T00:02:00Z', thread: 'inbox' }
	];
	assert.deepEqual(
		sortedLog(log).map((e) => e.id),
		['b', 'c', 'a']
	);
	assert.deepEqual(
		sortedLog(log, 'inbox').map((e) => e.id),
		['c', 'a']
	);
	assert.equal(isUnread(log[1], '2026-10-19T00:02:00Z'), true);
	assert.equal(isUnread(log[2], '2026-10-19T00:02:00Z'), false);
	// first visit: nothing is unread
	assert.equal(isUnread(log[1], null), false);
});

test('newestAt: the latest of snapshot.at and the log', () => {
	assert.equal(
		newestAt({
			v: 1,
			at: '2026-10-19T00:05:00Z',
			log: [
				{ id: 'a', at: '2026-10-19T00:07:00Z' },
				{ id: 'b', at: 'nope' }
			]
		}),
		'2026-10-19T00:07:00Z'
	);
	assert.equal(newestAt({ v: 1 }), null);
});
