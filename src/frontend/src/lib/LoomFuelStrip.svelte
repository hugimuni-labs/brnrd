<script lang="ts">
	// The fuel strip: one bar per shell, the 5h window or else the week, sticky at the top of
	// the cloud board. Quiet while every shell is healthy; promoted (warm border)
	// the moment a shell is low or dry. Promotion does not change the height: the
	// booth crop of a header must not jump. Tapping a shell opens its cores, and
	// picking a core sets the body the composer sends with.
	import { barGauge, fuelPromoted, resetsIn, shellLow, type LoomShell } from './loomSnapshot.ts';
	import { STATUS_BURNING, STATUS_SPENT } from './statusPalette.ts';
	import FuelMarks from './FuelMarks.svelte';

	let {
		fuel,
		body = $bindable(),
		now
	}: { fuel: LoomShell[]; body: string | null; now: number } = $props();

	let open = $state<string | null>(null);
	let promoted = $derived(fuelPromoted(fuel));
	let opened = $derived(fuel.find((s) => s.shell === open) ?? null);
</script>

<div
	class="sticky top-0 z-40 -mx-5 border-b border-stone-800/70 bg-[#0c0906]/88 px-5 py-2 backdrop-blur sm:-mx-6 sm:px-6"
	style="--fuel-track:#292524;--fuel-ink:#e7e5e4;--fuel-dim:#a8a29e;--fuel-ring:#44403c{promoted
		? `;border-color:${STATUS_BURNING}`
		: ''}"
	data-testid="fuel-strip"
	data-promoted={promoted ? 'true' : 'false'}
>
	<div class="flex flex-wrap items-stretch gap-2">
		{#each fuel as shell (shell.shell)}
			{@const low = shellLow(shell)}
			{@const binding = barGauge(shell.gauges)}
			<button
				type="button"
				class="min-w-0 flex-1 basis-36 border border-stone-800 bg-stone-900/30 px-2.5 py-1.5 text-left font-mono"
				style:border-color={open === shell.shell ? STATUS_BURNING : low ? STATUS_SPENT : undefined}
				aria-expanded={open === shell.shell}
				aria-label={`${shell.shell} ${binding?.window ?? 'fuel'}`}
				data-shell={shell.shell}
				data-low={low ? 'true' : 'false'}
				data-bar={binding?.window ?? ''}
				onclick={() => (open = open === shell.shell ? null : shell.shell)}
			>
				<span class="flex items-baseline justify-between gap-2">
					<span class="truncate text-[11px] tracking-wide text-stone-200">{shell.shell}</span>
					{#if shell.state === 'dry'}
						<span class="text-[10px] tracking-wider uppercase" style:color={STATUS_BURNING}
							>dry</span
						>
					{:else if shell.state === 'stale'}
						<span
							class="text-[10px] tracking-wider text-ink-mute uppercase"
							title="read before its last reset">stale</span
						>
					{:else if shell.state === 'error'}
						<span class="text-[10px] tracking-wider uppercase" style:color={STATUS_SPENT}
							>fails</span
						>
					{:else if low}
						<span class="text-[10px] tracking-wider uppercase" style:color={STATUS_BURNING}
							>low</span
						>
					{/if}
				</span>
				<FuelMarks gauges={shell.gauges} {now} />
			</button>
		{/each}
	</div>

	{#if opened}
		<div class="mt-2 flex flex-wrap items-center gap-1.5 font-mono text-[11px]" data-testid="cores">
			<span class="text-ink-mute">{opened.shell} cores</span>
			{#each opened.cores ?? [] as core (core)}
				{@const choice = `${opened.shell}/${core}`}
				<button
					type="button"
					class="border border-stone-700 px-2 py-1 text-stone-300 transition-colors"
					style:border-color={body === choice ? STATUS_BURNING : undefined}
					style:color={body === choice ? STATUS_BURNING : undefined}
					aria-pressed={body === choice}
					onclick={() => {
						body = choice;
						open = null;
					}}
				>
					{core}
				</button>
			{:else}
				<span class="text-ink-mute">none reported</span>
			{/each}
			{#each (opened.gauges ?? []).filter((g) => g.resets_at) as gauge (gauge.window)}
				<span class="text-ink-mute">· {gauge.window} resets {resetsIn(gauge.resets_at, now)}</span>
			{/each}
		</div>
	{/if}
</div>
