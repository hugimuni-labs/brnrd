import assert from 'node:assert/strict';
import test from 'node:test';

import {
	CLOTH_ROOT_CAP,
	CLOTH_WINDOW_MS,
	clothAgeLabel,
	clothSelvage,
	groupClothDays,
	inClothWindow,
	produceChips,
	selvageParts,
	weaveCloth
} from './cloth.ts';
import { loomBarFraction, loomPastStop } from './loomBand.ts';
import { LENS_ALL, applyLens, availableLenses, reconcileLens } from './loomLens.ts';
import { THERMAL_STOPS } from './statusPalette.ts';
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

test('cap: Infinity lifts the cap entirely — the "show older" control\'s whole trick', () => {
	// Cloth.svelte's "show older" button (the phone-density pass, 2026-08-02)
	// re-weaves with `cap: Infinity` rather than issuing a new fetch — every
	// root beyond `CLOTH_ROOT_CAP` already rode the same `rows` in, so lifting
	// the cap must render all of them with nothing left dropped.
	const rows = Array.from({ length: CLOTH_ROOT_CAP + 5 }, (_, index) =>
		row({ run_id: `run-${index}`, ended_at: endedAgo((index + 1) * HOUR) })
	);
	const weave = weaveCloth(rows, NOW, CLOTH_WINDOW_MS, Infinity);
	assert.equal(weave.trees.length, CLOTH_ROOT_CAP + 5);
	assert.equal(weave.dropped, 0);
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

// The day rule groups on the *local* calendar day of the run's own close
// timestamp — the clock `runLedger`'s "today" check already reads. These
// tests pin that: timestamps are built with the local Date constructor, so
// a 23:30 / 00:30 pair straddles the same midnight on any machine.
const LOCAL_NOW = new Date(2026, 7, 1, 18, 0).getTime();

function localIso(...parts: [number, number, number, number, number]): string {
	return new Date(...parts).toISOString();
}

test('day rhythm: roots group by local calendar day, newest day first', () => {
	const weave = weaveCloth(
		[
			row({ run_id: 'noon', name: 'noon run', ended_at: localIso(2026, 7, 1, 12, 0) }),
			row({ run_id: 'early', name: 'early run', ended_at: localIso(2026, 7, 1, 0, 30) }),
			row({ run_id: 'late-prev', name: 'late run', ended_at: localIso(2026, 6, 31, 23, 30) })
		],
		LOCAL_NOW,
		CLOTH_WINDOW_MS
	);
	const days = groupClothDays(weave.trees);
	assert.deepEqual(
		days.map((day) => day.dayLabel),
		['aug 1', 'jul 31'],
		'23:30 and 00:30 land either side of local midnight'
	);
	assert.deepEqual(
		days.map((day) => day.key),
		['2026-08-01', '2026-07-31']
	);
	assert.deepEqual(
		days.map((day) => day.runCount),
		[2, 1]
	);
	assert.deepEqual(
		days[0].trees.map((tree) => tree.root.id),
		['noon', 'early'],
		'within a day the weave keeps its newest-first order'
	);
	assert.equal(days[0].unnamed, null, 'named-only day carries no fold');
});

test('unnamed runs fold into one quiet line per day; a named run never folds', () => {
	const weave = weaveCloth(
		[
			row({
				run_id: 'named-1',
				name: 'the warp takes its shape',
				ended_at: localIso(2026, 7, 1, 11, 0)
			}),
			row({
				run_id: 'run-260801-1030-a1b2',
				wall_clock_seconds: 60,
				ended_at: localIso(2026, 7, 1, 10, 30)
			}),
			row({
				run_id: 'run-260801-0915-c3d4',
				wall_clock_seconds: 36,
				ended_at: localIso(2026, 7, 1, 9, 15)
			}),
			// Whitespace is not an authored name — it folds too.
			row({ run_id: 'run-260731-2148-f6hg', name: '  ', ended_at: localIso(2026, 6, 31, 21, 48) })
		],
		LOCAL_NOW,
		CLOTH_WINDOW_MS
	);
	const days = groupClothDays(weave.trees);
	assert.equal(days.length, 2);

	const aug1 = days[0];
	assert.deepEqual(
		aug1.trees.map((tree) => tree.root.id),
		['named-1'],
		'the named run stays a full row — it never folds'
	);
	assert.ok(aug1.unnamed);
	assert.equal(aug1.unnamed.count, 2);
	assert.equal(aug1.unnamed.totalSeconds, 96);
	assert.equal(aug1.unnamed.label, '2 unnamed ticks · 1m 36s total');
	assert.deepEqual(
		aug1.unnamed.trees.map((tree) => tree.root.id),
		['run-260801-1030-a1b2', 'run-260801-0915-c3d4'],
		'the raw rows survive whole behind the fold'
	);
	assert.equal(aug1.runCount, 3, 'the day rule counts named and folded alike');

	const jul31 = days[1];
	assert.deepEqual(jul31.trees, []);
	assert.ok(jul31.unnamed);
	assert.equal(jul31.unnamed.label, '1 unnamed tick · 0s total');
});

test('repo chips: a single-repo window shows no per-row label at all', () => {
	const weave = weaveCloth(
		[
			row({ run_id: 'a', repo_label: 'hugimuni-labs/brnrd', ended_at: endedAgo(HOUR) }),
			row({ run_id: 'b', repo_label: 'hugimuni-labs/brnrd', ended_at: endedAgo(2 * HOUR) }),
			row({
				run_id: 'w',
				parent_run_id: 'a',
				is_subspawn: true,
				repo_label: 'hugimuni-labs/brnrd',
				ended_at: endedAgo(HOUR / 2)
			})
		],
		NOW,
		CLOTH_WINDOW_MS
	);
	for (const tree of weave.trees) {
		assert.equal(tree.root.repoChip, null);
		for (const child of tree.children) assert.equal(child.repoChip, null);
	}
	assert.equal(weave.trees[0].root.repoLabel, 'hugimuni-labs/brnrd', 'the full label survives');
});

test('repo chips: multi-repo window marks only rows off the dominant repo', () => {
	const weave = weaveCloth(
		[
			row({ run_id: 'a', repo_label: 'acme/site', ended_at: endedAgo(HOUR) }),
			row({ run_id: 'b', repo_label: 'acme/site', ended_at: endedAgo(2 * HOUR) }),
			row({ run_id: 'c', repo_label: 'acme/tools', ended_at: endedAgo(3 * HOUR) }),
			row({ run_id: 'd', ended_at: endedAgo(4 * HOUR) })
		],
		NOW,
		CLOTH_WINDOW_MS
	);
	const byId = new Map(weave.trees.map((tree) => [tree.root.id, tree.root]));
	assert.equal(byId.get('a')?.repoChip, null, 'dominant-repo rows stay bare');
	assert.equal(byId.get('b')?.repoChip, null);
	assert.deepEqual(
		byId.get('c')?.repoChip,
		{ short: 'tools', full: 'acme/tools' },
		'off-dominant row wears the short chip, full label kept for hover'
	);
	assert.equal(byId.get('d')?.repoChip, null, 'a row with no repo label has nothing to wear');
});

test('curated line: authored names are named, id fallbacks are not', () => {
	const weave = weaveCloth(
		[
			row({ run_id: 'a', name: 'nightly sweep', ended_at: endedAgo(HOUR) }),
			row({ run_id: 'run-260731-2148-f6hg', ended_at: endedAgo(2 * HOUR) })
		],
		NOW,
		CLOTH_WINDOW_MS
	);
	assert.equal(weave.trees[0].root.named, true);
	assert.equal(weave.trees[1].root.named, false);
	assert.equal(weave.trees[1].root.name, 'run-260731-2148-f6hg', 'the id still shows when opened');
});

test("bars: fractions run the band's own scale against one window-wide max", () => {
	const weave = weaveCloth(
		[
			row({ run_id: 'big', wall_clock_seconds: 900, ended_at: endedAgo(2 * HOUR) }),
			row({
				run_id: 'worker',
				parent_run_id: 'big',
				is_subspawn: true,
				wall_clock_seconds: 400,
				ended_at: endedAgo(HOUR)
			}),
			// Three days back: the denominator is the whole visible window,
			// never per day — a long bar means the same thing on every day.
			row({ run_id: 'small-old-day', wall_clock_seconds: 100, ended_at: endedAgo(3 * DAY) }),
			row({ run_id: 'zero', ended_at: endedAgo(4 * HOUR) })
		],
		NOW,
		CLOTH_WINDOW_MS
	);
	assert.equal(weave.maxWallSeconds, 900, 'max spans roots and workers across all days');
	const lines = new Map(
		weave.trees.flatMap((tree) => [tree.root, ...tree.children]).map((line) => [line.id, line])
	);
	assert.equal(lines.get('big')?.barFraction, loomBarFraction(900, 900));
	assert.equal(lines.get('big')?.barFraction, 1, 'the longest run fills the bar');
	assert.equal(lines.get('worker')?.barFraction, loomBarFraction(400, 900));
	assert.equal(
		lines.get('small-old-day')?.barFraction,
		loomBarFraction(100, 900),
		'an older day still divides by the window-wide max'
	);
	assert.equal(
		lines.get('zero')?.barFraction,
		loomBarFraction(0, 900),
		'a zero-second run rides the band’s own floor — mirrored by sharing the function'
	);
	assert.equal(
		lines.get('zero')?.barFraction,
		0.06,
		'visibly a bar, not a dot — the shelf’s floor'
	);
});

test("bars: thermal color is the shelf's own age stop — shared, not copied", () => {
	const weave = weaveCloth(
		[
			row({ run_id: 'fresh', ended_at: endedAgo(2 * HOUR) }),
			row({ run_id: 'cooling', ended_at: endedAgo(8 * HOUR) }),
			row({ run_id: 'old', ended_at: endedAgo(3 * DAY) })
		],
		NOW,
		CLOTH_WINDOW_MS
	);
	for (const tree of weave.trees) {
		assert.equal(
			tree.root.color,
			THERMAL_STOPS[loomPastStop(tree.root.ageMs)],
			'the exact pair the shelf computes run.color from'
		);
	}
	const [fresh, cooling, old] = weave.trees.map((tree) => tree.root);
	assert.equal(fresh.color, THERMAL_STOPS.amber);
	assert.equal(cooling.color, THERMAL_STOPS['ember-ash']);
	assert.equal(old.color, THERMAL_STOPS.ash);
});

// The lens rail moved from the band to the cloth (the dissolution,
// 2026-08-02): chips lens the past inventory, so they filter what the cloth
// weaves — trees, day rules, folds all recompute on the lensed set — while
// the selvage stays the hem of the whole window, never of a lens. These
// tests pin that composition (`applyLens` → `weaveCloth`), the exact wiring
// `Cloth.svelte` runs.

test('lens: an origin chip filters the woven trees', () => {
	const rows = [
		row({ run_id: 'a', name: 'spawned a', source_system: 'spawn', ended_at: endedAgo(HOUR) }),
		row({
			run_id: 'b',
			name: 'scheduled b',
			source_system: 'schedule',
			ended_at: endedAgo(2 * HOUR)
		}),
		row({ run_id: 'c', name: 'spawned c', source_system: 'spawn', ended_at: endedAgo(3 * HOUR) })
	];
	const lenses = availableLenses(rows);
	assert.deepEqual(
		lenses.map((lens) => [lens.id, lens.count]),
		[
			['all', 3],
			['origin:spawn', 2],
			['origin:schedule', 1],
			['shape:bare', 3]
		],
		'the vocabulary is read off the rows the cloth holds'
	);
	const weave = weaveCloth(applyLens(rows, 'origin:spawn'), NOW, CLOTH_WINDOW_MS);
	assert.deepEqual(
		weave.trees.map((tree) => tree.root.id),
		['a', 'c'],
		'only the lensed runs weave'
	);
});

test('lens: day rules and their counts recompute on the lensed set', () => {
	const rows = [
		row({
			run_id: 'today-spawn',
			name: 'today spawn',
			source_system: 'spawn',
			ended_at: localIso(2026, 7, 1, 11, 0)
		}),
		row({
			run_id: 'today-sched',
			name: 'today sched',
			source_system: 'schedule',
			ended_at: localIso(2026, 7, 1, 9, 0)
		}),
		row({
			run_id: 'yesterday-sched',
			name: 'yesterday sched',
			source_system: 'schedule',
			ended_at: localIso(2026, 6, 31, 22, 0)
		})
	];
	const allDays = groupClothDays(weaveCloth(rows, LOCAL_NOW, CLOTH_WINDOW_MS).trees);
	assert.deepEqual(
		allDays.map((day) => day.runCount),
		[2, 1]
	);
	const lensedDays = groupClothDays(
		weaveCloth(applyLens(rows, 'origin:schedule'), LOCAL_NOW, CLOTH_WINDOW_MS).trees
	);
	assert.deepEqual(
		lensedDays.map((day) => [day.dayLabel, day.runCount]),
		[
			['aug 1', 1],
			['jul 31', 1]
		],
		'the aug 1 rule counts only what the lens lets through'
	);
});

test('lens: the selvage hems the whole window, never the lensed slice', () => {
	const rows = [
		row({
			run_id: 'a',
			source_system: 'spawn',
			wall_clock_seconds: 100,
			ended_at: endedAgo(HOUR),
			external_refs: [{ kind: 'pr', number: 7 }]
		}),
		row({
			run_id: 'b',
			source_system: 'schedule',
			wall_clock_seconds: 50,
			ended_at: endedAgo(2 * HOUR),
			external_refs: [{ kind: 'commit', sha: 'abc' }]
		})
	];
	const weave = weaveCloth(applyLens(rows, 'origin:spawn'), NOW, CLOTH_WINDOW_MS);
	assert.equal(weave.trees.length, 1, 'the weave is the lensed view');
	// The component computes the selvage from the un-lensed rows — the same
	// call regardless of which chip is lit.
	const summary = clothSelvage(rows, NOW, CLOTH_WINDOW_MS);
	assert.equal(summary.runCount, 2);
	assert.equal(summary.wallClockSeconds, 150);
	assert.equal(summary.prs, 1);
	assert.equal(summary.commits, 1);
});

test('lens: a stale selection reconciles to all rather than lying about the weave', () => {
	const rows = [row({ run_id: 'a', source_system: 'spawn', ended_at: endedAgo(HOUR) })];
	const lenses = availableLenses(rows);
	assert.equal(reconcileLens('origin:github', lenses), LENS_ALL, 'a vanished chip falls back');
	assert.equal(reconcileLens('origin:spawn', lenses), 'origin:spawn', 'a live chip holds');
});

// The lens that can strand rows. `stack:worker` keeps only sub-spawns, so
// every surviving row's parent is *gone* from the set — and `weaveCloth`
// drops any `depth: 1` run it meets before a root (`trees.length > 0`).
// A chip that counts N and weaves 0 would be the cloth lying with a number
// beside it. `nestShelfChildren` is what prevents that: a child whose
// parent is not in the set renders as a root. Pinned because the guard is
// three files away from the lens that needs it — neuter the `&& parent`
// clause there and this is the test that goes red.
test('lens: strands weave as roots once the lens removes their parents', () => {
	const rows = [
		row({ run_id: 'parent', source_system: 'cloud', ended_at: endedAgo(3 * HOUR) }),
		row({
			run_id: 'child-a',
			source_system: 'spawn',
			is_subspawn: true,
			parent_run_id: 'parent',
			ended_at: endedAgo(2 * HOUR)
		}),
		row({
			run_id: 'child-b',
			source_system: 'spawn',
			is_subspawn: true,
			parent_run_id: 'parent',
			ended_at: endedAgo(HOUR)
		})
	];
	const strands = availableLenses(rows).find((lens) => lens.id === 'stack:worker');
	assert.equal(strands?.count, 2, 'the chip counts both strands');

	const unlensed = weaveCloth(rows, NOW, CLOTH_WINDOW_MS);
	assert.deepEqual(
		unlensed.trees.map((tree) => [tree.root.id, tree.children.map((child) => child.id)]),
		[['parent', ['child-b', 'child-a']]],
		'unlensed, the strands hang under the run that dispatched them'
	);

	const weave = weaveCloth(applyLens(rows, 'stack:worker'), NOW, CLOTH_WINDOW_MS);
	assert.deepEqual(
		weave.trees.map((tree) => tree.root.id),
		['child-b', 'child-a'],
		'lensed, each strand stands on its own rather than vanishing with its parent'
	);
	assert.equal(
		weave.trees.length,
		strands?.count,
		'the weave holds exactly what the chip promised'
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

test("weld: a run's item relics surface as addresses on its line — referencing, never re-listing", () => {
	const weave = weaveCloth(
		[
			row({
				run_id: 'welded',
				ended_at: endedAgo(HOUR),
				external_refs: [
					{ kind: 'item', address: 'the-loom#band-animation' },
					{ kind: 'item', address: 'the-loom#band-animation' },
					{ kind: 'pr', number: 999 }
				]
			}),
			row({ run_id: 'plain', ended_at: endedAgo(2 * HOUR) })
		],
		NOW,
		CLOTH_WINDOW_MS
	);
	const lines = new Map(weave.trees.map((tree) => [tree.root.id, tree.root]));
	assert.deepEqual(lines.get('welded')?.items, ['the-loom#band-animation'], 'deduped address list');
	assert.deepEqual(lines.get('plain')?.items, [], 'an un-welded run carries no address');
});
