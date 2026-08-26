import assert from 'node:assert/strict';
import test from 'node:test';

import { renderRoomGraph, placeLabel, LEGEND } from './asciiRoom.ts';
import { compileRoomGraph } from './roomGraph.ts';
import type { LiveRun, LiveRunsResponse } from './liveRuns.ts';
import type { RunLedgerResponse, RunLedgerRow } from './runLedger.ts';

const NOW = Date.parse('2026-08-26T10:30:00Z');

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
		runner: { name: 'claude-fable' },
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
		usd_subscription_attributed: 0.42,
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

const busyScene = () =>
	compileRoomGraph(
		liveWire([
			liveRun({
				run_id: 'parent',
				name: 'the-loom-flattened',
				mood_rest: 'b·_·d',
				card_text: '## Plan\n- [x] model\n- [ ] renderer\n- [ ] PR',
				relics_counts: { commit: 2 },
				portals: { pending: 1, oldest_at: '2026-08-26T10:25:00Z' },
				edge: {
					at: '2026-08-26T10:28:00Z',
					phase: 'PostToolUse',
					act: 'mutate',
					tools: ['Edit'],
					detail: 'Edit asciiRoom.ts',
					out_bytes: 100,
					injected: true,
					dir: 'src/frontend'
				}
			}),
			liveRun({
				run_id: 'kid',
				name: 'the-kb-pass',
				is_subspawn: true,
				parent_run_id: 'parent',
				repo_label: 'hugimuni-labs/brnrd-knowledge',
				room: { env: 'worktree', branch: 'brr/kb-pass', dir: 'brr-wt-kid' },
				edge: {
					at: '2026-08-26T10:29:00Z',
					phase: 'PostToolUse',
					act: 'probe',
					tools: ['Bash'],
					detail: 'node --test graph.test.ts',
					out_bytes: 50,
					injected: false,
					dir: '.'
				}
			})
		]),
		ledgerWire([ledgerRow({ run_id: 'old' })])
	);

test('same graph, same board — the renderer is deterministic', () => {
	const graph = busyScene();
	assert.equal(renderRoomGraph(graph, { now: NOW }), renderRoomGraph(graph, { now: NOW }));
});

test('the busy scene tells the whole story on one board', () => {
	const board = renderRoomGraph(busyScene(), { now: NOW });
	// world: both islands, the camp, the shared checkout named
	assert.match(board, /hugimuni-labs\/brnrd /);
	assert.match(board, /hugimuni-labs\/brnrd-knowledge/);
	assert.match(board, /brr\/the-ascii-camera · the shared checkout/);
	assert.match(board, /brr\/kb-pass · brr-wt-kid/);
	// actors: resident standing in the chamber its boundary attested (the
	// terrain row carries the path; the body stands under it), strand at its rig
	assert.match(board, /└ src\/frontend {2}·mutate/);
	assert.match(board, /@ b·_·d/);
	assert.match(board, /a {2}RIG/);
	// process: the attested boundary with the injection pulse
	assert.match(board, /⌁ mutate · Edit asciiRoom\.ts {2}✉>>>/);
	// traffic: the letter resting, counted not fabricated
	assert.match(board, /◇×1 resting at the rack · oldest 5m/);
	// control: the chart row with course position
	assert.match(board, /K @ the-loom-flattened {3}course 1\/3 → renderer/);
	// time: live row above the cut line, spend only below it
	assert.match(board, /LIVE @ the-loom-flattened/);
	assert.match(board, /cut the-prior-run {2}30m {2}\$0\.42/);
	const liveLine = board.split('\n').find((l) => l.startsWith('LIVE @'));
	assert.ok(liveLine && !liveLine.includes('$'), 'live rows must not fabricate spend');
});

test('the map packs islands side by side when the plane is wide enough', () => {
	const wide = renderRoomGraph(busyScene(), { width: 140, now: NOW });
	const paired = wide.split('\n').some((l) => (l.match(/╔/g) ?? []).length === 2);
	assert.ok(paired, 'two islands should share a row at width 140');
	const narrow = renderRoomGraph(busyScene(), { width: 76, now: NOW });
	const stacked = narrow.split('\n').every((l) => (l.match(/╔/g) ?? []).length <= 1);
	assert.ok(stacked, 'islands should stack at width 76');
});

test('a forge-bound actor stands at the FORGE on the coast, not on its island', () => {
	const graph = compileRoomGraph(
		liveWire([
			liveRun({
				run_id: 'r1',
				edge: {
					at: '2026-08-26T10:28:00Z',
					phase: 'PostToolUse',
					act: 'publish',
					tools: ['Bash'],
					detail: 'git push origin brr/x',
					out_bytes: 10,
					injected: false,
					dir: '.'
				}
			})
		]),
		null
	);
	const board = renderRoomGraph(graph, { now: NOW });
	assert.match(board, /FORGE/);
	assert.match(board, /@ {2}FORGE/);
	assert.match(board, /⌁ publish · git push origin brr\/x/);
});

test('the injection pulse renders only when the boundary attested it', () => {
	const quiet = compileRoomGraph(
		liveWire([
			liveRun({
				run_id: 'r1',
				edge: {
					at: '2026-08-26T10:28:00Z',
					phase: 'PostToolUse',
					act: 'orient',
					tools: ['Read'],
					detail: 'Read liveRuns.ts',
					out_bytes: 10,
					injected: false,
					dir: 'src/frontend'
				}
			})
		]),
		null
	);
	assert.ok(!renderRoomGraph(quiet, { now: NOW }).includes('✉>>>'));
});

test('stage 0 — between wakes: ground and history survive, no body is invented', () => {
	const board = renderRoomGraph(
		compileRoomGraph(liveWire([]), ledgerWire([ledgerRow({ run_id: 'old' })])),
		{
			now: NOW
		}
	);
	assert.match(board, /G · quiet/);
	assert.match(board, /dormant · no camp, no actor/);
	assert.match(board, /cut the-prior-run/);
	assert.ok(!board.includes('@'), 'no actor glyph on a dormant board');
	assert.ok(!board.includes('CHARTS'), 'no control chrome without a run');
});

test('every line respects the width budget', () => {
	const board = renderRoomGraph(busyScene(), { width: 76, now: NOW });
	for (const line of board.split('\n')) {
		assert.ok(line.length <= 76, `line too wide: ${JSON.stringify(line)}`);
	}
});

test('distinct place kinds wear distinct stance words — the blur test', () => {
	const labels = new Set(
		(
			[
				{ kind: 'test-rig', label: null },
				{ kind: 'forge-dock', label: null },
				{ kind: 'correspondence-desk', label: null },
				{ kind: 'chart-table', label: null },
				{ kind: 'strand-bay', label: null },
				{ kind: 'watch-point', label: null },
				{ kind: 'wake-dock', label: null },
				{ kind: 'cut-line', label: null }
			] as const
		).map((p) => placeLabel(p))
	);
	assert.equal(labels.size, 8);
});

test('a host-absolute path in the detail folds to its tail — never printed whole', () => {
	const graph = compileRoomGraph(
		liveWire([
			liveRun({
				run_id: 'r1',
				edge: {
					at: '2026-08-26T10:28:00Z',
					phase: 'PostToolUse',
					act: 'mutate',
					tools: ['Edit'],
					detail: 'Edit /Users/gurio/Source/Projects/brnrd/src/frontend/repro/drive-ascii.mjs',
					out_bytes: 10,
					injected: false,
					dir: '.'
				}
			})
		]),
		null
	);
	const board = renderRoomGraph(graph, { now: NOW });
	assert.ok(!board.includes('/Users/'), 'host path must not reach the board');
	assert.match(board, /⌁ mutate · Edit …\/repro\/drive-ascii\.mjs/);
});

test('the tree grows from footsteps and the actor stands under its chamber', () => {
	const graph = compileRoomGraph(
		liveWire([
			liveRun({
				run_id: 'r1',
				mood_rest: 'b·_·d',
				edge: {
					at: '2026-08-26T10:12:00Z',
					phase: 'PostToolUse',
					act: 'mutate',
					tools: ['Edit'],
					detail: 'Edit roomGraph.ts',
					out_bytes: 10,
					injected: false,
					dir: 'src/frontend'
				}
			})
		]),
		null,
		{
			r1: [
				{ dir: 'src/brr', act: 'orient', at: '2026-08-26T10:05:00Z' },
				{ dir: 'src/frontend', act: 'mutate', at: '2026-08-26T10:08:00Z' }
			]
		}
	);
	const board = renderRoomGraph(graph, { now: NOW });
	assert.match(board, /├ src\/brr {2}·orient/);
	assert.match(board, /└ src\/frontend {2}·mutate ×2/);
	// the actor stands under its chamber row, body only — no restated stance
	const lines = board.split('\n');
	const chamberIdx = lines.findIndex((l) => l.includes('└ src/frontend'));
	assert.ok(chamberIdx > 0);
	assert.match(lines[chamberIdx + 1], /@ b·_·d\s*║/);
});

test('clock-free render drops elapsed labels but keeps the same line structure', () => {
	const graph = busyScene();
	const withNow = renderRoomGraph(graph, { now: NOW }).split('\n');
	const bare = renderRoomGraph(graph).split('\n');
	assert.equal(withNow.length, bare.length);
	assert.ok(withNow.some((l) => l.includes('oldest 5m')));
	assert.ok(!bare.some((l) => l.includes('oldest ')));
});

test('the legend names every glyph the boards above used', () => {
	for (const mark of ['@', '◇', '✉>>>', '⌁', 'CLOTH']) assert.ok(LEGEND.includes(mark));
});
