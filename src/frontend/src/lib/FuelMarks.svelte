<script lang="ts">
	// One shell's fuel marks, drawn the same in the board header and the loom strip.
	// The bar's length is fuel left: the 5h window when the harness has one, else
	// the week. The track's colour is the week's pace (neutral dark on pace, amber
	// then red as fuel spent runs ahead of the week elapsed). The week number sits
	// beside a 5h bar whenever the harness has a week, on pace or behind. A tick is
	// the 5h reset and each dot is a day still ahead of the weekly reset, both
	// placed on the week's span. The gauge's width does not change with the pace.
	import {
		barGauge,
		formatPct,
		isFiveHour,
		isWeek,
		weekMarks,
		weekPace,
		type LoomGauge
	} from './loomSnapshot.ts';

	let { gauges, now }: { gauges: LoomGauge[] | undefined; now: number } = $props();

	const bar = $derived(barGauge(gauges));
	const week = $derived(
		(gauges ?? []).find((gauge) => isWeek(gauge.window) && typeof gauge.left_pct === 'number') ??
			null
	);
	const five = $derived(
		(gauges ?? []).find((gauge) => isFiveHour(gauge.window) && gauge.resets_at) ?? null
	);
	const pace = $derived(weekPace(week, now));
	const marks = $derived(weekMarks(week, five, now));
	const width = $derived(
		bar?.left_pct === undefined ? null : Math.max(0, Math.min(100, bar.left_pct))
	);
	const weekBeside = $derived(!!bar && !!week && isFiveHour(bar.window));
	const label = $derived.by(() => {
		if (!bar) return '';
		const head = `${bar.window} ${formatPct(bar.left_pct)} left`;
		if (!week) return head;
		const paceWord =
			pace === 'amber' || pace === 'red'
				? 'behind'
				: pace === 'healthy'
					? 'on pace'
					: 'pace unknown';
		const days = marks.dots.length;
		const wait = `${days} ${days === 1 ? 'day' : 'days'} to the weekly reset`;
		if (isWeek(bar.window)) return `${head}, ${paceWord}, ${wait}`;
		return `${head}, week ${formatPct(week.left_pct)} ${paceWord}, ${wait}`;
	});
</script>

<span class="fuel-marks" data-bar={bar?.window ?? ''} data-pace={pace ?? ''}>
	{#if bar}
		<span
			class="fuel-track"
			data-bar={bar.window}
			data-pace={pace ?? ''}
			role="img"
			aria-label={label}
		>
			{#if width !== null}
				<span class="fuel-fill" style:width={`${width}%`}></span>
			{/if}
			{#if marks.tick !== null}
				<span class="fuel-tick" style:left={`${marks.tick}%`} title="5h reset" aria-hidden="true"
				></span>
			{/if}
			{#each marks.dots as dot (dot)}
				<span class="fuel-dot" style:left={`${dot}%`} aria-hidden="true"></span>
			{/each}
		</span>
		<span class="fuel-pct">{formatPct(bar.left_pct)}</span>
		{#if weekBeside && week}
			<span class="fuel-week">{week.window} {formatPct(week.left_pct)}</span>
		{/if}
	{/if}
</span>

<style>
	.fuel-marks {
		display: inline-flex;
		align-items: center;
		gap: 5px;
		height: 16px;
		min-width: 0;
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
		height: 8px;
		background: #2a2a2a;
	}
	.fuel-track[data-pace='amber'] {
		background: #e8b34a;
	}
	.fuel-track[data-pace='red'] {
		background: #d33;
	}
	.fuel-fill {
		position: absolute;
		top: 2px;
		left: 0;
		height: 4px;
		background: #f4f4f5;
	}
	.fuel-tick,
	.fuel-dot {
		position: absolute;
		z-index: 1;
		background: #171717;
		box-shadow: 0 0 0 0.5px #fff;
	}
	.fuel-tick {
		top: 0;
		bottom: 0;
		width: 1px;
		margin-left: -0.5px;
	}
	.fuel-dot {
		top: 50%;
		width: 3px;
		height: 3px;
		margin-top: -1.5px;
		margin-left: -1.5px;
		border-radius: 50%;
	}
	.fuel-pct {
		flex: none;
		width: 5ch;
		text-align: right;
	}
	.fuel-week {
		flex: none;
		width: 12ch;
		color: var(--fuel-dim, #666);
	}
</style>
