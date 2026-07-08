<script lang="ts">
	import { quotaLevel, timeUntil, type QuotaShell } from './quota';
	import { STATUS_GOOD, STATUS_WARN, STATUS_CRITICAL, STATUS_UNKNOWN } from './statusPalette';

	interface Props {
		shell: QuotaShell;
		now: number;
	}

	let { shell, now }: Props = $props();

	// Palette lives in `statusPalette.ts` (single source, shared with
	// LiveRuns/PRReviewQueue). ample = hearth-lit amber (full warmth); low =
	// frost creeping in (cooling, leaving the firelight) — a dimmer/
	// desaturated blue than the `sky-300` "stale report" badge below, so the
	// two don't collide as one hue meaning two things in the same card;
	// critical = void ash — the fire spent, not the fire gone red-hot (fixed
	// 2026-07-08 evening: the prior "dying ember" hex was still a genuinely
	// red hue under a warmer name, live-caught as "the 0% line is still
	// red"). Three real peer registers now — amber/frost/void — not
	// amber-primary with two narrow accents (still always icon+label, never
	// color alone).
	const LEVEL_COLOR: Record<string, string> = {
		ample: STATUS_GOOD,
		low: STATUS_WARN,
		critical: STATUS_CRITICAL,
		unknown: STATUS_UNKNOWN
	};

	const LEVEL_TEXT: Record<string, string> = {
		ample: 'ample',
		low: 'low',
		critical: 'critical',
		unknown: 'unknown'
	};
</script>

<div class="panel p-4">
	<div class="mb-3 flex items-center justify-between text-sm">
		<span class="font-mono font-medium tracking-wide text-amber-200 uppercase">{shell.shell}</span>
		{#if shell.status === 'stale'}
			<span
				class="border border-sky-900/60 bg-sky-950/40 px-1.5 py-0.5 font-mono text-[10px] tracking-wide text-sky-300 uppercase"
				>stale report</span
			>
		{/if}
	</div>
	<div class="space-y-3">
		{#each shell.windows as window (window.label)}
			{@const level = quotaLevel(window.percent)}
			{@const remaining = timeUntil(window.resets_at, now)}
			<div>
				<div class="mb-1 flex items-baseline justify-between font-mono text-xs text-stone-400">
					<span class="tracking-wide uppercase">{window.label}</span>
					<span class="flex items-center gap-1.5">
						<!-- status never carries meaning by color alone: icon + label -->
						<span
							class="inline-block h-2 w-2 rounded-full"
							style={`background-color: ${LEVEL_COLOR[level]}; box-shadow: 0 0 4px 1px ${LEVEL_COLOR[level]}90`}
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
					class="h-2 w-full overflow-hidden border border-stone-800/80 bg-stone-900"
					role="img"
					aria-label={`${window.label}: ${window.percent ?? 'unknown'} percent remaining`}
				>
					<div
						class="h-full transition-[width] duration-500 ease-out"
						style={`width: ${window.percent ?? 0}%; background-color: ${LEVEL_COLOR[level]}; box-shadow: 0 0 6px 0 ${LEVEL_COLOR[level]}b0, inset 0 0 3px 0 rgba(255,255,255,0.25)`}
					></div>
				</div>
				{#if remaining || window.reset}
					<div class="mt-1 text-right font-mono text-[11px] text-stone-500">
						{remaining ? `resets in ${remaining}` : window.reset}
					</div>
				{/if}
			</div>
		{/each}
	</div>
	{#if shell.credits && (shell.credits.summary || (shell.credits.total_cost_usd !== null && shell.credits.total_cost_usd !== undefined))}
		<div class="mt-3 border-t border-stone-800/80 pt-2 font-mono text-[11px] text-stone-400">
			<!-- Real spend, not a projection — see quota.ts QuotaCredits doc.
			     Fixed sky hue: this is the same "outside the firelight" /
			     metered-not-included signal the stale badge above uses, not a
			     new status color. -->
			<span class="text-sky-300">
				{shell.credits.summary ?? `$${shell.credits.total_cost_usd?.toFixed(2)} in credits`}
			</span>
		</div>
	{/if}
</div>
