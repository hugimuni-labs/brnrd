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

<div data-measure="gauge" class="gauge font-mono">
	<div class="gauge-topline">
		<span class="gauge-title">fuel</span>
		{#if slots}
			<span
				title={slots.title}
				class="gauge-slots"
				style={slots.level ? `color: ${LEVEL_COLOR[slots.level]}` : ''}>{slots.label}</span
			>
		{/if}
	</div>
	<div data-measure="fuel" class="fuel-deck" aria-label="quota fuel">
		{#if shells === null}
			<span class="fuel-empty">loading quota…</span>
		{:else if fuel.length === 0}
			<span class="fuel-empty">no quota report</span>
		{:else}
			{#each fuel as row (row.id)}
				{@const level = quotaLevel(row.percent)}
				<div
					class="fuel-cell {row.stale || row.daemonStale ? 'opacity-60' : ''}"
					title={row.tooltip}
				>
					<span class="fuel-label">{row.label}</span>
					<strong style={`color: ${LEVEL_COLOR[level]}`}
						>{row.percent === null ? '?' : `${Math.round(row.percent)}%`}</strong
					>
					<span class="fuel-track" role="img" aria-label={row.tooltip}>
						<span
							class="fuel-fill"
							style={`width: ${row.percent ?? 0}%; background-color: ${LEVEL_COLOR[level]}`}
						></span>
					</span>
					<span class="fuel-reset">
						{#if row.timeRemaining !== null}
							<svg
								viewBox="0 0 12 12"
								class="h-[9px] w-[9px] -rotate-90 scale-x-[-1]"
								aria-label="reset window remaining"
							>
								<circle
									cx="6"
									cy="6"
									r="5.5"
									fill="none"
									stroke-width="1"
									class="stroke-stone-800"
								/>
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
						{#if row.resetShort}↻{row.resetShort}{/if}
					</span>
				</div>
			{/each}
		{/if}
	</div>
	<div class="gauge-footline">
		{#if lead}
			<span data-measure="tank" class="tank-reading" aria-label="tank forecast">
				<span class="gauge-key">tank</span>
				<span class="truncate" style={`color: ${VERDICT_COLOR[lead.verdict]}`}>{lead.headline}</span
				>
			</span>
		{/if}
		<span data-measure="next-pick" class="next-pick">
			<span class="gauge-key">next</span>
			{#if runners === null}
				<span>loading…</span>
			{:else if activeBlock}
				<span class="truncate text-amber-200" title={profileTitle(activeBlock.profile.name)}
					>{activeBlock.profile.name}</span
				>
			{:else}
				<span>unavailable</span>
			{/if}
		</span>
		<button
			type="button"
			aria-expanded={benchOpen}
			aria-label={benchOpen ? 'fold the bench' : 'open the bench — project, environment, core'}
			onclick={onBenchToggle}
			class="bench-toggle"
		>
			{benchOpen ? '▾ bench' : '▸ bench'}
		</button>
	</div>
</div>

<style>
	.gauge {
		height: 140px;
		overflow: hidden;
		background: rgb(12 10 9 / 0.92);
		padding: 8px 10px 7px;
		color: rgb(168 162 158);
	}
	.gauge-topline,
	.gauge-footline {
		display: flex;
		align-items: center;
		min-width: 0;
	}
	.gauge-topline {
		height: 14px;
		justify-content: space-between;
		font-size: 9px;
		letter-spacing: 0.14em;
		text-transform: uppercase;
	}
	.gauge-title {
		color: rgb(214 211 209);
	}
	.gauge-slots,
	.gauge-key {
		color: rgb(120 113 108);
	}
	.fuel-deck {
		display: grid;
		grid-auto-flow: column;
		grid-auto-columns: calc((100% - 10px) / 2);
		grid-template-rows: repeat(2, 1fr);
		gap: 5px 10px;
		height: 85px;
		overflow-x: auto;
		padding: 5px 0 4px;
		scrollbar-width: none;
	}
	.fuel-deck::-webkit-scrollbar {
		display: none;
	}
	.fuel-cell {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		grid-template-rows: 14px 7px 10px;
		column-gap: 6px;
		min-width: 0;
	}
	.fuel-label {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-size: 10px;
		color: rgb(168 162 158);
	}
	.fuel-cell strong {
		font-size: 11px;
		font-weight: 500;
	}
	.fuel-track {
		grid-column: 1 / -1;
		display: block;
		height: 5px;
		background: rgb(41 37 36);
		box-shadow: inset 0 0 0 1px rgb(68 64 60 / 0.45);
	}
	.fuel-fill {
		display: block;
		height: 100%;
		transition: width 500ms ease-out;
		box-shadow: 0 0 8px currentColor;
	}
	.fuel-reset {
		grid-column: 1 / -1;
		display: flex;
		align-items: center;
		gap: 3px;
		font-size: 8px;
		color: rgb(120 113 108);
	}
	.fuel-empty {
		align-self: center;
		font-size: 10px;
		color: rgb(120 113 108);
	}
	.gauge-footline {
		height: 25px;
		gap: 10px;
		border-top: 1px solid rgb(68 64 60 / 0.55);
		font-size: 9px;
		white-space: nowrap;
	}
	.tank-reading,
	.next-pick {
		display: flex;
		min-width: 0;
		align-items: baseline;
		gap: 5px;
		overflow: hidden;
	}
	.tank-reading {
		flex: 1 1 auto;
	}
	.next-pick {
		flex: 0 1 38%;
	}
	.gauge-key {
		flex: none;
		letter-spacing: 0.12em;
		text-transform: uppercase;
	}
	.truncate {
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.bench-toggle {
		flex: none;
		min-height: 25px;
		cursor: pointer;
		border-left: 1px solid rgb(68 64 60);
		padding-left: 9px;
		color: rgb(168 162 158);
		font-size: 9px;
		letter-spacing: 0.12em;
		text-transform: uppercase;
	}
</style>
