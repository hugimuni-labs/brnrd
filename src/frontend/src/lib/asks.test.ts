import assert from 'node:assert/strict';
import test from 'node:test';

import {
	AsksAuthError,
	askHandle,
	askInTopics,
	askLabel,
	bucketAsks,
	doneWindow,
	fetchAsks,
	liveAttempt,
	moveFocus,
	splitAlive,
	touchedLabel,
	type AskRow
} from './asks.ts';

const row = (over: Partial<AskRow> = {}): AskRow => ({
	id: 'w-1',
	title: 't',
	type: 'action',
	return: 'in git',
	stage: 'making',
	touched_at: null,
	says: [],
	attempts: [],
	receipt: null,
	topics: [],
	stale: false,
	done: false,
	after: null,
	...over
});

test('touchedLabel is compact and empty when unknown', () => {
	const now = Date.parse('2026-09-22T00:00:00Z');
	assert.equal(touchedLabel(null, now), '');
	assert.equal(touchedLabel('nonsense', now), '');
	assert.equal(touchedLabel('2026-09-22T00:00:00Z', now), 'now');
	assert.equal(touchedLabel('2026-09-21T23:55:00Z', now), '5m');
	assert.equal(touchedLabel('2026-09-21T21:00:00Z', now), '3h');
	assert.equal(touchedLabel('2026-09-18T00:00:00Z', now), '4d');
	assert.equal(touchedLabel('2026-06-01T00:00:00Z', now), '16w');
});

test('splitAlive keeps order within each band', () => {
	const { alive, stale } = splitAlive([
		row({ id: 'a' }),
		row({ id: 'b', stale: true }),
		row({ id: 'c' })
	]);
	assert.deepEqual(
		alive.map((r) => r.id),
		['a', 'c']
	);
	assert.deepEqual(
		stale.map((r) => r.id),
		['b']
	);
});

test('askInTopics: null lights all; topicless rows are never hidden; alias resolves', () => {
	const resolve = (slug: string) => (slug === 'loom' ? 'the-loom' : null);
	assert.equal(askInTopics(row({ topics: ['loom'] }), null, resolve), true);
	assert.equal(askInTopics(row({ topics: [] }), new Set(['x']), resolve), true);
	assert.equal(askInTopics(row({ topics: ['loom'] }), new Set(['the-loom']), resolve), true);
	assert.equal(askInTopics(row({ topics: ['loom'] }), new Set(['other']), resolve), false);
	assert.equal(askInTopics(row({ topics: ['unknown'] }), new Set(['other']), resolve), false);
});

test('bucketAsks: making/delivered/unaddressed sort into their buckets, in order', () => {
	const rows = [
		row({ id: 'a', stage: 'making' }),
		row({ id: 'b', stage: 'delivered' }),
		row({ id: 'c', stage: null }),
		row({ id: 'd', stage: 'heard' }),
		row({ id: 'e', stage: 'understood' }),
		row({ id: 'f', stage: 'shaped' }),
		row({ id: 'g', stage: 'making' })
	];
	const { inHand, toJudge, unaddressed } = bucketAsks(rows);
	assert.deepEqual(
		inHand.map((r) => r.id),
		['a', 'g']
	);
	assert.deepEqual(
		toJudge.map((r) => r.id),
		['b']
	);
	assert.deepEqual(
		unaddressed.map((r) => r.id),
		['c', 'd', 'e', 'f']
	);
});

test('bucketAsks: a stage past the open lifecycle (accepted/reshaped/sprouted) lands in no bucket', () => {
	const { inHand, toJudge, unaddressed } = bucketAsks([row({ id: 'a', stage: 'accepted' })]);
	assert.deepEqual([...inHand, ...toJudge, ...unaddressed], []);
});

test('askLabel/askHandle: the sign wins the chip, the id wins the cell fallback', () => {
	assert.equal(askLabel(row({ id: 'w-45' })), 'w-45');
	assert.equal(askLabel(row({ id: 'w-45', sign: 'anltcs' })), 'w-45 · anltcs');
	assert.equal(askHandle(row({ id: 'w-45' })), 'w-45');
	assert.equal(askHandle(row({ id: 'w-45', sign: 'anltcs' })), 'anltcs');
	// present but empty ⇒ no sign to speak, same as absent
	assert.equal(askLabel(row({ id: 'w-45', sign: '' })), 'w-45');
	assert.equal(askHandle(row({ id: 'w-45', sign: '' })), 'w-45');
});

test('doneWindow: the newest three show, the rest count for the toggle', () => {
	const rows = ['a', 'b', 'c', 'd', 'e'].map((id) => row({ id }));
	const win = doneWindow(rows);
	assert.deepEqual(
		win.visible.map((r) => r.id),
		['a', 'b', 'c']
	);
	assert.equal(win.restCount, 2);
	// nothing hidden when the bucket is already small
	const small = doneWindow(rows.slice(0, 2));
	assert.deepEqual(
		small.visible.map((r) => r.id),
		['a', 'b']
	);
	assert.equal(small.restCount, 0);
});

test('liveAttempt finds the run wearing the ask', () => {
	assert.equal(liveAttempt(row({ attempts: ['r1', 'r2'] }), new Set(['r2'])), 'r2');
	assert.equal(liveAttempt(row({ attempts: ['r1'] }), new Set()), null);
});

test('moveFocus clamps and enters from either end', () => {
	assert.equal(moveFocus(null, 'j', 3), 0);
	assert.equal(moveFocus(null, 'k', 3), 2);
	assert.equal(moveFocus(2, 'j', 3), 2);
	assert.equal(moveFocus(0, 'k', 3), 0);
	assert.equal(moveFocus(1, 'j', 3), 2);
	assert.equal(moveFocus(0, 'j', 0), null);
});

test('fetchAsks: 200 returns the payload, 401 is an auth error, other statuses throw', async () => {
	const stub = (status: number, body: unknown): typeof fetch =>
		(async () => new Response(JSON.stringify(body), { status })) as typeof fetch;
	const payload = { asks: [], done: [], goals: [], stale_after_days: 60 };
	assert.deepEqual(await fetchAsks(stub(200, payload)), payload);
	await assert.rejects(fetchAsks(stub(401, {})), AsksAuthError);
	await assert.rejects(fetchAsks(stub(500, {})), /500/);
});
