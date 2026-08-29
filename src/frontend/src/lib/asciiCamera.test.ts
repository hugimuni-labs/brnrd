import assert from 'node:assert/strict';
import test from 'node:test';

import { compileRoomGraph, fileFromDetail, type TrailStep } from './roomGraph.ts';
import { compileTopology, dirId, islandRootId, routeBetween } from './roomTopology.ts';
import { emptyAtlas, layoutRoom, terminalRequest, type AtlasMemory } from './roomLayout.ts';
import {
	CAMERA_LINE_HEIGHT_FALLBACK_PX,
	LEGEND,
	cameraCenterFor,
	isCameraHotkey,
	renderWorld,
	type Camera
} from './asciiCamera.ts';
import { referenceFrames } from './referenceTrace.ts';
import type { LiveRun, LiveRunsResponse } from './liveRuns.ts';

const REPO = 'hugimuni-labs/brnrd';

function liveWire(runs: LiveRun[]): LiveRunsResponse {
	return {
		generated_at: '2026-08-27T10:20:00Z',
		runs,
		stale: false,
		reported_at: '2026-08-27T10:20:00Z',
		spawn_max_concurrent: 3
	};
}

/** The page's trail memory, emulated: attested footsteps, deduped by
 *  boundary timestamp. */
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

function pipeline(runs: LiveRun[], trails: Record<string, TrailStep[]>, memory: AtlasMemory) {
	const graph = compileRoomGraph(liveWire(runs), null, trails);
	const topo = compileTopology(graph);
	const placed = layoutRoom(topo, memory);
	return { graph, topo, layout: placed.layout, memory: placed.memory };
}

// ── determinism and the camera contract ─────────────────────────────────────

test('same graph, layout, camera and now ⇒ same bytes', () => {
	const trails: Record<string, TrailStep[]> = {};
	const frames = referenceFrames();
	recordTrails(trails, frames[1]);
	const { graph, topo, layout } = pipeline(frames[1], trails, emptyAtlas());
	const cam: Camera = { center: { x: 6, y: 0 }, cols: 76, rows: 20, level: 'island' };
	const now = Date.parse('2026-08-27T10:06:00Z');
	assert.equal(
		renderWorld(topo, layout, graph, cam, { now }),
		renderWorld(topo, layout, graph, cam, { now })
	);
});

test('resizing changes the camera window, never world coordinates', () => {
	const trails: Record<string, TrailStep[]> = {};
	const frames = referenceFrames();
	recordTrails(trails, frames[0]);
	const { graph, topo, layout } = pipeline(frames[0], trails, emptyAtlas());
	const before = JSON.stringify(layout.nodes);
	renderWorld(topo, layout, graph, { center: { x: 0, y: 0 }, cols: 60, rows: 12, level: 'island' });
	renderWorld(topo, layout, graph, {
		center: { x: 0, y: 0 },
		cols: 140,
		rows: 40,
		level: 'island'
	});
	assert.equal(JSON.stringify(layout.nodes), before);
});

test('a place outside the window is not painted; live actors off-frame become edge bearings', () => {
	const trails: Record<string, TrailStep[]> = {};
	const frames = referenceFrames();
	recordTrails(trails, frames[0]);
	const { graph, topo, layout } = pipeline(frames[0], trails, emptyAtlas());
	// camera framed far away from the actor's chamber
	const farCam: Camera = { center: { x: 200, y: 200 }, cols: 60, rows: 12, level: 'island' };
	const board = renderWorld(topo, layout, graph, farCam);
	const boardOnly = board.split('\nCHARTS')[0];
	assert.ok(!boardOnly.includes('frontend/'), 'terrain out of frame stays unpainted');
	assert.ok(/[←↑↖↙] @/.test(boardOnly), 'the off-frame actor renders as a bearing');
});

test('panning reveals space that was genuinely outside the previous view', () => {
	const trails: Record<string, TrailStep[]> = {};
	const frames = referenceFrames();
	recordTrails(trails, frames[2]);
	const { graph, topo, layout } = pipeline(frames[2], trails, emptyAtlas());
	const rootP = layout.nodes[islandRootId(REPO)];
	const testsP = layout.nodes[dirId(REPO, ['src', 'frontend', 'tests'])];
	const camA: Camera = {
		center: { x: rootP.x - 20, y: rootP.y },
		cols: 64,
		rows: 14,
		level: 'island'
	};
	const camB: Camera = {
		center: { x: testsP.x, y: testsP.y },
		cols: 64,
		rows: 14,
		level: 'island'
	};
	const a = renderWorld(topo, layout, graph, camA).split('\n')[5] ?? '';
	const aFull = renderWorld(topo, layout, graph, camA);
	const bFull = renderWorld(topo, layout, graph, camB);
	assert.ok(!aFull.split('\nCHARTS')[0].includes('tests/'));
	assert.ok(bFull.split('\nCHARTS')[0].includes('tests/'));
	void a;
});

test('cameraCenterFor frames the bounding box when it fits, else the destination', () => {
	const trails: Record<string, TrailStep[]> = {};
	const frames = referenceFrames();
	for (const frame of frames.slice(0, 3)) recordTrails(trails, frame);
	const { layout } = pipeline(frames[2], trails, emptyAtlas());
	const a = dirId(REPO, ['src', 'frontend', 'src', 'lib']);
	const b = dirId(REPO, ['src', 'frontend', 'tests']);
	const fits = cameraCenterFor(layout, [a, b], 200, 60);
	assert.deepEqual(fits, {
		x: (layout.nodes[a].x + layout.nodes[b].x) / 2,
		y: (layout.nodes[a].y + layout.nodes[b].y) / 2
	});
	const cramped = cameraCenterFor(layout, [a, b], 10, 3);
	assert.deepEqual(cramped, layout.nodes[a]);
});

test('atlas level compresses the same coordinates — islands only, no different geography', () => {
	const trails: Record<string, TrailStep[]> = {};
	const frames = referenceFrames();
	recordTrails(trails, frames[3]);
	const { graph, topo, layout } = pipeline(frames[3], trails, emptyAtlas());
	const cam: Camera = { center: { x: 0, y: 22 }, cols: 76, rows: 20, level: 'atlas' };
	const board = renderWorld(topo, layout, graph, cam).split('\nCHARTS')[0];
	assert.ok(board.includes('hugimuni-labs/brnrd'));
	assert.ok(board.includes('hugimuni-labs/brnrd-knowledge'));
	assert.ok(!board.includes('frontend/'), 'chamber terrain stays below atlas scale');
	assert.ok(!/[─│═║┄┆]/.test(board), 'corridors stay below atlas scale');
});

test('pager overflow rows stay inside the camera width', () => {
	const { graph, topo, layout } = pipeline([], {}, emptyAtlas());
	const pages = Array.from({ length: 4 }, (_, i) => ({
		runId: `r${i}`,
		glyph: '@',
		at: '2026-08-27T10:00:00Z',
		act: 'read',
		detail: null,
		injection: null
	}));
	const board = renderWorld(
		topo,
		layout,
		graph,
		{ center: { x: 0, y: 0 }, cols: 10, rows: 4, level: 'island' },
		{ pages }
	);
	assert.ok(board.split('\n').every((line) => line.length <= 10));
});

test('camera countdowns preserve overdue truth from the shared scheduler formatter', () => {
	const frames = referenceFrames();
	const { graph, topo, layout } = pipeline(frames[0], {}, emptyAtlas());
	graph.clockwork = [
		{ summary: 'late', nextAt: '2026-08-27T09:00:00Z', status: 'scheduled', repoLabel: null }
	];
	const board = renderWorld(
		topo,
		layout,
		graph,
		{ center: { x: -4, y: 2 }, cols: 76, rows: 20, level: 'island' },
		{ now: Date.parse('2026-08-27T10:00:00Z') }
	);
	assert.match(board, /T overdue 1h 0m/);
});

test('the legend names every glyph the camera actually renders', () => {
	// `lib` and HOME were rendered and unlisted. The legend prints the two
	// `⌂` kinds side by side rather than carrying a note about the
	// collision: a legend that explains its own open questions to the reader
	// has become a TODO with an audience.
	assert.match(LEGEND, /lib library/);
	assert.match(LEGEND, /⌂ island root · ⌂ HOME/);
	assert.ok(!/proposed|TODO/i.test(LEGEND), 'no design note ships inside the legend');
});

test('camera hotkeys yield modified keys back to the browser', () => {
	assert.equal(isCameraHotkey({ key: 'f', metaKey: false, ctrlKey: false }), true);
	assert.equal(isCameraHotkey({ key: 'a', metaKey: false, ctrlKey: false }), true);
	assert.equal(
		isCameraHotkey({ key: 'f', metaKey: false, ctrlKey: true }),
		false,
		'Ctrl+F is find'
	);
	assert.equal(
		isCameraHotkey({ key: 'a', metaKey: true, ctrlKey: false }),
		false,
		'Cmd+A is select all'
	);
	assert.equal(isCameraHotkey({ key: 'z', metaKey: false, ctrlKey: false }), false);
});

test('the line height is a fallback, not the stylesheet copied into TypeScript', () => {
	// Asserting `CONSTANT === 16.2` was a test of nothing: the value and the
	// assertion were the same fact written twice, and it would have gone on
	// passing while the CSS moved underneath it. What is actually load-bearing
	// is that the page *measures* — `getComputedStyle(probeEl).lineHeight` in
	// `measureCols`, beside the `charW` measurement it already trusted — and
	// falls back only when that read is unusable.
	assert.ok(Number.isFinite(CAMERA_LINE_HEIGHT_FALLBACK_PX));
	assert.ok(CAMERA_LINE_HEIGHT_FALLBACK_PX > 0);
	// Honestly uncovered: the measurement itself is DOM-side and this harness
	// is node-only. `repro/drive-ascii.mjs` is where a real drag-scale check
	// would live, and it does not have one.
});

test('garage names binding provider fuel in frame and in the off-frame HOME bearing', () => {
	const graph = compileRoomGraph(liveWire([]), null, undefined, {
		quota: {
			generated_at: '2026-08-27T10:20:00Z',
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
							last_known: { used: null, limit: null, percent: 44, reset: null }
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
							last_known: { used: null, limit: null, percent: 1, reset: null }
						}
					]
				}
			]
		}
	});
	const topo = compileTopology(graph);
	const layout = layoutRoom(topo, emptyAtlas()).layout;
	const home = layout.nodes[topo.homeId];
	const inFrame = renderWorld(topo, layout, graph, {
		center: home,
		cols: 80,
		rows: 18,
		level: 'island'
	});
	assert.match(inFrame, /⛁ ✗ claude 5h 12%/);
	assert.ok(!inFrame.includes('fable'), 'a core allowance is not the shell ceiling');

	const offFrame = renderWorld(topo, layout, graph, {
		center: { x: 200, y: 200 },
		cols: 80,
		rows: 18,
		level: 'island'
	});
	assert.match(offFrame, /HOME.*⛁ ✗ claude 5h 12%/);
});

// ── the reference trace: eight boundaries, one journey ──────────────────────

test('the reference trace walks the journey the spec names, on stable coordinates', () => {
	const trails: Record<string, TrailStep[]> = {};
	let memory = emptyAtlas();
	const places: (string | undefined)[] = [];
	const seen: Record<string, { x: number; y: number }> = {};
	const frames = referenceFrames();

	for (const frame of frames) {
		recordTrails(trails, frame);
		const { topo, layout, memory: next } = pipeline(frame, trails, memory);
		memory = next;
		places.push(topo.actorPlaces['run-260827-1000-ref1']);
		// stable coordinates: nothing ever moves once assigned
		for (const [id, p] of Object.entries(seen)) {
			if (layout.nodes[id]) assert.deepEqual(layout.nodes[id], p, `${id} moved`);
		}
		for (const [id, p] of Object.entries(layout.nodes)) seen[id] = p;
	}

	const campPrefix = `camp:${REPO}::brr/the-reference-journey::`;
	assert.deepEqual(places, [
		dirId(REPO, ['src', 'frontend']), // #81 wakes inside the tree
		dirId(REPO, ['src', 'frontend', 'src', 'lib']), // #82 walks deeper to edit
		`${dirId(REPO, ['src', 'frontend', 'tests'])}#rig`, // #83 the local rig
		`${campPrefix}#strand-bay`, // #84 its bay, while the strand crosses
		`${dirId(REPO, ['src', 'frontend', 'tests'])}#rig`, // #85 back at tests; letter arrives
		`${campPrefix}#chart-table`, // #86 the chart — it edits control state
		`${islandRootId(REPO)}#forge-dock`, // #87 the forge
		`${campPrefix}#cut-loom` // #88 cut
	]);
});

test('the strand crosses to the knowledge island while the parent stays put (#84)', () => {
	const trails: Record<string, TrailStep[]> = {};
	const frames = referenceFrames();
	for (const frame of frames.slice(0, 4)) recordTrails(trails, frame);
	const { topo } = pipeline(frames[3], trails, emptyAtlas());
	assert.equal(
		topo.actorPlaces['run-260827-1012-des1'],
		dirId('hugimuni-labs/brnrd-knowledge', ['design'])
	);
	assert.ok(topo.actorPlaces['run-260827-1000-ref1'].startsWith('camp:' + REPO));
	// and the crossing has a sea route
	const route = routeBetween(
		topo,
		topo.actorPlaces['run-260827-1000-ref1'],
		topo.actorPlaces['run-260827-1012-des1']
	);
	assert.ok(route && route.includes(topo.homeId), 'sea lane runs through HOME');
});

test('the injected boundary (#85) marks traffic to the actor, not actor movement', () => {
	const trails: Record<string, TrailStep[]> = {};
	const frames = referenceFrames();
	for (const frame of frames.slice(0, 5)) recordTrails(trails, frame);
	const { graph, topo } = pipeline(frames[4], trails, emptyAtlas());
	const resident = graph.actors.find((a) => a.runId === 'run-260827-1000-ref1');
	assert.equal(resident?.injected, true);
	// same place as #83 — the letter came to it
	assert.equal(
		topo.actorPlaces['run-260827-1000-ref1'],
		`${dirId(REPO, ['src', 'frontend', 'tests'])}#rig`
	);
});

test('the trace renders the journey on the board: terrain, actor, forge, cloth', () => {
	const trails: Record<string, TrailStep[]> = {};
	let memory = emptyAtlas();
	const frames = referenceFrames();
	let lastBoard = '';
	for (const frame of frames) {
		recordTrails(trails, frame);
		const { graph, topo, layout, memory: next } = pipeline(frame, trails, memory);
		memory = next;
		const actorPlace = topo.actorPlaces['run-260827-1000-ref1'];
		const center = actorPlace ? layout.nodes[actorPlace] : { x: 0, y: 0 };
		lastBoard = renderWorld(topo, layout, graph, {
			center,
			cols: 150,
			rows: 30,
			level: 'island'
		});
	}
	// after #88: the tree the journey grew is still on the board (durable
	// terrain), the resident stands at the cut loom, produce reached CLOTH
	assert.ok(lastBoard.includes('frontend/'));
	assert.ok(lastBoard.includes('X'), 'cut loom visible');
	assert.ok(lastBoard.includes('2c 1pr'), 'produce on the live cloth row');
});

// ── the condition line ──────────────────────────────────────────────────────

/** One live resident, so the pager block renders at all, built through the
 *  same compile the page uses — never a hand-built graph literal. */
function pagerBoard(quota: unknown, spawnMax: number | null, strands = 0): string {
	const run = {
		run_id: 'r1',
		repo_label: REPO,
		branch: 'brr/x',
		is_subspawn: false,
		edge: { act: 'orient', detail: 'sed -n 1,20p x.ts', dir: 'src', at: '2026-08-27T10:20:00Z' }
	} as unknown as LiveRun;
	const children = Array.from(
		{ length: strands },
		(_, i) =>
			({
				run_id: `s${i}`,
				repo_label: REPO,
				branch: 'brr/y',
				is_subspawn: true,
				parent_run_id: 'r1',
				edge: { act: 'mutate', detail: 'Edit y.ts', dir: 'src', at: '2026-08-27T10:20:00Z' }
			}) as unknown as LiveRun
	);
	const wire = { ...liveWire([run, ...children]), spawn_max_concurrent: spawnMax };
	const graph = compileRoomGraph(wire, null, undefined, { quota } as never);
	const topo = compileTopology(graph);
	const { layout } = layoutRoom(topo, emptyAtlas());
	const cam: Camera = { center: { x: 0, y: 0 }, cols: 140, rows: 26, level: 'island' };
	return renderWorld(topo, layout, graph, cam, {});
}

const CONDITION_QUOTA = {
	generated_at: '2026-08-27T10:20:00Z',
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
				{ label: 'weekly', used: null, limit: null, percent: 43, reset: null, resets_at: null }
			]
		},
		{
			shell: 'codex',
			status: 'known',
			windows: [
				{ label: 'weekly', used: null, limit: null, percent: 81, reset: null, resets_at: null }
			]
		}
	]
};

test('the pager wears the body: the binding ceiling, its clock, and the slots left', () => {
	// The pager read out the log and nothing about the body carrying it,
	// which is the difference between a feed and a worn device (maintainer,
	// 2026-08-28). The clock is half the reading: "10% resetting in 40
	// minutes" and "10% resetting in three days" are opposite instructions,
	// and the percentage alone cannot tell them apart.
	const board = pagerBoard(CONDITION_QUOTA, 8, 3);
	assert.match(board, /⌁ .*⛁ claude 5h 12% ↻/u, 'the binding ceiling, named, with its clock');
	assert.match(board, /⛁ codex week 81%/u, 'the other shell reads too');
	assert.ok(
		!/⛁ codex week 81% ↻/u.test(board),
		'and no clock is invented where the wire gave none'
	);
	assert.match(board, /◈ 3\/8 slots/u, 'how much body is free, not only how much is busy');
});

test('an unreported pool width says so rather than claiming zero', () => {
	// A strand is demonstrably out and no daemon has reported a width. `1/0`
	// would be arithmetic nobody could do and `1/8` a number nobody sent;
	// `?` is the honest shape of "unknown ceiling, known draw".
	const board = pagerBoard(CONDITION_QUOTA, null, 1);
	assert.match(board, /◈ 1\/\? slots/u);
});

test('nothing out and no width reported grows no slot reading', () => {
	// The unknown only matters against a draw. With neither, the line stays
	// quiet rather than printing a question mark at an empty room.
	const board = pagerBoard(CONDITION_QUOTA, null, 0);
	assert.ok(!/◈/u.test(board), 'no slot chip at all');
	assert.match(board, /⛁ claude/u, 'while the fuel it does have still reads');
});

test('a board with nothing attested grows no condition line at all', () => {
	// An empty strip beats a row of zeroes, which would read as measured.
	const board = pagerBoard({ generated_at: '2026-08-27T10:20:00Z', runner_quotas: [] }, null);
	assert.ok(board.includes('PAGER'), 'the pager itself still renders');
	assert.ok(!/⌁ .*⛁/u.test(board), 'but no fuel line is fabricated');
});

// ── the tree's own turns, and the ground under the terminal ────────────────

function boardOf(dirs: string[], opts: Parameters<typeof renderWorld>[4] = {}) {
	const run = {
		id: 'r1',
		run_id: 'r1',
		kind: 'daemon',
		stream: 'cloud:telegram:1:',
		label: '',
		name: 'the run',
		repo_label: REPO,
		started_at: '2026-08-27T10:00:00Z',
		last_seen: '2026-08-27T10:20:00Z',
		parent_run_id: null,
		is_subspawn: false,
		runner: { name: 'claude', shell: 'claude', core: 'opus' },
		phase: 'running',
		card_text: null,
		card_updated_at: null,
		relics_counts: null,
		portals: { pending: 0, oldest_at: null },
		room: { env: 'host', branch: 'brr/ground', dir: null },
		edge: null,
		daemon_stale: false
	} as unknown as LiveRun;
	const trails: Record<string, TrailStep[]> = {
		r1: dirs.map((dir, i) => ({ dir, act: 'orient', at: `2026-08-27T10:0${i}:00Z` }))
	};
	const graph = compileRoomGraph(liveWire([run]), null, trails);
	const topo = compileTopology(graph);
	const { layout } = layoutRoom(topo, emptyAtlas(), [terminalRequest('r1', 50, 7)]);
	const cam: Camera = { center: { x: 0, y: 2 }, cols: 120, rows: 34, level: 'island' };
	return { board: renderWorld(topo, layout, graph, cam, opts), topo, layout };
}

test('the tree draws its turns: a tee while siblings remain, an elbow at the last', () => {
	// `tree(1)` is legible because the junction says whether the column
	// continues. Two subtrees on purpose: `frontend` and `brnrd/brr` sit at
	// the same depth and therefore share a character column, and the first
	// version of this keyed "last child" by column — which gave the elbow to
	// whichever subtree happened to sit lowest and drew every other
	// subtree's final child as if more followed.
	const { board } = boardOf([
		'src/frontend/repro',
		'src/frontend/tests',
		'src/brnrd/brr/gates',
		'src/brnrd/brr/prompts'
	]);
	assert.match(board, /├───repro\//u, 'a sibling follows, so a tee');
	assert.match(board, /└───tests\//u, 'the last child of frontend gets the elbow');
	assert.match(board, /├───gates\//u);
	assert.match(board, /└───prompts\//u, 'and so does the last child of the other subtree');
});

test('the terminal is drawn on allocated ground, and on none if none was allocated', () => {
	const lines = [{ at: '2026-08-27T10:20:00Z', act: 'Bash', detail: 'git status' }];
	const { board } = boardOf(['src/frontend/src/lib'], { terminal: lines });
	assert.ok(board.includes('$ bench'), 'the window renders where it was allocated');
	// every board row carrying a frame edge carries no directory label: the
	// defect was a `┐` printing over `prompts/`, so the assertion is that no
	// row holds both.
	for (const row of board.split('\n')) {
		if (!/[┌└│┐┘]/u.test(row) || !row.includes('bench')) continue;
		assert.ok(!/\w+\//u.test(row.replace('$ bench', '')), `terrain inside the window row: ${row}`);
	}
});

test('no region allocated ⇒ no window, rather than one invented beside a camp', () => {
	const run = {
		id: 'r1',
		run_id: 'r1',
		kind: 'daemon',
		stream: 's',
		label: '',
		name: 'r',
		repo_label: REPO,
		started_at: '2026-08-27T10:00:00Z',
		last_seen: '2026-08-27T10:20:00Z',
		parent_run_id: null,
		is_subspawn: false,
		runner: { name: 'claude', shell: 'claude', core: 'opus' },
		phase: 'running',
		card_text: null,
		card_updated_at: null,
		relics_counts: null,
		portals: { pending: 0, oldest_at: null },
		room: { env: 'host', branch: 'brr/ground', dir: null },
		edge: null,
		daemon_stale: false
	} as unknown as LiveRun;
	const trails: Record<string, TrailStep[]> = {
		r1: [{ dir: 'src/lib', act: 'orient', at: '2026-08-27T10:01:00Z' }]
	};
	const graph = compileRoomGraph(liveWire([run]), null, trails);
	const topo = compileTopology(graph);
	const lines = [{ at: '2026-08-27T10:20:00Z', act: 'Bash', detail: 'git status' }];
	// One camera, framed wide enough to hold the whole labour band — otherwise
	// the absence proves only that the window fell off the top of the frame,
	// and a camera that invented a rectangle would pass this by luck. The
	// positive half is the control: the same camera *does* draw it when the
	// ground exists.
	const cam: Camera = { center: { x: 0, y: -4 }, cols: 100, rows: 30, level: 'island' };
	const allocated = layoutRoom(topo, emptyAtlas(), [terminalRequest('r1', 50, 7)]).layout;
	assert.ok(
		renderWorld(topo, allocated, graph, cam, { terminal: lines }).includes('$ bench'),
		'the same camera renders the window when ground was allocated'
	);
	const { layout } = layoutRoom(topo, emptyAtlas()); // no requests
	const board = renderWorld(topo, layout, graph, cam, { terminal: lines });
	assert.ok(
		!board.includes('$ bench'),
		'a window on unallocated ground is the defect wearing a fix'
	);
});
