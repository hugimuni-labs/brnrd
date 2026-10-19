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
	liveStrand,
	originLabel,
	planFraction,
	postLetter,
	refHref,
	resetsIn,
	shellLow,
	snapshotAge,
	sortThreads,
	sortWishes,
	sortedLog,
	tryHref,
	verdictOwed,
	askOwed,
	type LoomSnapshot,
	type LoomThread,
	type LoomWish
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

test('liveStrand: working or holding is live; idle, parked and absent threads are not', () => {
	const threads: LoomThread[] = [
		{
			id: 'a',
			strands: [
				{ id: 's1', state: 'molted' },
				{ id: 's2', body: 'claude/opus', state: 'holding' }
			]
		},
		{ id: 'b', strands: [{ id: 's3', body: 'claude/sonnet', state: 'working' }] },
		{
			id: 'c',
			strands: [
				{ id: 's4', body: 'claude/opus', state: 'parked' },
				{ id: 's5', state: 'idle' }
			]
		}
	];
	assert.equal(liveStrand(threads, 'a')?.id, 's2');
	assert.equal(liveStrand(threads, 'b')?.body, 'claude/sonnet');
	assert.equal(liveStrand(threads, 'c'), null);
	assert.equal(liveStrand(threads, 'inbox'), null);
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

test('postLetter: a verdict carries wish and verdict together, never one alone', async () => {
	const sent: unknown[] = [];
	const fetchImpl = (async (_url: string, init: RequestInit) => {
		sent.push(JSON.parse(String(init.body)));
		return new Response(JSON.stringify({ ok: true }), { status: 202 });
	}) as typeof fetch;
	await postLetter(
		{ thread: 'pricing-page', text: 'agree', wish: 'w-1', verdict: 'agree' },
		fetchImpl
	);
	await postLetter({ thread: 'inbox', text: 'half', wish: 'w-1' }, fetchImpl);
	await postLetter({ thread: 'inbox', text: 'half', verdict: 'correct' }, fetchImpl);
	assert.deepEqual(sent, [
		{ thread: 'pricing-page', text: 'agree', wish: 'w-1', verdict: 'agree' },
		{ thread: 'inbox', text: 'half' },
		{ thread: 'inbox', text: 'half' }
	]);
});

const wish = (over: Partial<LoomWish>): LoomWish => ({ id: 'w', ...over });

test('sortWishes: open first, then newest result, else newest wish', () => {
	const sorted = sortWishes([
		wish({ id: 'accepted', state: 'accepted', at: '2026-10-19T00:50:00Z' }),
		wish({ id: 'old', state: 'heard', at: '2026-10-19T00:01:00Z' }),
		wish({
			id: 'result-new',
			state: 'delivered',
			at: '2026-10-18T00:00:00Z',
			results: [{ at: '2026-10-19T00:40:00Z' }, { at: '2026-10-18T01:00:00Z' }]
		}),
		wish({ id: 'dropped', state: 'dropped', at: '2026-10-19T00:30:00Z' }),
		wish({ id: 'new', state: 'granting', at: '2026-10-19T00:20:00Z' })
	]);
	assert.deepEqual(
		sorted.map((w) => w.id),
		['result-new', 'new', 'old', 'accepted', 'dropped']
	);
});

test('verdictOwed: open and delivered with no answer; askOwed: an unanswered resident ask', () => {
	assert.equal(verdictOwed(wish({ state: 'delivered' })), true);
	assert.equal(verdictOwed(wish({ state: 'granting' })), false);
	assert.equal(verdictOwed(wish({ state: 'accepted' })), false);
	assert.equal(verdictOwed(wish({ state: 'delivered', answer: { verdict: 'agree' } })), false);
	assert.equal(askOwed(wish({ from: 'resident', state: 'scoped' })), true);
	assert.equal(askOwed(wish({ from: 'resident', state: 'scoped', answer: {} })), false);
	assert.equal(askOwed(wish({ from: 'person', state: 'scoped' })), false);
	// a delivered ask counts once, as a verdict
	assert.equal(askOwed(wish({ from: 'resident', state: 'delivered' })), false);
});

test('tryHref: brnrd.dev or a ui URL; refHref: any http(s) ref', () => {
	assert.equal(
		tryHref({ kind: 'pr', ref: 'https://brnrd.dev/thread/x' }),
		'https://brnrd.dev/thread/x'
	);
	assert.equal(tryHref({ ref: 'https://preview.brnrd.dev/' }), 'https://preview.brnrd.dev/');
	assert.equal(tryHref({ kind: 'ui', ref: 'http://localhost:5173/' }), 'http://localhost:5173/');
	const pr = { kind: 'pr', ref: 'https://github.com/hugimuni-labs/brnrd/pull/2347' };
	assert.equal(tryHref(pr), null);
	assert.equal(refHref(pr), pr.ref);
	// look-alike hosts and plain http are not brnrd.dev
	assert.equal(tryHref({ ref: 'https://brnrd.dev.evil.example/' }), null);
	assert.equal(tryHref({ ref: 'http://brnrd.dev/' }), null);
	assert.equal(tryHref({ kind: 'ui', ref: 'javascript:alert(1)' }), null);
	assert.equal(refHref({ kind: 'pr', ref: '#2347' }), null);
	assert.equal(refHref({}), null);
});
