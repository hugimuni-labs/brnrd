<script lang="ts">
	import { fuelRows, runnerBlocks, slotChip } from './railGauge';
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
	class="panel flex w-full flex-nowrap items-baseline gap-x-3 overflow-x-auto px-3 py-1.5 font-mono text-[10px] whitespace-nowrap"
>
	<span data-measure="next-pick" class="flex items-baseline gap-1.5 whitespace-nowrap">
		<span class="tracking-[0.13em] text-ink-quiet uppercase">next pick</span>
		{#if runners === null}
			<span class="text-ink-quiet">loading…</span>
		{:else if activeBlock}
			<span class="text-amber-200" title={profileTitle(activeBlock.profile.name)}
				>{activeBlock.profile.name}</span
			>
			<span class="text-ink-quiet">{activeBlock.badge}</span>
		{:else}
			<span class="text-ink-quiet">unavailable</span>
		{/if}
	</span>

	<span
		data-measure="fuel"
		class="flex items-baseline gap-3 whitespace-nowrap"
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
					class="whitespace-nowrap text-ink-quiet {row.stale || row.daemonStale
						? 'opacity-60'
						: ''}"
					title={row.tooltip}
				>
					{row.label}
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
		<span
			data-measure="tank"
			class="flex items-baseline gap-2 whitespace-nowrap"
			aria-label="tank forecast"
		>
			<span class="tracking-[0.13em] text-ink-quiet uppercase">tank</span>
			<span style={`color: ${VERDICT_COLOR[lead.verdict]}`}>{lead.headline}</span>
			{#if lead.stale}<span class="text-ink-mute">· last known</span>{/if}
		</span>
	{/if}

	<button
		type="button"
		aria-expanded={benchOpen}
		aria-label={benchOpen ? 'fold the bench' : 'open the bench — project, environment, core'}
		onclick={onBenchToggle}
		class="ml-auto shrink-0 cursor-pointer border border-stone-800/60 bg-stone-900/30 px-2 py-1 font-mono text-[9px] tracking-[0.13em] text-ink-quiet uppercase hover:border-stone-600/70 hover:text-stone-300"
	>
		{benchOpen ? '▾ bench' : '▸ bench'}
	</button>
</div>
