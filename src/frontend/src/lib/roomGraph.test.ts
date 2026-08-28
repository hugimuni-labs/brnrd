import assert from 'node:assert/strict';
import test from 'node:test';

import { compileRoomGraph, dirFromEdge, fileFromDetail, resolvePlace } from './roomGraph.ts';
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

test('garage and clockwork compile their real wire extras, preserving stale binding fuel', () => {
	const graph = compileRoomGraph(liveWire([]), null, undefined, {
		wakes: {
			generated_at: '2026-08-26T10:20:00Z',
			total: 1,
			rows: [
				{
					id: 'wake-1',
					kind: 'scheduled',
					source: 'schedule',
					summary: 'Tend the room',
					scheduled_for: '2026-08-26T12:00:00Z',
					status: 'armed',
					phase: 'at',
					bucket: 'at',
					repo_label: 'hugimuni-labs/brnrd',
					daemon_name: 'brnrd',
					conversation_key: null,
					reported_at: '2026-08-26T10:20:00Z'
				}
			]
		},
		quota: {
			generated_at: '2026-08-26T10:20:00Z',
			runner_quotas: [
				{
					shell: 'claude',
					status: 'stale',
					daemon_stale: true,
					windows: [
						{
							label: 'weekly',
							used: null,
							limit: null,
							percent: null,
							reset: null,
							last_known: { used: null, limit: null, percent: 43, reset: null }
						},
						{
							label: '5h window',
							used: null,
							limit: null,
							percent: null,
							reset: null,
							last_known: { used: null, limit: null, percent: 12, reset: null }
						},
						{
							label: 'weekly (Fable)',
							used: null,
							limit: null,
							percent: null,
							reset: null,
							last_known: { used: null, limit: null, percent: 2, reset: null }
						}
					]
				}
			]
		}
	});

	assert.deepEqual(
		graph.clockwork.map((entry) => entry.summary),
		['Tend the room']
	);
	assert.deepEqual(graph.garage, [
		{
			shell: 'claude',
			status: 'stale',
			windows: [{ label: '5h', percent: 12 }],
			// No reset instant on this stale snapshot, so no clock — null, not
			// a zero. A ceiling without its clock is half an instruction (10%
			// resetting in 40 minutes and 10% resetting in three days are
			// opposite advice), so the row carries the clock when the wire
			// attests one and says nothing when it does not.
			resetShort: null
		}
	]);
});

test('the binding row carries its reset clock when the wire attests one', () => {
	const graph = compileRoomGraph(liveWire([]), null, undefined, {
		quota: {
			generated_at: '2026-08-26T10:20:00Z',
			runner_quotas: [
				{
					shell: 'claude',
					status: 'known',
					windows: [
						{
							label: '5h window',
							used: null,
							limit: null,
							percent: 12,
							reset: 'resets 8:10pm',
							resets_at: 1787950000
						},
						{
							label: 'weekly',
							used: null,
							limit: null,
							percent: 43,
							reset: 'resets Aug 29',
							resets_at: 1788005000
						}
					]
				}
			]
		}
	} as never);
	assert.equal(graph.garage[0].windows[0].label, '5h', 'the binding window, not the weekly one');
	assert.ok(graph.garage[0].resetShort, 'and its clock rides with it');
});

test('slots read the pool the wire has carried all along', () => {
	// `spawn_max_concurrent` has been on the live-runs response since the loom
	// envelope's phase 1 and nothing read it onto the graph, so the room could
	// show a resident and three strands without ever saying how many more it
	// could hold.
	const wire = liveWire([]);
	const graph = compileRoomGraph({ ...wire, spawn_max_concurrent: 8 }, null);
	assert.deepEqual(graph.slots, { active: 0, max: 8 });

	// A daemon that never reported a width says so: null is not zero, and
	// `0/0 slots` would be a claim nobody made.
	const unreported = compileRoomGraph({ ...wire, spawn_max_concurrent: null }, null);
	assert.equal(unreported.slots.max, null);
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

test('only a daemon-attested tree directory grows terrain', () => {
	const observedRootDetails = [
		'git fetch origin/brr/room-fuel.md',
		'opacity 0.4/0.3/0.2',
		'gh api pull/1671',
		'Write /tmp/reply1.md'
	];
	const runs = observedRootDetails.map((detail, index) =>
		liveRun({
			run_id: `root-${index}`,
			room: { env: 'worktree', branch: `brr/observed-${index}`, dir: `run-observed-${index}` },
			edge: edge('mutate', detail)
		})
	);
	runs.push(
		liveRun({
			run_id: 'in-tree',
			room: { env: 'worktree', branch: 'brr/real-path', dir: 'run-real-path' },
			edge: edge('mutate', 'Edit roomGraph.ts', { dir: 'src/frontend/src/lib' })
		})
	);
	const graph = compileRoomGraph(liveWire(runs), null);
	assert.deepEqual(
		graph.islands[0].camps.flatMap((camp) => camp.chambers.map((chamber) => chamber.dir)),
		['src/frontend/src/lib']
	);
	for (const run of runs.slice(0, -1)) {
		assert.equal(
			graph.actors.find((actor) => actor.runId === run.run_id)?.place.label,
			run.room?.dir
		);
	}
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

// ── TERRAIN GROWS FROM TERRAIN ─────────────────────────────────────────────

test('a detail path extends attested ground, and only that', () => {
	// `src` was attested by a real cwd, so `src/frontend/src/lib` — named in
	// a detail while the actor sat at the root — is ground it can reach.
	const grown = dirFromEdge(
		{ act: 'orient', detail: "sed -n '1,180p' src/frontend/src/lib/quota.ts", dir: '.', at: null },
		['src']
	);
	assert.equal(grown, 'src/frontend/src/lib', 'the file leaf drops; chambers are directories');
});

test('the shapes that kept minting fake chambers cannot extend anything', () => {
	// Each of these grew a room on the live map at some point, and each was
	// answered with a narrower shape rule that then met the next shape.
	// `0.4/0.3/0.2` is a version or an opacity ramp; `origin/brr/…` is a git
	// ref; `pull/1671` is a URL fragment; `~/.local/state/brnrd/…` is the
	// account home, which matches because it carries the project's own name.
	const attested = ['src', 'src/frontend'];
	for (const detail of [
		'stroke-opacity 0.4/0.3/0.2 across the ramp',
		'git push origin/brr/the-fuel-you-can-read',
		'gh api repos/hugimuni-labs/brnrd/pulls/1671/comments',
		'cat ~/.local/state/brnrd/accounts/acc_x/home/knowledge/index.md'
	]) {
		assert.equal(
			dirFromEdge({ act: 'orient', detail, dir: '.', at: null }, attested),
			null,
			detail
		);
	}
});

test('an attested cwd is terrain without needing to extend anything', () => {
	// The daemon resolved it against the run's real checkout before
	// publishing. It is the ground everything else grows from.
	assert.equal(
		dirFromEdge({ act: 'mutate', detail: 'Edit x.ts', dir: 'src/brr', at: null }, []),
		'src/brr'
	);
});

test('with nothing attested yet, a detail path grows nothing', () => {
	// Not a silent drop: the boundary is still rendered on the actor's own
	// line. It just does not mint ground out of a string nobody has stood on.
	assert.equal(
		dirFromEdge({ act: 'orient', detail: 'cat src/frontend/x.ts', dir: '.', at: null }, []),
		null
	);
	assert.equal(
		dirFromEdge({ act: 'orient', detail: 'cat src/frontend/x.ts', dir: '.', at: null }),
		null,
		'and omitting the attested set is the same answer, never a wider one'
	);
});

test('the deepest attested ground wins when several could grow', () => {
	const dir = dirFromEdge(
		{ act: 'mutate', detail: 'Edit src/frontend/src/lib/roomGraph.ts', dir: '.', at: null },
		['src', 'src/frontend']
	);
	assert.equal(dir, 'src/frontend/src/lib');
});

test('the island takes its first step off the root from the run own room', () => {
	// A fresh run has no trail. Without seeding the room's own dir there is
	// nothing to extend, and the island could never grow past its root — the
	// exact "a dozen edits into src/frontend grew zero terrain" gap.
	const graph = compileRoomGraph(
		liveWire([
			liveRun({
				run_id: 'r1',
				room: { branch: 'brr/x', dir: 'src' },
				edge: edge('mutate', 'Edit src/frontend/src/lib/quota.ts', {
					dir: '.',
					at: '2026-08-28T10:00:00Z'
				})
			})
		]),
		null
	);
	const chambers = graph.islands[0].camps[0].chambers ?? [];
	assert.ok(
		JSON.stringify(chambers).includes('src/frontend'),
		'the detail reached ground the room block already attested'
	);
});
