<script lang="ts">
	import { DIAL_WEDGE_RADIUS, dialDasharray, fuelRows, runnerBlocks, slotChip } from './railGauge';
	import { quotaLevel, type QuotaShell } from './quota';
	import type { RunnersResponse } from './runners';
	import type { RunLedgerRow } from './runLedger';
	import type { ScheduledWake } from './scheduledWakes';
	import { readTanks, type TankVerdict } from './tankForecast';
	import { STATUS_BURNING, STATUS_COOLING, STATUS_SPENT, STATUS_UNKNOWN } from './statusPalette';

	// THE GAUGE (w-68, signed 2026-08-19: "Gauge + Bench - yes, exactly,
	// thanks!"). One line, fixed height, never grows, sticky forever, no
	// disclosure — next pick · fuel · tank, the three things a reader
	// glances at without ever meaning to touch. Nothing here may become
	// tall: picking a project, an environment, or a core is THE BENCH's
	// job now, one tap away via `onBenchToggle` below, never this
	// component's own accordion.
	//
	// The whole line is one `overflow-x-auto` row rather than the old slim
	// bar's `flex-wrap` — wrapping is exactly what let the old rail grow
	// with the catalog (more quota windows -> more lines -> a taller
	// "fixed" gauge, which was never fixed at all). A row that can only
	// ever scroll sideways cannot grow vertically no matter how large the
	// account gets; `measure-rail.mjs` is the check that this held.
	interface Props {
		runners: RunnersResponse | null;
		shells: QuotaShell[] | null;
		ledgerRows?: RunLedgerRow[] | null;
		scheduledWakes?: ScheduledWake[] | null;
		now?: number;
		activeSpawns?: number | null;
		maxSpawns?: number | null;
		benchOpen: boolean;
		onBenchToggle: () => void;
	}

	let {
		runners,
		shells,
		ledgerRows = null,
		scheduledWakes = null,
		now = Date.now(),
		activeSpawns = null,
		maxSpawns = null,
		benchOpen,
		onBenchToggle
	}: Props = $props();

	let blocks = $derived(
		runnerBlocks(
			runners?.profiles ?? [],
			runners?.default ?? null,
			runners?.wake_request ?? null,
			runners?.sticky ?? null,
			now
		)
	);
	let activeBlock = $derived(blocks.find((block) => block.active) ?? null);
	let fuel = $derived(fuelRows(shells ?? []));
	let slots = $derived(activeSpawns === null ? null : slotChip(activeSpawns, maxSpawns));
	let tanks = $derived(readTanks(shells ?? [], ledgerRows, scheduledWakes, now));
	let lead = $derived(tanks[0] ?? null);

	const VERDICT_COLOR: Record<TankVerdict, string> = {
		exhausting: STATUS_SPENT,
		tight: STATUS_BURNING,
		sustainable: STATUS_COOLING,
		unknown: STATUS_UNKNOWN
	};
	const LEVEL_COLOR: Record<string, string> = {
		burning: STATUS_BURNING,
		cooling: STATUS_COOLING,
		spent: STATUS_SPENT,
		unknown: STATUS_UNKNOWN
	};

	function profileTitle(name: string): string {
		const profile = runners?.profiles.find((candidate) => candidate.name === name);
		return profile ? `${profile.shell ?? '?'} · ${profile.model ?? 'default'}` : name;
	}
</script>

<div
	data-measure="gauge"
	class="flex min-h-[37.5px] w-full items-baseline gap-x-2 bg-stone-950/70 px-3 py-1.5 font-mono text-[10px]"
>
	<!-- Meters first, visible always (defect fixed 2026-08-19, minutes after
	     w-68 deploy: "I really like the bench but the gauge is not there is
	     it?" — next-pick's own text, profile name + badge + countdown, was
	     long enough to push fuel and tank past the 390px edge on the old
	     next-pick/fuel/tank order, so the two things a reader actually
	     glances at were the two things hidden). Fuel and tank now render
	     *before* next-pick and neither is allowed to shrink (`shrink-0`):
	     the row can only ever take space away from next-pick, never from
	     the meters. `overflow-x-auto` moves onto fuel alone, capped to
	     `max-w-[55%]` of the row, so a pathological catalog (many
	     shells × many quota windows) scrolls *inside its own box* instead
	     of stretching the line — the outer row itself no longer needs to
	     scroll for the common case, only next-pick's name ever truncates. -->
	<div class="flex min-w-0 flex-1 flex-nowrap items-baseline gap-x-3">
		<span
			data-measure="fuel"
			class="flex max-w-[55%] shrink-0 items-baseline gap-3 overflow-x-auto whitespace-nowrap"
			aria-label="quota fuel"
		>
			{#if shells === null}
				<span class="text-ink-mute">loading quota…</span>
			{:else if fuel.length === 0}
				<span class="text-ink-mute">no quota report</span>
			{:else}
				{#each fuel as row (row.id)}
					{@const level = quotaLevel(row.percent)}
					<span
						class="inline-flex items-baseline gap-1 whitespace-nowrap text-ink-quiet {row.stale || row.daemonStale
							? 'opacity-60'
							: ''}"
						title={row.tooltip}
					>
						{row.label}
						<span class="inline-block h-[3px] w-8 bg-stone-900" role="img" aria-label={row.tooltip}>
							<span
								class="block h-full transition-[width] duration-500 ease-out"
								style={`width: ${row.percent ?? 0}%; background-color: ${LEVEL_COLOR[level]}`}
							></span>
						</span>
						{#if row.timeRemaining !== null}
							<svg
								viewBox="0 0 12 12"
								class="h-[9px] w-[9px] shrink-0 -rotate-90 scale-x-[-1] self-center"
								aria-label="reset window remaining"
							>
								<circle cx="6" cy="6" r="5.5" fill="none" stroke-width="1" class="stroke-stone-800" />
								<circle
									cx="6"
									cy="6"
									r={DIAL_WEDGE_RADIUS}
									fill="none"
									stroke-width={DIAL_WEDGE_RADIUS * 2}
									class="stroke-stone-500"
									stroke-dasharray={dialDasharray(row.timeRemaining)}
								/>
							</svg>
						{/if}
						{#if row.resetShort}<span class="text-ink-quiet">↻{row.resetShort}</span>{/if}
						<span style={`color: ${LEVEL_COLOR[level]}`}
							>{row.percent === null ? '?' : `${Math.round(row.percent)}%`}</span
						>
					</span>
				{/each}
			{/if}
			{#if slots}
				<span
					title={slots.title}
					class="text-ink-quiet"
					style={slots.level ? `color: ${LEVEL_COLOR[slots.level]}` : ''}>{slots.label}</span
				>
			{/if}
		</span>

		{#if lead}
			<!-- `headlineFor` (tankForecast.ts) is prose, not a fixed enum —
			     "not enough of this window has elapsed to read a rate" reads
			     fine as a sentence and overflowed the viewport on its own
			     before this fix, the exact species defect 1 was filed for.
			     Same `min-w-0 truncate` cell pattern as next-pick's name, one
			     rung higher priority: capped wider (`max-w-[45%]` vs
			     next-pick's uncapped flex-1) so a short verdict never
			     truncates in practice, only a genuinely long sentence does. -->
			<span
				data-measure="tank"
				class="flex max-w-[45%] shrink items-baseline gap-2 whitespace-nowrap"
				aria-label="tank forecast"
			>
				<span class="shrink-0 tracking-[0.13em] text-ink-quiet uppercase">tank</span>
				<span class="min-w-0 truncate" style={`color: ${VERDICT_COLOR[lead.verdict]}`}
					>{lead.headline}</span
				>
				{#if lead.stale}<span class="shrink-0 text-ink-mute">· last known</span>{/if}
			</span>
		{/if}

		<!-- The one shrinkable cell: `min-w-0` lets it collapse below its own
		     content width (flex's default `min-width: auto` would otherwise
		     refuse to shrink past the unbroken profile name), and `truncate`
		     on the name alone — not the whole cell — keeps the "next pick"
		     label and the countdown badge always legible, ellipsis eating
		     only the part that grows with the catalog. `overflow-hidden`
		     is the backstop for the pathological case (fuel and tank alone
		     already claim the full 390px, so this cell's own flex-basis
		     goes to zero): the label/badge are `shrink-0` by design — they
		     never truncate — so without a clip they would paint past this
		     box's own right edge and grow the *page's* scrollWidth, which
		     is exactly how `next-pick`'s badge widened the viewport before
		     this fix even after the name span itself measured 0. Clipped
		     is legible-or-absent; overflowing is legible-and-widens-the-
		     page, the one failure mode `measure-rail.mjs`'s scrollLeft
		     assertion exists to catch. -->
		<span
			data-measure="next-pick"
			class="flex min-w-0 flex-1 items-baseline gap-1.5 overflow-hidden"
		>
			<span class="shrink-0 tracking-[0.13em] text-ink-quiet uppercase">next pick</span>
			{#if runners === null}
				<span class="shrink-0 text-ink-quiet">loading…</span>
			{:else if activeBlock}
				<span class="min-w-0 truncate text-amber-200" title={profileTitle(activeBlock.profile.name)}
					>{activeBlock.profile.name}</span
				>
				<span class="shrink-0 text-ink-quiet">{activeBlock.badge}</span>
			{:else}
				<span class="shrink-0 text-ink-quiet">unavailable</span>
			{/if}
		</span>
	</div>

	<button
		type="button"
		aria-expanded={benchOpen}
		aria-label={benchOpen ? 'fold the bench' : 'open the bench — project, environment, core'}
		onclick={onBenchToggle}
		class="shrink-0 cursor-pointer border border-stone-800/60 bg-stone-900/30 px-2 py-1 font-mono text-[9px] tracking-[0.13em] text-ink-quiet uppercase hover:border-stone-600/70 hover:text-stone-300"
	>
		{benchOpen ? '▾ bench' : '▸ bench'}
	</button>
</div>
