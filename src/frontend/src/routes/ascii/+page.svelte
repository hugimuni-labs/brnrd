<script lang="ts">
	// /ascii — the reference camera over the unbounded room (#1652).
	//
	// The page owns nothing semantic: it polls the wires, compiles
	// RoomGraph → RoomTopology → RoomLayout, and asks the ASCII camera for
	// one window into the world. The world is never laid out to fit this
	// viewport: resizing changes the window, panning moves it, and node
	// coordinates live in the atlas memory (persisted) — never recomputed
	// to fit.
	//
	// Motion doctrine, ASCII edition: nothing animates. A line that changed
	// between two polls flashes once (diffed on a clock-free render, so a
	// minute passing moves nothing); everything else holds still.
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import { fetchLiveRuns, LiveRunsAuthError, type LiveRunsResponse } from '$lib/liveRuns';
	import { fetchRunLedger, type RunLedgerResponse } from '$lib/runLedger';
	import { fetchScheduledWakes, type ScheduledWakesResponse } from '$lib/scheduledWakes';
	import { fetchQuota, type QuotaResponse } from '$lib/quota';
	import { compileRoomGraph, dirFromEdge, fileFromDetail, type TrailStep } from '$lib/roomGraph';
	import { compileTopology, type PlaceId } from '$lib/roomTopology';
	import { layoutRoom, emptyAtlas, type AtlasMemory } from '$lib/roomLayout';
	import {
		diffTransitions,
		walkFor,
		advanceWalks,
		walkPositions,
		easeCamera,
		type Walk
	} from '$lib/roomMotion';
	import {
		recordPages,
		pagerFeed,
		readingsFor,
		advanceReadings,
		readingPhases,
		type PagerPage,
		type Reading
	} from '$lib/roomPager';
	import {
		renderWorld,
		cameraCenterFor,
		LEGEND,
		type Camera,
		type CameraLevel
	} from '$lib/asciiCamera';
	import { referenceFrames } from '$lib/referenceTrace';

	const POLL_MS = 2000;
	const SLOW_MS = 60_000;
	const DEMO_STEP_MS = 3600;
	const TICK_MS = 160; // motion ticker: walk steps + camera easing
	const MIN_COLS = 64;
	const MAX_COLS = 220;
	const ROWS = 26;
	const PAN_STEP = 4; // world units per keypress
	let cols = $state(76);
	let probeEl = $state<HTMLElement | null>(null);
	let deckEl = $state<HTMLElement | null>(null);

	let charW = 7.2; // measured at mount/resize; used to convert drag px → chars
	function measureCols() {
		if (!probeEl || !deckEl) return;
		const w = probeEl.getBoundingClientRect().width / 20;
		if (w <= 0) return;
		charW = w;
		const avail = deckEl.clientWidth - 8;
		cols = Math.max(MIN_COLS, Math.min(MAX_COLS, Math.floor(avail / w)));
	}

	let lines = $state<string[]>([]);
	let changed = $state<number[]>([]);
	let loading = $state(true);
	let signedOut = $state(false);
	let stale = $state(false);
	let demo = $state(false);
	let frameNote = $state('');
	let showLegend = $state(true);
	// The camera is the reader's (his steer, 2026-08-27), refined to
	// follow-when-idle (his sign-off, 2026-08-28): it follows the lead actor
	// until the reader's hand moves it — drag, arrows — and the hand always
	// wins. `f` re-arms the follow.
	let follow = $state(true);
	let framedOnce = false;
	let level = $state<CameraLevel>('island');
	let levelForced = $state(false);

	let live: LiveRunsResponse | null = null;
	let ledger: RunLedgerResponse | null = null;
	let wakes: ScheduledWakesResponse | null = null;
	let quota: QuotaResponse | null = null;

	// terrain memory: attested footsteps per run, deduped by boundary
	// timestamp — "only what you touch comes into being"
	const TRAILS_KEY = 'brnrd-ascii-trails';
	let trails: Record<string, TrailStep[]> = {};
	const TRAIL_CAP = 60;
	const TRAIL_RUNS_CAP = 24;

	// atlas memory: assigned world coordinates. Client-persisted for now
	// (the spec's accepted first slice; server-side atlas is the durable
	// target) — a reload rebuilds the same map because this survives.
	const ATLAS_KEY = 'brnrd-ascii-atlas-v1';
	let atlas: AtlasMemory = emptyAtlas();

	// pager memory: injections attested while this reader watched — pages
	// name their carrier boundary, never content (roomPager.ts)
	const PAGER_KEY = 'brnrd-ascii-pager';
	let pager: Record<string, PagerPage[]> = {};
	let readings: Reading[] = [];

	// the camera
	let camCenter = { x: 0, y: 0 };
	let lastRoute: PlaceId[] | null = null;

	function loadStores() {
		try {
			const raw = localStorage.getItem(TRAILS_KEY);
			if (raw) trails = JSON.parse(raw) as Record<string, TrailStep[]>;
		} catch {
			trails = {};
		}
		try {
			const raw = localStorage.getItem(ATLAS_KEY);
			if (raw) atlas = JSON.parse(raw) as AtlasMemory;
			if (!atlas || typeof atlas.nodes !== 'object') atlas = emptyAtlas();
		} catch {
			atlas = emptyAtlas();
		}
		try {
			const raw = localStorage.getItem(PAGER_KEY);
			if (raw) pager = JSON.parse(raw) as Record<string, PagerPage[]>;
		} catch {
			pager = {};
		}
	}

	function saveTrails() {
		try {
			const ids = Object.keys(trails);
			if (ids.length > TRAIL_RUNS_CAP) {
				for (const id of ids.sort().slice(0, ids.length - TRAIL_RUNS_CAP)) delete trails[id];
			}
			localStorage.setItem(TRAILS_KEY, JSON.stringify(trails));
		} catch {
			/* storage full/blocked — the map stays session-local */
		}
	}

	function saveAtlas() {
		try {
			localStorage.setItem(ATLAS_KEY, JSON.stringify(atlas));
		} catch {
			/* same forgiveness */
		}
	}

	function savePager() {
		try {
			const ids = Object.keys(pager);
			if (ids.length > TRAIL_RUNS_CAP) {
				for (const id of ids.sort().slice(0, ids.length - TRAIL_RUNS_CAP)) delete pager[id];
			}
			localStorage.setItem(PAGER_KEY, JSON.stringify(pager));
		} catch {
			/* same forgiveness */
		}
	}

	function recordTrails() {
		let moved = false;
		for (const run of live?.runs ?? []) {
			const dir = dirFromEdge(run.edge, run.repo_label);
			const at = run.edge?.at ?? null;
			if (!dir || !at) continue;
			const trail = (trails[run.run_id] ??= []);
			if (trail.some((s) => s.at === at)) continue;
			trail.push({ dir, act: run.edge?.act ?? null, at, file: fileFromDetail(run.edge?.detail) });
			if (trail.length > TRAIL_CAP) trail.splice(0, trail.length - TRAIL_CAP);
			moved = true;
		}
		if (moved && !demo) saveTrails();
	}

	// the flash marks *state* motion only: diff on the clock-free render
	let prevBare: string[] = [];
	// motion state (#1654 slice 3): walks derive from BoundaryTransition
	// receipts only; the camera eases toward its target between paints
	let walks: Walk[] = [];
	let prevPlaces: Record<string, PlaceId> | null = null;
	let camTarget = { x: 0, y: 0 };
	let scene: {
		graph: ReturnType<typeof compileRoomGraph>;
		topo: ReturnType<typeof compileTopology>;
		layout: ReturnType<typeof layoutRoom>['layout'];
	} | null = null;
	let lastNow = 0;

	/** Poll boundary: recompile the world, mint transition receipts, run the
	 *  flash diff. Paints once; the ticker keeps painting between polls. */
	function compute(now: number) {
		recordTrails();
		const graph = compileRoomGraph(live, ledger, trails, { wakes, quota });
		// pages: attested injections accumulate; a fresh page starts the
		// mind-connect ceremony for its reader (bounded, receipt-driven)
		const glyphs = Object.fromEntries(graph.actors.map((a) => [a.runId, a.glyph]));
		const freshPages = recordPages(live?.runs ?? [], pager, glyphs);
		if (freshPages.length > 0) {
			readings = readingsFor(freshPages, readings);
			if (!demo) savePager();
		}
		stale = graph.stale;
		const topo = compileTopology(graph);
		const placed = layoutRoom(topo, atlas);
		if (Object.keys(placed.memory.nodes).length !== Object.keys(atlas.nodes).length) {
			atlas = placed.memory;
			if (!demo) saveAtlas();
		}
		const layout = placed.layout;
		scene = { graph, topo, layout };
		lastNow = now;

		// transition receipts: one attested place change ⇒ one walk
		const transitions = diffTransitions(prevPlaces, topo.actorPlaces, topo);
		prevPlaces = topo.actorPlaces;
		const liveIds = new Set(Object.keys(topo.actorPlaces));
		walks = walks.filter((w) => liveIds.has(w.actorRunId)); // cut drops the body
		for (const t of transitions) {
			if (t.route.length < 2) continue; // appearance — no walk to fake
			const walk = walkFor(t, layout);
			if (walk) walks = [...walks.filter((w) => w.actorRunId !== t.actorRunId), walk];
		}
		const lead = graph.actors.find((a) => !a.strand) ?? graph.actors[0];
		const leadT = transitions.find((t) => t.actorRunId === lead?.runId);
		if (leadT && leadT.route.length > 1) lastRoute = leadT.route;
		const leadPlace = lead ? (topo.actorPlaces[lead.runId] ?? null) : null;

		// dormant mode returns to Atlas; a live resident gets Island scale
		if (!levelForced) level = graph.actors.length === 0 ? 'atlas' : 'island';

		// the camera is the reader's: one initial framing, then it moves only
		// by hand — or eases with the actor when follow is explicitly on
		const frameIds: PlaceId[] = [];
		if (leadPlace) {
			frameIds.push(leadPlace);
			const rootId = lead ? `repo:${lead.islandLabel}` : null;
			if (rootId && layout.nodes[rootId]) frameIds.push(rootId);
			if (lastRoute) frameIds.push(...lastRoute);
		}
		if (!framedOnce && frameIds.length > 0) {
			camCenter = cameraCenterFor(layout, frameIds, cols, ROWS, level);
			camTarget = camCenter;
			framedOnce = true;
		} else if (follow) {
			camTarget = cameraCenterFor(layout, frameIds, cols, ROWS, level);
		}

		// flash diff on the clock-free, walk-free render: state motion only
		// (pages are state — a fresh page flashes; the reading tether is
		// presentation and stays off this render, like walk positions)
		const cam: Camera = { center: camCenter, cols, rows: ROWS, level };
		const bare = renderWorld(topo, layout, graph, cam, {
			highlightRoute: lastRoute,
			pages: pagerFeed(pager)
		}).split('\n');
		const delta: number[] = [];
		for (let i = 0; i < bare.length; i++) {
			if (prevBare.length > 0 && bare[i] !== prevBare[i]) delta.push(i);
		}
		prevBare = bare;
		changed = delta;
		paint();
	}

	/** Display tick: current camera + walk positions over the cached scene.
	 *  Never touches the flash diff — a walking frame is not a state change. */
	function paint() {
		if (!scene) return;
		const cam: Camera = { center: camCenter, cols, rows: ROWS, level };
		lines = renderWorld(scene.topo, scene.layout, scene.graph, cam, {
			now: lastNow,
			highlightRoute: lastRoute,
			actorPositions: walkPositions(walks),
			pages: pagerFeed(pager),
			reading: readingPhases(readings)
		}).split('\n');
	}

	/** The motion ticker: advance walks, ease the camera, repaint when
	 *  anything actually moved. */
	function tick() {
		let moved = false;
		if (walks.length > 0) {
			const adv = advanceWalks(walks);
			walks = adv.walks;
			moved = true;
		}
		if (readings.length > 0) {
			readings = advanceReadings(readings);
			moved = true;
		}
		if (follow && (camCenter.x !== camTarget.x || camCenter.y !== camTarget.y)) {
			camCenter = easeCamera(camCenter, camTarget);
			moved = true;
		}
		if (moved) paint();
	}

	function onKey(e: KeyboardEvent) {
		if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
		const pan = (dx: number, dy: number) => {
			follow = false;
			camCenter = { x: camCenter.x + dx, y: camCenter.y + dy };
			camTarget = camCenter;
			paint();
			e.preventDefault();
		};
		if (e.key === 'ArrowLeft') pan(-PAN_STEP, 0);
		else if (e.key === 'ArrowRight') pan(PAN_STEP, 0);
		else if (e.key === 'ArrowUp') pan(0, -PAN_STEP);
		else if (e.key === 'ArrowDown') pan(0, PAN_STEP);
		else if (e.key === 'f') {
			follow = !follow;
			paint();
		} else if (e.key === 'a') {
			levelForced = true;
			level = level === 'island' ? 'atlas' : 'island';
			paint();
		}
	}

	// drag = the primary camera verb: px deltas convert through the measured
	// char cell into world units at the current scale
	let dragging = $state(false);
	let dragLast = { x: 0, y: 0 };
	function onPointerDown(e: PointerEvent) {
		dragging = true;
		dragLast = { x: e.clientX, y: e.clientY };
		(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
	}
	function onPointerMove(e: PointerEvent) {
		if (!dragging) return;
		const lineH = charW * 2.25; // 12px font · 1.35 line-height vs ~7.2px cell
		const sx = level === 'island' ? 2 : 0.5;
		const sy = level === 'island' ? 1 : 0.25;
		const dx = (e.clientX - dragLast.x) / charW / sx;
		const dy = (e.clientY - dragLast.y) / lineH / sy;
		if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) return;
		dragLast = { x: e.clientX, y: e.clientY };
		follow = false;
		camCenter = { x: camCenter.x - dx, y: camCenter.y - dy };
		camTarget = camCenter;
		paint();
	}
	function onPointerUp() {
		dragging = false;
	}

	onMount(() => {
		demo = new URLSearchParams(location.search).has('demo');
		let stop = false;
		let timer: ReturnType<typeof setTimeout> | null = null;
		if (!demo) loadStores();
		else atlas = emptyAtlas();
		measureCols();
		const onResize = () => measureCols();
		window.addEventListener('resize', onResize);
		window.addEventListener('keydown', onKey);
		// the motion ticker — advances walks and eases the camera; a tick
		// with nothing moving paints nothing
		const ticker = setInterval(tick, TICK_MS);
		const cleanup = () => {
			stop = true;
			window.removeEventListener('resize', onResize);
			window.removeEventListener('keydown', onKey);
			clearInterval(ticker);
			if (timer) clearTimeout(timer);
		};

		if (demo) {
			// the reference trace (#81–#88): the journey the spec says a
			// reader must be able to follow without opening a dossier
			const frames = referenceFrames();
			ledger = {
				generated_at: '2026-08-27T10:00:00Z',
				rows: [],
				stale: false,
				reported_at: '2026-08-27T10:00:00Z',
				span_seconds_served: 86400
			} as unknown as RunLedgerResponse;
			let i = 0;
			const step = () => {
				if (stop) return;
				live = {
					generated_at: '2026-08-27T10:20:00Z',
					runs: frames[i % frames.length],
					stale: false,
					reported_at: '2026-08-27T10:20:00Z',
					spawn_max_concurrent: 3
				};
				frameNote = `trace #${81 + (i % frames.length)} / #81–#88`;
				compute(Date.parse('2026-08-27T10:21:00Z'));
				loading = false;
				i += 1;
				if (i % frames.length === 0) {
					// the world persists between journeys; the trail memory
					// resets so the replay tells the same story each loop
					trails = {};
					lastRoute = null;
					prevPlaces = null;
					walks = [];
					pager = {};
					readings = [];
				}
				timer = setTimeout(step, DEMO_STEP_MS);
			};
			step();
			return cleanup;
		}

		let lastSlowAt = 0;
		const poll = async () => {
			if (stop) return;
			try {
				const nowMs = Date.now();
				if (nowMs - lastSlowAt > SLOW_MS) {
					lastSlowAt = nowMs;
					try {
						ledger = await fetchRunLedger(fetch, 8);
					} catch {
						/* keep last */
					}
					try {
						wakes = await fetchScheduledWakes();
					} catch {
						/* keep last */
					}
					try {
						quota = await fetchQuota();
					} catch {
						/* keep last */
					}
				}
				live = await fetchLiveRuns();
				signedOut = false;
				compute(nowMs);
			} catch (err) {
				if (err instanceof LiveRunsAuthError) signedOut = true;
			} finally {
				loading = false;
			}
			if (!stop) timer = setTimeout(poll, POLL_MS);
		};
		poll();
		return cleanup;
	});
</script>

<svelte:head>
	<title>brnrd · the room, in characters</title>
	<meta name="robots" content="noindex" />
</svelte:head>

<div class="deck" bind:this={deckEl}>
	<span class="probe" bind:this={probeEl} aria-hidden="true">MMMMMMMMMMMMMMMMMMMM</span>
	<header>
		<span class="mark">b·_·d</span>
		<span class="title">the room, in characters</span>
		<span class="hint">drag / ←↑↓→ move camera · f follow on/off · a atlas</span>
		<span class="status">
			{#if loading}connecting…{:else if signedOut}signed out — <a href={resolve('/')}>sign in</a
				>{:else if demo}{frameNote}{:else if stale}wire stale{:else}{follow
					? 'live · following'
					: 'live · your camera'}{/if}
		</span>
	</header>

	{#if !signedOut}
		<pre
			class="board"
			class:grabbing={dragging}
			onpointerdown={onPointerDown}
			onpointermove={onPointerMove}
			onpointerup={onPointerUp}
			onpointercancel={onPointerUp}>{#each lines as line, i (i)}<span
					class="line"
					class:delta={changed.includes(i)}>{line + '\n'}</span
				>{/each}</pre>
	{/if}

	<button class="legend-toggle" onclick={() => (showLegend = !showLegend)}>
		{showLegend ? 'hide legend' : 'legend'}
	</button>
	{#if showLegend}
		<pre class="legend">{LEGEND}</pre>
	{/if}
</div>

<style>
	:global(body) {
		background: #0b0f0c;
	}
	.deck {
		min-height: 100vh;
		padding: 1rem;
		font-family: 'SFMono-Regular', ui-monospace, Menlo, monospace;
		color: #9be9a8;
	}
	header {
		display: flex;
		gap: 0.75rem;
		align-items: baseline;
		margin-bottom: 0.75rem;
	}
	.mark {
		color: #e8c15a;
		font-weight: 600;
	}
	.title {
		color: #6ea87a;
	}
	.hint {
		color: #3d5a46;
		font-size: 0.75rem;
	}
	.status {
		margin-left: auto;
		color: #587a61;
		font-size: 0.85rem;
	}
	.status a {
		color: #9be9a8;
	}
	.probe {
		position: absolute;
		visibility: hidden;
		font-size: 12px;
		white-space: pre;
	}
	.board {
		margin: 0;
		font-size: 12px;
		line-height: 1.35;
		overflow-x: auto;
		white-space: pre;
		cursor: grab;
		touch-action: none;
		user-select: none;
	}
	.board.grabbing {
		cursor: grabbing;
	}
	.line {
		display: inline;
	}
	.line.delta {
		animation: settle 1.4s ease-out 1;
	}
	@keyframes settle {
		0% {
			background: rgba(232, 193, 90, 0.35);
			color: #ffe9b0;
		}
		100% {
			background: transparent;
		}
	}
	.legend-toggle {
		margin-top: 1rem;
		background: none;
		border: 1px solid #2a4030;
		color: #587a61;
		font: inherit;
		font-size: 0.8rem;
		padding: 0.2rem 0.6rem;
		cursor: pointer;
	}
	.legend {
		margin-top: 0.5rem;
		color: #587a61;
		font-size: 0.8rem;
	}
</style>
