<script lang="ts">
	import {
		DIAL_WEDGE_RADIUS,
		availableQuotaShells,
		dialDasharray,
		runnerBlocks,
		slotChip
	} from './railGauge';
	import { fuelProviderGroups, type FuelProviderGroup } from './fuelProviders';
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
		/** A provider row was tapped — "press a provider row" from the fuel
		 *  design (design-resident-field.md §Settings, fuel, and the next
		 *  dispatch): the parent opens the Bench focused on that provider's
		 *  Resources + Next-run (Shell/Core) selection. The gauge itself stays
		 *  disclosure-free — it only reports the tap. */
		onProviderExpand?: (provider: string) => void;
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
		onBenchToggle,
		onProviderExpand
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
	let availableShells = $derived(availableQuotaShells(shells ?? [], runners?.profiles));
	// One row per harness provider — the flat per-meter grid this replaces
	// (`git log` on this file/`fuelRows` carries it) named a shell·window pair
	// per cell and grew a cell for every window a provider reported. Grouped,
	// the provider itself is the readable primary, and everything else it
	// reports layers behind it as topology, not a second row.
	let providerGroups = $derived(fuelProviderGroups(availableShells));
	let slots = $derived(activeSpawns === null ? null : slotChip(activeSpawns, maxSpawns));
	let tanks = $derived(readTanks(availableShells, ledgerRows, scheduledWakes, now));
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

	/** The collapsed row's own tooltip/aria text: the primary reading plus
	 *  every ghost, so a reader who can't see opacity still gets the topology
	 *  ("there is more here") in words. */
	function providerTooltip(group: FuelProviderGroup): string {
		const parts = group.meters.map(
			(meter) =>
				`${meter.label}: ${meter.percent === null ? 'unknown' : `${Math.round(meter.percent)}% left`}`
		);
		return parts.length > 0 ? parts.join(' · ') : `${group.provider}: no quota report`;
	}

	function expandProvider(provider: string) {
		onProviderExpand?.(provider);
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
	<div data-measure="fuel" class="fuel-deck" aria-label="quota fuel, by provider">
		{#if shells === null}
			<span class="fuel-empty">loading quota…</span>
		{:else if providerGroups.length === 0}
			<span class="fuel-empty">no quota report</span>
		{:else}
			{#each providerGroups as group (group.provider)}
				{@const primary = group.primary}
				{@const level = quotaLevel(primary?.percent ?? null)}
				<button
					type="button"
					class="fuel-provider-row"
					title={providerTooltip(group)}
					onclick={() => expandProvider(group.provider)}
				>
					<div class="fuel-provider-head">
						<span class="fuel-label">{group.provider}</span>
						{#if primary}
							<strong style={`color: ${LEVEL_COLOR[level]}`}
								>{primary.percent === null ? '?' : `${Math.round(primary.percent)}%`}</strong
							>
						{:else}
							<span class="fuel-empty">no report</span>
						{/if}
					</div>
					<span class="fuel-stack" role="img" aria-label={providerTooltip(group)}>
						<!-- Ghosts render first (painted behind), dimmest-and-narrowest
						     last in source order so a later, more-recent-window ghost
						     doesn't visually cover an earlier one — enough topology to
						     read "there is more here" without asking the reader to
						     decode every meter. Never averaged or normalized against
						     the primary: each track keeps its own width and color. -->
						{#each group.secondary as ghost, index (ghost.id)}
							<span
								class="fuel-ghost"
								style={`width: ${ghost.percent ?? 0}%; bottom: ${(index + 1) * 3}px; background-color: ${LEVEL_COLOR[quotaLevel(ghost.percent)]}; opacity: ${0.4 - index * 0.1}`}
							></span>
						{/each}
						{#if primary}
							<span
								class="fuel-fill"
								style={`width: ${primary.percent ?? 0}%; background-color: ${LEVEL_COLOR[level]}`}
							></span>
						{/if}
					</span>
					<span class="fuel-reset">
						{#if primary?.timeRemaining !== null && primary?.timeRemaining !== undefined}
							<svg
								viewBox="0 0 12 12"
								class="h-[9px] w-[9px] rotate-90 scale-x-[-1]"
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
									stroke-dasharray={dialDasharray(primary.timeRemaining)}
								/>
							</svg>
						{/if}
						{#if primary?.resetShort}↻{primary.resetShort}{/if}
						{#if group.secondary.length > 0}
							<span class="fuel-more" aria-hidden="true">+{group.secondary.length}</span>
						{/if}
					</span>
				</button>
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
	/* One row per harness provider (design-resident-field.md §Settings, fuel,
	   and the next dispatch), not one cell per meter — a fixed-count list
	   (currently claude/codex), so unlike the old flat grid this never grows
	   with the catalog. `overflow-y: auto` is the same "never grows" belt the
	   old grid wore as `overflow-x` — a pathological account with more
	   providers scrolls, it does not stretch the gauge. */
	.fuel-deck {
		display: flex;
		flex-direction: column;
		justify-content: center;
		gap: 4px;
		height: 85px;
		overflow-y: auto;
		padding: 5px 0 4px;
		scrollbar-width: none;
	}
	.fuel-deck::-webkit-scrollbar {
		display: none;
	}
	.fuel-provider-row {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		grid-template-rows: 14px 12px 8px;
		column-gap: 6px;
		min-width: 0;
		flex: none;
		width: 100%;
		border: 0;
		background: none;
		padding: 0;
		font: inherit;
		color: inherit;
		text-align: left;
		cursor: pointer;
	}
	.fuel-provider-head {
		grid-column: 1 / -1;
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 6px;
		min-width: 0;
	}
	.fuel-label {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-size: 10px;
		color: rgb(168 162 158);
	}
	.fuel-provider-head strong {
		font-size: 11px;
		font-weight: 500;
	}
	/* The ghost stack: a dim baseline track, then every secondary meter
	   layered above it at falling opacity, then the primary fill on top —
	   "there is more here" as topology, never averaged into the primary
	   reading and never a manufactured symmetrical track. */
	.fuel-stack {
		grid-column: 1 / -1;
		position: relative;
		display: block;
		height: 12px;
	}
	.fuel-stack::before {
		content: '';
		position: absolute;
		inset: 0 0 0 0;
		bottom: 0;
		height: 5px;
		background: rgb(41 37 36);
		box-shadow: inset 0 0 0 1px rgb(68 64 60 / 0.45);
	}
	.fuel-fill,
	.fuel-ghost {
		position: absolute;
		left: 0;
		bottom: 0;
		display: block;
	}
	.fuel-fill {
		height: 5px;
		transition: width 500ms ease-out;
		box-shadow: 0 0 8px currentColor;
	}
	.fuel-ghost {
		height: 4px;
		transition: width 500ms ease-out;
	}
	.fuel-reset {
		grid-column: 1 / -1;
		display: flex;
		align-items: center;
		gap: 3px;
		font-size: 8px;
		color: rgb(120 113 108);
	}
	.fuel-more {
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
