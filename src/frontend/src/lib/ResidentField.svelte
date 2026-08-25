<script lang="ts">
	// The resident field — the live body, drawn (design-resident-field.md
	// §The Shed when occupied; research-resident-as-explorable-machine
	// §5–6). Not a peer-card grid: the resident is the dominant node on its
	// room baseplate, dispatched strands hang off it as limbs on real
	// dispatch traces, one visible level deep. Everything that moves is a
	// receipt from `diffFieldEvents` — a packet is a run's own rune
	// travelling its own dispatch trace on a recorded event; a quiet tool
	// call renders a still field. Pressing any cell speaks the same
	// selection grammar every loom surface speaks (`onSelect`), answered by
	// the run node / overlay — the field never grows its own inspector.
	import { fade } from 'svelte/transition';
	import MoodChip from './MoodChip.svelte';
	import WithheldNotice from './WithheldNotice.svelte';
	import { glitchReveal, typeReveal } from './transitions';
	import {
		heartbeatLevel,
		lifecycleNotice,
		liveRunDisplayName,
		moodFace,
		roomLine,
		runCourse,
		type LiveRun
	} from './liveRuns';
	import {
		actColor,
		buildField,
		diffFieldEvents,
		edgeParts,
		fieldRunKey,
		type FieldEvent
	} from './residentField';
	import { runFacesInWindow } from './runFace';
	import { ageSince } from './runLedger';
	import { STATUS_GOOD, STATUS_WARN, STATUS_UNKNOWN, statusDotStyle } from './statusPalette';
	import type { WithheldLane } from './withheld';

	interface Props {
		runs: LiveRun[];
		stale: boolean;
		now: number;
		onSelect?: (runId: string) => void;
		selectedId?: string | null;
		withheld?: WithheldLane | null;
	}

	let { runs, stale, now, onSelect, selectedId = null, withheld = null }: Props = $props();

	let field = $derived(buildField(runs));
	let faceWindow = $derived(runFacesInWindow(runs.map((run) => fieldRunKey(run))));

	// ── receipts → motion ────────────────────────────────────────────────
	// The previous snapshot this component has already drawn. Compared, not
	// subscribed: the poll hands us state; the diff is what may move.
	let prevRuns: LiveRun[] | null = null;
	interface Packet {
		id: number;
		/** SVG path the packet rides. */
		d: string;
		glyph: string;
		color: string;
		durMs: number;
		/** Ride the path backwards (a strand's work returning home). */
		reverse: boolean;
		/** The dispatch trace this packet transits, when it rides one —
		 *  that trace brightens while the packet is on it. */
		traceKey: string | null;
	}
	let packets = $state<Packet[]>([]);
	/** Cells flashing a just-crossed boundary, keyed by run, valued by act
	 *  color — one pulse per recorded boundary, then still again. */
	let flashes = $state<Record<string, string>>({});
	let packetSeq = 0;
	let reduced = false;
	if (typeof window !== 'undefined') {
		reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
	}

	// Ceremony tempo: events are rare (a boundary every handful of seconds
	// at most, a spawn every few minutes), so the motion can afford to be
	// watched — slow, steady, glowing (maintainer, 2026-08-25, verbatim
	// brief). These are durations for *one* pulse; nothing loops.
	const SPAWN_MS = 2800;
	const RETURN_MS = 2800;
	const INJECT_MS = 3600;
	const FLASH_MS = 1600;

	// ── geometry ─────────────────────────────────────────────────────────
	// Trace paths are measured off the real cells after layout — the trunk
	// drops from the root cell's port, stubs branch to each limb. Expected
	// node count is small (research §8: "the hard problem is information
	// architecture, not GPU scale"), so re-measuring per poll is cheap.
	const GUTTER = 28; // px the limb column indents; traces live here
	const TRUNK_X = 13;

	let containerEl: HTMLDivElement | undefined = $state();
	let cellEls = $state<Record<string, HTMLElement | undefined>>({});
	interface Trace {
		key: string;
		d: string;
		pads: { x: number; y: number }[];
	}
	let traces = $state<Trace[]>([]);
	let fieldH = $state(0);
	let fieldW = $state(0);

	function tracePath(rootKey: string, limbKey: string): Trace | null {
		const box = containerEl?.getBoundingClientRect();
		const rootRect = cellEls[rootKey]?.getBoundingClientRect();
		const limbRect = cellEls[limbKey]?.getBoundingClientRect();
		if (!box || !rootRect || !limbRect) return null;
		const x = TRUNK_X;
		const y0 = rootRect.bottom - box.top;
		const yStub = limbRect.top - box.top + limbRect.height / 2;
		const xEnd = limbRect.left - box.left;
		// A machined route, not a bare hairline: rounded elbow at the bend,
		// square port pads where the trace leaves the dispatcher and enters
		// the limb. Both ends bite 3px *into* their cell — a trace that
		// stops at the border reads as almost-touching, never as connected
		// (maintainer, live read: "the line is not connected to the card
		// firmly").
		const y0i = y0 - 3;
		const xEndI = xEnd + 3;
		const r = Math.min(8, Math.max(0, yStub - y0i - 2), Math.max(0, xEndI - x - 2));
		const d =
			r > 1
				? `M ${x} ${y0i} V ${yStub - r} Q ${x} ${yStub} ${x + r} ${yStub} H ${xEndI}`
				: `M ${x} ${y0i} V ${yStub} H ${xEndI}`;
		return {
			key: limbKey,
			d,
			pads: [
				{ x, y: y0i },
				{ x: xEndI, y: yStub }
			]
		};
	}

	function measure() {
		const box = containerEl?.getBoundingClientRect();
		if (!box) return;
		fieldH = box.height;
		fieldW = box.width;
		const next: Trace[] = [];
		for (const root of field) {
			const rootKey = fieldRunKey(root.run);
			for (const limb of root.limbs) {
				const trace = tracePath(rootKey, fieldRunKey(limb.run));
				if (trace) next.push(trace);
			}
		}
		traces = next;
	}

	$effect(() => {
		void field; // re-measure whenever the topology re-derives
		const raf = requestAnimationFrame(measure);
		return () => cancelAnimationFrame(raf);
	});
	$effect(() => {
		if (typeof window === 'undefined') return;
		const onResize = () => measure();
		window.addEventListener('resize', onResize);
		return () => window.removeEventListener('resize', onResize);
	});

	/** The portal drop: correspondence arriving from the world falls from
	 *  the field's top edge into the target cell — the message being put
	 *  before the runner, at the boundary that attested it. */
	function portalPath(key: string): string | null {
		const box = containerEl?.getBoundingClientRect();
		const rect = cellEls[key]?.getBoundingClientRect();
		if (!box || !rect) return null;
		const x = rect.right - box.left - 18;
		const y = rect.top - box.top + Math.min(14, rect.height / 2);
		return `M ${x} 0 V ${y}`;
	}

	function launch(event: FieldEvent) {
		const key = event.runId;
		if (event.kind === 'boundary' || event.kind === 'inject') {
			const run = runs.find((r) => fieldRunKey(r) === key);
			const color = actColor(run?.edge?.act);
			flashes = { ...flashes, [key]: color };
			setTimeout(() => {
				const rest = { ...flashes };
				delete rest[key];
				flashes = rest;
			}, FLASH_MS);
		}
		if (reduced) return;
		// Packet glyphs are geometric on purpose — the rune vocabulary
		// belongs to *topics* (the heddles), and a run wearing one here
		// collided with it (maintainer, 2026-08-25, live read).
		let d: string | null = null;
		let traceKey: string | null = null;
		let glyph = '◇';
		let color = '#e8b34a';
		let durMs = SPAWN_MS;
		let reverse = false;
		if (event.kind === 'spawn') {
			d = traces.find((t) => t.key === key)?.d ?? null;
			traceKey = key;
			glyph = '◇';
			color = faceWindow.get(key)?.color ?? color;
		} else if (event.kind === 'return') {
			// The departed strand's trace is gone with it; the receipt rides
			// home along its parent's trunk region — measured before the DOM
			// forgets, falling back to the parent cell's own port.
			const parentKey = event.parentId;
			d = traces.find((t) => t.key === key)?.d ?? null;
			traceKey = key;
			if (!d && parentKey) {
				const box = containerEl?.getBoundingClientRect();
				const rect = cellEls[parentKey]?.getBoundingClientRect();
				if (box && rect)
					d = `M ${TRUNK_X} ${rect.bottom - box.top + 24} V ${rect.bottom - box.top}`;
			}
			glyph = '◆';
			color = '#a8cbdb';
			durMs = RETURN_MS;
			reverse = true;
		} else if (event.kind === 'inject' || event.kind === 'message') {
			d = portalPath(key);
			glyph = '◈';
			color = '#a8cbdb';
			durMs = INJECT_MS;
		}
		if (!d) return;
		const id = ++packetSeq;
		packets = [...packets, { id, d, glyph, color, durMs, reverse, traceKey }];
		setTimeout(() => {
			packets = packets.filter((p) => p.id !== id);
		}, durMs + 400);
	}

	$effect(() => {
		const next = runs;
		const events = diffFieldEvents(prevRuns, next);
		prevRuns = next;
		if (events.length === 0) return;
		// Measure first so a spawn's fresh trace exists before its packet
		// launches; receipts stagger slightly so simultaneous events read
		// as a sequence, not a burst.
		requestAnimationFrame(() => {
			measure();
			events.forEach((event, i) => setTimeout(() => launch(event), i * 320));
		});
	});

	function level(run: LiveRun) {
		return heartbeatLevel(run.last_seen, now, stale);
	}
	const LEVEL_COLOR = { running: STATUS_GOOD, stalling: STATUS_WARN, unknown: STATUS_UNKNOWN };
	function statusWord(run: LiveRun): string {
		const lvl = level(run);
		if (lvl === 'running') {
			const notice = lifecycleNotice(run);
			if (notice) return notice.word;
			if (run.phase) return run.phase;
			return 'running';
		}
		return lvl;
	}
	function statusColor(run: LiveRun): string {
		const lvl = level(run);
		if (lvl === 'running') {
			const tone = lifecycleNotice(run)?.tone;
			if (tone === 'awaiting') return STATUS_WARN;
			if (tone === 'starting') return STATUS_UNKNOWN;
		}
		return LEVEL_COLOR[lvl];
	}
	function isAwaiting(run: LiveRun): boolean {
		return level(run) === 'running' && lifecycleNotice(run)?.tone === 'awaiting';
	}
	function runnerLabel(run: LiveRun): string | null {
		const bits = [run.runner?.shell, run.runner?.core].filter(Boolean);
		return bits.length ? bits.join(' · ') : null;
	}
	function press(run: LiveRun) {
		onSelect?.(fieldRunKey(run));
	}
	function cellRef(el: HTMLElement, key: string) {
		cellEls[key] = el;
		return {
			update(next: string) {
				if (next !== key) {
					delete cellEls[key];
					key = next;
					cellEls[key] = el;
				}
			},
			destroy() {
				delete cellEls[key];
			}
		};
	}
</script>

<div class="panel relative p-3" bind:this={containerEl} data-resident-field>
	{#if stale}
		<div class="mb-2 flex justify-end">
			<span
				class="border border-sky-900/60 bg-sky-950/40 px-1.5 py-0.5 font-mono text-[10px] tracking-wide text-sky-300 uppercase"
				>stale report</span
			>
		</div>
	{/if}

	{#if runs.length === 0}
		{#if withheld}
			<WithheldNotice {withheld} />
		{:else}
			<p class="text-sm text-ink-quiet">Nothing awake right now.</p>
		{/if}
	{:else}
		<!-- The trace layer: dispatch edges + travelling receipts. Drawn over
		     the cells (packets cross rooms on purpose — signal over chrome)
		     but never interactive. -->
		<svg
			class="pointer-events-none absolute inset-0 z-20"
			width={fieldW}
			height={fieldH}
			viewBox={`0 0 ${fieldW || 1} ${fieldH || 1}`}
			aria-hidden="true"
		>
			{#each traces as trace (trace.key)}
				{@const live = packets.some((p) => p.traceKey === trace.key)}
				<path
					d={trace.d}
					fill="none"
					stroke-width="1"
					pathLength="1"
					class="field-trace"
					class:field-trace--live={live}
				/>
				{#each trace.pads as pad, i (i)}
					<rect
						x={pad.x - 2}
						y={pad.y - 2}
						width="4"
						height="4"
						class="field-pad"
						class:field-pad--live={live}
						in:fade={{ duration: 900 }}
					/>
				{/each}
			{/each}
			{#each packets as packet (packet.id)}
				<!-- A packet is a comet, not a floating glyph: a centered core
				     with two trail dots lagging the same path (the trail's
				     `begin` offsets it behind the head), so the signal reads as
				     data *flowing*, and the head sits ON the trace — a <text>
				     node's default origin is its baseline corner, which is the
				     "very offset romboid" the live read caught. -->
				<g class="field-packet" style={`--packet-glow: ${packet.color}`}>
					<circle r="4.5" fill={packet.color} opacity="0.16">
						<animateMotion
							dur={`${packet.durMs}ms`}
							path={packet.d}
							rotate="0"
							fill="freeze"
							keyPoints={packet.reverse ? '1;0' : '0;1'}
							keyTimes="0;1"
							calcMode="spline"
							keySplines="0.35 0 0.25 1"
						/>
					</circle>
					<text font-size="9" fill={packet.color} text-anchor="middle" dominant-baseline="central">
						{packet.glyph}
						<animateMotion
							dur={`${packet.durMs}ms`}
							path={packet.d}
							rotate="0"
							fill="freeze"
							keyPoints={packet.reverse ? '1;0' : '0;1'}
							keyTimes="0;1"
							calcMode="spline"
							keySplines="0.35 0 0.25 1"
						/>
					</text>
					{#each [0.12, 0.24] as lag, i (lag)}
						<!-- Hidden until its own ride begins — an unstarted SMIL
						     element would otherwise sit visible at the SVG origin. -->
						<circle r={2 - i * 0.7} fill={packet.color} opacity="0">
							<set attributeName="opacity" to={0.5 - i * 0.22} begin={`${lag}s`} fill="freeze" />
							<animateMotion
								dur={`${packet.durMs}ms`}
								path={packet.d}
								rotate="0"
								fill="freeze"
								begin={`${lag}s`}
								keyPoints={packet.reverse ? '1;0' : '0;1'}
								keyTimes="0;1"
								calcMode="spline"
								keySplines="0.35 0 0.25 1"
							/>
						</circle>
					{/each}
				</g>
			{/each}
		</svg>

		<div class="relative z-10 space-y-2">
			{#each field as root (fieldRunKey(root.run))}
				{@const run = root.run}
				{@const key = fieldRunKey(run)}
				{@const face = faceWindow.get(key)}
				{@const mood = moodFace(
					run.mood,
					run.mood_glyph,
					run.mood_pitch,
					run.mood_frames,
					run.mood_rest
				)}
				{@const room = roomLine(run.room)}
				{@const edge = edgeParts(run.edge)}
				{@const course = runCourse(run.card_text)}
				{@const color = statusColor(run)}
				<!-- The root cell: the thought's face on its room's plinth. The
				     run's hash hue survives as the port-edge accent only — the
				     rune glyphs belong to topics now (heddles), never to run
				     identity. -->
				<button
					type="button"
					use:cellRef={key}
					class="subpanel field-cell block w-full cursor-pointer p-2.5 text-left text-xs transition-[box-shadow] duration-700"
					class:field-selected={selectedId === key}
					style={`border-left: 2px solid ${face ? `color-mix(in srgb, ${face.color} 55%, #d9a441)` : 'rgba(217,164,65,0.4)'};${
						flashes[key]
							? ` box-shadow: 0 0 14px -2px ${flashes[key]}, inset 0 0 10px -6px ${flashes[key]};`
							: ''
					}`}
					onclick={() => press(run)}
					in:glitchReveal={{ duration: 320 }}
					data-field-cell={key}
				>
					<div class="flex items-center justify-between gap-2">
						<span class="flex min-w-0 items-center gap-1.5">
							<span
								class="inline-block h-2 w-2 shrink-0 rounded-full"
								style={statusDotStyle(level(run) === 'stalling' ? 'cooling' : 'burning', color)}
								aria-hidden="true"
							></span>
							<span
								class="truncate font-mono text-[10px] font-medium tracking-wide uppercase"
								style={`color: ${color}`}
								use:typeReveal={{ text: statusWord(run) }}>{statusWord(run)}</span
							>
							{#if run.is_subspawn}
								<span
									class="shrink-0 border border-amber-900/60 bg-amber-950/40 px-1 py-0.5 font-mono text-[9px] tracking-wide text-amber-300 uppercase"
									>↳ strand</span
								>
							{/if}
						</span>
						<span class="flex shrink-0 items-center gap-1.5 font-mono text-[10px] text-ink-quiet">
							{#if (run.portals?.pending ?? 0) > 0}
								<!-- Correspondence resting at the door — put to read, not
								     yet folded in. Breathes until a boundary attests the
								     read; the ◈ drop above is its arrival. -->
								<span
									class="field-waiting text-sky-300"
									title="correspondence waiting — put to read"
									in:fade={{ delay: 2800, duration: 900 }}
									out:fade={{ duration: 500 }}>◈ {run.portals?.pending}</span
								>
							{/if}
							{ageSince(run.started_at, now) ?? ''}
							<span class="text-[9px] text-ink-mute">▸</span>
						</span>
					</div>
					<div class="mt-1 flex min-w-0 items-center gap-2">
						<span
							class="truncate text-base font-medium tracking-tight text-amber-100"
							use:typeReveal={{ text: liveRunDisplayName(run) }}>{liveRunDisplayName(run)}</span
						>
						<MoodChip face={mood} seed={key} variant="stage" class="ml-auto shrink-0" />
					</div>
					{#if runnerLabel(run)}
						<p class="font-mono text-[10px] text-stone-400">{runnerLabel(run)}</p>
					{/if}
					{#if edge}
						{#key run.edge?.at}
							<p
								class="mt-1 truncate font-mono text-[10px] text-stone-300"
								title={edge.detail ?? undefined}
								in:fade={{ duration: 700 }}
							>
								<span style={`color: ${edge.color}`}>⌁ {edge.act ?? '?'}</span>
								{#if edge.detail}
									· <span use:typeReveal={{ text: edge.detail, duration: 2400 }}>{edge.detail}</span
									>
								{/if}
								{#if run.edge?.dir && run.edge.dir !== '.'}
									<span class="text-ink-mute"> · {run.edge.dir}/</span>
								{/if}
								{#if run.edge?.out_bytes != null}
									<span class="text-ink-mute"> · {run.edge.out_bytes} B</span>
								{/if}
								{#if run.edge?.injected}
									<span class="text-sky-300"> ⇣</span>
								{/if}
							</p>
						{/key}
					{/if}
					<!-- The baseplate: the room this thought occupies — the face is
					     the thought; the plate is the room (research §5). -->
					{#if room || course}
						<div
							class="mt-1.5 flex items-center justify-between gap-2 border-t border-stone-800/70 pt-1 font-mono text-[10px]"
						>
							{#if room}
								<span class="truncate text-stone-400" title={room}>⌂ {room}</span>
							{/if}
							{#if course}
								<span class="shrink-0 text-ink-quiet">course {course.done}/{course.total}</span>
							{/if}
						</div>
					{/if}
					<div class="mt-1.5 h-1 overflow-hidden bg-stone-900" aria-hidden="true">
						<div
							class={`h-full ${
								level(run) !== 'running'
									? 'w-full'
									: isAwaiting(run)
										? 'w-1/3 animate-[loom-scan_6s_ease-in-out_infinite]'
										: 'w-1/3 animate-[loom-scan_1.4s_ease-in-out_infinite]'
							}`}
							style={`background-color: ${color}; opacity: ${
								level(run) === 'running' ? (isAwaiting(run) ? 0.6 : 1) : 0.3
							}`}
						></div>
					</div>
				</button>

				{#if root.limbs.length > 0}
					<div class="space-y-1.5" style={`padding-left: ${GUTTER}px`}>
						{#each root.limbs as limb (fieldRunKey(limb.run))}
							{@const lrun = limb.run}
							{@const lkey = fieldRunKey(lrun)}
							{@const lface = faceWindow.get(lkey)}
							{@const ledge = edgeParts(lrun.edge)}
							{@const lcolor = statusColor(lrun)}
							{@const lroom = roomLine(lrun.room)}
							<button
								type="button"
								use:cellRef={lkey}
								class="subpanel field-cell block w-full cursor-pointer p-2 text-left text-xs transition-[box-shadow] duration-700"
								class:field-selected={selectedId === lkey}
								style={`border-left: 2px solid ${lface ? `color-mix(in srgb, ${lface.color} 55%, #d9a441)` : 'rgba(217,164,65,0.3)'};${
									flashes[lkey]
										? ` box-shadow: 0 0 12px -2px ${flashes[lkey]}, inset 0 0 8px -6px ${flashes[lkey]};`
										: ''
								}`}
								onclick={() => press(lrun)}
								in:glitchReveal={{ duration: 320 }}
								data-field-cell={lkey}
							>
								<div class="flex items-center justify-between gap-2">
									<span class="flex min-w-0 items-center gap-1.5">
										<span
											class="inline-block h-1.5 w-1.5 shrink-0 rounded-full"
											style={statusDotStyle(
												level(lrun) === 'stalling' ? 'cooling' : 'burning',
												lcolor
											)}
											aria-hidden="true"
										></span>
										<span
											class="truncate font-medium text-amber-100/90"
											use:typeReveal={{ text: liveRunDisplayName(lrun) }}
											>{liveRunDisplayName(lrun)}</span
										>
										{#if limb.hands > 0}
											<span
												class="shrink-0 font-mono text-[9px] text-ink-mute"
												title="collapsed deeper strands — inspect on the run route"
												>+{limb.hands} hands</span
											>
										{/if}
									</span>
									<span
										class="flex shrink-0 items-center gap-1.5 font-mono text-[10px] text-ink-quiet"
									>
										{#if (lrun.portals?.pending ?? 0) > 0}
											<span
												class="field-waiting text-sky-300"
												title="correspondence waiting — put to read"
												in:fade={{ delay: 2800, duration: 900 }}
												out:fade={{ duration: 500 }}>◈ {lrun.portals?.pending}</span
											>
										{/if}
										{ageSince(lrun.started_at, now) ?? ''}
									</span>
								</div>
								{#if runnerLabel(lrun)}
									<p class="font-mono text-[9px] text-ink-mute">{runnerLabel(lrun)}</p>
								{/if}
								{#if ledge}
									{#key lrun.edge?.at}
										<p
											class="mt-0.5 truncate font-mono text-[10px] text-stone-300"
											title={ledge.detail ?? undefined}
											in:fade={{ duration: 700 }}
										>
											<span style={`color: ${ledge.color}`}>⌁ {ledge.act ?? '?'}</span>
											{#if ledge.detail}
												· <span use:typeReveal={{ text: ledge.detail, duration: 2400 }}
													>{ledge.detail}</span
												>
											{/if}
											{#if lrun.edge?.dir && lrun.edge.dir !== '.'}
												<span class="text-ink-mute"> · {lrun.edge.dir}/</span>
											{/if}
											{#if lrun.edge?.out_bytes != null}
												<span class="text-ink-mute"> · {lrun.edge.out_bytes} B</span>
											{/if}
										</p>
									{/key}
								{/if}
								{#if lroom}
									<p class="truncate font-mono text-[9px] text-ink-mute" title={lroom}>
										⌂ {lroom}
									</p>
								{/if}
								<div class="mt-1 h-0.5 overflow-hidden bg-stone-900" aria-hidden="true">
									<div
										class={`h-full ${
											level(lrun) !== 'running'
												? 'w-full'
												: isAwaiting(lrun)
													? 'w-1/3 animate-[loom-scan_6s_ease-in-out_infinite]'
													: 'w-1/3 animate-[loom-scan_1.4s_ease-in-out_infinite]'
										}`}
										style={`background-color: ${lcolor}; opacity: ${
											level(lrun) === 'running' ? (isAwaiting(lrun) ? 0.6 : 1) : 0.3
										}`}
									></div>
								</div>
							</button>
						{/each}
					</div>
				{/if}
			{/each}
		</div>
	{/if}
</div>

<style>
	/* A dispatch trace draws itself in once (state birth), then holds as a
	   faint machined line; it brightens only while a packet transits it. */
	.field-trace {
		stroke: rgba(217, 164, 65, 0.3);
		stroke-dasharray: 1;
		stroke-dashoffset: 1;
		animation: field-trace-draw 1.6s ease-out forwards;
		filter: drop-shadow(0 0 2px rgba(217, 164, 65, 0.3));
		transition: stroke 600ms ease;
	}
	.field-trace--live {
		stroke: rgba(232, 179, 74, 0.85);
		filter: drop-shadow(0 0 3px rgba(232, 179, 74, 0.6))
			drop-shadow(0 0 7px rgba(232, 179, 74, 0.4));
	}
	@keyframes field-trace-draw {
		to {
			stroke-dashoffset: 0;
		}
	}
	.field-pad {
		fill: rgba(217, 164, 65, 0.55);
		transition: fill 600ms ease;
	}
	.field-pad--live {
		fill: #e8b34a;
		filter: drop-shadow(0 0 3px rgba(232, 179, 74, 0.8));
	}
	.field-packet {
		filter: drop-shadow(0 0 4px var(--packet-glow, #e8b34a))
			drop-shadow(0 0 9px var(--packet-glow, #e8b34a));
	}
	.field-selected {
		filter: brightness(1.35);
	}
	/* The plinth: the face is the thought, the plate is the room it stands
	   on — a shallow axonometric slab behind each cell, text kept flat
	   (research §5/§8: depth from the baseplate, never from tilting type). */
	.field-cell {
		position: relative;
		isolation: isolate;
		background:
			linear-gradient(165deg, rgba(243, 232, 216, 0.03), transparent 40%), rgba(12, 9, 6, 0.55);
		/* One key light from above: a machined top edge catching it, the
		   plinth falling away beneath. Coherent light reads as expensive;
		   more glow reads as cheap. */
		box-shadow: inset 0 1px 0 rgba(243, 232, 216, 0.07);
	}
	.field-cell::after {
		content: '';
		position: absolute;
		z-index: -1;
		left: 10px;
		right: -6px;
		top: 10px;
		bottom: -7px;
		border: 1px solid rgba(217, 164, 65, 0.14);
		background: linear-gradient(160deg, rgba(217, 164, 65, 0.05), rgba(4, 3, 2, 0.6));
		transform: skewX(-14deg);
		transform-origin: 100% 100%;
		pointer-events: none;
	}
	/* Correspondence resting at the door: a slow breath, standing signal —
	   this is a live unread message, not decoration; it ends the moment
	   the read is attested and the count drops. */
	.field-waiting {
		animation: field-waiting-breathe 5s ease-in-out infinite;
	}
	@keyframes field-waiting-breathe {
		0%,
		100% {
			opacity: 0.45;
		}
		50% {
			opacity: 1;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.field-trace {
			animation: none;
			stroke-dashoffset: 0;
		}
		.field-waiting {
			animation: none;
		}
	}
</style>
