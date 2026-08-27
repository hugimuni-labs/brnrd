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
	import { compileRoomGraph, fileFromDetail, type TrailStep } from '$lib/roomGraph';
	import { compileTopology, routeBetween, type PlaceId } from '$lib/roomTopology';
	import { layoutRoom, emptyAtlas, type AtlasMemory } from '$lib/roomLayout';
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
	const MIN_COLS = 64;
	const MAX_COLS = 220;
	const ROWS = 26;
	const PAN_STEP = 4; // world units per keypress
	let cols = $state(76);
	let probeEl = $state<HTMLElement | null>(null);
	let deckEl = $state<HTMLElement | null>(null);

	function measureCols() {
		if (!probeEl || !deckEl) return;
		const charW = probeEl.getBoundingClientRect().width / 20;
		if (charW <= 0) return;
		const avail = deckEl.clientWidth - 8;
		cols = Math.max(MIN_COLS, Math.min(MAX_COLS, Math.floor(avail / charW)));
	}

	let lines = $state<string[]>([]);
	let changed = $state<number[]>([]);
	let loading = $state(true);
	let signedOut = $state(false);
	let stale = $state(false);
	let demo = $state(false);
	let frameNote = $state('');
	let showLegend = $state(true);
	let follow = $state(true);
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

	// the camera
	let camCenter = { x: 0, y: 0 };
	let lastActorPlace: PlaceId | null = null;
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

	function recordTrails() {
		let moved = false;
		for (const run of live?.runs ?? []) {
			const dir = run.edge?.dir && run.edge.dir !== '.' ? run.edge.dir : null;
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

	function repaint(now: number) {
		recordTrails();
		const graph = compileRoomGraph(live, ledger, trails, { wakes, quota });
		stale = graph.stale;
		const topo = compileTopology(graph);
		const placed = layoutRoom(topo, atlas);
		if (Object.keys(placed.memory.nodes).length !== Object.keys(atlas.nodes).length) {
			atlas = placed.memory;
			if (!demo) saveAtlas();
		}
		const layout = placed.layout;

		// dormant mode returns to Atlas; a live resident gets Island scale
		if (!levelForced) level = graph.actors.length === 0 ? 'atlas' : 'island';

		// follow: the resident (or first actor), framed with its island root
		// so the destination keeps its context; the route it took is marked.
		const lead = graph.actors.find((a) => !a.strand) ?? graph.actors[0];
		const leadPlace = lead ? (topo.actorPlaces[lead.runId] ?? null) : null;
		if (leadPlace && leadPlace !== lastActorPlace) {
			lastRoute =
				lastActorPlace && lastActorPlace !== leadPlace
					? routeBetween(topo, lastActorPlace, leadPlace)
					: null;
			lastActorPlace = leadPlace;
		}
		if (follow) {
			const frameIds: PlaceId[] = [];
			if (leadPlace) {
				frameIds.push(leadPlace);
				const rootId = lead ? `repo:${lead.islandLabel}` : null;
				if (rootId && layout.nodes[rootId]) frameIds.push(rootId);
				if (lastRoute) frameIds.push(...lastRoute);
			}
			camCenter = cameraCenterFor(layout, frameIds, cols, ROWS, level);
		}

		const cam: Camera = { center: camCenter, cols, rows: ROWS, level };
		const opts = { highlightRoute: lastRoute };
		const next = renderWorld(topo, layout, graph, cam, { ...opts, now }).split('\n');
		const bare = renderWorld(topo, layout, graph, cam, opts).split('\n');
		const delta: number[] = [];
		for (let i = 0; i < bare.length; i++) {
			if (prevBare.length > 0 && bare[i] !== prevBare[i]) delta.push(i);
		}
		prevBare = bare;
		lines = next;
		changed = delta;
	}

	function onKey(e: KeyboardEvent) {
		if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
		const pan = (dx: number, dy: number) => {
			follow = false;
			camCenter = { x: camCenter.x + dx, y: camCenter.y + dy };
			repaint(Date.now());
			e.preventDefault();
		};
		if (e.key === 'ArrowLeft') pan(-PAN_STEP, 0);
		else if (e.key === 'ArrowRight') pan(PAN_STEP, 0);
		else if (e.key === 'ArrowUp') pan(0, -PAN_STEP);
		else if (e.key === 'ArrowDown') pan(0, PAN_STEP);
		else if (e.key === 'f') {
			follow = true;
			repaint(Date.now());
		} else if (e.key === 'a') {
			levelForced = true;
			level = level === 'island' ? 'atlas' : 'island';
			repaint(Date.now());
		}
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
		const cleanup = () => {
			stop = true;
			window.removeEventListener('resize', onResize);
			window.removeEventListener('keydown', onKey);
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
				repaint(Date.parse('2026-08-27T10:21:00Z'));
				loading = false;
				i += 1;
				if (i % frames.length === 0) {
					// the world persists between journeys; the trail memory
					// resets so the replay tells the same story each loop
					trails = {};
					lastActorPlace = null;
					lastRoute = null;
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
				repaint(nowMs);
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
		<span class="hint">←↑↓→ pan · f follow · a atlas</span>
		<span class="status">
			{#if loading}connecting…{:else if signedOut}signed out — <a href={resolve('/')}>sign in</a
				>{:else if demo}{frameNote}{:else if stale}wire stale{:else}{follow
					? 'live · following'
					: 'live · panned'}{/if}
		</span>
	</header>

	{#if !signedOut}
		<pre class="board">{#each lines as line, i (i)}<span
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
