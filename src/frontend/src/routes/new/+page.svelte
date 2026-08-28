<script lang="ts">
	// The axonometric room — the live system drawn as a place (the /new
	// study, 2026-08-26). Everything spatial comes from $lib/isoField;
	// everything semantic from $lib/residentField. Deliberately imports no
	// panel/card/layout grammar from the rest of the dashboard: the scene IS
	// the interface. Motion doctrine unchanged: mount assembles (state
	// birth), and after that nothing moves except on a recorded event.
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import {
		fetchLiveRuns,
		LiveRunsAuthError,
		liveRunDisplayName,
		moodFace,
		runCourse,
		type LiveRun
	} from '$lib/liveRuns';
	import {
		actColor,
		benchCommand,
		buildField,
		diffFieldEvents,
		fieldRunKey,
		truncPathTail
	} from '$lib/residentField';
	import {
		boxFaces,
		buildScene,
		floorPath,
		floorTextTransform,
		iso,
		paintOrder,
		polyPoints,
		residentAnatomy,
		sceneBounds,
		steleCellQuad,
		TRAIL_MAX,
		type Machine,
		type ResidentBody
	} from '$lib/isoField';
	import { moodSigil, SIGIL_COLS, SIGIL_ROWS } from '$lib/moodSigil';
	import { demoFrames } from './demo';

	const POLL_MS = 2000;
	const DEMO_STEP_MS = 3600;
	const RISE_MS = 700;
	const SINK_MS = 1100;
	const FLASH_MS = 1600;
	const PACKET_MS = 2600;
	const INJECT_MS = 3200;

	let runs = $state<LiveRun[]>([]);
	let stale = $state(false);
	let loading = $state(true);
	let signedOut = $state(false);
	let demo = $state(false);
	let selected = $state<string | null>(null);
	let cameraZoom = $state(1);
	let cameraX = $state(0);
	let cameraY = $state(0);
	let panning = $state(false);
	let panPointer: number | null = null;
	let panOrigin = { x: 0, y: 0, cameraX: 0, cameraY: 0 };
	// The entity round's body studies. The committed direction is the carved
	// mood (?body=stele, the default — his steer: the face rendered
	// isometrically, invader-symmetric, stave-boned): structures are drawn,
	// the being is carved. ?body=glyph keeps the halo study, ?body=automaton
	// the boxed one.
	let bodyStyle = $state<ResidentBody>('stele');

	let reduced = false;
	if (typeof window !== 'undefined') {
		reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
	}

	// ── scene (all pure) ───────────────────────────────────────────────────
	let field = $derived(buildField(runs));
	let scene = $derived(buildScene(field));
	let ordered = $derived(paintOrder(scene.machines));
	let bounds = $derived(sceneBounds(scene));
	let residentRun = $derived(field.find((r) => !r.orphan)?.run ?? null);
	let pendingTotal = $derived(runs.reduce((acc, run) => acc + (run.portals?.pending ?? 0), 0));
	let focusRun = $derived(
		(selected && runs.find((r) => fieldRunKey(r) === selected)) || residentRun
	);
	let focusCourse = $derived(runCourse(focusRun?.card_text, focusRun?.course));

	// ── the face the figure wears ─────────────────────────────────────────
	// The run's own authored mood, resolved daemon-side (`moodFace`'s honesty
	// bar: unknown handle ⇒ name only ⇒ no face here — never a guessed one).
	// It breathes on its own clock: ambience, not a receipt — the one licensed
	// exception, same as the beacon's glow, because a being that never
	// blinks is a label.
	const BREATH_MS = 4600;
	const FRAME_MS = 260;
	let residentFace = $derived(
		moodFace(
			residentRun?.mood,
			residentRun?.mood_glyph,
			residentRun?.mood_pitch,
			residentRun?.mood_frames,
			residentRun?.mood_rest
		)
	);
	let faceFrame = $state<string | null>(null);
	$effect(() => {
		const face = residentFace;
		faceFrame = face ? (face.rest ?? face.glyph) : null;
		if (!face?.sequences?.length || reduced) return;
		const frames = face.sequences[0];
		const pending: ReturnType<typeof setTimeout>[] = [];
		const breathe = () => {
			frames.forEach((frame, i) => {
				pending.push(setTimeout(() => (faceFrame = frame), i * FRAME_MS));
			});
			pending.push(
				setTimeout(() => (faceFrame = face.rest ?? face.glyph), frames.length * FRAME_MS)
			);
		};
		const iv = setInterval(breathe, BREATH_MS);
		return () => {
			clearInterval(iv);
			pending.forEach(clearTimeout);
		};
	});

	// ── the overture: the room assembles once, then latches still ─────────
	let overture = $state(false);
	function armOverture(slots: number) {
		if (reduced) return;
		overture = true;
		const total = 1500 + slots * 170 + RISE_MS + 300;
		setTimeout(() => (overture = false), total);
	}

	// ── receipts → motion ─────────────────────────────────────────────────
	interface Packet {
		id: number;
		d: string;
		color: string;
		dur: number;
		reverse: boolean;
	}
	interface Dying {
		id: number;
		machine: Machine;
	}
	let packets = $state<Packet[]>([]);
	let flashes = $state<Record<string, string>>({});
	let births = $state<Record<string, boolean>>({});
	let dying = $state<Dying[]>([]);
	let msgDropSeq = $state(0);
	let seq = 0;
	// The act-trail: per run, the last TRAIL_MAX recorded boundary acts,
	// newest first — the torso's slits are these, so every lit mark on the
	// figure's face is a receipt (the windows' redemption). Starts from the
	// current edge act (state), grows only on recorded boundaries (motion
	// doctrine: the trail is state, its *change* is the receipt).
	let trails = $state<Record<string, string[]>>({});
	function recordAct(runId: string, act: string | null | undefined) {
		if (!act) return;
		trails = { ...trails, [runId]: [act, ...(trails[runId] ?? [])].slice(0, TRAIL_MAX) };
	}

	function pushPacket(d: string | null, color: string, dur: number, reverse = false) {
		if (!d || reduced) return;
		const id = ++seq;
		packets = [...packets, { id, d, color, dur, reverse }];
		setTimeout(() => {
			packets = packets.filter((p) => p.id !== id);
		}, dur + 250);
	}

	function applySnapshot(next: LiveRun[], prev: LiveRun[] | null) {
		// #1510: a row a retired daemon froze in the registry is not part of
		// the body — it would stand on the floor forever, wearing a live lamp.
		const alive = next.filter((run) => run.daemon_stale !== true);
		const events = diffFieldEvents(prev, alive);
		const prevScene = prev ? buildScene(buildField(prev)) : null;
		const nextScene = buildScene(buildField(alive));
		runs = alive;
		// Trail bookkeeping is state, not motion — it runs under reduced
		// motion too. Seed a first-seen run from its current act; after that
		// only recorded boundaries append.
		for (const run of alive) {
			const key = fieldRunKey(run);
			if (!(key in trails)) recordAct(key, run.edge?.act);
		}
		for (const ev of events) {
			if (ev.kind === 'boundary' || ev.kind === 'inject') {
				recordAct(ev.runId, alive.find((r) => fieldRunKey(r) === ev.runId)?.edge?.act);
			}
		}
		if (reduced || events.length === 0) return;

		// An attested read and an inject boundary usually land in the same
		// poll tick and mean the same moment — one transit, the ◈'s.
		const readIds = new Set(events.filter((e) => e.kind === 'read').map((e) => e.runId));
		for (const ev of events) {
			if (ev.kind === 'spawn') {
				births = { ...births, [ev.runId]: true };
				setTimeout(() => {
					births = Object.fromEntries(Object.entries(births).filter(([k]) => k !== ev.runId));
				}, RISE_MS + 150);
				const conduit = nextScene.conduits.find((c) => c.key === ev.runId);
				pushPacket(conduit ? floorPath(conduit.points) : null, actColor('dispatch'), PACKET_MS);
			} else if (ev.kind === 'return') {
				const gone = prevScene?.machines.find((m) => m.key === ev.runId);
				if (gone) {
					const id = ++seq;
					dying = [...dying, { id, machine: gone }];
					setTimeout(() => {
						dying = dying.filter((d) => d.id !== id);
					}, SINK_MS + 150);
				}
				const conduit = prevScene?.conduits.find((c) => c.key === ev.runId);
				pushPacket(
					conduit ? floorPath(conduit.points) : null,
					actColor('publish'),
					PACKET_MS,
					true
				);
			} else if (ev.kind === 'boundary') {
				const run = alive.find((r) => fieldRunKey(r) === ev.runId);
				flashes = { ...flashes, [ev.runId]: actColor(run?.edge?.act) };
				setTimeout(() => {
					flashes = Object.fromEntries(Object.entries(flashes).filter(([k]) => k !== ev.runId));
				}, FLASH_MS);
			} else if (ev.kind === 'inject') {
				if (!readIds.has(ev.runId)) {
					pushPacket(
						nextScene.gatePath.length ? floorPath(nextScene.gatePath) : null,
						actColor('orient'),
						INJECT_MS
					);
				}
			} else if (ev.kind === 'read') {
				// The resting ◈ is carried home — the read attested as a
				// transit, never a silent disappearance.
				pushPacket(
					nextScene.gatePath.length ? floorPath(nextScene.gatePath) : null,
					'#d9a441',
					INJECT_MS
				);
			} else if (ev.kind === 'message') {
				msgDropSeq += 1;
			}
		}
	}

	// ── feed: live poll, or the demo replay ───────────────────────────────
	onMount(() => {
		const params = new URLSearchParams(window.location.search);
		demo = params.has('demo');
		const bodyParam = params.get('body');
		bodyStyle = bodyParam === 'automaton' || bodyParam === 'glyph' ? bodyParam : 'stele';
		let stop = false;
		let prev: LiveRun[] | null = null;

		if (demo) {
			const frames = demoFrames();
			let idx = 0;
			loading = false;
			armOverture(frames[0].length);
			applySnapshot(frames[0], null);
			prev = frames[0];
			const step = () => {
				if (stop) return;
				idx += 1;
				if (idx >= frames.length) {
					// hold the empty floor a beat, then replay from the top —
					// a deliberate reset, so the assembly reads again.
					setTimeout(() => {
						if (stop) return;
						idx = 0;
						prev = null;
						trails = {}; // the replay starts its day over — so does the trail
						armOverture(frames[0].length);
						applySnapshot(frames[0], null);
						prev = frames[0];
						setTimeout(step, DEMO_STEP_MS);
					}, 2600);
					return;
				}
				applySnapshot(frames[idx], prev);
				prev = frames[idx];
				setTimeout(step, DEMO_STEP_MS);
			};
			setTimeout(step, DEMO_STEP_MS);
			return () => {
				stop = true;
			};
		}

		let timer: ReturnType<typeof setTimeout> | null = null;
		const poll = async () => {
			try {
				const data = await fetchLiveRuns();
				stale = data.stale;
				signedOut = false;
				const first = loading;
				if (first) {
					loading = false;
					armOverture(buildField(data.runs).reduce((n, r) => n + 1 + r.limbs.length, 0));
				}
				applySnapshot(data.runs, first ? null : prev);
				prev = runs;
			} catch (error) {
				if (error instanceof LiveRunsAuthError) {
					signedOut = true;
					loading = false;
				}
			}
			if (!stop) timer = setTimeout(poll, POLL_MS);
		};
		poll();
		return () => {
			stop = true;
			if (timer) clearTimeout(timer);
		};
	});

	// ── drawing helpers (screen-space only; no layout decisions) ──────────
	function gridLines(cols: number, rows: number): { d: string; major: boolean }[] {
		const lines: { d: string; major: boolean }[] = [];
		for (let i = 0; i <= cols; i++) {
			const a = iso(i, 0);
			const b = iso(i, rows);
			lines.push({ d: `M ${a.x} ${a.y} L ${b.x} ${b.y}`, major: i % 4 === 0 });
		}
		for (let j = 0; j <= rows; j++) {
			const a = iso(0, j);
			const b = iso(cols, j);
			lines.push({ d: `M ${a.x} ${a.y} L ${b.x} ${b.y}`, major: j % 4 === 0 });
		}
		return lines;
	}
	let grid = $derived(gridLines(scene.cols, scene.rows));
	let plate = $derived([
		iso(0, 0),
		iso(scene.cols, 0),
		iso(scene.cols, scene.rows),
		iso(0, scene.rows)
	]);
	const RIM_DROP = 7;
	let frontRim = $derived([
		iso(0, scene.rows),
		iso(scene.cols, scene.rows),
		{ x: iso(scene.cols, scene.rows).x, y: iso(scene.cols, scene.rows).y + RIM_DROP },
		{ x: iso(0, scene.rows).x, y: iso(0, scene.rows).y + RIM_DROP }
	]);
	let rightRim = $derived([
		iso(scene.cols, 0),
		iso(scene.cols, scene.rows),
		{ x: iso(scene.cols, scene.rows).x, y: iso(scene.cols, scene.rows).y + RIM_DROP },
		{ x: iso(scene.cols, 0).x, y: iso(scene.cols, 0).y + RIM_DROP }
	]);

	/** Floor labels are painted signage, not rows — a long name gets cut,
	 *  the full one lives in the HUD when the machine is pressed. */
	function trunc(name: string, max = 18): string {
		return name.length > max ? name.slice(0, max - 1) + '…' : name;
	}

	/** Every label is horizontal (the maintainer's steer: isometric floor
	 *  text beside a horizontal callout read as two systems). Strand labels
	 *  hang under the block's front corner, combing onto two baselines so
	 *  adjacent lane neighbours never collide. */
	function strandLabelDy(m: Machine): number {
		return m.order % 2 === 0 ? 30 : 15;
	}

	function machineDelay(m: Machine): string {
		return overture ? `${320 + m.order * 120}ms` : '0ms';
	}
	function conduitDelay(i: number): string {
		return overture ? `${180 + i * 90}ms` : '0ms';
	}
	/** Screen-space destinations around the resident's dock. An act is a
	 * place: the avatar travels when the attested edge changes instead of
	 * glowing in place like a status lamp. Long easing matches the real
	 * boundary tempo; reduced-motion settles directly at the destination. */
	function avatarTravel(act: string | null | undefined): { x: number; y: number } {
		switch (act) {
			case 'orient':
				return { x: -76, y: -18 };
			case 'mutate':
				return { x: -54, y: 52 };
			case 'probe':
				return { x: 58, y: 48 };
			case 'publish':
				return { x: 82, y: -16 };
			case 'dispatch':
				return { x: 26, y: 62 };
			case 'wait':
				return { x: -22, y: 24 };
			default:
				return { x: 0, y: 18 };
		}
	}

	const atlasBlocks = [
		{ x: 0.55, y: 0.55, w: 1.15, d: 1.35, h: 0.5 },
		{ x: 1.8, y: 0.55, w: 1.05, d: 1.35, h: 0.82 },
		{ x: 3.0, y: 0.55, w: 0.9, d: 1.35, h: 1.12 }
	];
	let atlasFaces = $derived(atlasBlocks.map((b) => boxFaces(b.x, b.y, b.w, b.d, b.h)));
	let plaza = $derived([iso(3.35, 1.45), iso(6.9, 1.45), iso(6.9, 4.9), iso(3.35, 4.9)]);
	let camp = $derived([
		iso(0.65, 4.65),
		iso(scene.cols - 0.65, 4.65),
		iso(scene.cols - 0.65, scene.rows - 0.65),
		iso(0.65, scene.rows - 0.65)
	]);
	let trunk = $derived(
		floorPath([
			{ x: scene.cols - 0.9, y: 0.55 },
			{ x: 7.35, y: 0.55 },
			{ x: 7.35, y: 3.15 },
			{ x: 6.2, y: 3.15 }
		])
	);

	function pressFloor() {
		selected = null;
	}
	function beginPan(event: PointerEvent) {
		if (event.button !== 0) return;
		pressFloor();
		panning = true;
		panPointer = event.pointerId;
		panOrigin = { x: event.clientX, y: event.clientY, cameraX, cameraY };
		(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
	}
	function movePan(event: PointerEvent) {
		if (!panning || event.pointerId !== panPointer) return;
		cameraX = panOrigin.cameraX + event.clientX - panOrigin.x;
		cameraY = panOrigin.cameraY + event.clientY - panOrigin.y;
	}
	function endPan(event: PointerEvent) {
		if (event.pointerId !== panPointer) return;
		panning = false;
		panPointer = null;
	}
	function zoomMap(event: WheelEvent) {
		event.preventDefault();
		cameraZoom = Math.max(0.8, Math.min(1.4, cameraZoom * Math.exp(-event.deltaY * 0.0012)));
	}
	function resetCamera() {
		cameraX = 0;
		cameraY = 0;
		cameraZoom = 1;
	}
	function pressMachine(key: string) {
		selected = selected === key ? null : key;
	}

	// The gate's two posts, precomputed shapes.
	let gatePosts = $derived([
		boxFaces(scene.gate.x - 0.42, scene.gate.y - 0.06, 0.22, 0.24, 1.15),
		boxFaces(scene.gate.x + 0.2, scene.gate.y - 0.06, 0.22, 0.24, 1.15)
	]);
	let gateBeam = $derived({
		a: iso(scene.gate.x - 0.31, scene.gate.y + 0.06, 1.15),
		b: iso(scene.gate.x + 0.31, scene.gate.y + 0.06, 1.15)
	});
	let gateRest = $derived(iso(scene.gate.x, scene.gate.y + 0.35, 0.62));
</script>

<svelte:head>
	<title>brnrd — the room</title>
	<meta name="robots" content="noindex" />
</svelte:head>

<div class="room" data-the-room>
	<header class="hud hud-top">
		<span class="mark">the room</span>
		<div class="hud-tools">
			<div class="camera" aria-label="map zoom">
				<button onclick={() => (cameraZoom = Math.max(0.8, cameraZoom - 0.1))} aria-label="zoom out"
					>−</button
				>
				<button class="camera-readout" onclick={resetCamera} aria-label="reset map camera"
					>{Math.round(cameraZoom * 100)}%</button
				>
				<button onclick={() => (cameraZoom = Math.min(1.4, cameraZoom + 0.1))} aria-label="zoom in"
					>+</button
				>
			</div>
			<span class="state">
				{#if loading}connecting…{:else if signedOut}signed out{:else if demo}replay{:else if stale}stale
					<i class="dot warn"></i>{:else}live <i class="dot live"></i>{/if}
			</span>
		</div>
	</header>

	<div
		class="stage"
		class:is-stale={stale && !demo}
		class:panning
		onpointerdown={beginPan}
		onpointermove={movePan}
		onpointerup={endPan}
		onpointercancel={endPan}
		onwheel={zoomMap}
		role="presentation"
	>
		<svg
			viewBox={`${bounds.x} ${bounds.y} ${bounds.w} ${bounds.h}`}
			class:ov={overture}
			style={`--camera:${cameraZoom};--camera-x:${cameraX}px;--camera-y:${cameraY}px`}
			aria-label="the live system, drawn as a room"
		>
			<defs>
				<filter id="stone-body" x="-20%" y="-20%" width="140%" height="150%">
					<feTurbulence
						type="fractalNoise"
						baseFrequency="0.055 0.11"
						numOctaves="3"
						seed="17"
						result="grain"
					/>
					<feDisplacementMap in="SourceGraphic" in2="grain" scale="2.4" />
					<feDropShadow dx="0" dy="7" stdDeviation="6" flood-color="#000" flood-opacity="0.72" />
				</filter>
				<linearGradient id="stone-face" x1="0" y1="0" x2="1" y2="1">
					<stop offset="0" stop-color="#211d18" />
					<stop offset="0.55" stop-color="#0d0b09" />
					<stop offset="1" stop-color="#242019" />
				</linearGradient>
			</defs>
			<!-- the plate: the daemon's floor -->
			<g class="plate">
				<polygon points={polyPoints(frontRim)} class="plate-rim-face" />
				<polygon points={polyPoints(rightRim)} class="plate-rim-face" />
				<polygon points={polyPoints(plate)} class="plate-top" />
				<g class="grid">
					{#each grid as line (line.d)}
						<path d={line.d} class="grid-line" class:major={line.major} />
					{/each}
				</g>
				<polygon points={polyPoints(plate)} class="plate-edge" />
			</g>

			<!-- Permanent ATLAS architecture and the run-shaped CAMP. The room
			     has a readable civic body before a single fixture or strand arrives. -->
			<g class="world-frame">
				<polygon points={polyPoints(camp)} class="camp-court" />
				<polygon points={polyPoints(plaza)} class="trunk-plaza" />
				<path d={trunk} class="trunk-line" />
				{#each atlasFaces as block, i (i)}
					<polygon points={polyPoints(block.left)} class="atlas-l" />
					<polygon points={polyPoints(block.right)} class="atlas-r" />
					<polygon points={polyPoints(block.top)} class="atlas-t" />
				{/each}
				<text transform={floorTextTransform(0.7, 2.25)} class="zone-label atlas-label">atlas</text>
				<text transform={floorTextTransform(1.0, 7.95)} class="zone-label camp-label"
					>camp · this run</text
				>
				<text transform={floorTextTransform(4.05, 4.45)} class="zone-label plaza-label"
					>trunk plaza</text
				>
			</g>

			<!-- conduits: cables in the floor's negative space -->
			{#each scene.conduits as conduit, i (conduit.key)}
				<g class="conduit" style={`animation-delay:${conduitDelay(i)}`}>
					<path d={floorPath(conduit.points)} class="conduit-keel" />
					<path d={floorPath(conduit.points)} class="conduit-line" />
					<!-- one pad, at the dispatch port; the far end vanishes
					     behind its machine, which is the correct occlusion -->
					{#each [conduit.points[0]] as pad (pad.x + ':' + pad.y)}
						{@const p = iso(pad.x, pad.y)}
						<polygon
							points={polyPoints([
								{ x: p.x, y: p.y - 2.2 },
								{ x: p.x + 4.4, y: p.y },
								{ x: p.x, y: p.y + 2.2 },
								{ x: p.x - 4.4, y: p.y }
							])}
							class="conduit-pad"
						/>
					{/each}
				</g>
			{/each}
			{#if scene.gatePath.length}
				<g class="conduit" style={`animation-delay:${conduitDelay(scene.conduits.length)}`}>
					<path d={floorPath(scene.gatePath)} class="conduit-line gate-feed" />
				</g>
			{/if}

			<!-- the portal gate on the back edge -->
			<g class="gate">
				{#each gatePosts as post, i (i)}
					<polygon points={polyPoints(post.left)} class="gate-face-l" />
					<polygon points={polyPoints(post.right)} class="gate-face-r" />
					<polygon points={polyPoints(post.top)} class="gate-face-t" />
				{/each}
				<line
					x1={gateBeam.a.x}
					y1={gateBeam.a.y}
					x2={gateBeam.b.x}
					y2={gateBeam.b.y}
					class="gate-beam"
				/>
				<!-- the one label that stays floor-painted: a static planned
				     stencil reads as part of the floor plan (his 2nd read);
				     only variable-length run names go horizontal -->
				<text
					transform={floorTextTransform(scene.gate.x - 0.55, scene.gate.y + 0.85)}
					class="floor-label dim">portal</text
				>
				{#if pendingTotal > 0}
					{#key msgDropSeq}
						<g class="msg" class:drop={msgDropSeq > 0}>
							<rect
								x={gateRest.x - 4.6}
								y={gateRest.y - 4.6}
								width="9.2"
								height="9.2"
								transform={`rotate(45 ${gateRest.x} ${gateRest.y})`}
								class="msg-core"
							/>
							{#if pendingTotal > 1}
								<text x={gateRest.x + 10} y={gateRest.y + 3} class="msg-count">×{pendingTotal}</text
								>
							{/if}
						</g>
					{/key}
				{/if}
			</g>

			<!-- packets: one recorded event, one transit. A packet is a small
			     floor-plane diamond — the conduit pads' own shape, moving —
			     drawn UNDER the machines so it rides the floor and vanishes
			     behind the block it arrives at (the maintainer's first-read
			     steer: a dot pasted over the scene has no place in it). -->
			<!-- CSS motion paths, not SMIL: an <animateMotion> inserted into a
			     long-running document resolves begin="0s" against the DOCUMENT
			     timeline, so a packet born at t=30s is already "finished" and
			     freezes glowing at its endpoint — measured live by the
			     maintainer ("the blue dot doesn't move"). CSS animations
			     start at insertion, which is the semantics a receipt needs. -->
			{#each packets as packet (packet.id)}
				<g
					class="pkt"
					class:rev={packet.reverse}
					style={`--c:${packet.color};offset-path:path('${packet.d}');animation-duration:${packet.dur}ms`}
				>
					<polygon points="0,-4.4 8.8,0 0,4.4 -8.8,0" class="pkt-halo" />
					<polygon points="0,-2.4 4.8,0 0,2.4 -4.8,0" class="pkt-core" />
				</g>
			{/each}

			<!-- the machines, back to front -->
			{#each ordered as m (m.key)}
				{@const run = m.run}
				{@const lamp = actColor(run.edge?.act)}
				{@const awaiting = run.lifecycle === 'awaiting'}
				<g
					class={`machine kind-${m.kind}`}
					class:birth={births[m.key]}
					class:awaiting
					class:selected={selected === m.key}
					style={`animation-delay:${machineDelay(m)}`}
					data-room-machine={m.key}
					role="button"
					tabindex="0"
					aria-label={`inspect ${liveRunDisplayName(run)}`}
					onpointerdown={(e) => {
						e.stopPropagation();
						pressMachine(m.key);
					}}
					onkeydown={(e) => {
						if (e.key === 'Enter' || e.key === ' ') {
							e.preventDefault();
							pressMachine(m.key);
						}
					}}
				>
					{#if m.kind === 'resident'}
						<!-- THE FIGURE — the entity round: a torso, a head that
						     wears the run's own mood, an act-trail of recorded
						     boundaries (the windows' redemption: every slit is a
						     receipt), and a bench where the current command lies. -->
						{@const anat = residentAnatomy(m, bodyStyle)}
						{@const tf = boxFaces(
							anat.torso.x,
							anat.torso.y,
							anat.torso.w,
							anat.torso.d,
							anat.torso.h
						)}
						{@const bef = boxFaces(
							anat.bench.x,
							anat.bench.y,
							anat.bench.w,
							anat.bench.d,
							anat.bench.h
						)}
						{@const trail = trails[m.key] ?? []}
						{@const course = runCourse(run.card_text, run.course)}
						{@const crown = anat.head
							? iso(
									anat.head.x + anat.head.w / 2,
									anat.head.y + anat.head.d / 2,
									anat.head.z0 + anat.head.h
								)
							: { x: anat.faceAnchor.x, y: anat.faceAnchor.y - 21 }}
						{@const travel = avatarTravel(run.edge?.act)}
						<polygon
							points={polyPoints([
								iso(m.x - 0.3, m.y - 0.3),
								iso(m.x + m.w + 0.3, m.y - 0.3),
								iso(m.x + m.w + 0.3, m.y + m.d + 0.3),
								iso(m.x - 0.3, m.y + m.d + 0.3)
							])}
							class="plinth"
						/>
						<!-- The resident is the proven account avatar, not a new
						     abstract block. It stands as a readable sprite on the
						     run machinery; the stone body below remains its dock. -->
						<g
							class="avatar-entity"
							class:reduced
							style={`transform:translate(${travel.x}px, ${travel.y}px)`}
						>
							<ellipse cx={crown.x} cy={crown.y + 59} rx="35" ry="11" class="avatar-shadow" />
							<rect
								x={crown.x - 39}
								y={crown.y - 11}
								width="78"
								height="78"
								rx="11"
								class="avatar-frame"
							/>
							<rect
								x={crown.x - 34}
								y={crown.y - 6}
								width="68"
								height="68"
								rx="8"
								class="avatar-stone"
							/>
							{#if faceFrame}
								<text x={crown.x} y={crown.y + 34} text-anchor="middle" class="avatar-expression"
									>{faceFrame}</text
								>
							{/if}
						</g>
						<polygon points={polyPoints(tf.left)} class="face-l" />
						<polygon points={polyPoints(tf.right)} class="face-r" />
						<polygon points={polyPoints(tf.top)} class="face-t" />
						{#if flashes[m.key]}
							<polygon
								points={polyPoints(tf.top)}
								class="face-flash"
								style={`fill:${flashes[m.key]}`}
							/>
						{/if}
						<!-- the act-trail: recorded boundary acts, newest at top -->
						{#each anat.trailSlits as slit, i (i)}
							<line
								x1={slit.a.x}
								y1={slit.a.y}
								x2={slit.b.x}
								y2={slit.b.y}
								class="trail-slit"
								class:unlit={!trail[i]}
								style={trail[i] ? `stroke:${actColor(trail[i])}` : undefined}
							/>
						{/each}
						{#if anat.head}
							{@const hf = boxFaces(
								anat.head.x,
								anat.head.y,
								anat.head.w,
								anat.head.d,
								anat.head.h,
								anat.head.z0
							)}
							<polygon points={polyPoints(hf.left)} class="face-l" />
							<polygon points={polyPoints(hf.right)} class="face-r" />
							<polygon points={polyPoints(hf.top)} class="face-t" />
							{#if faceFrame}
								<!-- the visor: a screen turned to the viewer — text on
								     screens is horizontal because screens are -->
								<rect
									x={anat.faceAnchor.x - 21}
									y={anat.faceAnchor.y - 8}
									width="42"
									height="16"
									rx="2"
									class="visor"
								/>
								<text
									x={anat.faceAnchor.x}
									y={anat.faceAnchor.y + 3.5}
									text-anchor="middle"
									class="face-text"
								>
									{faceFrame}
								</text>
							{/if}
						{:else if bodyStyle === 'stele'}
							<!-- the carved mood: the stone is drawn, the being is
							     carved — the mood's own frame becomes a pixel sigil
							     grooved into the stele's face, re-derived every
							     breath frame so the carving itself breathes -->
							{#if faceFrame}
								{#each moodSigil(faceFrame) as sigilRow, ri (ri)}
									{#each sigilRow as lit, ci (ci)}
										{#if lit}
											<polygon
												points={polyPoints(
													steleCellQuad(anat.torso, ri, ci, SIGIL_ROWS, SIGIL_COLS)
												)}
												class="carve"
											/>
										{/if}
									{/each}
								{/each}
							{/if}
						{:else}
							<!-- the core-glyph: structures are drawn, the being is
							     written — a hovering face-core tethered to its dock,
							     no volume anywhere on the entity -->
							{@const dockTop = iso(
								anat.torso.x + anat.torso.w / 2,
								anat.torso.y + anat.torso.d / 2,
								anat.torso.h
							)}
							<line
								x1={dockTop.x}
								y1={dockTop.y}
								x2={anat.faceAnchor.x}
								y2={anat.faceAnchor.y + 21}
								class="tether"
							/>
							<circle cx={anat.faceAnchor.x} cy={anat.faceAnchor.y} r="21" class="halo" />
							{#if faceFrame}
								<text
									x={anat.faceAnchor.x}
									y={anat.faceAnchor.y + 5}
									text-anchor="middle"
									class="face-text glyph"
								>
									{faceFrame}
								</text>
							{/if}
						{/if}
						<!-- the bench: where the current command lies -->
						<polygon points={polyPoints(bef.left)} class="bench-l" />
						<polygon points={polyPoints(bef.right)} class="bench-r" />
						<polygon points={polyPoints(bef.top)} class="bench-t" />
						{#if run.edge?.detail}
							<circle
								cx={anat.benchAnchor.x - 5}
								cy={anat.benchAnchor.y - 3}
								r="1.8"
								style={`fill:${lamp}`}
							/>
							<text x={anat.benchAnchor.x} y={anat.benchAnchor.y} class="bench-cmd">
								{trunc(benchCommand(run.edge.detail), 34)}
							</text>
							{#if (run.edge.dir && run.edge.dir !== '.') || run.room?.dir || run.room?.branch}
								<text x={anat.benchAnchor.x} y={anat.benchAnchor.y + 10} class="bench-dir">
									{truncPathTail(
										(run.edge.dir && run.edge.dir !== '.' ? run.edge.dir : null) ??
											run.room?.dir ??
											run.room?.branch ??
											'',
										30
									)}
								</text>
							{/if}
						{/if}
						<!-- the course: plan rows as pads on the telemetry edge -->
						{#if course}
							{#each Array.from({ length: Math.min(course.total, 10) }, (_, i) => i) as i (i)}
								{@const cp = iso(
									m.x + m.w + 0.55,
									m.y - 0.3 + ((i + 0.5) / Math.min(course.total, 10)) * (m.d + 0.6)
								)}
								<rect
									x={cp.x - 2.6}
									y={cp.y - 2.6}
									width="5.2"
									height="5.2"
									transform={`rotate(45 ${cp.x} ${cp.y})`}
									class="course-pad"
									class:done={i < course.done}
								/>
							{/each}
						{/if}
						<line x1={crown.x} y1={crown.y} x2={crown.x} y2={crown.y - 16} class="mast" />
						<circle
							cx={crown.x}
							cy={crown.y - 19}
							r="2.6"
							class="beacon"
							style={`fill:${lamp};--c:${lamp}`}
						/>
						<!-- the callout: name at the antenna, in the figure's own void -->
						<text
							x={crown.x + travel.x - 44}
							y={crown.y + travel.y - 20}
							text-anchor="end"
							class="callout"
						>
							resident<tspan class="core-tag"
								>{run.runner?.core ? ` · ${run.runner.core}` : ''}</tspan
							>
						</text>
					{:else}
						{@const f = boxFaces(m.x, m.y, m.w, m.d, m.h)}
						{@const plot = { x: m.x - 0.38, y: m.y - 0.38, w: m.w + 0.76, d: m.d + 0.76 }}
						<polygon
							points={polyPoints([
								iso(plot.x, plot.y),
								iso(plot.x + plot.w, plot.y),
								iso(plot.x + plot.w, plot.y + plot.d),
								iso(plot.x, plot.y + plot.d)
							])}
							class="plot"
						/>
						<polygon points={polyPoints(f.left)} class="face-l" />
						<polygon points={polyPoints(f.right)} class="face-r" />
						<polygon points={polyPoints(f.top)} class="face-t" />
						{#if flashes[m.key]}
							<polygon
								points={polyPoints(f.top)}
								class="face-flash"
								style={`fill:${flashes[m.key]}`}
							/>
						{/if}
						<circle
							cx={f.frontCorner.x}
							cy={f.frontCorner.y - 3}
							r="2.2"
							class="lamp"
							style={`fill:${lamp};--c:${lamp}`}
						/>
						{#if m.kind === 'orphan'}
							{@const stub = iso(m.x + m.w / 2, m.y)}
							{@const stubEnd = iso(m.x + m.w / 2, m.y - 0.7)}
							<line x1={stub.x} y1={stub.y} x2={stubEnd.x} y2={stubEnd.y} class="severed" />
						{/if}
						{#if m.hands > 0}
							{#each Array.from({ length: Math.min(m.hands, 3) }, (_, i) => i) as i (i)}
								{@const c = boxFaces(m.x + m.w + 0.28, m.y + 0.12 + i * 0.42, 0.26, 0.26, 0.2)}
								<polygon points={polyPoints(c.left)} class="crate-l" />
								<polygon points={polyPoints(c.right)} class="crate-r" />
								<polygon points={polyPoints(c.top)} class="crate-t" />
							{/each}
							{#if m.hands > 3}
								{@const hp = iso(m.x + m.w + 0.75, m.y + 0.7)}
								<text x={hp.x} y={hp.y} class="hands-count">+{m.hands}</text>
							{/if}
						{/if}
						<text
							x={f.floorFront.x}
							y={f.floorFront.y + strandLabelDy(m)}
							text-anchor="middle"
							class="floor-label"
						>
							{trunc(liveRunDisplayName(run), 18)}{#if run.runner?.core}<tspan class="core-tag">
									· {run.runner.core}</tspan
								>{/if}{#if awaiting}<tspan class="core-tag"> · await</tspan>{/if}
						</text>
						{#if run.room?.branch}
							<text
								x={f.floorFront.x}
								y={f.floorFront.y + strandLabelDy(m) + 10}
								text-anchor="middle"
								class="plot-branch">{trunc(run.room.branch, 24)}</text
							>
						{/if}
					{/if}
				</g>
			{/each}

			<!-- returning machines sinking into the floor -->
			{#each dying as d (d.id)}
				{@const box =
					d.machine.kind === 'resident' ? residentAnatomy(d.machine, bodyStyle).torso : d.machine}
				{@const f = boxFaces(box.x, box.y, box.w, box.d, box.h)}
				<g class={`machine sink kind-${d.machine.kind}`}>
					<polygon points={polyPoints(f.left)} class="face-l" />
					<polygon points={polyPoints(f.right)} class="face-r" />
					<polygon points={polyPoints(f.top)} class="face-t" />
				</g>
			{/each}

			{#if !loading && scene.machines.length === 0}
				{@const center = iso(scene.cols / 2, scene.rows / 2)}
				<text x={center.x} y={center.y} text-anchor="middle" class="floor-label empty-note">
					{signedOut ? 'sign in to see the room' : 'between wakes — daemon listening'}
				</text>
			{/if}
		</svg>
	</div>

	{#if selected && focusRun}
		<!-- the dossier: pressing a machine expands its record — same room
		     vocabulary (mono, hairline rules), no panel class imported -->
		<section class="dossier" aria-label="machine dossier">
			<header>
				<span class="dossier-name">{liveRunDisplayName(focusRun)}</span>
				<button class="dossier-close" onpointerdown={() => (selected = null)}>×</button>
			</header>
			<dl>
				{#if focusRun.runner?.core}
					<dt>core</dt>
					<dd>
						{focusRun.runner.core}{focusRun.runner.class ? ` · ${focusRun.runner.class}` : ''}
					</dd>
				{/if}
				{#if focusRun.room?.branch || focusRun.room?.dir}
					<dt>room</dt>
					<dd>
						{[focusRun.room?.branch, focusRun.room?.dir].filter(Boolean).join(' · ') ||
							'the shared checkout'}
					</dd>
				{/if}
				{#if focusRun.edge?.act}
					<dt>edge</dt>
					<dd>
						<span style={`color:${actColor(focusRun.edge.act)}`}>{focusRun.edge.act}</span>
						{#if focusRun.edge.detail}· {focusRun.edge.detail}{/if}
						{#if focusRun.edge.dir && focusRun.edge.dir !== '.'}· in {focusRun.edge.dir}{/if}
					</dd>
				{/if}
				{#if focusCourse}
					<dt>course</dt>
					<dd>
						{focusCourse.done}/{focusCourse.total}{focusCourse.current
							? ` · ${focusCourse.current}`
							: ''}
					</dd>
				{/if}
				{#if focusRun.lifecycle}
					<dt>state</dt>
					<dd>{focusRun.lifecycle}</dd>
				{:else if focusRun.phase}
					<dt>phase</dt>
					<dd>{focusRun.phase}</dd>
				{/if}
				{#if focusRun.portals?.pending}
					<dt>portal</dt>
					<dd>◈ {focusRun.portals.pending} waiting</dd>
				{/if}
			</dl>
		</section>
	{/if}
	<footer class="hud hud-bottom">
		{#if focusRun}
			<span class="focus-name">{liveRunDisplayName(focusRun)}</span>
			{#if focusRun.room?.branch}<span class="sep">·</span><span>{focusRun.room.branch}</span>{/if}
			{#if focusRun.edge?.act}
				<span class="sep">·</span>
				<span style={`color:${actColor(focusRun.edge.act)}`}>⌁ {focusRun.edge.act}</span>
				{#if focusRun.edge.detail}<span class="detail">{focusRun.edge.detail}</span>{/if}
			{/if}
			{#if focusCourse}<span class="sep">·</span><span
					>course {focusCourse.done}/{focusCourse.total}</span
				>{/if}
		{:else if signedOut}
			<a href={resolve('/login')}>sign in</a> to stand in the room
		{:else if !loading}
			between wakes — the floor holds
		{/if}
	</footer>
</div>

<style>
	.room {
		position: fixed;
		inset: 0;
		background:
			radial-gradient(52% 58% at 50% 46%, rgba(217, 164, 65, 0.11), transparent 72%),
			linear-gradient(180deg, #100c08 0%, #080604 100%);
		display: flex;
		flex-direction: column;
		overflow: hidden;
		font-family: var(
			--font-mono,
			ui-monospace,
			'SF Mono',
			'Cascadia Mono',
			'JetBrains Mono',
			monospace
		);
	}

	.hud {
		display: flex;
		align-items: baseline;
		gap: 0.6em;
		padding: 12px 16px;
		font-size: 11px;
		color: #a8a29e;
		flex: none;
		z-index: 2;
	}
	.hud-top {
		justify-content: space-between;
	}
	.hud-tools,
	.camera {
		display: flex;
		align-items: center;
		gap: 8px;
	}
	.camera {
		gap: 2px;
		color: #6f6962;
	}
	.camera button {
		width: 24px;
		height: 22px;
		border: 1px solid rgba(217, 164, 65, 0.22);
		background: rgba(20, 14, 8, 0.8);
		color: #c9b99e;
		font: inherit;
		cursor: pointer;
	}
	.camera .camera-readout {
		width: 43px;
		font-size: 9px;
	}
	.camera button:hover,
	.camera button:focus-visible {
		border-color: rgba(255, 205, 110, 0.65);
		color: #ffe0a5;
	}
	.hud-top .mark {
		color: #f3e8d8;
		letter-spacing: 0.22em;
		text-transform: uppercase;
		font-size: 11px;
	}
	.hud-top .state {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		color: #8a827a;
	}
	.dot {
		width: 6px;
		height: 6px;
		border-radius: 50%;
		display: inline-block;
	}
	.dot.live {
		background: #7fbf7f;
		box-shadow: 0 0 6px rgba(127, 191, 127, 0.8);
	}
	.dot.warn {
		background: #d3a75e;
		box-shadow: 0 0 6px rgba(211, 167, 94, 0.8);
	}

	.stage {
		flex: 1;
		min-height: 0;
		display: flex;
		align-items: center;
		justify-content: center;
		padding: 0 18px 6px;
		cursor: grab;
		touch-action: none;
		user-select: none;
	}
	.stage.panning {
		cursor: grabbing;
	}
	.stage.is-stale {
		filter: grayscale(0.35) brightness(0.82);
	}
	svg {
		width: 100%;
		height: 100%;
		max-height: 100%;
		overflow: visible;
		transform: translate(var(--camera-x), var(--camera-y)) scale(var(--camera));
		transition: transform 180ms ease-out;
	}
	.stage.panning svg {
		transition: none;
	}

	/* ── atlas / camp / plaza: architecture before activity ───────────── */
	.camp-court {
		fill: rgba(72, 140, 171, 0.035);
		stroke: rgba(99, 190, 213, 0.22);
		stroke-width: 0.85;
		stroke-dasharray: 5 4;
	}
	.trunk-plaza {
		fill: rgba(217, 164, 65, 0.085);
		stroke: rgba(255, 205, 110, 0.48);
		stroke-width: 1.05;
	}
	.trunk-line {
		fill: none;
		stroke: rgba(255, 205, 110, 0.52);
		stroke-width: 3;
		stroke-linecap: square;
		filter: drop-shadow(0 0 5px rgba(217, 164, 65, 0.28));
	}
	.atlas-t {
		fill: #30240f;
		stroke: rgba(255, 205, 110, 0.62);
		stroke-width: 0.85;
	}
	.atlas-l {
		fill: #100c07;
		stroke: rgba(217, 164, 65, 0.2);
		stroke-width: 0.55;
	}
	.atlas-r {
		fill: #1c1409;
		stroke: rgba(217, 164, 65, 0.28);
		stroke-width: 0.55;
	}
	.zone-label {
		font-size: 8px;
		letter-spacing: 0.2em;
		text-transform: uppercase;
		pointer-events: none;
	}
	.atlas-label,
	.plaza-label {
		fill: rgba(255, 220, 159, 0.72);
	}
	.camp-label {
		fill: rgba(115, 197, 218, 0.64);
	}
	.plot {
		fill: rgba(79, 157, 194, 0.06);
		stroke: rgba(110, 198, 255, 0.36);
		stroke-width: 0.8;
		stroke-dasharray: 4 3;
	}
	.plot-branch {
		fill: rgba(110, 198, 255, 0.68);
		font-size: 7.5px;
		letter-spacing: 0.04em;
		pointer-events: none;
	}

	/* ── the plate ─────────────────────────────────────────────────────── */
	.plate-top {
		fill: #171009;
	}
	.plate-rim-face {
		fill: #070503;
	}
	.plate-edge {
		fill: none;
		stroke: rgba(217, 164, 65, 0.34);
		stroke-width: 1;
	}
	.grid-line {
		fill: none;
		stroke: rgba(217, 164, 65, 0.105);
		stroke-width: 0.7;
	}
	.grid-line.major {
		stroke: rgba(217, 164, 65, 0.19);
	}

	/* ── conduits ──────────────────────────────────────────────────────── */
	.conduit-keel {
		fill: none;
		stroke: rgba(0, 0, 0, 0.55);
		stroke-width: 3.5;
	}
	.conduit-line {
		fill: none;
		stroke: rgba(217, 164, 65, 0.28);
		stroke-width: 1.4;
	}
	.conduit-line.gate-feed {
		stroke: rgba(110, 198, 255, 0.22);
		stroke-dasharray: 5 4;
	}
	.conduit-pad {
		fill: rgba(217, 164, 65, 0.5);
	}

	/* ── machines ──────────────────────────────────────────────────────── */
	.machine {
		cursor: pointer;
	}
	.kind-resident .face-t {
		fill: #38260f;
		stroke: rgba(255, 205, 110, 0.75);
		stroke-width: 1.1;
	}
	.kind-resident .face-l {
		fill: #1a1209;
		stroke: rgba(255, 205, 110, 0.28);
		stroke-width: 0.7;
	}
	.kind-resident .face-r {
		fill: #241809;
		stroke: rgba(255, 205, 110, 0.34);
		stroke-width: 0.7;
	}
	.avatar-entity {
		pointer-events: none;
		filter: drop-shadow(0 8px 12px rgba(0, 0, 0, 0.68));
		transition: transform 3.2s cubic-bezier(0.3, 0.05, 0.2, 1);
		transform-box: view-box;
		transform-origin: 0 0;
	}
	.avatar-entity.reduced {
		transition: none;
	}
	.avatar-shadow {
		fill: rgba(255, 190, 70, 0.12);
		filter: blur(4px);
	}
	.avatar-frame {
		fill: #0a0806;
		stroke: rgba(255, 205, 110, 0.72);
		stroke-width: 1.2;
	}
	.avatar-stone {
		fill: url(#stone-face);
		stroke: rgba(197, 173, 129, 0.42);
		stroke-width: 2.5;
		filter: url(#stone-body);
	}
	.avatar-expression {
		fill: #ffb72e;
		font-size: 19px;
		font-weight: 600;
		letter-spacing: 0.08em;
		filter: drop-shadow(0 0 5px rgba(255, 151, 35, 0.95));
	}
	.plinth {
		fill: rgba(217, 164, 65, 0.06);
		stroke: rgba(217, 164, 65, 0.3);
		stroke-width: 0.8;
	}
	.mast {
		stroke: rgba(255, 205, 110, 0.6);
		stroke-width: 1;
	}
	.beacon {
		filter: drop-shadow(0 0 4px var(--c));
	}

	/* the figure: trail, face, bench, course */
	.trail-slit {
		stroke-width: 2.2;
		filter: drop-shadow(0 0 2.5px currentColor);
	}
	.trail-slit.unlit {
		stroke: rgba(255, 205, 110, 0.1);
		filter: none;
	}
	.visor {
		fill: #0b0803;
		stroke: rgba(255, 205, 110, 0.55);
		stroke-width: 0.8;
	}
	.face-text {
		fill: #ffd9a0;
		font-size: 9px;
		letter-spacing: 0.5px;
	}
	.face-text.glyph {
		font-size: 12.5px;
		filter: drop-shadow(0 0 5px rgba(255, 205, 110, 0.7));
	}
	.halo {
		fill: rgba(11, 8, 3, 0.85);
		stroke: rgba(255, 205, 110, 0.5);
		stroke-width: 0.9;
	}
	.tether {
		stroke: rgba(255, 205, 110, 0.28);
		stroke-width: 0.8;
		stroke-dasharray: 1.5 3;
	}
	.carve {
		fill: rgba(255, 217, 160, 0.92);
		filter: drop-shadow(0 0 2.5px rgba(255, 205, 110, 0.8));
	}
	.bench-t {
		fill: #2a1e0d;
		stroke: rgba(255, 205, 110, 0.4);
		stroke-width: 0.7;
	}
	.bench-l {
		fill: #140e06;
	}
	.bench-r {
		fill: #1c1308;
	}
	.bench-cmd {
		fill: #cdbfa8;
		font-size: 8px;
	}
	.bench-dir {
		fill: #7d7264;
		font-size: 7px;
	}
	.course-pad {
		fill: none;
		stroke: rgba(217, 164, 65, 0.35);
		stroke-width: 0.7;
	}
	.course-pad.done {
		fill: rgba(217, 164, 65, 0.55);
		stroke: rgba(255, 205, 110, 0.8);
	}

	.kind-strand .face-t,
	.kind-orphan .face-t {
		fill: #1c2733;
		stroke: rgba(110, 198, 255, 0.5);
		stroke-width: 0.9;
	}
	.kind-strand .face-l,
	.kind-orphan .face-l {
		fill: #0e1319;
		stroke: rgba(110, 198, 255, 0.18);
		stroke-width: 0.6;
	}
	.kind-strand .face-r,
	.kind-orphan .face-r {
		fill: #131a22;
		stroke: rgba(110, 198, 255, 0.24);
		stroke-width: 0.6;
	}
	.kind-orphan .face-t,
	.kind-orphan .face-l,
	.kind-orphan .face-r {
		stroke-dasharray: 3 2.4;
		fill-opacity: 0.7;
	}
	.severed {
		stroke: rgba(110, 198, 255, 0.4);
		stroke-width: 1.2;
		stroke-dasharray: 2.5 3.5;
	}
	.lamp {
		filter: drop-shadow(0 0 3.5px var(--c));
	}
	.machine.awaiting {
		opacity: 0.62;
	}
	.machine.selected .face-t {
		stroke-width: 1.6;
	}

	.crate-t {
		fill: #262f1c;
		stroke: rgba(211, 167, 94, 0.4);
		stroke-width: 0.6;
	}
	.crate-l {
		fill: #12160d;
	}
	.crate-r {
		fill: #191f12;
	}
	.hands-count {
		fill: #8a827a;
		font-size: 8px;
	}

	.face-flash {
		animation: face-flash 1.6s ease-out forwards;
		pointer-events: none;
	}
	@keyframes face-flash {
		0% {
			opacity: 0;
		}
		18% {
			opacity: 0.6;
		}
		100% {
			opacity: 0;
		}
	}

	/* ── the gate ──────────────────────────────────────────────────────── */
	.gate-face-t {
		fill: #2c2010;
		stroke: rgba(217, 164, 65, 0.6);
		stroke-width: 0.8;
	}
	.gate-face-l {
		fill: #171006;
	}
	.gate-face-r {
		fill: #1f1609;
		stroke: rgba(217, 164, 65, 0.3);
		stroke-width: 0.6;
	}
	.gate-beam {
		stroke: rgba(217, 164, 65, 0.65);
		stroke-width: 1.8;
		filter: drop-shadow(0 0 3px rgba(217, 164, 65, 0.5));
	}
	.msg-core {
		fill: #d9a441;
		filter: drop-shadow(0 0 5px rgba(217, 164, 65, 0.85));
	}
	.msg-count {
		fill: #d9a441;
		font-size: 9px;
	}
	.msg.drop {
		animation: msg-drop 1.4s cubic-bezier(0.2, 0.9, 0.3, 1.15) both;
	}
	@keyframes msg-drop {
		0% {
			transform: translateY(-34px);
			opacity: 0;
		}
		55% {
			opacity: 1;
		}
		100% {
			transform: translateY(0);
		}
	}

	/* ── labels ────────────────────────────────────────────────────────── */
	.floor-label {
		fill: #a8a29e;
		font-size: 9.5px;
		letter-spacing: 0.04em;
		pointer-events: none;
	}
	.callout {
		fill: #f3e8d8;
		font-size: 11px;
		letter-spacing: 0.05em;
		pointer-events: none;
	}
	.floor-label.dim {
		fill: rgba(168, 162, 158, 0.55);
		font-size: 8px;
		letter-spacing: 0.18em;
		text-transform: uppercase;
	}
	.core-tag {
		fill: #8a827a;
		font-size: 8.5px;
	}
	.empty-note {
		font-size: 12px;
		fill: #8a827a;
		letter-spacing: 0.08em;
	}

	/* ── packets ───────────────────────────────────────────────────────── */
	.pkt {
		offset-rotate: 0deg;
		animation-name: pkt-travel;
		animation-timing-function: linear;
		animation-fill-mode: forwards;
	}
	.pkt.rev {
		animation-direction: reverse;
	}
	@keyframes pkt-travel {
		from {
			offset-distance: 0%;
		}
		to {
			offset-distance: 100%;
		}
	}
	.pkt-core {
		fill: var(--c);
	}
	.pkt-halo {
		fill: var(--c);
		opacity: 0.22;
		filter: blur(2px);
	}

	/* ── the overture: the room assembles, once ────────────────────────── */
	svg.ov .plate-edge {
		stroke-dasharray: 2400;
		stroke-dashoffset: 2400;
		animation: rim-draw 1.4s ease forwards;
	}
	svg.ov .grid {
		opacity: 0;
		animation: fade-in 0.9s ease forwards 0.5s;
	}
	svg.ov .conduit {
		opacity: 0;
		animation: fade-in 0.7s ease forwards;
		/* animation-delay set inline per conduit */
	}
	svg.ov .gate {
		opacity: 0;
		animation: fade-in 0.8s ease forwards 0.9s;
	}
	svg.ov .machine:not(.sink) {
		opacity: 0;
		animation: rise-in 0.7s cubic-bezier(0.2, 0.8, 0.25, 1) forwards;
		/* animation-delay set inline per machine (body order) */
	}
	.machine.birth {
		animation: rise-in 0.7s cubic-bezier(0.2, 0.8, 0.25, 1) both;
	}
	.machine.sink {
		animation: sink-out 1.1s ease-in forwards;
		pointer-events: none;
	}
	@keyframes rim-draw {
		to {
			stroke-dashoffset: 0;
		}
	}
	@keyframes fade-in {
		to {
			opacity: 1;
		}
	}
	@keyframes rise-in {
		0% {
			opacity: 0;
			transform: translateY(16px);
		}
		100% {
			opacity: 1;
			transform: translateY(0);
		}
	}
	@keyframes sink-out {
		0% {
			opacity: 1;
			transform: translateY(0);
		}
		100% {
			opacity: 0;
			transform: translateY(12px);
		}
	}

	.dossier {
		flex: none;
		margin: 0 12px 4px;
		padding: 10px 14px 12px;
		border: 1px solid rgba(217, 164, 65, 0.28);
		background: rgba(18, 13, 8, 0.92);
		font-size: 11px;
		color: #a8a29e;
		max-height: 38vh;
		overflow-y: auto;
		animation: dossier-in 0.28s ease-out;
	}
	@keyframes dossier-in {
		from {
			opacity: 0;
			transform: translateY(8px);
		}
		to {
			opacity: 1;
			transform: translateY(0);
		}
	}
	.dossier header {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		margin-bottom: 6px;
	}
	.dossier-name {
		color: #f3e8d8;
		font-size: 12px;
		letter-spacing: 0.06em;
	}
	.dossier-close {
		background: none;
		border: none;
		color: #8a827a;
		font-size: 16px;
		cursor: pointer;
		padding: 0 2px;
		line-height: 1;
	}
	.dossier dl {
		display: grid;
		grid-template-columns: max-content 1fr;
		gap: 3px 14px;
		margin: 0;
	}
	.dossier dt {
		color: #8a827a;
		text-transform: uppercase;
		font-size: 9px;
		letter-spacing: 0.18em;
		align-self: baseline;
		padding-top: 1px;
	}
	.dossier dd {
		margin: 0;
		overflow-wrap: anywhere;
		/* An edge detail is one recorded command, and a command can be a
		   whole heredoc — the dossier is a record card, not a scrollback.
		   Clamp the row; the full text stays in the run route. */
		display: -webkit-box;
		-webkit-box-orient: vertical;
		-webkit-line-clamp: 3;
		line-clamp: 3;
		overflow: hidden;
	}

	.hud-bottom {
		border-top: 1px solid rgba(217, 164, 65, 0.14);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
		display: block;
	}
	.hud-bottom .focus-name {
		color: #f3e8d8;
	}
	.hud-bottom .sep {
		color: rgba(138, 130, 122, 0.6);
		margin: 0 0.35em;
	}
	.hud-bottom .detail {
		color: #8a827a;
		margin-left: 0.4em;
	}
	.hud-bottom a {
		color: #d9a441;
	}

	@media (prefers-reduced-motion: reduce) {
		svg.ov .plate-edge,
		svg.ov .grid,
		svg.ov .conduit,
		svg.ov .gate,
		svg.ov .machine:not(.sink),
		.machine.birth,
		.machine.sink,
		.msg.drop,
		.face-flash {
			animation: none;
			opacity: 1;
		}
	}
</style>
