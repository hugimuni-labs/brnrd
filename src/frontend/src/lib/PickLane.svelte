<script lang="ts">
	import { glitchReveal } from './transitions';
	import { moodFace, type DaemonMood, type LiveRun } from './liveRuns';
	import type { ScheduledWake } from './scheduledWakes';
	import type { WeavingRow } from './warpGraph';
	import { armedOverflow, pickRows, PICKING_ROW_CAP, type PickRow } from './pickLane';
	import { runFacesInWindow, type RunFace } from './runFace';
	import Crossing from './Crossing.svelte';
	import { crossingCells } from './crossing';
	import { statusDotStyle, glowFor, STATUS_BURNING } from './statusPalette';

	// THE PICK — one object, one place, moving (his 2026-08-02 steer: "really
	// turn the UI around pushing a run object through the stages of the
	// execution"). This one lane replaces two components and a list: the future
	// shelf, the NOW seam's 128px three-column instrument, and the
	// `from the warp · weaving` rows. All three were separate drawings of the
	// same objects at different phases, stacked — which is exactly what made the
	// future read as "out of place" and the now read as a repeat.
	//
	// One row grammar, two heats. The row's *position* is its progress: armed
	// picks fall from the top (furthest first) toward the seam rule at the
	// bottom, and a run burning now sits against it. `pickLane.ts` owns the
	// ordering and the arithmetic; this file only draws.

	interface Props {
		liveRuns: LiveRun[] | null;
		scheduledWakes: ScheduledWake[] | null;
		/** The `taken:`-live join the page already computes for the warp. Folded
		 *  onto the picking row as chips — the weld, drawn on the object rather
		 *  than in a second list. */
		weaving?: WeavingRow[];
		now: number;
		/** Selection is the page's: the lane reports, the unfold below answers. */
		onSelect?: (kind: 'run' | 'wake', id: string) => void;
		selectedId?: string | null;
		/** The daemon's resting face (#566), for the idle lane. `null` on a
		 *  pre-upgrade daemon that publishes no mood, and then the idle line
		 *  renders its hollow dot rather than inventing a face. */
		daemonMood?: DaemonMood | null;
		/** The warp threads in authored order, and run id → threads crossed
		 *  (`crossing.ts`). Same alphabet everywhere it is drawn — same threads,
		 *  same cells, same width — so a strip here and a strip on the cloth line
		 *  this pick becomes are legibly one statement. Armed rows read their
		 *  threads from the schedule entry's `serves:` row instead of this index;
		 *  see `PickRow.crosses`. */
		threads?: string[];
		crossingIndex?: Map<string, string[]>;
		/** The set-probed topic faces (`warpGraph.topicFaces`). */
		topicFaces?: Map<string, RunFace>;
		/** Canonical topic ids lit on the heddle rail; null = all. The whole
		 *  dashboard filters by the lit set (his 2026-08-11 steer) — hidden
		 *  picks are counted in an honest line, never silently vanished. */
		selectedTopics?: ReadonlySet<string> | null;
	}

	let {
		liveRuns,
		scheduledWakes,
		weaving = [],
		now,
		onSelect,
		selectedId = null,
		daemonMood = null,
		threads = [],
		crossingIndex = new Map(),
		topicFaces = new Map<string, RunFace>(),
		selectedTopics = null
	}: Props = $props();

	let allRows = $derived(pickRows({ liveRuns, scheduledWakes, weaving, now }));
	// The heddle lens over the lane: a row's topics come from the taken:
	// index (picking) or its own serves: claim (armed). A row making no
	// topic claim is honestly "not this topic" under a real filter — and
	// the hidden count renders below, so a burning run never just vanishes.
	function rowTopics(row: PickRow): string[] {
		return crossingIndex.get(row.id) ?? row.crosses;
	}
	let rows = $derived(
		selectedTopics === null
			? allRows
			: allRows.filter((row) => rowTopics(row).some((id) => selectedTopics.has(id)))
	);
	let hiddenByTopics = $derived(allRows.length - rows.length);
	let overflow = $derived(armedOverflow(scheduledWakes, now));
	let picking = $derived(rows.filter((row) => row.phase === 'picking'));
	let armed = $derived(rows.filter((row) => row.phase === 'armed'));
	let shownPicking = $derived(picking.slice(0, PICKING_ROW_CAP));
	// THE FACE IN THREE TENSES, piece 2: display-time collision re-roll, over
	// exactly the rows this lane draws (`shownPicking`, not `picking` — the
	// folded-past-the-cap rows never render, so they shouldn't spend a probe
	// slot). Armed rows carry no face at all (see the comment at the render
	// site below), so the window is picking-only.
	let faceWindow = $derived(runFacesInWindow(shownPicking.map((row) => row.id)));
	let restingFace = $derived(daemonMood ? moodFace(daemonMood.name, daemonMood.glyph) : null);
	let clockLabel = $derived(
		new Date(now).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
	);

	function select(row: PickRow) {
		onSelect?.(row.kind, row.id);
	}
</script>

<div class="panel px-3 py-2" aria-label="the pick lane">
	{#if liveRuns === null && scheduledWakes === null}
		<p class="font-mono text-[9px] text-ink-mute">acquiring</p>
	{:else}
		{#if picking.length === 0}
			<!-- Idle stays almost nothing (his 08-02 steer, held): presence, and
			     the fact that nothing is weaving. The next fire is already the
			     first armed row directly below this rule, so the old "next in …"
			     whisper would be the third drawing of a thing now drawn once. -->
			<div class="flex items-baseline gap-2">
				{#if restingFace}
					{#if restingFace.glyph}
						<span class="font-mono text-[11px] text-amber-200/80" aria-hidden="true"
							>{restingFace.glyph}</span
						>
					{/if}
					<span class="font-mono text-[8px] text-ink-quiet">{restingFace.name}</span>
				{:else}
					<span
						class="h-2 w-2 self-center rounded-full border border-stone-600 bg-stone-950"
						aria-hidden="true"
					></span>
				{/if}
				<span class="ml-auto font-mono text-[9px] text-ink-mute">nothing weaving</span>
			</div>
		{:else}
			<div class="flex flex-col gap-1">
				{#each shownPicking as row, index (row.id)}
					<!-- The face, only on this heat: `row.id` is a real run id here,
					     while an armed row's is a schedule entry's — a wake is not a
					     run, and a mark drawn on one would claim an identity that has
					     no other surface to travel to. -->
					{@const face = faceWindow.get(row.id)}
					<!-- Picking: the same object, lit. `|global` (#970) because the
					     0→1 case creates this whole branch, and a local intro inside
					     a freshly-born each block never fires — the most common
					     ignition was the one that didn't play. -->
					<button
						type="button"
						class="w-full cursor-pointer border bg-stone-950/90 px-1.5 py-1 text-left font-mono leading-tight text-amber-100 {selectedId ===
						row.id
							? 'border-amber-400/80 brightness-125'
							: 'border-amber-700/50'}"
						style={glowFor(row.urgency, STATUS_BURNING)}
						title={row.label}
						aria-expanded={selectedId === row.id}
						onclick={() => select(row)}
						in:glitchReveal|global={{ duration: 260, delay: 35 + index * 38 }}
					>
						<span class="flex min-w-0 items-baseline gap-1.5">
							<!-- Same 8px lead slot the armed row's dot occupies, so the
							     crossing strip after it starts at the same x whatever the
							     row's heat. Alignment across the *cloth* is not claimed:
							     that row wraps, so what travels between the two surfaces is
							     the strip's alphabet — same threads, same order, same
							     width — not its position. -->
							<span
								class="inline-block w-2 shrink-0 text-center text-amber-300/80"
								aria-hidden="true">↯</span
							>
							<Crossing cells={crossingCells(threads, crossingIndex.get(row.id), topicFaces)} />
							{#if face}
								<span class="shrink-0 text-[9px]" aria-hidden="true" style={`color: ${face.color}`}
									>{face.glyph}</span
								>
							{/if}
							<span class="min-w-0 flex-1 truncate text-[9px]">{row.label}</span>
							{#if row.clock || row.note}
								<span class="shrink-0 text-[8px] text-amber-500/80">{row.note ?? row.clock}</span>
							{/if}
						</span>
						{#if row.serves.length > 0}
							<!-- The weld, on the object: which warp items this pick lifted.
							     This is what retires the `from the warp · weaving` list —
							     the same fact, carried by the thing it is a fact about. -->
							<span class="mt-0.5 flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5">
								{#each row.serves as item (item.callSign + item.headline)}
									<span class="truncate text-[8px] text-amber-400/70">
										⟶ {item.headline}
										<span class="text-ink-quiet">{item.callSign}</span>
									</span>
								{/each}
							</span>
						{/if}
					</button>
				{/each}
				{#if picking.length > shownPicking.length}
					<span class="text-center font-mono text-[8px] text-amber-500/70"
						>+{picking.length - shownPicking.length} more picking</span
					>
				{/if}
			</div>
		{/if}

		<!-- The seam. What used to be a 128px three-column theater with two
		     decorative hairlines is one rule: the line a pick crosses when it
		     starts. Above it, something is burning; below it, the queue waiting
		     to. (His 08-02 read — "the live run should appear on top beneath the
		     rack" — is why the burning side is the top one: the sections carry a
		     run's life top to bottom, the lane carries the queue, and making the
		     lane rhyme with the sections cost the reader the row they open the
		     page for.) -->
		<div class="mt-2 flex items-center gap-2" aria-hidden="true">
			<span class="h-px flex-1 bg-amber-900/50"></span>
			<span class="font-mono text-[8px] tracking-[0.18em] text-amber-200/70 uppercase">now</span>
			<span class="font-mono text-[8px] text-ink-mute">{clockLabel}</span>
			<span class="h-px w-6 bg-amber-900/50"></span>
		</div>

		<div class="mt-1.5 flex flex-col">
			{#each armed as row, index (row.id)}
				<!-- Armed: the same object, cold — the queue behind the rule, soonest
			     first. Frost thaws toward amber as the fire nears, and the bar is
			     imminence drawn as geometry, so reading down from the rule is
			     reading further into the future. -->
				<button
					type="button"
					class="flex h-[20px] w-full cursor-pointer items-center justify-start gap-1.5 border border-transparent px-1.5 text-left"
					style={`color: ${row.color};${selectedId === row.id ? ' filter: brightness(1.6);' : ''}`}
					title={row.label}
					aria-expanded={selectedId === row.id}
					onclick={() => select(row)}
					in:glitchReveal={{ duration: 240, delay: 70 + index * 26 }}
				>
					<span
						class="h-2 w-2 shrink-0 rounded-full"
						style={statusDotStyle('burning', row.color, row.urgency)}
						aria-hidden="true"
					></span>
					<!-- The forward weld: an armed pick draws its crossing from the
				     schedule entry's own `serves:` row, in the same cells as the
				     burning pick above it — and at the same x, since both rows
				     share this lead slot and padding box. Before this existed it
				     was the one row in the lane with a blank where its threads
				     belong. -->
					<Crossing
						cells={crossingCells(threads, row.crosses, topicFaces)}
						label="threads this pick serves"
					/>
					<span
						class="h-[7px] shrink-0 rounded-r-[1px]"
						style={`width: ${(row.barFraction * 34).toFixed(2)}%; background-color: ${row.color}`}
						aria-hidden="true"
					></span>
					<span class="truncate font-mono text-[9px] leading-none whitespace-nowrap">
						{row.clock ? `${row.clock} · ` : ''}{row.note ? `${row.note} · ` : ''}{row.label}
					</span>
				</button>
			{/each}
		</div>

		{#if overflow > 0}
			<!-- The tail of the queue. The lane keeps the *soonest* armed picks,
			     because those are the ones about to burn; what the cap left off is
			     said, never dropped silently — a truncated surface renders
			     identically to a complete one, and only this line knows. -->
			<p class="mt-1 font-mono text-[9px] text-ink-mute">+{overflow} further out</p>
		{/if}
	{/if}
	{#if hiddenByTopics > 0}
		<!-- The heddle lens's honest remainder: a filtered lane must say what
		     it is not showing — a hidden burning run rendered as silence would
		     be the filter lying about the machine. -->
		<p class="mt-1 font-mono text-[9px] text-ink-mute">
			+{hiddenByTopics} pick{hiddenByTopics === 1 ? '' : 's'} outside lit topics
		</p>
	{/if}
</div>
