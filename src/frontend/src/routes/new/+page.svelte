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
		runCourse,
		type LiveRun
	} from '$lib/liveRuns';
	import { actColor, buildField, diffFieldEvents, fieldRunKey } from '$lib/residentField';
	import {
		boxFaces,
		buildScene,
		floorPath,
		iso,
		paintOrder,
		polyPoints,
		sceneBounds,
		type Machine
	} from '$lib/isoField';
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
	let focusCourse = $derived(runCourse(focusRun?.card_text));

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
		if (reduced || events.length === 0) return;

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
				pushPacket(
					nextScene.gatePath.length ? floorPath(nextScene.gatePath) : null,
					actColor('orient'),
					INJECT_MS
				);
			} else if (ev.kind === 'message') {
				msgDropSeq += 1;
			}
		}
	}

	// ── feed: live poll, or the demo replay ───────────────────────────────
	onMount(() => {
		demo = new URLSearchParams(window.location.search).has('demo');
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

	/** Deterministic per-window brightness — inhabited, never twinkling. */
	function windowAlpha(i: number): number {
		return 0.28 + ((i * 7) % 5) * 0.13;
	}

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
		return overture ? `${1500 + m.order * 170}ms` : '0ms';
	}
	function conduitDelay(i: number): string {
		return overture ? `${1150 + i * 130}ms` : '0ms';
	}

	function pressFloor() {
		selected = null;
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
		<span class="state">
			{#if loading}connecting…{:else if signedOut}signed out{:else if demo}replay{:else if stale}stale
				<i class="dot warn"></i>{:else}live <i class="dot live"></i>{/if}
		</span>
	</header>

	<div class="stage" class:is-stale={stale && !demo} onpointerdown={pressFloor} role="presentation">
		<svg
			viewBox={`${bounds.x} ${bounds.y} ${bounds.w} ${bounds.h}`}
			class:ov={overture}
			aria-label="the live system, drawn as a room"
		>
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
				{#if scene.gatePath.length}
					{@const gp = iso(scene.gate.x, scene.gate.y + 0.9)}
					<text x={gp.x} y={gp.y + 16} text-anchor="middle" class="floor-label dim">portal</text>
				{/if}
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
			{#each packets as packet (packet.id)}
				<g class="pkt" style={`--c:${packet.color}`}>
					<animateMotion
						dur={`${packet.dur}ms`}
						path={packet.d}
						keyPoints={packet.reverse ? '1;0' : '0;1'}
						keyTimes="0;1"
						calcMode="linear"
						fill="freeze"
					/>
					<polygon points="0,-4.4 8.8,0 0,4.4 -8.8,0" class="pkt-halo" />
					<polygon points="0,-2.4 4.8,0 0,2.4 -4.8,0" class="pkt-core" />
				</g>
			{/each}

			<!-- the machines, back to front -->
			{#each ordered as m (m.key)}
				{@const f = boxFaces(m.x, m.y, m.w, m.d, m.h)}
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
						<polygon
							points={polyPoints([
								iso(m.x - 0.3, m.y - 0.3),
								iso(m.x + m.w + 0.3, m.y - 0.3),
								iso(m.x + m.w + 0.3, m.y + m.d + 0.3),
								iso(m.x - 0.3, m.y + m.d + 0.3)
							])}
							class="plinth"
						/>
					{/if}
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
					{#if m.kind === 'resident'}
						<!-- lit slits: the tower is inhabited -->
						{#each Array.from({ length: 8 }, (_, i) => i) as i (i)}
							{@const wx = m.x + m.w + 0.001}
							{@const col = i % 2}
							{@const row = (i - col) / 2}
							{@const p = iso(wx, m.y + m.d * (0.45 + col * 0.3), m.h - 0.42 - row * 0.4)}
							<rect
								x={p.x - 4}
								y={p.y}
								width="5"
								height="2.2"
								class="window"
								style={`opacity:${windowAlpha(i)}`}
							/>
						{/each}
						{@const mastBase = iso(m.x + m.w * 0.3, m.y + m.d * 0.3, m.h)}
						<line
							x1={mastBase.x}
							y1={mastBase.y}
							x2={mastBase.x}
							y2={mastBase.y - 24}
							class="mast"
						/>
						<circle
							cx={mastBase.x}
							cy={mastBase.y - 27}
							r="2.6"
							class="beacon"
							style={`fill:${lamp};--c:${lamp}`}
						/>
						<!-- the callout: name at the mast, in the void the tower owns -->
						<text x={mastBase.x - 9} y={mastBase.y - 24} text-anchor="end" class="callout">
							{trunc(liveRunDisplayName(run), 26)}<tspan class="core-tag"
								>{run.runner?.core ? ` · ${run.runner.core}` : ''}</tspan
							>
						</text>
					{:else}
						<circle
							cx={f.frontCorner.x}
							cy={f.frontCorner.y - 3}
							r="2.2"
							class="lamp"
							style={`fill:${lamp};--c:${lamp}`}
						/>
					{/if}
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
					{#if m.kind !== 'resident'}
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
					{/if}
				</g>
			{/each}

			<!-- returning machines sinking into the floor -->
			{#each dying as d (d.id)}
				{@const f = boxFaces(d.machine.x, d.machine.y, d.machine.w, d.machine.d, d.machine.h)}
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
			radial-gradient(60% 45% at 32% 30%, rgba(217, 164, 65, 0.055), transparent 70%), #0c0906;
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
		padding: 4px 8px;
	}
	.stage.is-stale {
		filter: grayscale(0.35) brightness(0.82);
	}
	svg {
		width: 100%;
		height: 100%;
		max-height: 100%;
		overflow: visible;
	}

	/* ── the plate ─────────────────────────────────────────────────────── */
	.plate-top {
		fill: #120d08;
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
		stroke: rgba(217, 164, 65, 0.07);
		stroke-width: 0.7;
	}
	.grid-line.major {
		stroke: rgba(217, 164, 65, 0.13);
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
	.plinth {
		fill: rgba(217, 164, 65, 0.06);
		stroke: rgba(217, 164, 65, 0.3);
		stroke-width: 0.8;
	}
	.window {
		fill: #f3e8d8;
	}
	.mast {
		stroke: rgba(255, 205, 110, 0.6);
		stroke-width: 1;
	}
	.beacon {
		filter: drop-shadow(0 0 4px var(--c));
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
