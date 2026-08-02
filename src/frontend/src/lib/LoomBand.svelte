<script lang="ts">
	import { glitchReveal } from './transitions';
	import { durationLabel } from './runLedger';
	import { liveRunDisplayName, moodFace, type DaemonMood, type LiveRun } from './liveRuns';
	import type { ScheduledWake } from './scheduledWakes';
	import { LOOM_CENTER_ZONE_PX } from './loomBand';
	import { STATUS_BURNING, glowFor } from './statusPalette';
	import { futureEtaLabel, futureShelfRows, nextFutureWake } from './futureShelf';

	// The dissolution (2026-08-02): each tense owns exactly one object. The
	// cloth is the past, the rack is the future, and this band is the now —
	// the NOW seam alone, the same instrument zoomed to the pick being woven.
	// The past shelf's closed-run bars live in the cloth (whose 30d window
	// covers every step the old 6h→7d stepper offered, lens rail included);
	// the future shelf's ETA bars live in the rack (`FutureShelf.svelte`).
	// The seam keeps one whisper of the future — the idle "next in …" line —
	// because an idle seam's whole job is to answer "when does the next
	// thing happen". A live run here is a strand crossing the seam: what is
	// being woven through the shed right now, and for how long.

	interface Props {
		liveRuns: LiveRun[] | null;
		/** Read only for the idle seam's "next in …" whisper — the schedule
		 *  itself renders in the rack (`FutureShelf`). */
		scheduledWakes: ScheduledWake[] | null;
		now: number;
		/** Selection is the page's: the band reports, the detail sheet answers. */
		onSelect?: (kind: 'run' | 'wake', id: string) => void;
		/**
		 * The daemon's resting face (#566), for the NOW seam when nothing is
		 * burning. `null` on a pre-upgrade daemon that publishes no mood — and
		 * then the idle seam renders exactly what it rendered before this
		 * existed, hollow dot and all.
		 */
		daemonMood?: DaemonMood | null;
	}

	let { liveRuns, scheduledWakes, now, onSelect, daemonMood = null }: Props = $props();

	// The resting face, normalized once. Null whenever the wire has nothing to
	// say — no daemon mood, or a mood with no name — and the seam falls back to
	// its hollow dot rather than inventing a face to fill the space.
	let restingFace = $derived(daemonMood ? moodFace(daemonMood.name, daemonMood.glyph) : null);

	// The idle seam's one look ahead: the soonest wake still ahead of now,
	// read through the same rows the rack renders in full.
	let nextWake = $derived(nextFutureWake(futureShelfRows(scheduledWakes, now)));

	function select(kind: 'run' | 'wake', id: string) {
		onSelect?.(kind, id);
	}

	function elapsedLabel(run: LiveRun): string {
		const started = run.started_at ? Date.parse(run.started_at) : Number.NaN;
		if (!Number.isFinite(started)) return '';
		return durationLabel(Math.max(0, (now - started) / 1000));
	}
</script>

<div class="panel overflow-hidden px-3 py-2.5" aria-label="live runs at the now seam">
	<div
		class="grid items-center font-mono text-[9px] tracking-[0.16em] text-ink-mute uppercase"
		style={`grid-template-columns: minmax(0, 1fr) ${LOOM_CENTER_ZONE_PX}px minmax(0, 1fr)`}
	>
		<span aria-hidden="true"></span>
		<span class="text-center text-amber-200">now</span>
		<span aria-hidden="true"></span>
	</div>

	<div
		class="mt-1 grid h-[128px]"
		style={`grid-template-columns: minmax(0, 1fr) ${LOOM_CENTER_ZONE_PX}px minmax(0, 1fr)`}
	>
		<!-- The flanks the shelves vacated. A seam only reads as a seam with
		     material either side, so each side keeps one quiet warp hairline —
		     the threads the seam is drawn across, with nothing shelved on
		     them. The past's bars are the cloth's now; the future's are the
		     rack's. -->
		<div class="flex items-center pr-1.5" aria-hidden="true">
			<span class="h-px w-full bg-stone-800/60"></span>
		</div>

		<!-- The NOW seam: an instrument, not a snapshot. Idle it answers
		     "when does the next thing happen"; active it answers "what is
		     running and for how long". Everything else is the sheet's job. -->
		<div class="relative z-10 border-x border-amber-900/40 bg-stone-950/70 px-1">
			{#if liveRuns === null}
				<div
					class="absolute inset-0 flex items-center justify-center font-mono text-[9px] text-ink-mute"
				>
					acquiring
				</div>
			{:else if liveRuns.length === 0}
				<div class="absolute inset-0 flex flex-col items-center justify-center gap-1">
					<!-- Idle is not empty: the daemon is breathing, and #566 gives that
					     a face. The resting glyph takes the hollow dot's place rather
					     than crowding a line beside it — the dot was always the
					     placeholder for "nothing to show here", and now there is
					     something. No mood on the wire (pre-upgrade daemon) ⇒ the dot,
					     exactly as before. -->
					{#if restingFace}
						<span class="flex min-w-0 max-w-full items-baseline gap-1 px-1">
							{#if restingFace.glyph}
								<span class="shrink-0 font-mono text-[11px] text-amber-200/80" aria-hidden="true"
									>{restingFace.glyph}</span
								>
							{/if}
							<span class="truncate font-mono text-[8px] text-ink-quiet">{restingFace.name}</span>
						</span>
					{:else}
						<span
							class="h-2.5 w-2.5 rounded-full border border-stone-600 bg-stone-950"
							aria-hidden="true"
						></span>
					{/if}
					<span class="font-mono text-[10px] text-stone-400">
						{new Date(now).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
					</span>
					{#if nextWake}
						<span class="max-w-full truncate px-1 text-center font-mono text-[8px] text-ink-mute">
							next {futureEtaLabel(nextWake.etaMs)}
						</span>
					{/if}
				</div>
			{:else}
				<div class="absolute inset-1 flex flex-col justify-center gap-1 overflow-hidden">
					{#each liveRuns.slice(0, 2) as run, index (run.id)}
						{@const stopId = run.run_id || run.id}
						<!-- Server truth only now: the optimistic "I just tapped it"
						     state belongs to the panel that issued the stop, not to a
						     band that merely reports position. -->
						{@const stopping = run.stop_requested}
						<!-- `|global` (#970): the 0→1 case creates this whole `{:else}`
						     branch, and a local intro inside the freshly-born each block
						     never fires — the seam's most common ignition was the one
						     that didn't play. -->
						<div
							class="flex min-w-0 items-stretch gap-px"
							in:glitchReveal|global={{ duration: 260, delay: 35 + index * 38 }}
						>
							<button
								type="button"
								class="min-w-0 flex-1 cursor-pointer border border-amber-700/50 bg-stone-950/90 px-1.5 py-1 text-left font-mono leading-tight text-amber-100"
								style={glowFor(liveRuns.length > 1 ? 'attention' : 'calm', STATUS_BURNING)}
								title={liveRunDisplayName(run) || run.repo_label || 'live run'}
								onclick={() => select('run', stopId)}
							>
								<span class="block truncate text-[9px]">
									{liveRunDisplayName(run) || run.repo_label || 'live run'}
								</span>
								{#if elapsedLabel(run)}
									<span class="mt-0.5 block text-[8px] text-amber-500/80">
										{stopping ? 'stopping…' : elapsedLabel(run)}
									</span>
								{/if}
							</button>
							<!-- The stop control used to sit here as a `w-7` sibling
							     (#492). It moved to the node panel's expanded view
							     (`RunNodeInline`) on 2026-07-19: a destructive action was
							     taking width from a 9px cell that had none to spare. The
							     *state* stays — a stopping run still says so. -->
						</div>
					{/each}
					{#if liveRuns.length > 2}
						<span class="text-center font-mono text-[8px] text-amber-500/70"
							>+{liveRuns.length - 2}</span
						>
					{/if}
				</div>
			{/if}
		</div>

		<div class="flex items-center pl-1.5" aria-hidden="true">
			<span class="h-px w-full bg-stone-800/60"></span>
		</div>
	</div>
</div>
