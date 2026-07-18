<script lang="ts">
	import SpoolRack from './SpoolRack.svelte';
	import { fuelRows, runnerBlocks } from './controlStrip';
	import { quotaLevel, type QuotaShell } from './quota';
	import type { RunnersResponse } from './runners';
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
	}

	let { runners, shells, runnersError = null, runnersNote = null, onTap }: Props = $props();
	let expanded = $state(false);
	let blocks = $derived(
		runnerBlocks(runners?.profiles ?? [], runners?.default ?? null, runners?.wake_request ?? null)
	);
	let fuel = $derived(fuelRows(shells ?? []));

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
								<span style={`color: ${LEVEL_COLOR[level]}`}>{row.percentLabel}</span>
							</div>
							<div class="h-[3px] w-full bg-stone-900" role="img" aria-label={row.tooltip}>
								<div
									class="h-full transition-[width] duration-500 ease-out {row.stale
										? 'opacity-50'
										: ''}"
									style={`width: ${row.percent ?? 0}%; ${statusBarStyle(level, LEVEL_COLOR[level])}`}
								></div>
							</div>
						</div>
					{/each}
				</div>
			{/if}
		</div>
	</div>

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
