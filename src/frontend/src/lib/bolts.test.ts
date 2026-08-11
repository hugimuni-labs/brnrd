import assert from 'node:assert/strict';
import test from 'node:test';

import {
	BOLT_TAKEN_CAP,
	boltCardDataFromLedgerRows,
	boltCardSections,
	boltSummonsLabel,
	boltVerdictLabel,
	boltsTakenStorageKey,
	parseBoltDeclaration,
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

test('unackedBolts carries measured spend through, latest non-null value wins', () => {
	const rows = [
		row({
			run_id: 'run-1',
			bolt: 'accepted',
			ended_at: '2026-08-07T22:00:00Z',
			wall_clock_seconds: 120,
			tokens_input: 100,
			usd_subscription_attributed: 1.5
		}),
		row({
			run_id: 'run-1',
			bolt: 'accepted',
			ended_at: '2026-08-07T22:05:00Z',
			// This re-report leaves wall_clock_seconds/usd null — must not
			// blank out the earlier measurement.
			tokens_input: 140,
			tokens_output: 60
		})
	];
	const [bolt] = unackedBolts(rows, []);
	assert.equal(bolt.wallClockSeconds, 120);
	assert.equal(bolt.tokensInput, 140);
	assert.equal(bolt.tokensOutput, 60);
	assert.equal(bolt.usdSubscriptionAttributed, 1.5);
	assert.equal(bolt.usdCreditsEquivalent, null);
});

test('boltVerdictLabel names the daemon dissent case, not just the raw value', () => {
	assert.equal(boltVerdictLabel('accepted'), 'accepted');
	assert.equal(boltVerdictLabel('annotated'), 'accepted — with dissent');
});

test('boltCardSections: asks/decisions/owed are unconditionally absent — no wire carries them', () => {
	const sections = boltCardSections({
		relics: [],
		wallClockSeconds: null,
		tokensInput: null,
		tokensOutput: null,
		usdSubscriptionAttributed: null,
		usdCreditsEquivalent: null
	});
	assert.equal(sections.asks, 'absent');
	assert.equal(sections.decisions, 'absent');
	assert.equal(sections.owed, 'absent');
});

test('boltCardSections: produce and spend distinguish empty (real, honest) from absent (unlabeled)', () => {
	const nothing = boltCardSections({
		relics: [],
		wallClockSeconds: null,
		tokensInput: null,
		tokensOutput: null,
		usdSubscriptionAttributed: null,
		usdCreditsEquivalent: null
	});
	assert.equal(nothing.produce, 'empty');
	assert.equal(nothing.spend, 'empty');

	const something = boltCardSections({
		relics: [{ kind: 'commit' }],
		wallClockSeconds: 90,
		tokensInput: null,
		tokensOutput: null,
		usdSubscriptionAttributed: null,
		usdCreditsEquivalent: null
	});
	assert.equal(something.produce, 'present');
	assert.equal(something.spend, 'present');
});

// #1259: an audit/design run whose produce is tickets + kb, never a commit,
// must not read as "nothing produced this run" — `produce` gates on
// `relics.length`, not on any commit/branch/PR-shaped subset, so an
// issue-only or kb-only manifest already renders 'present'. Pinned here so
// a future refactor that narrows the gate to commit-shaped kinds gets
// caught the same wake it lands.
test('boltCardSections: an issue/kb-only manifest still reads as produce present (#1259)', () => {
	const ticketsAndKb = boltCardSections({
		relics: [
			{ kind: 'issue', number: 1238, action: 'opened' },
			{ kind: 'kb', path: 'kb/trace.md' }
		],
		wallClockSeconds: null,
		tokensInput: null,
		tokensOutput: null,
		usdSubscriptionAttributed: null,
		usdCreditsEquivalent: null
	});
	assert.equal(ticketsAndKb.produce, 'present');
});

test('boltCardDataFromLedgerRows returns null when no row carries a recognised bolt', () => {
	assert.equal(boltCardDataFromLedgerRows([]), null);
	assert.equal(boltCardDataFromLedgerRows([row({ bolt: null })]), null);
	assert.equal(boltCardDataFromLedgerRows([row({ bolt: 'something-future' })]), null);
});

test('boltCardDataFromLedgerRows merges every row for one run — relics accumulate, spend/bolt latest-wins', () => {
	const rows = [
		row({ bolt: 'accepted', external_refs: [{ kind: 'commit' }], wall_clock_seconds: 60 }),
		row({ bolt: 'annotated', external_refs: [{ kind: 'pr' }], usd_subscription_attributed: 2 })
	];
	const data = boltCardDataFromLedgerRows(rows);
	assert.ok(data);
	// The later row's bolt state wins — a re-report reflects the run's
	// current disposition, not its first.
	assert.equal(data?.bolt, 'annotated');
	assert.deepEqual(
		data?.relics.map((r) => r.kind),
		['commit', 'pr']
	);
	// Spend not reset by the second row's nulls.
	assert.equal(data?.wallClockSeconds, 60);
	assert.equal(data?.usdSubscriptionAttributed, 2);
});

// ── #1236/#1255: the declaration column, threaded through ─────────────────
// The two tests above (`boltCardSections: asks/decisions/owed are
// unconditionally absent`) still pin the old contract for the zero-argument
// call every pre-#1236 call site makes — left untouched on purpose. These
// pin the new one-argument shape the wire now actually carries.

test('parseBoltDeclaration reads null/malformed as null — same degrade-not-throw contract as parseBoltState', () => {
	assert.equal(parseBoltDeclaration(null), null);
	assert.equal(parseBoltDeclaration(undefined), null);
	assert.equal(parseBoltDeclaration('not an object'), null);
	assert.equal(parseBoltDeclaration([1, 2, 3]), null);
});

test('parseBoltDeclaration reads a bare record with no recognised fields as an empty declaration, not null', () => {
	// `run_ledger.bolt_declaration_value` always sends every key; an object
	// this bare is unreachable on the real wire, but the parser still owes
	// it an honest reading rather than a thrown error — every array defaults
	// closed, never invented content.
	assert.deepEqual(parseBoltDeclaration({}), {
		asks: [],
		owed: [],
		decisions: [],
		spendDeclared: null,
		next: null,
		dissent: []
	});
});

test('parseBoltDeclaration reads the {omitted} marker as its own state, not a plain declaration', () => {
	const parsed = parseBoltDeclaration({
		omitted: true,
		reason: 'persistence row cap exceeded: asks'
	});
	assert.deepEqual(parsed, { omitted: true, reason: 'persistence row cap exceeded: asks' });
	// A missing/blank reason still reads as omitted, never crashes.
	assert.deepEqual(parseBoltDeclaration({ omitted: true }), {
		omitted: true,
		reason: 'persistence limits exceeded'
	});
});

test('parseBoltDeclaration parses a full declaration and filters malformed rows rather than throwing', () => {
	const parsed = parseBoltDeclaration({
		asks: [
			{ event: 'evt-1', disposition: 'answered' },
			{ event: 'evt-2', disposition: 'deferred:schedule.md', label: 'the notes repair' },
			{ disposition: 'answered' }, // no event — dropped
			'not a row' // wrong shape — dropped
		],
		owed: [
			{ label: 'the-thing', ref: 'promise-1', why: 'ran out of budget' },
			{ ref: 'promise-2' } // no why — dropped
		],
		decisions: ['"notify.gate stopgap": extended', 3, null],
		spend_declared: '~$12, 56m',
		next: 'none',
		dissent: ['evt-…-4e17 undispositioned']
	});
	assert.ok(parsed && !('omitted' in parsed));
	assert.equal(parsed?.asks.length, 2);
	assert.equal(parsed?.asks[1].label, 'the notes repair');
	assert.equal(parsed?.owed.length, 1);
	assert.equal(parsed?.owed[0].label, 'the-thing');
	// Non-string decisions entries are dropped, not stringified — the wire
	// only ever sends strings here (`cut_verb._parse_decisions`).
	assert.deepEqual(parsed?.decisions, ['"notify.gate stopgap": extended']);
	assert.equal(parsed?.spendDeclared, '~$12, 56m');
	assert.equal(parsed?.next, 'none');
	assert.deepEqual(parsed?.dissent, ['evt-…-4e17 undispositioned']);
});

test('boltCardSections: a declaration argument turns asks/decisions/owed/spendDeclared data-driven', () => {
	const declared = parseBoltDeclaration({
		asks: [{ event: 'evt-1', disposition: 'answered' }],
		owed: [],
		decisions: ['kept'],
		spend_declared: null,
		dissent: []
	});
	const sections = boltCardSections(
		{
			relics: [],
			wallClockSeconds: null,
			tokensInput: null,
			tokensOutput: null,
			usdSubscriptionAttributed: null,
			usdCreditsEquivalent: null
		},
		declared
	);
	assert.equal(sections.asks, 'present');
	assert.equal(sections.decisions, 'present');
	// Declared, but the row carries zero owed rows and no spend estimate —
	// 'empty' is the honest state, not 'absent'.
	assert.equal(sections.owed, 'empty');
	assert.equal(sections.spendDeclared, 'empty');
});

test('boltCardSections: the {omitted} marker reports its own state on every declaration-backed section', () => {
	const omitted = parseBoltDeclaration({
		omitted: true,
		reason: 'persistence JSON byte cap exceeded'
	});
	const sections = boltCardSections(
		{
			relics: [],
			wallClockSeconds: null,
			tokensInput: null,
			tokensOutput: null,
			usdSubscriptionAttributed: null,
			usdCreditsEquivalent: null
		},
		omitted
	);
	assert.equal(sections.asks, 'omitted');
	assert.equal(sections.decisions, 'omitted');
	assert.equal(sections.owed, 'omitted');
	assert.equal(sections.spendDeclared, 'omitted');
});

test('unackedBolts and boltCardDataFromLedgerRows carry the declaration through, latest-cut-wins', () => {
	const declarationA = { asks: [{ event: 'evt-1', disposition: 'answered' }] };
	const declarationB = { asks: [], owed: [{ ref: 'p-1', why: 'later' }] };
	const rows = [
		row({ run_id: 'run-1', bolt: 'accepted', bolt_declaration: declarationA }),
		row({
			run_id: 'run-1',
			bolt: 'annotated',
			ended_at: '2026-08-07T22:05:00Z',
			bolt_declaration: declarationB
		})
	];
	const [unacked] = unackedBolts(rows, []);
	assert.ok(unacked.declaration && !('omitted' in unacked.declaration));
	assert.equal(unacked.declaration?.owed[0]?.ref, 'p-1');

	const card = boltCardDataFromLedgerRows(rows);
	assert.ok(card?.declaration && !('omitted' in card.declaration));
	assert.equal(card?.declaration?.owed[0]?.ref, 'p-1');
});

test('unackedBolts and boltCardDataFromLedgerRows leave declaration null when the row never carried one', () => {
	const rows = [row({ run_id: 'run-1', bolt: 'accepted' })];
	const [unacked] = unackedBolts(rows, []);
	assert.equal(unacked.declaration, null);
	assert.equal(boltCardDataFromLedgerRows(rows)?.declaration, null);
});
