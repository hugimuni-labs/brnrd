import { deepEqual, equal } from 'node:assert/strict';
import { test } from 'node:test';
import { rollupProduceGauge } from './produceGauge.ts';
import type { RunLedgerRow } from './runLedger';

const NOW = Date.parse('2026-07-16T18:00:00Z');

function row(fields: Partial<RunLedgerRow>): RunLedgerRow {
	return {
		ended_at: '2026-07-16T17:00:00Z',
		external_refs: [],
		...fields
	} as RunLedgerRow;
}

test('rollup keeps the trailing 24h honest and groups spend by shell', () => {
	const summary = rollupProduceGauge(
		[
			row({
				wall_clock_seconds: 3_600,
				tokens_input: 1_000,
				tokens_output: null,
				runner_shell: 'Claude',
				weekly_pct_delta: 1.5,
				usd_subscription_attributed: 0.1,
				external_refs: [
					{ kind: 'pr', number: 12 },
					{ kind: 'commit', sha: 'aaa' },
					{ kind: 'kb', path: 'subject-a.md' },
					{ kind: 'reply' }
				]
			}),
			row({
				ended_at: '2026-07-15T18:00:00Z',
				wall_clock_seconds: 60,
				tokens_input: 500,
				runner_shell: 'codex',
				weekly_pct_delta: -90,
				usd_subscription_attributed: 0.2,
				external_refs: [
					{ kind: 'pr', number: '12' },
					{ kind: 'pr' },
					{ kind: 'commit', sha: 'bbb' },
					{ kind: 'kb_page', path: 'decision-b.md' },
					{ kind: 'summary', text: 'not produce' }
				]
			}),
			row({ runner_shell: 'codex', weekly_pct_delta: 2.5 }),
			row({
				repo_label: 'another/repo',
				external_refs: [{ kind: 'pr', number: 12 }]
			}),
			row({
				ended_at: '2026-07-15T17:59:59Z',
				wall_clock_seconds: 99_999,
				external_refs: [{ kind: 'pr', number: 99 }]
			}),
			row({ ended_at: '2026-07-16T18:00:01Z', wall_clock_seconds: 99_999 }),
			row({ ended_at: 'not-an-instant', wall_clock_seconds: 99_999 })
		],
		NOW
	);

	equal(summary.runCount, 4);
	equal(summary.wallClockSeconds, 3_660);
	equal(summary.tokensInput, 1_500);
	equal(summary.tokensOutput, null);
	deepEqual(summary.weeklyQuota, [
		{ shell: 'claude', percent: 1.5 },
		{ shell: 'codex', percent: 2.5 }
	]);
	equal(summary.usdSubscriptionAttributed, 0.30000000000000004);
	deepEqual(
		{
			prs: summary.prs,
			commits: summary.commits,
			kbPages: summary.kbPages,
			replies: summary.replies
		},
		{ prs: 2, commits: 2, kbPages: 2, replies: 1 }
	);
});

test('empty window omits nullable spend instead of inventing zeroes', () => {
	const summary = rollupProduceGauge([], NOW);

	deepEqual(summary, {
		runCount: 0,
		wallClockSeconds: null,
		tokensInput: null,
		tokensOutput: null,
		weeklyQuota: [],
		usdSubscriptionAttributed: null,
		prs: 0,
		commits: 0,
		kbPages: 0,
		replies: 0
	});
});
