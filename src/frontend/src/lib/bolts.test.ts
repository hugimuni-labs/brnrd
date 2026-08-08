import assert from 'node:assert/strict';
import test from 'node:test';

import {
	BOLT_TAKEN_CAP,
	boltSummonsLabel,
	boltsTakenStorageKey,
	parseBoltState,
	readTakenBolts,
	serializeTakenBolts,
	takeAll,
	takeBolt,
	unackedBolts
} from './bolts.ts';
import type { RunLedgerRow } from './runLedger.ts';

function row(overrides: Partial<RunLedgerRow> = {}): RunLedgerRow {
	return {
		run_id: 'run-1',
		event_id: null,
		started_at: null,
		ended_at: '2026-08-07T22:00:00Z',
		wall_clock_seconds: null,
		runner_shell: null,
		runner_core: null,
		core_expected: null,
		core_mismatch: null,
		substitution_reason: null,
		repo_label: 'Gurio/brr',
		source_system: null,
		name: null,
		external_refs: null,
		parent_run_id: null,
		is_subspawn: null,
		tokens_input: null,
		tokens_output: null,
		tokens_cache_read: null,
		tokens_cache_creation: null,
		context_window_used: null,
		weekly_pct_delta: null,
		five_hour_pct_delta: null,
		usd_subscription_attributed: null,
		usd_credits_equivalent: null,
		estimate_vs_actual: null,
		...overrides
	};
}

test('boltsTakenStorageKey namespaces by account, the publishScope pattern', () => {
	assert.equal(boltsTakenStorageKey('acct-1'), 'brnrd.bolts.taken.acct-1');
});

test('parseBoltState recognises exactly the two data-contract values', () => {
	assert.equal(parseBoltState('accepted'), 'accepted');
	assert.equal(parseBoltState('annotated'), 'annotated');
	assert.equal(parseBoltState(null), null);
	assert.equal(parseBoltState(undefined), null);
	assert.equal(parseBoltState(''), null);
	// A future value this reader doesn't recognise degrades to "no bolt",
	// never an error — the data contract: the writer ships separately.
	assert.equal(parseBoltState('cut'), null);
});

test('readTakenBolts validates on read — garbage in reads as nothing taken', () => {
	assert.deepEqual(readTakenBolts(null), []);
	assert.deepEqual(readTakenBolts(undefined), []);
	assert.deepEqual(readTakenBolts(''), []);
	assert.deepEqual(readTakenBolts('not json'), []);
	assert.deepEqual(readTakenBolts('{"not":"an array"}'), []);
	assert.deepEqual(readTakenBolts('[1, 2, "run-1", "", null]'), ['run-1']);
	assert.deepEqual(readTakenBolts('["run-1","run-2"]'), ['run-1', 'run-2']);
});

test('serializeTakenBolts round-trips and applies the FIFO cap on write', () => {
	assert.equal(serializeTakenBolts(['run-1', 'run-2']), '["run-1","run-2"]');
	const many = Array.from({ length: BOLT_TAKEN_CAP + 10 }, (_, i) => `run-${i}`);
	const written = JSON.parse(serializeTakenBolts(many));
	assert.equal(written.length, BOLT_TAKEN_CAP);
	// The newest ids survive the cap, not the oldest.
	assert.equal(written[written.length - 1], `run-${BOLT_TAKEN_CAP + 9}`);
});

test('takeBolt is idempotent in content and moves a re-take to the front', () => {
	assert.deepEqual(takeBolt([], 'run-1'), ['run-1']);
	assert.deepEqual(takeBolt(['run-1'], 'run-1'), ['run-1']);
	assert.deepEqual(takeBolt(['run-1', 'run-2'], 'run-1'), ['run-2', 'run-1']);
	// An empty id is a no-op — nothing to ack against.
	assert.deepEqual(takeBolt(['run-1'], ''), ['run-1']);
});

test('takeAll acks every id in one write', () => {
	assert.deepEqual(takeAll([], ['run-1', 'run-2']), ['run-1', 'run-2']);
	assert.deepEqual(takeAll(['run-1'], ['run-2', 'run-1']), ['run-2', 'run-1']);
});

test('unackedBolts skips rows with no run id, no bolt field, or an unrecognised bolt value', () => {
	assert.deepEqual(
		unackedBolts([row({ run_id: null, bolt: 'accepted' })], []),
		[],
		'no run id — never addressable'
	);
	assert.deepEqual(
		unackedBolts([row({ bolt: null })], []),
		[],
		'absent bolt — old row, never an error'
	);
	assert.deepEqual(
		unackedBolts([row({ bolt: 'something-future' })], []),
		[],
		'unrecognised value — degrades to no bolt'
	);
});

test("unackedBolts excludes ids already in the viewer's ack store", () => {
	const rows = [row({ run_id: 'run-1', bolt: 'accepted' })];
	assert.deepEqual(unackedBolts(rows, ['run-1']), []);
	assert.deepEqual(unackedBolts(rows, new Set(['run-1'])), []);
	assert.equal(unackedBolts(rows, []).length, 1);
});

test('unackedBolts merges re-reports of the same run by id, newest first', () => {
	const rows = [
		row({ run_id: 'run-1', name: null, ended_at: '2026-08-07T22:00:00Z', bolt: 'accepted' }),
		row({
			run_id: 'run-1',
			name: 'the-bolt',
			ended_at: '2026-08-07T22:05:00Z',
			bolt: 'accepted',
			external_refs: [{ kind: 'commit' }]
		}),
		row({ run_id: 'run-2', ended_at: '2026-08-06T10:00:00Z', bolt: 'annotated' })
	];
	const bolts = unackedBolts(rows, []);
	assert.equal(bolts.length, 2);
	// Newest close first.
	assert.equal(bolts[0].runId, 'run-1');
	assert.equal(bolts[0].name, 'the-bolt');
	assert.equal(bolts[0].named, true);
	assert.equal(bolts[0].relics.length, 1);
	assert.equal(bolts[0].endedAt, Date.parse('2026-08-07T22:05:00Z'));
	assert.equal(bolts[1].runId, 'run-2');
	assert.equal(bolts[1].bolt, 'annotated');
	// A run with no authored name falls back to its id — never invented.
	assert.equal(bolts[1].name, 'run-2');
	assert.equal(bolts[1].named, false);
});

test('unackedBolts skips a row whose ended_at cannot be parsed', () => {
	assert.deepEqual(unackedBolts([row({ ended_at: 'not a date', bolt: 'accepted' })], []), []);
	assert.deepEqual(unackedBolts([row({ ended_at: null, bolt: 'accepted' })], []), []);
});

test('boltSummonsLabel is singular at one, plural otherwise', () => {
	assert.equal(boltSummonsLabel(1), '1 bolt awaits taking');
	assert.equal(boltSummonsLabel(2), '2 bolts await taking');
	assert.equal(boltSummonsLabel(0), '0 bolts await taking');
});
