<script lang="ts">
	import { quotaLevel, timeUntil, type QuotaShell } from './quota';

	interface Props {
		shell: QuotaShell;
		now: number;
	}

	let { shell, now }: Props = $props();

	// Status palette (fixed, never themed — dataviz skill's palette.md).
	// Distinct from any categorical hue on purpose: a status color must
	// never be mistaken for a series identity.
	const LEVEL_COLOR: Record<string, string> = {
		ample: '#0ca30c',
		low: '#fab219',
		critical: '#d03b3b',
		unknown: '#57534e' // stone-600 — recedes, not a fourth status hue
	};

	const LEVEL_TEXT: Record<string, string> = {
		ample: 'ample',
		low: 'low',
		critical: 'critical',
		unknown: 'unknown'
	};
</script>

<div class="rounded-md border border-stone-800 bg-stone-900/60 p-3">
	<div class="mb-2 flex items-center justify-between text-sm">
		<span class="font-medium text-amber-200">{shell.shell}</span>
		{#if shell.status === 'stale'}
			<span class="rounded bg-sky-900/40 px-1.5 py-0.5 text-xs text-sky-300">stale report</span>
		{/if}
	</div>
	<div class="space-y-2">
		{#each shell.windows as window (window.label)}
			{@const level = quotaLevel(window.percent)}
			{@const remaining = timeUntil(window.resets_at, now)}
			<div>
				<div class="mb-1 flex items-baseline justify-between text-xs text-stone-400">
					<span>{window.label}</span>
					<span class="flex items-center gap-1.5">
						<!-- status never carries meaning by color alone: icon + label -->
						<span
							class="inline-block h-2 w-2 rounded-full"
							style={`background-color: ${LEVEL_COLOR[level]}`}
							aria-hidden="true"
						></span>
						<span style={`color: ${LEVEL_COLOR[level]}`}>
							{window.percent === null || window.percent === undefined
								? 'unknown'
								: `${Math.round(window.percent)}% left (${LEVEL_TEXT[level]})`}
						</span>
					</span>
				</div>
				<!-- The track drains, it doesn't fill (maintainer correction,
				     2026-07-05): the colored bar is *remaining*, and it recedes
				     toward empty as the window is consumed, not the reverse. -->
				<div
					class="h-2 w-full overflow-hidden rounded-full bg-stone-800"
					role="img"
					aria-label={`${window.label}: ${window.percent ?? 'unknown'} percent remaining`}
				>
					<div
						class="h-full rounded-full transition-[width] duration-500 ease-out"
						style={`width: ${window.percent ?? 0}%; background-color: ${LEVEL_COLOR[level]}`}
					></div>
				</div>
				{#if remaining || window.reset}
					<div class="mt-1 text-right text-[11px] text-stone-500">
						{remaining ? `resets in ${remaining}` : window.reset}
					</div>
				{/if}
			</div>
		{/each}
	</div>
</div>
