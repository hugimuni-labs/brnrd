import assert from 'node:assert/strict';
import test from 'node:test';

import { compileRoomGraph, fileFromDetail, resolvePlace } from './roomGraph.ts';
import { compileTopology } from './roomTopology.ts';
import type { LiveRun, LiveRunsResponse } from './liveRuns.ts';
import type { RunLedgerResponse, RunLedgerRow } from './runLedger.ts';
import type { ScheduledWake, ScheduledWakesResponse } from './scheduledWakes.ts';

// ── fixtures — the wire's shape, not a private vocabulary ───────────────────

function liveRun(over: Partial<LiveRun> & { run_id: string }): LiveRun {
	return {
		id: over.run_id,
		kind: 'daemon',
		stream: 'cloud:telegram:1:',
		label: '',
		name: '',
		repo_label: 'hugimuni-labs/brnrd',
		started_at: '2026-08-26T10:00:00Z',
		last_seen: '2026-08-26T10:20:00Z',
		parent_run_id: null,
		is_subspawn: false,
		runner: { name: 'claude-fable', shell: 'claude', core: 'fable' },
		phase: 'running',
		card_text: null,
		card_updated_at: null,
		relics_counts: null,
		portals: { pending: 0, oldest_at: null },
		room: { env: 'host', branch: 'brr/the-ascii-camera', dir: null },
		edge: null,
		daemon_stale: false,
		...over
	} as LiveRun;
}

function edge(act: string, detail: string, over: Partial<NonNullable<LiveRun['edge']>> = {}) {
	return {
		at: '2026-08-26T10:10:00Z',
		phase: 'PostToolUse',
		act,
		tools: ['Bash'],
		detail,
		out_bytes: 100,
		injected: false,
		dir: '.',
		...over
	};
}

function liveWire(runs: LiveRun[]): LiveRunsResponse {
	return {
		generated_at: '2026-08-26T10:20:00Z',
		runs,
		stale: false,
		reported_at: '2026-08-26T10:20:00Z',
		spawn_max_concurrent: 3
	};
}

function ledgerRow(over: Partial<RunLedgerRow> & { run_id: string }): RunLedgerRow {
	return {
		event_id: null,
		started_at: '2026-08-26T08:00:00Z',
		ended_at: '2026-08-26T08:30:00Z',
		wall_clock_seconds: 1800,
		runner_shell: 'claude',
		runner_core: 'sonnet',
		core_expected: null,
		core_mismatch: null,
		substitution_reason: null,
		repo_label: 'hugimuni-labs/brnrd',
		source_system: 'cloud',
		name: 'the-prior-run',
		external_refs: [],
		parent_run_id: null,
		is_subspawn: false,
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
	} as RunLedgerRow;
}

function ledgerWire(rows: RunLedgerRow[]): RunLedgerResponse {
	return {
		generated_at: '2026-08-26T10:20:00Z',
		rows,
		stale: false,
		reported_at: '2026-08-26T10:20:00Z',
		span_seconds_served: 86400
	};
}

function wakesWire(rows: ScheduledWake[]): ScheduledWakesResponse {
	return { generated_at: '2026-08-26T10:20:00Z', rows, total: rows.length };
}

function scheduledWake(id: string, status: string, scheduledFor: string | null): ScheduledWake {
	return {
		id,
		kind: 'scheduled',
		source: 'schedule',
		status,
		phase: 'at',
		bucket: 'scheduled',
		summary: id,
		repo_label: null,
		daemon_name: null,
		conversation_key: null,
		scheduled_for: scheduledFor,
		reported_at: null
	};
}

// ── resolvePlace: noun + verb, never verb alone ─────────────────────────────

test('orient in the tree resolves to the chamber the boundary attested', () => {
	const place = resolvePlace(
		liveRun({ run_id: 'r1', edge: edge('orient', 'Read isoField.ts', { dir: 'src/frontend' }) })
	);
	assert.deepEqual(place, { kind: 'chamber', label: 'src/frontend' });
});

test('orient on the inbox is a correspondence-desk visit, not a chamber', () => {
	const place = resolvePlace(liveRun({ run_id: 'r1', edge: edge('orient', 'Read inbox.json') }));
	assert.equal(place.kind, 'correspondence-desk');
});

test('mutate on .card is the chart table; mutate on source is the workbench chamber', () => {
	assert.equal(
		resolvePlace(liveRun({ run_id: 'r1', edge: edge('mutate', 'Write .card') })).kind,
		'chart-table'
	);
	assert.equal(
		resolvePlace(
			liveRun({ run_id: 'r1', edge: edge('mutate', 'Edit roomGraph.ts', { dir: 'src/frontend' }) })
		).kind,
		'chamber'
	);
});

test('local probe is the rig; a gh-run probe crosses to the forge', () => {
	assert.equal(
		resolvePlace(liveRun({ run_id: 'r1', edge: edge('probe', 'node --test x.test.ts') })).kind,
		'test-rig'
	);
	assert.equal(
		resolvePlace(liveRun({ run_id: 'r1', edge: edge('probe', 'gh pr checks 1648') })).kind,
		'forge-dock'
	);
});

test('publish defaults to the forge; brnrd do publish goes to the desk', () => {
	assert.equal(
		resolvePlace(liveRun({ run_id: 'r1', edge: edge('publish', 'git push origin brr/x') })).kind,
		'forge-dock'
	);
	assert.equal(
		resolvePlace(liveRun({ run_id: 'r1', edge: edge('publish', 'brnrd do --reply evt-1') })).kind,
		'correspondence-desk'
	);
});

test('wait and dispatch are stations tethered to their detail', () => {
	assert.equal(
		resolvePlace(liveRun({ run_id: 'r1', edge: edge('wait', 'brnrd await --timeout 30m') })).kind,
		'watch-point'
	);
	assert.equal(
		resolvePlace(liveRun({ run_id: 'r1', edge: edge('dispatch', 'spawn: the-lane') })).kind,
		'strand-bay'
	);
});

test('lifecycle outranks the edge: a closing run stands at the cut line', () => {
	const run = liveRun({ run_id: 'r1', lifecycle: 'closing', edge: edge('publish', 'git push') });
	assert.equal(resolvePlace(run).kind, 'cut-line');
	assert.equal(resolvePlace(liveRun({ run_id: 'r2', lifecycle: 'starting' })).kind, 'wake-dock');
});

test('no boundary yet still resolves somewhere real — the camp chamber', () => {
	const place = resolvePlace(
		liveRun({ run_id: 'r1', room: { env: 'worktree', branch: 'brr/x', dir: 'brr-wt-x' } })
	);
	assert.deepEqual(place, { kind: 'chamber', label: 'brr-wt-x' });
});

// ── compileRoomGraph ────────────────────────────────────────────────────────

test('resident wears @, strands wear letters by start order', () => {
	const graph = compileRoomGraph(
		liveWire([
			liveRun({ run_id: 'parent', name: 'the-parent' }),
			liveRun({
				run_id: 'kid2',
				is_subspawn: true,
				parent_run_id: 'parent',
				started_at: '2026-08-26T11:00:00Z'
			}),
			liveRun({
				run_id: 'kid1',
				is_subspawn: true,
				parent_run_id: 'parent',
				started_at: '2026-08-26T10:30:00Z'
			})
		]),
		null
	);
	const glyphs = Object.fromEntries(graph.actors.map((a) => [a.runId, a.glyph]));
	assert.equal(glyphs.parent, '@');
	assert.equal(glyphs.kid1, 'a');
	assert.equal(glyphs.kid2, 'b');
});

test('camps group by branch+dir on their island; a cross-repo strand raises its own island', () => {
	const graph = compileRoomGraph(
		liveWire([
			liveRun({ run_id: 'parent' }),
			liveRun({
				run_id: 'kid',
				is_subspawn: true,
				parent_run_id: 'parent',
				repo_label: 'hugimuni-labs/brnrd-knowledge',
				room: { env: 'worktree', branch: 'brr/kb-pass', dir: 'brr-wt-kid' }
			})
		]),
		null
	);
	assert.deepEqual(
		graph.islands.map((i) => i.label),
		['hugimuni-labs/brnrd', 'hugimuni-labs/brnrd-knowledge']
	);
	const kbIsland = graph.islands[1];
	assert.equal(kbIsland.camps.length, 1);
	assert.deepEqual(kbIsland.camps[0].actorGlyphs, ['a']);
});

test('a dormant repo from the ledger keeps its ground — no camp, no actor', () => {
	const graph = compileRoomGraph(
		liveWire([]),
		ledgerWire([ledgerRow({ run_id: 'old', repo_label: 'hugimuni-labs/other' })])
	);
	assert.deepEqual(graph.islands, [{ label: 'hugimuni-labs/other', camps: [], forge: {} }]);
	assert.equal(graph.actors.length, 0);
});

test('an armed await stands a ^ watch fact carrying its deadline', () => {
	const graph = compileRoomGraph(
		liveWire([
			liveRun({
				run_id: 'r-wait',
				lifecycle: 'awaiting',
				await_until: '2026-08-26T10:32:00Z'
			})
		]),
		null
	);
	const wait = graph.watch.find((w) => w.mark === '^');
	assert.ok(wait, 'the tower sees the wait');
	assert.equal(wait!.source, 'r-wait');
	assert.equal(wait!.until, '2026-08-26T10:32:00Z');
	// a weaving run stands no ^ fact — the tower reports armed waits only
	const calm = compileRoomGraph(liveWire([liveRun({ run_id: 'r-live' })]), null);
	assert.equal(calm.watch.filter((w) => w.mark === '^').length, 0);
});

test('clockwork keeps every row the schedule wire sent — the narrowing is upstream', () => {
	// The predecessor of this test fed `completed` / `cancelled` /
	// `anchoring` rows and asserted a hand-written blocklist dropped them.
	// It was green against data production cannot make: `fetchScheduledWakes`
	// requests `?kind=scheduled`, so a finished run never reaches this wire,
	// and `anchoring` is not a status — it is `scheduled_for === null`. The
	// server's real dead vocabulary is eleven spellings over two sets
	// (`dashboard.py:63-64`); a listed pair caught two. A fixture is coverage
	// only if production can still produce it.
	const graph = compileRoomGraph(liveWire([]), null, undefined, {
		wakes: wakesWire([
			scheduledWake('nightly', 'recurring', '2026-08-26T11:00:00Z'),
			scheduledWake('paced', 'quota-paced', '2026-08-26T12:00:00Z')
		])
	});
	assert.deepEqual(
		graph.clockwork.map((entry) => entry.summary),
		['nightly', 'paced'],
		'a quota-paced wake is deferred, not dead — dropping it would hide real future intent'
	);
});

test('an anchoring entry carries no instant, so it can never own the countdown', () => {
	// The real shape of "not scheduled yet": an `every:` row the daemon has
	// seen but not yet computed a first fire for. It stays on the wire; the
	// camera's own `.filter((e) => e.nextAt)` is what keeps it off the T.
	const graph = compileRoomGraph(liveWire([]), null, undefined, {
		wakes: wakesWire([scheduledWake('anchoring', 'recurring', null)])
	});
	assert.equal(graph.clockwork.length, 1, 'the entry is real intent and stays');
	assert.equal(graph.clockwork[0].nextAt, null, 'it just has no instant to count to');
});

test('forge counts aggregate the island’s attested PR / issue / merge produce', () => {
	const graph = compileRoomGraph(
		liveWire([
			liveRun({ run_id: 'r1', relics_counts: { pr: 2, issue: 1, commit: 5 } }),
			liveRun({ run_id: 'r2', relics_counts: { pr: 1 } })
		]),
		null
	);
	assert.deepEqual(graph.islands[0].forge, { pr: 3, issue: 1 });
	// commits stay on the camp spur, not the dock
	assert.equal(graph.islands[0].camps[0].commits, 5);
});

test('work that names no legible resource stands at the camp work-bench', () => {
	// the shell place (2026-08-28): an act whose detail names no path — a
	// bare command — puts the actor at `$`, in plain sight, instead of
	// dissolving it into the camp marker
	const graph = compileRoomGraph(
		liveWire([liveRun({ run_id: 'r-shell', edge: edge('orient', 'ps aux') })]),
		null
	);
	const topo = compileTopology(graph);
	assert.ok(topo.actorPlaces['r-shell'].endsWith('#work-bench'), topo.actorPlaces['r-shell']);
});

test('cloth: live rows carry no spend; cut rows read the attested usd', () => {
	const graph = compileRoomGraph(
		liveWire([liveRun({ run_id: 'live1' })]),
		ledgerWire([
			ledgerRow({ run_id: 'cut1', usd_subscription_attributed: 1.57 }),
			ledgerRow({ run_id: 'cut2', usd_credits_equivalent: 0.42 })
		])
	);
	const live = graph.cloth.find((r) => r.runId === 'live1');
	assert.equal(live?.tense, 'live');
	assert.equal(live?.usd, null);
	assert.equal(graph.cloth.find((r) => r.runId === 'cut1')?.usd, 1.57);
	assert.equal(graph.cloth.find((r) => r.runId === 'cut2')?.usd, 0.42);
});

test('one run, one row: a live run already in the ledger keeps the cut row', () => {
	const graph = compileRoomGraph(
		liveWire([liveRun({ run_id: 'both' })]),
		ledgerWire([ledgerRow({ run_id: 'both' })])
	);
	const rows = graph.cloth.filter((r) => r.runId === 'both');
	assert.equal(rows.length, 1);
	assert.equal(rows[0].tense, 'cut');
});

test('pending letters sum across actors; stale daemon rows are never actors', () => {
	const graph = compileRoomGraph(
		liveWire([
			liveRun({ run_id: 'r1', portals: { pending: 2, oldest_at: null } }),
			liveRun({ run_id: 'r2', portals: { pending: 1, oldest_at: null } }),
			liveRun({ run_id: 'dead', daemon_stale: true, portals: { pending: 9, oldest_at: null } })
		]),
		null
	);
	assert.equal(graph.pendingLetters, 3);
	assert.ok(!graph.actors.some((a) => a.runId === 'dead'));
});

test('terrain accretes from the trail, deduped by boundary timestamp', () => {
	const run = liveRun({
		run_id: 'r1',
		edge: edge('mutate', 'Edit x.ts', { dir: 'src/frontend', at: '2026-08-26T10:12:00Z' })
	});
	const trail = [
		{ dir: 'src/brr', act: 'orient', at: '2026-08-26T10:05:00Z' },
		{ dir: 'src/frontend', act: 'mutate', at: '2026-08-26T10:08:00Z' },
		// the current boundary already recorded — must not double-count
		{ dir: 'src/frontend', act: 'mutate', at: '2026-08-26T10:12:00Z' }
	];
	const graph = compileRoomGraph(liveWire([run]), null, { r1: trail });
	const camp = graph.islands[0].camps[0];
	assert.deepEqual(
		camp.chambers.map((c) => [c.dir, c.visits]),
		[
			['src/brr', 1],
			['src/frontend', 2]
		]
	);
});

test('without a remembered trail the current boundary still grows one chamber', () => {
	const run = liveRun({
		run_id: 'r1',
		edge: edge('probe', 'pytest', { dir: 'tests' })
	});
	const graph = compileRoomGraph(liveWire([run]), null);
	assert.deepEqual(
		graph.islands[0].camps[0].chambers.map((c) => c.dir),
		['tests']
	);
});

test('fileFromDetail reads the leaf and refuses the non-file', () => {
	assert.equal(fileFromDetail('Edit asciiRoom.ts'), 'asciiRoom.ts');
	assert.equal(fileFromDetail('node --test src/lib/roomGraph.test.ts'), 'roomGraph.test.ts');
	assert.equal(fileFromDetail('git status --short'), null);
	assert.equal(fileFromDetail('pip install pkg==1.2.3'), null);
	assert.equal(fileFromDetail(null), null);
});

test('the empty world is a graph, not a crash', () => {
	const graph = compileRoomGraph(null, null);
	assert.deepEqual(graph.islands, []);
	assert.deepEqual(graph.actors, []);
	assert.deepEqual(graph.cloth, []);
	assert.equal(graph.pendingLetters, 0);
});
