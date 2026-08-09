import assert from 'node:assert/strict';
import test from 'node:test';

import {
	DIGEST_FALLBACK_WINDOW_MS,
	buildDigest,
	digestAnchor,
	digestLastLookedStorageKey,
	isSummonsWorthy,
	readLastLookedAt,
	serializeLastLookedAt
} from './digest.ts';
import type { BoltRow } from './bolts.ts';
import type { RunLedgerRow } from './runLedger.ts';

const NOW = Date.parse('2026-08-09T20:00:00Z');

function row(overrides: Partial<RunLedgerRow> = {}): RunLedgerRow {
	return {
		run_id: 'run-1',
		event_id: null,
		started_at: null,
		ended_at: '2026-08-09T19:00:00Z',
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

function bolt(overrides: Partial<BoltRow> = {}): BoltRow {
	return {
		runId: 'run-1',
		name: 'run-1',
		named: false,
		bolt: 'accepted',
		repoLabel: 'Gurio/brr',
		endedAt: NOW,
		relics: [],
		wallClockSeconds: null,
		tokensInput: null,
		tokensOutput: null,
		usdSubscriptionAttributed: null,
		usdCreditsEquivalent: null,
		declaration: null,
		...overrides
	};
}

test('digestLastLookedStorageKey namespaces by account, the bolts.ts pattern', () => {
	assert.equal(digestLastLookedStorageKey('acct-1'), 'brnrd.digest.lastLookedAt.acct-1');
});

test('readLastLookedAt: absent, corrupt, negative, and future values all read as "never looked"', () => {
	assert.equal(readLastLookedAt(null, NOW), null);
	assert.equal(readLastLookedAt(undefined, NOW), null);
	assert.equal(readLastLookedAt('not-a-number', NOW), null);
	assert.equal(readLastLookedAt('-5', NOW), null);
	assert.equal(readLastLookedAt('0', NOW), null);
	assert.equal(readLastLookedAt(String(NOW + 1), NOW), null, 'a future instant is never trusted');
});

test('readLastLookedAt / serializeLastLookedAt round-trip a real instant', () => {
	const at = NOW - 3_600_000;
	assert.equal(readLastLookedAt(serializeLastLookedAt(at), NOW), at);
});

test('digestAnchor falls back to the 24h window only when nothing was ever recorded', () => {
	assert.equal(digestAnchor(null, NOW), NOW - DIGEST_FALLBACK_WINDOW_MS);
	const looked = NOW - 5 * 60_000;
	assert.equal(
		digestAnchor(looked, NOW),
		looked,
		'a recorded look is never overridden by the fallback'
	);
});

test('isSummonsWorthy: a bare reply-only completion does not summon', () => {
	assert.equal(
		isSummonsWorthy(bolt({ relics: [{ kind: 'reply', excerpt: 'went back to sleep' }] })),
		false
	);
});

test('isSummonsWorthy: dissent (annotated) always summons', () => {
	assert.equal(isSummonsWorthy(bolt({ bolt: 'annotated' })), true);
});

test('isSummonsWorthy: an ask addressed to the viewer summons', () => {
	assert.equal(
		isSummonsWorthy(
			bolt({
				declaration: {
					asks: [{ event: 'evt-1', disposition: 'answered' }],
					owed: [],
					decisions: [],
					spendDeclared: null,
					next: null,
					dissent: []
				}
			})
		),
		true
	);
});

test('isSummonsWorthy: a declaration too large to persist summons rather than hiding', () => {
	assert.equal(
		isSummonsWorthy(
			bolt({ declaration: { omitted: true, reason: 'persistence limits exceeded' } })
		),
		true
	);
});

test('isSummonsWorthy: produce worth announcing (pr/commit/kb) summons, same vocabulary as the cloth', () => {
	assert.equal(isSummonsWorthy(bolt({ relics: [{ kind: 'commit', sha: 'abc' }] })), true);
	assert.equal(isSummonsWorthy(bolt({ relics: [{ kind: 'pr', number: 12 }] })), true);
	assert.equal(isSummonsWorthy(bolt({ relics: [{ kind: 'kb', path: 'subject-a.md' }] })), true);
});

test('isSummonsWorthy: a clean scheduled tick with no bolt state at all does not summon', () => {
	assert.equal(isSummonsWorthy(bolt({ bolt: 'accepted', declaration: null, relics: [] })), false);
});

test('buildDigest: the aggregate windows to the anchor, not a fixed 24h', () => {
	const sinceMs = NOW - 2 * 60 * 60_000; // 2h ago
	const rows = [
		row({ run_id: 'in-window', ended_at: new Date(NOW - 30 * 60_000).toISOString() }),
		row({ run_id: 'outside-window', ended_at: new Date(NOW - 5 * 60 * 60_000).toISOString() })
	];
	const digest = buildDigest(rows, sinceMs, NOW);
	assert.equal(digest.since, sinceMs);
	assert.equal(digest.summary.runCount, 1, 'only the row inside the since-anchor window counts');
});

test('buildDigest: rows are the summons-worthy subset only, newest first, linked to the run node', () => {
	const rows = [
		row({
			run_id: 'quiet-tick',
			ended_at: new Date(NOW - 10 * 60_000).toISOString(),
			bolt: 'accepted',
			external_refs: [{ kind: 'reply', excerpt: 'nothing to do' }]
		}),
		row({
			run_id: 'shipped-a-pr',
			name: 'the-fix',
			ended_at: new Date(NOW - 20 * 60_000).toISOString(),
			bolt: 'accepted',
			external_refs: [{ kind: 'pr', number: 42 }]
		})
	];
	const digest = buildDigest(rows, NOW - 60 * 60_000, NOW);
	assert.deepEqual(
		digest.rows.map((r) => r.runId),
		['shipped-a-pr'],
		'the quiet reply-only tick stays out of the digest rows'
	);
	assert.equal(
		digest.rows[0].href,
		'/runs/Gurio__brr/shipped-a-pr#receipt',
		"a digest row is always summons-worthy, so it links straight to the run's #receipt section (design-run-route.md rung 2)"
	);
});

test('buildDigest: a run outside the window never appears in rows even when summons-worthy', () => {
	const rows = [
		row({
			run_id: 'old-pr',
			ended_at: new Date(NOW - 5 * 60 * 60_000).toISOString(),
			bolt: 'accepted',
			external_refs: [{ kind: 'pr', number: 1 }]
		})
	];
	const digest = buildDigest(rows, NOW - 60 * 60_000, NOW);
	assert.deepEqual(digest.rows, []);
});
