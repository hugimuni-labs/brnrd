<script lang="ts">
	import SpoolRack from './SpoolRack.svelte';
	import { fuelRows, runnerBlocks } from './controlStrip';
	import { quotaLevel, type QuotaShell } from './quota';
	import type { RunnersResponse } from './runners';
	import type { RunLedgerRow } from './runLedger';
	import type { ScheduledWake } from './scheduledWakes';
	import { readTanks, type TankVerdict } from './tankForecast';
	import {
		STATUS_BURNING,
		STATUS_COOLING,
		STATUS_SPENT,
		STATUS_UNKNOWN,
		statusBarStyle
	} from './statusPalette';

	interface Props {
		runners: RunnersResponse | null;
		shells: QuotaShell[] | null;
		runnersError?: string | null;
		runnersNote?: string | null;
		onTap?: (profileName: string) => void;
		/** Slice 2 inputs. Both optional: the strip's first two regions must
		 *  keep working on a page (or a test) that has no ledger or schedule. */
		ledgerRows?: RunLedgerRow[] | null;
		scheduledWakes?: ScheduledWake[] | null;
		now?: number;
	}

	let {
		runners,
		shells,
		runnersError = null,
		runnersNote = null,
		onTap,
		ledgerRows = null,
		scheduledWakes = null,
		now = Date.now()
	}: Props = $props();
	let expanded = $state(false);
	let blocks = $derived(
		runnerBlocks(runners?.profiles ?? [], runners?.default ?? null, runners?.wake_request ?? null)
	);
	let fuel = $derived(fuelRows(shells ?? []));

	// The tank line: slice 2's whole visible surface. `readTanks` sorts worst
	// verdict first, and the strip is a glance instrument, so it shows the
	// leading one — the window about to run dry, not whichever shell the
	// provider listed first.
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

<div class="panel mt-4">
	<div class="grid md:grid-cols-[minmax(13rem,0.9fr)_minmax(0,1.1fr)]">
		<button
			type="button"
			class="group min-w-0 cursor-pointer border-b border-stone-800/70 p-2.5 text-left hover:bg-amber-950/20 md:border-r md:border-b-0"
			aria-expanded={expanded}
			onclick={() => (expanded = !expanded)}
		>
			<div
				class="mb-1 flex items-center justify-between gap-2 font-mono text-[9px] tracking-[0.13em] text-stone-500 uppercase"
			>
				<span>next-wake runner</span>
				<span class="text-stone-600 group-hover:text-stone-400" aria-hidden="true"
					>{expanded ? '▾' : '▸'} rack</span
				>
			</div>
			{#if runners === null}
				<div class="font-mono text-xs text-stone-500">next wake · loading…</div>
			{:else if blocks.length === 0}
				<div class="font-mono text-xs text-stone-500">next wake · unavailable</div>
			{:else}
				<div class="flex min-w-0 items-stretch gap-1.5">
					{#each blocks as block (block.kind)}
						<span
							title={profileTitle(block.profile.name)}
							class="min-w-0 border px-2 py-1 font-mono {block.active
								? 'border-amber-700/70 bg-amber-950/55 text-amber-100'
								: 'border-stone-800/60 bg-stone-950/30 text-stone-500 opacity-55'}"
						>
							<span class="block truncate text-[11px] font-medium">
								{block.active ? 'next wake · ' : ''}{block.profile.name}
							</span>
							<span
								class="mt-0.5 block truncate text-[8px] tracking-[0.11em] uppercase {block.kind ===
								'requested'
									? 'text-amber-300'
									: 'text-sky-300'}">{block.badge}</span
							>
						</span>
					{/each}
				</div>
			{/if}
		</button>

		<div class="min-w-0 p-2.5" aria-label="quota fuel">
			<div class="mb-1 font-mono text-[9px] tracking-[0.13em] text-stone-500 uppercase">fuel</div>
			{#if shells === null}
				<div class="font-mono text-[10px] text-stone-600">loading quota…</div>
			{:else if fuel.length === 0}
				<div class="font-mono text-[10px] text-stone-600">no quota report</div>
			{:else}
				<div class="grid grid-cols-2 gap-x-3 gap-y-1 sm:grid-cols-4 md:grid-cols-2 lg:grid-cols-4">
					{#each fuel as row (row.id)}
						{@const level = quotaLevel(row.percent)}
						<div class="min-w-0" title={row.tooltip}>
							<div
								class="mb-0.5 flex items-baseline justify-between gap-1 font-mono text-[9px] {row.stale
									? 'text-stone-600'
									: 'text-stone-400'}"
							>
								<span class="truncate">{row.label}</span>
								<span class="flex items-baseline gap-1">
									{#if row.resetShort}
										<span class="text-stone-500">↻{row.resetShort}</span>
									{/if}
									<span style={`color: ${LEVEL_COLOR[level]}`}>{row.percentLabel}</span>
								</span>
							</div>
							<div class="h-[3px] w-full bg-stone-900" role="img" aria-label={row.tooltip}>
								<div
									class="h-full transition-[width] duration-500 ease-out {row.stale
										? 'opacity-50'
										: ''}"
									style={`width: ${row.percent ?? 0}%; ${statusBarStyle(level, LEVEL_COLOR[level])}`}
								></div>
							</div>
							{#if row.timeFraction !== null}
								<!-- The window's own clock: how far through this
								     5h/week period we are. Reads against the fuel
								     bar above it — time ahead of fuel = burning
								     slow, fuel ahead of time = burning hot. -->
								<div
									class="mt-[1px] h-[1.5px] w-full bg-stone-900/70"
									aria-hidden="true"
								>
									<div
										class="h-full bg-stone-600 transition-[width] duration-500 ease-out {row.stale
											? 'opacity-40'
											: ''}"
										style={`width: ${row.timeFraction * 100}%`}
									></div>
								</div>
							{/if}
						</div>
					{/each}
				</div>
			{/if}
		</div>
	</div>

	{#if lead}
		<!-- Slice 2 (design-wyrd §4 band 1). The fuel bars above answer "how
		     much is left"; this answers "does it last", which is the question
		     the two bars were already carrying between them and making the
		     reader compute by eye. Measured from the window's own numbers —
		     `100 - percent` drawn over the elapsed share of the window — so it
		     costs no join and cannot disagree with the bar above it.

		     Deliberately one line for the leading window only: this is a glance
		     strip. The per-window detail is the fuel grid; the verdict is here. -->
		<div
			class="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-t border-stone-800/70 px-2.5 py-2 font-mono text-[10px]"
			aria-label="tank forecast"
		>
			<span class="tracking-[0.13em] text-stone-500 uppercase">tank</span>
			<span class="text-stone-400">{lead.label}</span>
			<span style={`color: ${VERDICT_COLOR[lead.verdict]}`}>{lead.headline}</span>
			{#if lead.ratePerHour !== null}
				<!-- The rate names its source. `measured` is the recent-burn series
				     (#491/#493) — the current pace, read from sampled levels over
				     the last few hours; `window avg` is whole-window arithmetic,
				     which lags the pace by however much of the window already
				     happened. They answer different questions and the reader
				     deciding whether to dispatch deserves to know which one is
				     speaking. -->
				<span
					class="text-stone-600"
					title={lead.rateSource === 'measured'
						? `current pace, measured over the last ${Math.round((lead.rateSpanMinutes ?? 0) / 60)}h of samples`
						: 'average draw across this whole window so far'}
				>
					{lead.ratePerHour < 1 ? lead.ratePerHour.toFixed(1) : Math.round(lead.ratePerHour)}%/h
					{lead.rateSource === 'measured' ? '· measured' : '· window avg'}
				</span>
			{/if}
			{#if lead.committedDraw !== null}
				<!-- The half the window cannot know: what is already queued to
				     draw on it. Priced from runs the daemon tagged
				     `source_system=schedule`, never from a self-reported slug. -->
				<span class="text-stone-500" title="scheduled wakes queued before this window resets">
					· {lead.committedWakes} scheduled ≈ {lead.committedDraw < 1
						? lead.committedDraw.toFixed(1)
						: Math.round(lead.committedDraw)}%
				</span>
			{:else if lead.committedWakes > 0}
				<!-- Count without a price: the wakes are real, the per-wake cost
				     is not yet measurable. Saying so beats inventing a number. -->
				<span class="text-stone-600">· {lead.committedWakes} scheduled, cost unmeasured</span>
			{/if}
			{#if lead.stale}
				<span class="text-stone-600">· stale report</span>
			{/if}
		</div>
	{/if}

	{#if expanded}
		<div class="border-t border-stone-800/70 p-3">
			<!-- Action receipts live with the control that caused them; keeping
			     them in the expanded rack avoids turning the glance strip into a
			     transient status-message row. -->
			{#if runnersError}
				<p class="mb-2 text-sm text-red-400">{runnersError}</p>
			{/if}
			{#if runnersNote}
				<p class="mb-2 font-mono text-xs text-amber-300">{runnersNote}</p>
			{/if}
			{#if runners === null}
				{#if !runnersError}
					<p class="text-sm text-stone-500">Loading…</p>
				{/if}
			{:else}
				<SpoolRack
					profiles={runners.profiles}
					defaultProfile={runners.default}
					stale={runners.stale}
					wakeRequest={runners.wake_request ?? null}
					{onTap}
				/>
			{/if}
		</div>
	{/if}
</div>
