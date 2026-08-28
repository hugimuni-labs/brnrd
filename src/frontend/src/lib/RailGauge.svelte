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
	// thanks!"). One line, fixed height, never grows, sticky forever —
	// next pick · fuel · tank, the three things a reader glances at without
	// meaning to touch.
	//
	// Since 2026-08-28 a provider row is also a *control*: pressing it opens
	// that provider's windows and cores. The fixed-height rule is untouched
	// and load-bearing — the expansion mounts **below** this component, never
	// inside it, so `.fuel-deck` stays 85px however many providers or windows
	// an account grows. What the row owns is the press and the pressed state;
	// what it must never grow is itself.
	//
	// The bench's `▸ settings` handle used to sit on this component's footline
	// while its body mounted below the provider bay — a handle and a body with
	// a whole panel between them (maintainer, 2026-08-28: "the settings button
	// dropdown that control them is very much above as you can see"). Both now
	// live in `RailBench`, above this rail. The gauge is readings only again.
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
		/** Which provider row is pressed open, or null. **This is the provider
		 *  selection** — not a pointer to one stored elsewhere. A pressed row
		 *  shows that provider's windows and that provider's cores directly
		 *  beneath the gauge, which is why no `CLAUDE | CODEX` tab strip
		 *  exists any more and why nothing can disagree with it. */
		openProvider?: string | null;
		onProviderToggle?: (provider: string) => void;
	}

	let {
		runners,
		shells,
		ledgerRows = null,
		scheduledWakes = null,
		now = Date.now(),
		activeSpawns = null,
		maxSpawns = null,
		openProvider = null,
		onProviderToggle
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
	// the provider's *binding* window is the row's one bar, and every other
	// window it reports is a named number on the ledger line beneath it.
	// (Those used to be overlaid fills on the same track; removed 2026-08-28,
	// see `fuelProviders.ts`' own note — three near-coincident bars with no
	// key made the headline number and the longest fill read as different
	// answers to the same question.)
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

	/** The collapsed row's own tooltip/aria text: every meter in words, with
	 *  the binding one named as such — the same fact the bar draws, for a
	 *  reader who is not looking at the bar. */
	function providerTooltip(group: FuelProviderGroup): string {
		const parts = group.meters.map((meter) => {
			const reading = meter.percent === null ? 'unknown' : `${Math.round(meter.percent)}% left`;
			if (meter.id === group.primary?.id) return `${meter.label}: ${reading} — binding`;
			if (meter.scope === 'core') return `${meter.label}: ${reading} (core allowance)`;
			return `${meter.label}: ${reading}`;
		});
		return parts.length > 0 ? parts.join(' · ') : `${group.provider}: no quota report`;
	}

	function toggleProvider(provider: string) {
		onProviderToggle?.(provider);
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
				{@const open = openProvider === group.provider}
				<button
					type="button"
					class="fuel-provider-row"
					class:is-open={open}
					aria-expanded={open}
					aria-label={`${group.provider} — ${
						open ? 'fold' : 'open'
					} its windows and cores. ${providerTooltip(group)}`}
					title={providerTooltip(group)}
					onclick={() => toggleProvider(group.provider)}
				>
					<div class="fuel-provider-head">
						<span class="fuel-label">{group.provider}</span>
						{#if primary}
							<!-- The number never travels without the window it measures.
							     Unlabelled, "34%" was read against whichever fill happened
							     to be longest, and the two were routinely different
							     readings. -->
							<span class="fuel-reading">
								<span class="fuel-window">{primary.windowName}</span>
								<strong style={`color: ${LEVEL_COLOR[level]}`}
									>{primary.percent === null ? '?' : `${Math.round(primary.percent)}%`}</strong
								>
							</span>
						{:else}
							<span class="fuel-empty">no report</span>
						{/if}
						<span class="fuel-caret" aria-hidden="true">{open ? '▾' : '▸'}</span>
					</div>
					<!-- One track, one quantity: the binding window's remaining fuel.
					     Every other reading this provider has is a number on the ledger
					     line below and a full-width bar in the bench's Resources list —
					     never a second fill on this axis. -->
					<span class="fuel-track" role="img" aria-label={providerTooltip(group)}>
						{#if primary}
							<span
								class="fuel-fill"
								style={`width: ${primary.percent ?? 0}%; color: ${LEVEL_COLOR[level]}`}
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
						{#if primary?.resetShort}<span class="fuel-reset-clock">↻{primary.resetShort}</span
							>{/if}
						<!-- THE LEDGER: every window this provider reports that is not
						     the binding one, named and numbered. This is what the ghost
						     stack was trying to say in overlapping fills, and it says it
						     in the one notation that needs no key. -->
						{#each group.secondary as meter (meter.id)}
							<span class="fuel-ledger" title={meter.tooltip}>
								<span class="fuel-ledger-name"
									>{meter.scope === 'core'
										? meter.label.replace(' · ', '/')
										: meter.windowName}</span
								>
								<span style={`color: ${LEVEL_COLOR[quotaLevel(meter.percent)]}`}
									>{meter.percent === null ? '?' : `${Math.round(meter.percent)}%`}</span
								>
							</span>
						{/each}
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
	.fuel-provider-row.is-open .fuel-label,
	.fuel-provider-row:hover .fuel-label {
		color: rgb(231 229 228);
	}
	.fuel-provider-head {
		grid-column: 1 / -1;
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 6px;
		min-width: 0;
	}
	/* The row is the control now, so it has to look like one at a glance —
	   a caret costs 1 character and is the difference between a readout and
	   an affordance. */
	.fuel-caret {
		flex: none;
		width: 7px;
		font-size: 8px;
		color: rgb(120 113 108);
	}
	.fuel-provider-row.is-open .fuel-caret {
		color: rgb(214 211 209);
	}
	.fuel-label {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-size: 10px;
		color: rgb(168 162 158);
	}
	.fuel-reading {
		display: flex;
		flex: none;
		align-items: baseline;
		gap: 5px;
	}
	/* Lowercase, matching the ledger's own tokens two lines down. Uppercased,
	   `5H` sat directly above a `5h` in the same row and read as a typo. */
	.fuel-window {
		font-size: 8px;
		letter-spacing: 0.08em;
		color: rgb(120 113 108);
	}
	.fuel-provider-head strong {
		font-size: 11px;
		font-weight: 500;
		font-variant-numeric: tabular-nums;
	}
	/* One fill on one track. The `color` (not `background-color`) is set
	   inline so the bar's own glow is its own colour rather than the row
	   text's — the fill reads as lit, not painted. */
	.fuel-track {
		grid-column: 1 / -1;
		position: relative;
		display: block;
		height: 12px;
	}
	.fuel-track::before {
		content: '';
		position: absolute;
		inset: 3px 0 3px 0;
		background: rgb(41 37 36);
		box-shadow: inset 0 0 0 1px rgb(68 64 60 / 0.45);
	}
	.fuel-fill {
		position: absolute;
		top: 3px;
		bottom: 3px;
		left: 0;
		display: block;
		background: currentColor;
		box-shadow: 0 0 7px currentColor;
		transition: width 500ms ease-out;
	}
	.fuel-reset {
		grid-column: 1 / -1;
		display: flex;
		align-items: center;
		gap: 6px;
		min-width: 0;
		overflow: hidden;
		font-size: 8px;
		font-variant-numeric: tabular-nums;
		white-space: nowrap;
		color: rgb(120 113 108);
	}
	.fuel-reset-clock {
		flex: none;
	}
	.fuel-ledger {
		display: flex;
		flex: none;
		align-items: baseline;
		gap: 3px;
	}
	/* A separator the ledger owns, so the row reads as one list of readings
	   rather than a run of unrelated chips. */
	.fuel-ledger::before {
		content: '·';
		margin-right: 3px;
		color: rgb(68 64 60);
	}
	.fuel-ledger-name {
		color: rgb(87 83 78);
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
	/* No settings toggle on this line any more (the bench owns its own handle
	   above the rail), so the pick reading takes the width the button used to
	   hold rather than truncating beside a control that has moved. */
	.next-pick {
		flex: 0 1 46%;
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
</style>
