<script lang="ts">
	import { quotaLevel, timeUntil, type QuotaShell } from './quota';
	import {
		STATUS_GOOD,
		STATUS_WARN,
		STATUS_CRITICAL,
		STATUS_UNKNOWN,
		statusDotStyle,
		statusBarStyle
	} from './statusPalette';

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
							style={statusDotStyle(level, LEVEL_COLOR[level])}
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
						style={`width: ${window.percent ?? 0}%; ${statusBarStyle(level, LEVEL_COLOR[level])}`}
					></div>
				</div>
				{#if remaining || window.reset}
					<div class="mt-1 text-right font-mono text-[11px] text-stone-500">
						{remaining ? `resets in ${remaining}` : window.reset}
					</div>
				{/if}
			</div>
		{/each}
		{#if shell.reset_credits}
			<!-- Unredeemed free "Full reset" grants (Codex, via the app-server
			     quota probe — #315). Deliberately a line, not a track: it is a
			     count of one-shot escape hatches, not a headroom that drains, and
			     rendering it as a bar would lie about its shape. It earns a place
			     next to the windows anyway, because it changes what they *mean* —
			     a 4%-left week with four resets in the pocket is not an emergency. -->
			<div class="font-mono text-[11px] text-stone-500">
				{shell.reset_credits} free rate-limit reset{shell.reset_credits === 1 ? '' : 's'}
				available
			</div>
		{/if}
		{#if shell.credits && shell.credits.enabled !== false && (shell.credits.summary || (shell.credits.remaining_percentage !== null && shell.credits.remaining_percentage !== undefined) || (shell.credits.total_cost_usd !== null && shell.credits.total_cost_usd !== undefined))}
			{@const creditsPct = shell.credits.remaining_percentage ?? null}
			{@const creditsLevel = quotaLevel(creditsPct)}
			{@const creditsColor = LEVEL_COLOR[creditsLevel]}
			{@const creditsRemaining = timeUntil(shell.credits.resets_at, now)}
			<!-- Credits used to render as a small text note below the track,
			     visually demoted relative to the windows above it even though
			     it's the same shape of information (a headroom percent that
			     drains toward a reset) — live-caught 2026-07-08 ("why have it
			     like a note at all, rather than a properly placed indicator
			     block within the window track"). Now a peer row: same
			     label/dot/bar/reset structure as a real quota window, plus one
			     extra spent/limit line the windows don't have. `credits.summary`
			     still backstops shells that only ever proved a raw
			     `total_cost_usd` (no structured percentage) — that case skips
			     the bar (nothing to drain) but keeps the row shape. -->
			<div>
				<div class="mb-1 flex items-baseline justify-between font-mono text-xs text-stone-400">
					<span class="tracking-wide uppercase">credits</span>
					<span class="flex items-center gap-1.5">
						<span
							class="inline-block h-2 w-2 rounded-full"
							style={statusDotStyle(creditsLevel, creditsColor)}
							aria-hidden="true"
						></span>
						<span style={`color: ${creditsColor}`}>
							{creditsPct === null
								? (shell.credits.summary ??
									(shell.credits.total_cost_usd !== null &&
									shell.credits.total_cost_usd !== undefined
										? `$${shell.credits.total_cost_usd.toFixed(2)} in credits`
										: 'unknown'))
								: `${Math.round(creditsPct)}% left (${LEVEL_TEXT[creditsLevel]})`}
						</span>
					</span>
				</div>
				{#if creditsPct !== null}
					<div
						class="h-2 w-full overflow-hidden border border-stone-800/80 bg-stone-900"
						role="img"
						aria-label={`credits: ${creditsPct} percent remaining`}
					>
						<div
							class="h-full transition-[width] duration-500 ease-out"
							style={`width: ${creditsPct}%; ${statusBarStyle(creditsLevel, creditsColor)}`}
						></div>
					</div>
				{/if}
				{#if (shell.credits.spent_amount !== null && shell.credits.spent_amount !== undefined && shell.credits.limit_amount !== null && shell.credits.limit_amount !== undefined) || creditsRemaining || shell.credits.reset}
					<div
						class="mt-1 flex items-baseline justify-between font-mono text-[11px] text-stone-500"
					>
						<span>
							{#if shell.credits.spent_amount !== null && shell.credits.spent_amount !== undefined && shell.credits.limit_amount !== null && shell.credits.limit_amount !== undefined}
								{shell.credits.currency ?? '$'}{shell.credits.spent_amount.toFixed(2)} / {shell
									.credits.currency ?? '$'}{shell.credits.limit_amount.toFixed(2)} spent
							{/if}
						</span>
						<span>
							{#if creditsRemaining}
								resets in {creditsRemaining}
							{:else if shell.credits.reset}
								{shell.credits.reset}
							{/if}
						</span>
					</div>
				{/if}
			</div>
		{/if}
	</div>
</div>
