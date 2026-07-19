import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { RelicRecord, RunLedgerRow } from './runLedger.ts';
import {
	LENS_ALL,
	LENS_REVIEW,
	applyLens,
	availableLenses,
	lensMatches,
	produceShape,
	reconcileLens
} from './loomLens.ts';

function relic(kind: string): RelicRecord {
	return { kind } as RelicRecord;
}

function row(overrides: Partial<RunLedgerRow> = {}): RunLedgerRow {
	return {
		run_id: 'run-1',
		event_id: null,
		started_at: null,
		ended_at: '2026-07-19T12:00:00Z',
		wall_clock_seconds: 60,
		runner_shell: 'claude',
		runner_core: 'claude-opus-4-8',
		core_expected: null,
		core_mismatch: null,
		substitution_reason: null,
		repo_label: 'Gurio/brr',
		source_system: 'telegram',
		name: null,
		external_refs: [],
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
	} as RunLedgerRow;
}

test('produce shape names the furthest a run got, not everything it touched', () => {
	// A run that opened a PR also made commits. It is `shipped`, once — the
	// ordering is the claim, and a run must land in exactly one shape or the
	// counts on the chips would sum to more than the shelf holds.
	assert.equal(produceShape([relic('commit'), relic('pr'), relic('kb')]), 'shipped');
	assert.equal(produceShape([relic('commit'), relic('kb')]), 'committed');
	assert.equal(produceShape([relic('kb_page')]), 'filed');
	assert.equal(produceShape([]), 'bare');
	assert.equal(produceShape([relic('message')]), 'bare');
});

test('the origin vocabulary is read off the data, never declared', () => {
	// The point of the whole module: a dispatch source nothing in the frontend
	// has ever heard of shows up as a lens the moment it appears in a row.
	const lenses = availableLenses([
		row({ source_system: 'telegram' }),
		row({ source_system: 'telegram' }),
		row({ source_system: 'a-source-invented-tomorrow' })
	]);
	const origins = lenses.filter((lens) => lens.facet === 'origin');
	assert.deepEqual(
		origins.map((lens) => [lens.id, lens.count]),
		[
			['origin:telegram', 2],
			['origin:a-source-invented-tomorrow', 1]
		]
	);
});

test('origins sort by weight so the busiest source does not move under a poll', () => {
	const lenses = availableLenses([
		row({ source_system: 'schedule' }),
		row({ source_system: 'telegram' }),
		row({ source_system: 'telegram' }),
		row({ source_system: 'telegram' })
	]);
	const origins = lenses.filter((lens) => lens.facet === 'origin').map((lens) => lens.id);
	assert.deepEqual(origins, ['origin:telegram', 'origin:schedule']);
});

test('a lens matching nothing is not offered', () => {
	// Rows with no PR and no kb page: `shipped` and `filed` must be absent
	// entirely rather than present with a zero. A chip reading `shipped 0` is
	// the surface asserting a shape-space it has not checked.
	const lenses = availableLenses([row({ external_refs: [relic('commit')] }), row()]);
	const ids = lenses.map((lens) => lens.id);
	assert.ok(ids.includes('shape:committed'));
	assert.ok(ids.includes('shape:bare'));
	assert.ok(!ids.includes('shape:shipped'));
	assert.ok(!ids.includes('shape:filed'));
	assert.ok(!ids.includes('stack:worker'));
});

test('the review lens appears only when something is actually waiting', () => {
	assert.ok(!availableLenses([row()], 0).some((lens) => lens.id === LENS_REVIEW));
	const lens = availableLenses([row()], 3).find((candidate) => candidate.id === LENS_REVIEW);
	assert.equal(lens?.count, 3);
	assert.equal(lens?.facet, 'artifact');
});

test('the review lens leaves the shelf whole', () => {
	// It is a lens over artifact edges, not over runs: an open PR outlives the
	// run that opened it, so narrowing the spine to "runs whose PR is still
	// unreviewed" would hide the board to answer a question nobody asked.
	const rows = [row(), row({ external_refs: [relic('pr')] })];
	assert.equal(applyLens(rows, LENS_REVIEW).length, 2);
});

test('chip counts and the filtered shelf are computed by one function', () => {
	// The invariant that keeps a chip from lying: whatever number the chip
	// shows is the length of what the shelf renders under it.
	const rows = [
		row({ source_system: 'telegram', external_refs: [relic('pr')] }),
		row({ source_system: 'schedule', external_refs: [relic('commit')] }),
		row({ source_system: 'schedule', is_subspawn: true }),
		row({ source_system: 'spawn', is_subspawn: true, external_refs: [relic('kb')] })
	];
	for (const lens of availableLenses(rows)) {
		assert.equal(applyLens(rows, lens.id).length, lens.count, `count drift on ${lens.id}`);
	}
});

test('all is the identity lens', () => {
	const rows = [row(), row({ source_system: 'schedule' })];
	assert.equal(applyLens(rows, LENS_ALL), rows);
});

test('an unknown lens matches nothing rather than everything', () => {
	// The failure this guards: a stale selection falling through to a truthy
	// default would show the whole shelf under a chip claiming to narrow it.
	assert.equal(lensMatches(row(), 'origin:gone'), false);
	assert.equal(lensMatches(row(), 'nonsense'), false);
	assert.equal(applyLens([row(), row()], 'nonsense').length, 0);
});

test('a selection whose lens has left the vocabulary falls back to all', () => {
	// Stepping the past window re-derives the vocabulary. A selected lens that
	// no longer exists would otherwise leave an empty shelf under a chip the
	// reader can no longer see to un-click.
	const lenses = availableLenses([row({ source_system: 'telegram' })]);
	assert.equal(reconcileLens('origin:telegram', lenses), 'origin:telegram');
	assert.equal(reconcileLens('origin:github', lenses), LENS_ALL);
	assert.equal(reconcileLens(LENS_ALL, lenses), LENS_ALL);
});

test('a blank source system contributes no origin lens', () => {
	// Old rows predate the field. They still belong to the shelf and to every
	// shape lens — they simply have no origin to slice on, and an `origin:`
	// chip with an empty label would be unreadable.
	const lenses = availableLenses([row({ source_system: null }), row({ source_system: '  ' })]);
	assert.equal(
		lenses.filter((lens) => lens.facet === 'origin').length,
		0,
		'a null source must not become a lens'
	);
	assert.equal(lenses.find((lens) => lens.id === LENS_ALL)?.count, 2);
});
