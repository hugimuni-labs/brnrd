<script lang="ts">
	// The 5h window is the bar when available, otherwise the week. A wedge
	// empties toward that bar's own reset; the week sits beside a 5h bar.
	// Colours are the status palette. Both the board header
	// and the loom strip draw this, so the booth crop and the cloud strip agree.
	import { quotaLevel } from './quota.ts';
	import {
		barGauge,
		formatPct,
		isFiveHour,
		isWeek,
		resetFraction,
		resetsIn,
		type LoomGauge
	} from './loomSnapshot.ts';
	import { DIAL_WEDGE_RADIUS, dialDasharray } from './railGauge.ts';
	import { STATUS_BURNING, STATUS_COOLING, STATUS_SPENT, STATUS_UNKNOWN } from './statusPalette.ts';

	let { gauges, now }: { gauges: LoomGauge[] | undefined; now: number } = $props();

	const fill: Record<string, string> = {
		burning: STATUS_BURNING,
		cooling: STATUS_COOLING,
		spent: STATUS_SPENT,
		unknown: STATUS_UNKNOWN
	};

	const binding = $derived(barGauge(gauges));
	const week = $derived((gauges ?? []).find((gauge) => isWeek(gauge.window)));
	const weekBeside = $derived(!!binding && isFiveHour(binding.window) && !!week);
	const level = $derived(quotaLevel(binding?.left_pct));
	const colour = $derived(fill[level]);
	const fraction = $derived(binding ? resetFraction(binding.window, binding.resets_at, now) : null);
	const when = $derived(binding ? resetsIn(binding.resets_at, now) : null);
	const width = $derived(
		binding?.left_pct === undefined ? null : Math.max(0, Math.min(100, binding.left_pct))
	);
</script>

<span class="fuel-marks" data-bar={binding?.window ?? ''}>
	{#if binding}
		<span
			class="fuel-track"
			data-bar={binding.window}
			role="img"
			aria-label={`${binding.window} ${formatPct(binding.left_pct)} left`}
		>
			{#if width !== null}
				<span class="fuel-fill" style:width={`${width}%`} style:background={colour}></span>
			{/if}
		</span>
		<span class="fuel-pct">{formatPct(binding.left_pct)}</span>
		<span class="fuel-clock">
			{#if fraction !== null}
				<svg class="fuel-dial" viewBox="0 0 12 12" aria-label="reset window remaining">
					<circle cx="6" cy="6" r="5.5" fill="none" stroke-width="1" class="fuel-ring" />
					<circle
						cx="6"
						cy="6"
						r={DIAL_WEDGE_RADIUS}
						fill="none"
						stroke-width={DIAL_WEDGE_RADIUS * 2}
						stroke={colour}
						stroke-dasharray={dialDasharray(fraction)}
					/>
				</svg>
			{/if}
		</span>
		<span class="fuel-when">{when ? `resets ${when}` : ''}</span>
		{#if weekBeside && week}
			<span class="fuel-side">week {formatPct(week.left_pct)}</span>
		{/if}
	{/if}
</span>

<style>
	.fuel-marks {
		display: inline-flex;
		align-items: center;
		gap: 5px;
		height: 16px;
		flex: none;
		color: var(--fuel-ink, #171717);
		font:
			11px/16px ui-monospace,
			Menlo,
			monospace;
		white-space: nowrap;
	}
	.fuel-track {
		position: relative;
		flex: none;
		width: 64px;
		height: 6px;
		background: var(--fuel-track, #e6e6e6);
	}
	.fuel-fill {
		position: absolute;
		top: 0;
		bottom: 0;
		left: 0;
	}
	.fuel-pct {
		flex: none;
		width: 6ch;
		text-align: right;
	}
	.fuel-clock {
		flex: none;
		width: 14px;
		height: 14px;
	}
	.fuel-dial {
		flex: none;
		width: 14px;
		height: 14px;
		transform: rotate(90deg) scaleX(-1);
	}
	.fuel-ring {
		stroke: var(--fuel-ring, #ccc);
	}
	.fuel-when,
	.fuel-side {
		color: var(--fuel-dim, #666);
	}
	.fuel-when {
		flex: none;
		width: 16ch;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.fuel-side {
		flex: none;
		width: 11ch;
	}
</style>
