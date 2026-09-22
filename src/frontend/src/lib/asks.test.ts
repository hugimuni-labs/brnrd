import assert from 'node:assert/strict';
import test from 'node:test';

import {
	AsksAuthError,
	askHandle,
	askInTopics,
	askLabel,
	attemptGlyph,
	bucketAsks,
	deliveryLine,
	doneWindow,
	fetchAsks,
	liveAttempt,
	moveFocus,
	previewNames,
	receiptChips,
	sayText,
	seatNowLine,
	seatRun,
	splitAlive,
	touchedLabel,
	type AskRow,
	type AskSay
} from './asks.ts';
import type { LiveRun } from './liveRuns.ts';

const liveRun = (over: Partial<LiveRun> = {}): LiveRun => ({
	id: 'l1',
	kind: 'daemon',
	stream: 'default',
	label: '',
	name: '',
	run_id: 'run-1',
	repo_label: 'hugimuni-labs/brnrd',
	started_at: null,
	last_seen: null,
	parent_run_id: null,
	is_subspawn: false,
	runner: {},
	phase: null,
	card_text: null,
	card_updated_at: null,
	...over
});

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

test('sayText: the excerpt leads, the event id is only the last resort', () => {
	const say = (over: Partial<AskSay> = {}): AskSay => ({
		event: 'evt-abc',
		at: null,
		excerpt: null,
		...over
	});
	assert.equal(
		sayText(say({ excerpt: 'slick ui to inspect the done things' })),
		'slick ui to inspect the done things'
	);
	assert.equal(sayText(say()), 'evt-abc');
	assert.equal(sayText(say({ excerpt: '' })), 'evt-abc'); // present but empty ⇒ still the floor
});

test("attemptGlyph: the row's first topic stands in, topicless resolves nothing", () => {
	const glyphFor = (topic: string) => (topic === 'the-loom' ? 'ᚠ' : null);
	assert.equal(attemptGlyph(row({ topics: ['the-loom', 'ops'] }), glyphFor), 'ᚠ');
	assert.equal(attemptGlyph(row({ topics: ['ops'] }), glyphFor), null); // resolver misses it
	assert.equal(attemptGlyph(row({ topics: [] }), glyphFor), null); // nothing to resolve
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

test('previewNames: the newest three titles, dot-joined; empty bucket is the empty string', () => {
	const rows = ['a', 'b', 'c', 'd'].map((id) => row({ id, title: `title ${id}` }));
	assert.equal(previewNames(rows), 'title a · title b · title c');
	assert.equal(previewNames(rows.slice(0, 1)), 'title a');
	assert.equal(previewNames([]), '');
});

test('deliveryLine: the newest receipt title wins, else return:, else nothing', () => {
	assert.equal(
		deliveryLine(row({ receipts: [{ ref: '#2082', title: 'ledger step 6a' }], return: 'in git' })),
		'ledger step 6a'
	);
	// a receipt with no title yet ⇒ falls through to return:, never a blank
	assert.equal(
		deliveryLine(row({ receipts: [{ ref: '#2082', title: null }], return: 'in git' })),
		'in git'
	);
	assert.equal(deliveryLine(row({ receipts: null, return: 'in git' })), 'in git');
	assert.equal(deliveryLine(row({ receipts: [], return: null })), null);
});

test('receiptChips: plural receipts win, else the legacy singular field, else none', () => {
	assert.deepEqual(
		receiptChips(
			row({
				receipts: [
					{ ref: '#2082', title: 'ledger step 6a', url: 'https://github.com/x/y/pull/2082' },
					{ ref: '#2081', title: null, url: null }
				]
			})
		),
		[
			{ label: '#2082 · ledger step 6a', url: 'https://github.com/x/y/pull/2082' },
			{ label: '#2081', url: null }
		]
	);
	assert.deepEqual(
		receiptChips(row({ receipts: null, receipt: 'https://github.com/x/y/pull/2080' })),
		[{ label: 'https://github.com/x/y/pull/2080', url: 'https://github.com/x/y/pull/2080' }]
	);
	assert.deepEqual(receiptChips(row({ receipts: null, receipt: 'kb/design-the-ask.md' })), [
		{ label: 'kb/design-the-ask.md', url: null }
	]);
	assert.deepEqual(receiptChips(row({ receipts: null, receipt: null })), []);
	// receipts present but empty ⇒ no fallback to the legacy field — the
	// server said "none", not "nothing new to say"
	assert.deepEqual(receiptChips(row({ receipts: [], receipt: 'kb/design-the-ask.md' })), []);
});

test('seatRun/seatNowLine: the first non-strand live run stands for the seat', () => {
	const strand: LiveRun = { ...liveRun(), id: 's1', run_id: 'run-strand', is_subspawn: true };
	const seat: LiveRun = {
		...liveRun(),
		id: 's2',
		run_id: 'run-seat',
		is_subspawn: false,
		card_text: 'checking the printer vendor renewal\n\nmore detail below'
	};
	assert.equal(seatRun([strand, seat]), seat);
	assert.equal(seatRun([strand]), null);
	assert.equal(seatRun(null), null);
	assert.equal(seatNowLine(seat), 'checking the printer vendor renewal');
	assert.equal(seatNowLine({ card_text: '  ' }), null);
	assert.equal(seatNowLine({ card_text: null }), null);
	assert.equal(seatNowLine(null), null);
});

test('fetchAsks: 200 returns the payload, 401 is an auth error, other statuses throw', async () => {
	const stub = (status: number, body: unknown): typeof fetch =>
		(async () => new Response(JSON.stringify(body), { status })) as typeof fetch;
	const payload = { asks: [], done: [], goals: [], stale_after_days: 60 };
	assert.deepEqual(await fetchAsks(stub(200, payload)), payload);
	await assert.rejects(fetchAsks(stub(401, {})), AsksAuthError);
	await assert.rejects(fetchAsks(stub(500, {})), /500/);
});
