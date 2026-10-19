<script lang="ts">
	// The fuel strip: one gauge per shell, sticky at the top of the board.
	// Quiet while every shell is healthy; promoted (taller, warm) the moment a
	// shell is low or dry, so the one reading that changes what you can ask
	// for is the one the eye lands on. Tapping a shell opens its cores, and
	// picking a core sets the body the composer sends with: body choice lives
	// here and nowhere else.
	import { quotaLevel } from './quota.ts';
	import { fuelPromoted, resetsIn, shellLow, type LoomShell } from './loomSnapshot.ts';

	let {
		fuel,
		body = $bindable(),
		now
	}: { fuel: LoomShell[]; body: string | null; now: number } = $props();

	let open = $state<string | null>(null);
	let promoted = $derived(fuelPromoted(fuel));
	let opened = $derived(fuel.find((s) => s.shell === open) ?? null);

	const fill: Record<string, string> = {
		burning: 'bg-amber-200/70',
		cooling: 'bg-amber-400',
		spent: 'bg-orange-500',
		unknown: 'bg-stone-700'
	};

	const short = (window: string) => (window === 'session' ? 'session' : window);
</script>

<div
	class={`sticky top-0 z-40 -mx-5 border-b px-5 backdrop-blur sm:-mx-6 sm:px-6 ${
		promoted
			? 'border-orange-700/50 bg-[#1a0f07]/92 py-3'
			: 'border-stone-800/70 bg-[#0c0906]/88 py-2'
	}`}
	data-testid="fuel-strip"
	data-promoted={promoted ? 'true' : 'false'}
>
	<div class="flex flex-wrap items-stretch gap-2">
		{#each fuel as shell (shell.shell)}
			{@const low = shellLow(shell)}
			<button
				type="button"
				class={`min-w-0 flex-1 basis-36 border px-2.5 text-left font-mono transition-colors ${
					low
						? 'border-orange-600/60 bg-orange-950/30 py-2 shadow-[0_0_16px_rgba(234,88,12,0.18)]'
						: 'border-stone-800 bg-stone-900/30 py-1.5 hover:border-amber-800/60'
				} ${open === shell.shell ? 'border-amber-600/70' : ''}`}
				aria-expanded={open === shell.shell}
				aria-label={`${shell.shell} fuel`}
				data-shell={shell.shell}
				data-low={low ? 'true' : 'false'}
				onclick={() => (open = open === shell.shell ? null : shell.shell)}
			>
				<span class="flex items-baseline justify-between gap-2">
					<span
						class={`truncate tracking-wide ${low ? 'text-sm font-semibold text-orange-200' : 'text-[11px] text-stone-200'}`}
					>
						{shell.shell}
					</span>
					{#if shell.state === 'dry'}
						<span class="text-[10px] tracking-wider text-orange-300 uppercase">dry</span>
					{:else if shell.state === 'stale'}
						<span
							class="text-[10px] tracking-wider text-ink-mute uppercase"
							title="read before its last reset">stale</span
						>
					{:else if low}
						<span class="text-[10px] tracking-wider text-orange-300 uppercase">low</span>
					{/if}
				</span>
				{#each shell.gauges ?? [] as gauge (gauge.window)}
					{@const level = quotaLevel(gauge.left_pct)}
					<span class="mt-1 flex items-center gap-2 text-[10px] text-ink-quiet">
						<span class="w-11 shrink-0 truncate">{short(gauge.window)}</span>
						<span
							class={`relative block flex-1 overflow-hidden bg-stone-800/80 ${low ? 'h-1.5' : 'h-1'}`}
						>
							{#if gauge.left_pct !== undefined}
								<span
									class={`absolute inset-y-0 left-0 ${fill[level]}`}
									style={`width: ${Math.max(0, Math.min(100, gauge.left_pct))}%`}
								></span>
							{/if}
						</span>
						<span
							class={`w-8 shrink-0 text-right ${level === 'spent' ? 'text-orange-300' : 'text-stone-300'}`}
						>
							{gauge.left_pct !== undefined ? `${gauge.left_pct}%` : '—'}
						</span>
					</span>
					{#if low && gauge.resets_at}
						<span class="mt-0.5 block pl-13 text-[10px] text-orange-200/80">
							resets {resetsIn(gauge.resets_at, now)}
						</span>
					{/if}
				{/each}
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
					class={`border px-2 py-1 transition-colors ${
						body === choice
							? 'border-amber-500/80 bg-amber-950/40 text-amber-100'
							: 'border-stone-700 text-stone-300 hover:border-amber-700/70'
					}`}
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
