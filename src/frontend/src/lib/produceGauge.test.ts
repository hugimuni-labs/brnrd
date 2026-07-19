import { deepEqual, equal } from 'node:assert/strict';
import { test } from 'node:test';
import { produceGaugeLinks, rollupProduceGauge } from './produceGauge.ts';
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

test('linked produce stays inside the window, counted vocabulary, and safe URL schemes', () => {
	const links = produceGaugeLinks(
		[
			row({
				external_refs: [
					{ kind: 'pr', number: 12, url: 'https://example.test/pull/12' },
					{ kind: 'commit', sha: 'abcdef0123', subject: 'ship it', url: null },
					{ kind: 'kb_page', path: 'subject-a.md', url: 'https://example.test/subject-a' },
					{ kind: 'reply', excerpt: 'done — receipt', url: 'https://example.test/reply/1' },
					{ kind: 'summary', text: 'not produce', url: 'https://example.test/summary' },
					{ kind: 'file', path: 'proof.png', url: 'https://example.test/proof' },
					{ kind: 'pr', number: 99, url: 'javascript:alert(1)' }
				]
			}),
			row({
				external_refs: [{ kind: 'pr', number: 12, url: 'https://example.test/pull/12' }]
			}),
			row({
				ended_at: '2026-07-15T17:59:59Z',
				external_refs: [{ kind: 'pr', number: 11, url: 'https://example.test/pull/11' }]
			})
		],
		NOW
	);

	deepEqual(links, [
		{ kind: 'pr', label: 'PR #12', url: 'https://example.test/pull/12' },
		{ kind: 'kb', label: 'subject-a.md', url: 'https://example.test/subject-a' },
		{ kind: 'reply', label: 'done — receipt', url: 'https://example.test/reply/1' }
	]);
});

// The instruments used to hold their own 24h constant while the loom above
// them cycled 6h → 7d, so turning the dial moved the band and left the gauge —
// and its "last 24h" caption — frozen. The window is a parameter now; these
// pin that it actually reaches both the rollup and the link list.
test('the gauge rolls up whatever window the loom hands it', () => {
	const rows = [
		row({ ended_at: '2026-07-16T17:00:00Z', wall_clock_seconds: 60 }),
		row({ ended_at: '2026-07-14T17:00:00Z', wall_clock_seconds: 600 })
	];

	equal(rollupProduceGauge(rows, NOW).runCount, 1, 'default stays trailing 24h');
	equal(rollupProduceGauge(rows, NOW, 6 * 3_600_000).runCount, 1);
	equal(rollupProduceGauge(rows, NOW, 7 * 86_400_000).runCount, 2, '7d reaches the older run');
	equal(rollupProduceGauge(rows, NOW, 7 * 86_400_000).wallClockSeconds, 660);
});

test('produce links honour the same window as the rollup', () => {
	const rows = [
		row({
			ended_at: '2026-07-14T17:00:00Z',
			external_refs: [{ kind: 'pr', number: 7, url: 'https://example.test/pr/7' }]
		})
	];

	deepEqual(produceGaugeLinks(rows, NOW), [], 'outside 24h the relic is not claimed');
	equal(produceGaugeLinks(rows, NOW, 7 * 86_400_000).length, 1);
});
