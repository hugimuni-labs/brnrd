import assert from 'node:assert/strict';
import test from 'node:test';

import {
	CLOTH_ROOT_CAP,
	CLOTH_WINDOW_MS,
	clothAgeLabel,
	clothSelvage,
	inClothWindow,
	produceChips,
	selvageParts,
	weaveCloth
} from './cloth.ts';
import type { RunLedgerRow } from './runLedger.ts';

const NOW = Date.parse('2026-08-01T12:00:00Z');

function row(over: Partial<RunLedgerRow>): RunLedgerRow {
	return {
		run_id: null,
		event_id: null,
		started_at: null,
		ended_at: null,
		wall_clock_seconds: null,
		runner_shell: null,
		runner_core: null,
		core_expected: null,
		core_mismatch: null,
		substitution_reason: null,
		repo_label: null,
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
		...over
	};
}

function endedAgo(ms: number): string {
	return new Date(NOW - ms).toISOString();
}

const HOUR = 60 * 60 * 1000;
const DAY = 24 * HOUR;

test('window predicate: parseable close inside the window, nothing else', () => {
	assert.ok(inClothWindow(row({ ended_at: endedAgo(2 * DAY) }), NOW, CLOTH_WINDOW_MS));
	assert.ok(!inClothWindow(row({ ended_at: endedAgo(31 * DAY) }), NOW, CLOTH_WINDOW_MS));
	assert.ok(!inClothWindow(row({ ended_at: endedAgo(-HOUR) }), NOW, CLOTH_WINDOW_MS), 'future');
	assert.ok(!inClothWindow(row({ ended_at: 'not-a-date' }), NOW, CLOTH_WINDOW_MS));
	assert.ok(!inClothWindow(row({ ended_at: null }), NOW, CLOTH_WINDOW_MS));
});

test('grouping: roots newest-first, worker subruns nested beneath their root', () => {
	const rows = [
		row({ run_id: 'old-root', ended_at: endedAgo(5 * HOUR) }),
		row({
			run_id: 'worker-late',
			parent_run_id: 'old-root',
			is_subspawn: true,
			ended_at: endedAgo(3 * HOUR)
		}),
		row({ run_id: 'new-root', ended_at: endedAgo(1 * HOUR) }),
		row({
			run_id: 'worker-early',
			parent_run_id: 'old-root',
			is_subspawn: true,
			ended_at: endedAgo(4 * HOUR)
		})
	];
	const weave = weaveCloth(rows, NOW, CLOTH_WINDOW_MS);
	assert.equal(weave.dropped, 0);
	assert.deepEqual(
		weave.trees.map((tree) => tree.root.id),
		['new-root', 'old-root']
	);
	assert.deepEqual(
		weave.trees[1].children.map((child) => child.id),
		['worker-late', 'worker-early'],
		'children age-ordered (newest first) beneath their root'
	);
	assert.deepEqual(weave.trees[0].children, []);
});

test('grouping: an orphan subrun renders as a root rather than vanishing', () => {
	const rows = [
		row({
			run_id: 'orphan',
			parent_run_id: 'parent-out-of-window',
			is_subspawn: true,
			ended_at: endedAgo(HOUR)
		})
	];
	const weave = weaveCloth(rows, NOW, CLOTH_WINDOW_MS);
	assert.equal(weave.trees.length, 1);
	assert.equal(weave.trees[0].root.id, 'orphan');
});

test('grouping: rows outside the window are not woven', () => {
	const rows = [
		row({ run_id: 'in', ended_at: endedAgo(29 * DAY) }),
		row({ run_id: 'out', ended_at: endedAgo(31 * DAY) })
	];
	const weave = weaveCloth(rows, NOW, CLOTH_WINDOW_MS);
	assert.deepEqual(
		weave.trees.map((tree) => tree.root.id),
		['in']
	);
});

test('curated line: name, repo, chips, duration, age, node link', () => {
	const weave = weaveCloth(
		[
			row({
				run_id: 'run-1',
				name: 'nightly sweep',
				repo_label: 'acme/site',
				wall_clock_seconds: 754,
				ended_at: endedAgo(3 * HOUR),
				external_refs: [
					{ kind: 'pr', number: 7, url: 'https://example.com/pr/7' },
					{ kind: 'commit', sha: 'abc1234' },
					{ kind: 'commit', sha: 'def5678' },
					{ kind: 'kb_page', path: 'kb/notes.md' },
					{ kind: 'summary', text: 'prose, not produce' }
				]
			})
		],
		NOW,
		CLOTH_WINDOW_MS
	);
	const line = weave.trees[0].root;
	assert.equal(line.name, 'nightly sweep');
	assert.equal(line.repoLabel, 'acme/site');
	assert.deepEqual(
		line.chips.map((chip) => chip.label),
		['1pr', '2c', '1kb'],
		'kb_page counts as kb; summary is prose, not a chip'
	);
	assert.equal(line.bare, false);
	assert.equal(line.duration, '12m 34s');
	assert.equal(line.age, '3h 0m ago');
	assert.equal(line.href, '/runs/acme__site/run-1');
});

test('curated line: falls back to the run id for a nameless run, no link without one', () => {
	const weave = weaveCloth(
		[
			row({ run_id: 'bare-run', ended_at: endedAgo(HOUR) }),
			row({ event_id: 'evt-9', ended_at: endedAgo(2 * HOUR) })
		],
		NOW,
		CLOTH_WINDOW_MS
	);
	const [named, eventOnly] = weave.trees.map((tree) => tree.root);
	assert.equal(named.name, 'bare-run');
	assert.equal(named.bare, true, 'no produce → faint line, never invented chips');
	assert.equal(eventOnly.id, 'evt-9');
	assert.equal(eventOnly.href, null, 'no run_id names no durable node');
});

test('selvage: gauge-grammar sums over the window — pr dedupe, kb alias, null wall stays null', () => {
	const rows = [
		row({
			run_id: 'a',
			repo_label: 'acme/site',
			wall_clock_seconds: 100,
			ended_at: endedAgo(DAY),
			external_refs: [
				{ kind: 'pr', number: 7 },
				{ kind: 'commit', sha: 'abc' }
			]
		}),
		row({
			run_id: 'b',
			repo_label: 'acme/site',
			wall_clock_seconds: 50,
			ended_at: endedAgo(2 * DAY),
			// The same PR reported by a second run is one piece of produce.
			external_refs: [
				{ kind: 'pr', number: 7 },
				{ kind: 'kb_page', path: 'kb/x.md' }
			]
		}),
		row({ run_id: 'out-of-window', wall_clock_seconds: 999, ended_at: endedAgo(31 * DAY) })
	];
	const summary = clothSelvage(rows, NOW, CLOTH_WINDOW_MS);
	assert.equal(summary.runCount, 2);
	assert.equal(summary.wallClockSeconds, 150);
	assert.equal(summary.prs, 1, 'same repo#number dedupes');
	assert.equal(summary.commits, 1);
	assert.equal(summary.kbPages, 1);

	const noWall = clothSelvage(
		[row({ run_id: 'c', ended_at: endedAgo(DAY) })],
		NOW,
		CLOTH_WINDOW_MS
	);
	assert.equal(noWall.wallClockSeconds, null, 'absent metrics never become fabricated zeroes');
});

test('selvage parts: runs always speak, produce speaks only when nonzero', () => {
	const parts = selvageParts(
		clothSelvage(
			[
				row({
					run_id: 'a',
					wall_clock_seconds: 3600,
					ended_at: endedAgo(DAY),
					external_refs: [{ kind: 'commit', sha: 'abc' }]
				})
			],
			NOW,
			CLOTH_WINDOW_MS
		)
	);
	assert.deepEqual(parts, ['1 run', '60m 00s', '1 commit']);
	assert.deepEqual(selvageParts(clothSelvage([], NOW, CLOTH_WINDOW_MS)), ['0 runs']);
});

test('cap: roots beyond the cap come back as an explicit drop count', () => {
	const rows = Array.from({ length: CLOTH_ROOT_CAP + 5 }, (_, index) =>
		row({ run_id: `run-${index}`, ended_at: endedAgo((index + 1) * HOUR) })
	);
	const weave = weaveCloth(rows, NOW, CLOTH_WINDOW_MS);
	assert.equal(weave.trees.length, CLOTH_ROOT_CAP);
	assert.equal(weave.dropped, 5, 'the drop is part of the return value — never silent');
	assert.equal(weave.trees[0].root.id, 'run-0', 'newest roots survive the cap');
});

test('cap: workers ride their root and never count against it', () => {
	const rows: RunLedgerRow[] = [];
	for (let index = 0; index < 3; index += 1) {
		rows.push(row({ run_id: `root-${index}`, ended_at: endedAgo((index + 1) * HOUR) }));
		rows.push(
			row({
				run_id: `worker-${index}`,
				parent_run_id: `root-${index}`,
				is_subspawn: true,
				ended_at: endedAgo((index + 1) * HOUR + 30 * 60 * 1000)
			})
		);
	}
	const weave = weaveCloth(rows, NOW, CLOTH_WINDOW_MS, 3);
	assert.equal(weave.trees.length, 3);
	assert.equal(weave.dropped, 0);
	for (const tree of weave.trees) assert.equal(tree.children.length, 1);
});

test('re-reported rows merge into one line: relics accumulate, largest wall wins', () => {
	const weave = weaveCloth(
		[
			row({
				run_id: 'dup',
				wall_clock_seconds: 10,
				ended_at: endedAgo(2 * HOUR),
				external_refs: [{ kind: 'commit', sha: 'abc' }]
			}),
			row({
				run_id: 'dup',
				wall_clock_seconds: 40,
				ended_at: endedAgo(HOUR),
				external_refs: [{ kind: 'pr', number: 3 }]
			})
		],
		NOW,
		CLOTH_WINDOW_MS
	);
	assert.equal(weave.trees.length, 1);
	const line = weave.trees[0].root;
	assert.equal(line.wallSeconds, 40);
	assert.deepEqual(
		line.chips.map((chip) => chip.label),
		['1pr', '1c']
	);
});

test('produce chips and age labels speak the loom grammar', () => {
	assert.deepEqual(produceChips([]), []);
	assert.deepEqual(
		produceChips([{ kind: 'kb' }, { kind: 'kb_page' }]).map((chip) => chip.label),
		['2kb']
	);
	assert.equal(clothAgeLabel(5 * 60 * 1000), '5m ago');
	assert.equal(clothAgeLabel(3 * HOUR + 12 * 60 * 1000), '3h 12m ago');
	assert.equal(clothAgeLabel(2 * DAY + 5 * HOUR), '2d 5h ago');
});
