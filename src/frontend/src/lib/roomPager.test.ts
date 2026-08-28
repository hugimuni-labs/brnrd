import assert from 'node:assert/strict';
import test from 'node:test';

import { compileRoomGraph, dirFromEdge, fileFromDetail, type TrailStep } from './roomGraph.ts';
import { compileTopology, dirId } from './roomTopology.ts';
import { emptyAtlas, layoutRoom } from './roomLayout.ts';
import { renderWorld, activityMark, type Camera } from './asciiCamera.ts';
import {
	recordPages,
	pagerFeed,
	readingsFor,
	advanceReadings,
	readingPhases,
	PAGER_CAP,
	READING_TICKS,
	type PagerPage,
	type Reading
} from './roomPager.ts';
import { referenceFrames } from './referenceTrace.ts';
import type { LiveRun, LiveRunsResponse } from './liveRuns.ts';

const RESIDENT = 'run-260827-1000-ref1';

function liveWire(runs: LiveRun[]): LiveRunsResponse {
	return {
		generated_at: '2026-08-27T10:20:00Z',
		runs,
		stale: false,
		reported_at: '2026-08-27T10:20:00Z',
		spawn_max_concurrent: 3
	};
}

function recordTrails(trails: Record<string, TrailStep[]>, runs: LiveRun[]) {
	for (const run of runs) {
		const dir = run.edge?.dir && run.edge.dir !== '.' ? run.edge.dir : null;
		const at = run.edge?.at ?? null;
		if (!dir || !at) continue;
		const trail = (trails[run.run_id] ??= []);
		if (trail.some((s) => s.at === at)) continue;
		trail.push({ dir, act: run.edge?.act ?? null, at, file: fileFromDetail(run.edge?.detail) });
	}
}

function sceneAt(frameIdx: number) {
	const trails: Record<string, TrailStep[]> = {};
	const frames = referenceFrames();
	for (const f of frames.slice(0, frameIdx + 1)) recordTrails(trails, f);
	const graph = compileRoomGraph(liveWire(frames[frameIdx]), null, trails);
	const topo = compileTopology(graph);
	const { layout } = layoutRoom(topo, emptyAtlas());
	return { graph, topo, layout, runs: frames[frameIdx] };
}

// ── the feed: attested injections only, deduped, capped ─────────────────────

test('recordPages mints a page only from an injected edge, once', () => {
	const frames = referenceFrames();
	const store: Record<string, PagerPage[]> = {};
	// #81–#84: boundaries, none injected — no pages
	for (const f of frames.slice(0, 4)) assert.deepEqual(recordPages(f, store), []);
	assert.deepEqual(pagerFeed(store), []);
	// #85: the injection — exactly one page, naming its carrier boundary
	const fresh = recordPages(frames[4], store, { [RESIDENT]: '@' });
	assert.equal(fresh.length, 1);
	assert.equal(fresh[0].runId, RESIDENT);
	assert.equal(fresh[0].act, 'probe');
	assert.equal(fresh[0].glyph, '@');
	// the same poll seen again mints nothing — the page already arrived
	assert.deepEqual(recordPages(frames[4], store), []);
	assert.equal(pagerFeed(store).length, 1);
});

test('the feed is capped and ordered newest first', () => {
	const store: Record<string, PagerPage[]> = {};
	for (let i = 0; i < PAGER_CAP + 10; i++) {
		const at = `2026-08-27T10:${String(i % 60).padStart(2, '0')}:${String(Math.floor(i / 60)).padStart(2, '0')}Z`;
		recordPages(
			[
				{
					run_id: 'r1',
					edge: {
						at,
						phase: null,
						act: 'orient',
						tools: [],
						detail: null,
						out_bytes: null,
						injected: true
					}
				}
			],
			store
		);
	}
	assert.equal(store['r1'].length, PAGER_CAP);
	const feed = pagerFeed(store);
	for (let i = 1; i < feed.length; i++) assert.ok(feed[i - 1].at >= feed[i].at, 'newest first');
});

// ── the ceremony: bounded, receipt-driven, one glance per actor ─────────────

test('a fresh page starts one bounded reading; it advances and ends', () => {
	const page: PagerPage = {
		at: '2026-08-27T10:14:00Z',
		runId: RESIDENT,
		glyph: '@',
		act: 'probe',
		detail: null
	};
	let readings = readingsFor([page], []);
	assert.equal(readings.length, 1);
	assert.equal(readings[0].ticksLeft, READING_TICKS);
	assert.deepEqual(readingPhases(readings), { [RESIDENT]: READING_TICKS });
	// a second page for the same actor restarts, never stacks
	readings = readingsFor([page], readings);
	assert.equal(readings.length, 1);
	let guard = 0;
	while (readings.length > 0 && guard < READING_TICKS + 5) {
		readings = advanceReadings(readings);
		guard++;
	}
	assert.equal(readings.length, 0, 'the ceremony ends');
	assert.ok(guard <= READING_TICKS, 'bounded by its tick budget');
	// no fresh pages ⇒ readings pass through untouched
	const standing: Reading[] = [{ actorRunId: 'x', ticksLeft: 3 }];
	assert.equal(readingsFor([], standing), standing);
});

// ── the render: the strip, the mind-connect, the embodied act ───────────────

const CAM: Camera = { center: { x: 6, y: 0 }, cols: 96, rows: 20, level: 'island' };

test('the PAGER strip renders pages by carrier and flashes as state', () => {
	const { graph, topo, layout } = sceneAt(4); // #85, the injection
	const store: Record<string, PagerPage[]> = {};
	recordPages(referenceFrames()[4], store, { [RESIDENT]: '@' });
	const out = renderWorld(topo, layout, graph, CAM, { pages: pagerFeed(store) });
	assert.match(out, /▯ PAGER/);
	assert.match(out, /✉×1 read/);
	assert.match(out, /10:14 ✉ @ rode probe/);
	// clock-free and deterministic: the flash diff can ride it
	assert.equal(out, renderWorld(topo, layout, graph, CAM, { pages: pagerFeed(store) }));
	// no pages ⇒ the field still stands, honestly empty (the pager is the
	// injection-status instrument now, not a sometimes-strip): both tenses
	// named, nothing invented
	const empty = renderWorld(topo, layout, graph, CAM, {});
	assert.match(empty, /▯ PAGER/);
	assert.match(empty, /none read yet/);
});

test('the mind-connect renders in place: tether at the actor, no relocation', () => {
	const { graph, topo, layout } = sceneAt(4);
	// frame the actor: the ceremony happens where it stands
	const center = layout.nodes[topo.actorPlaces[RESIDENT]];
	const cam: Camera = { center, cols: 96, rows: 20, level: 'island' };
	const bare = renderWorld(topo, layout, graph, cam, {});
	const reading = renderWorld(topo, layout, graph, cam, {
		reading: { [RESIDENT]: READING_TICKS }
	});
	// the bare render has the standing PAGER field but no tether — the plug
	// only closes (▯⌁) while a reading ceremony is live
	assert.doesNotMatch(bare, /▯⌁/);
	assert.match(reading, /▯⌁ PAGER/, 'the pager field shows the connection');
	assert.match(reading, /▯[⌁∿≋]b·_·d/, 'pager + tether + the face that is the body');
	// the mind-connect reaches *down* toward the pager field below the map:
	// the reading render carries tether frames the bare render does not
	const tetherFrames = (s: string) => (s.match(/[∿≋]/g) ?? []).length;
	assert.ok(
		tetherFrames(reading) > tetherFrames(bare),
		'the tether line descends toward the pager field'
	);
	// the actor's place did not change — only its stance did
	assert.equal(topo.actorPlaces[RESIDENT], compileTopology(graph).actorPlaces[RESIDENT]);
});

test('a mutate at the chart table is embodied: ✎ with the leaf', () => {
	const { graph, topo, layout } = sceneAt(5); // #86: Write .card
	const actor = graph.actors.find((a) => a.runId === RESIDENT)!;
	const kind = topo.nodes[topo.actorPlaces[RESIDENT]].kind;
	assert.equal(kind, 'chart-table');
	assert.equal(activityMark(actor, kind), '✎ .card');
	const out = renderWorld(topo, layout, graph, CAM, {});
	assert.match(out, /b·_·d ✎ \.card/, 'the act stands beside the body');
});

// ── the dynamic trie: footsteps derive from the paths the acts name ─────────

const REPO = 'hugimuni-labs/brnrd';

function edgeOf(detail: string | null, dir: string | null = '.') {
	return {
		at: '2026-08-27T10:00:00Z',
		phase: null,
		act: 'mutate',
		tools: [],
		detail,
		out_bytes: null,
		injected: false,
		dir
	};
}

test('dirFromEdge: the cwd wins when it is not the root', () => {
	assert.equal(dirFromEdge(edgeOf('Edit x.ts', 'src/brr')), 'src/brr');
});

test('dirFromEdge: detail text never substitutes for daemon-attested cwd', () => {
	assert.equal(dirFromEdge(edgeOf('grep -rn "x" src/frontend/src/lib')), null);
	assert.equal(dirFromEdge(edgeOf('Edit /Users/g/Source/Projects/brnrd/src/lib/x.ts')), null);
	assert.equal(dirFromEdge(edgeOf(null)), null);
});

test('an attested in-tree cwd grows the trie and places the actor in its chamber', () => {
	const runs = [
		{
			run_id: 'r-root',
			repo_label: REPO,
			is_subspawn: false,
			parent_run_id: null,
			started_at: '2026-08-27T10:00:00Z',
			room: { env: 'host', branch: 'brr/x', dir: null },
			edge: edgeOf(
				'Edit · /Users/g/Source/Projects/brnrd/src/frontend/src/lib/roomGraph.ts',
				'src/frontend/src/lib'
			)
		}
	] as unknown as LiveRun[];
	const graph = compileRoomGraph(liveWire(runs), null, {});
	const chamberDirs = graph.islands[0].camps[0].chambers.map((c) => c.dir);
	assert.deepEqual(chamberDirs, ['src/frontend/src/lib']);
	const topo = compileTopology(graph);
	assert.equal(topo.actorPlaces['r-root'], dirId(REPO, ['src', 'frontend', 'src', 'lib']));
});

test('an orient at the portal rack reads as opening a letter', () => {
	assert.equal(
		activityMark({ act: 'orient', detail: 'Read inbox.json' }, 'portal-rack'),
		'✉ inbox.json'
	);
	// generic reading elsewhere; nothing legible ⇒ no mark, never a guess
	assert.equal(activityMark({ act: 'orient', detail: 'Read .card' }, 'chart-table'), '☰ .card');
	assert.equal(activityMark({ act: 'orient', detail: null }, 'chart-table'), null);
	assert.equal(activityMark({ act: 'mutate', detail: null }, 'chart-table'), '✎');
	// stations only — a chamber already renders its own file leaf
	assert.equal(activityMark({ act: 'mutate', detail: 'Edit a.ts' }, 'directory'), null);
});

test('the feed scopes to live runs, and the store keeps the rest', () => {
	// Unscoped, the strip read `✉×152 read · nothing waiting` on a quiet
	// account — a number that is neither wrong nor about anything the reader
	// can act on, because 149 of those pages rode runs that ended days ago.
	// The store keeps them on purpose: a page is how a finished run's traffic
	// stays inspectable. What changed is that the *strip* is a condition
	// readout, and a condition is about now.
	const store: Record<string, PagerPage[]> = {
		'run-live': [
			{ at: '2026-08-28T16:30:00Z', runId: 'run-live', glyph: '@', act: 'orient', detail: null }
		],
		'run-dead': [
			{ at: '2026-08-27T09:00:00Z', runId: 'run-dead', glyph: 'a', act: 'mutate', detail: null },
			{ at: '2026-08-27T09:05:00Z', runId: 'run-dead', glyph: 'a', act: 'publish', detail: null }
		]
	};
	assert.equal(pagerFeed(store).length, 3, 'unscoped is still the whole history');
	const scoped = pagerFeed(store, new Set(['run-live']));
	assert.deepEqual(
		scoped.map((p) => p.runId),
		['run-live']
	);
	assert.equal(store['run-dead'].length, 2, 'and the dead run keeps its pages');
});

test('no live runs is a real answer, not a missing argument', () => {
	// `undefined` means "do not scope"; an empty set means "nothing is live".
	// Collapsing the two would make a quiet account render its whole history
	// as if it were current — the exact defect this scoping exists to end.
	const store: Record<string, PagerPage[]> = {
		'run-dead': [
			{ at: '2026-08-27T09:00:00Z', runId: 'run-dead', glyph: 'a', act: 'mutate', detail: null }
		]
	};
	assert.equal(pagerFeed(store, new Set()).length, 0);
	assert.equal(pagerFeed(store, undefined).length, 1);
});
