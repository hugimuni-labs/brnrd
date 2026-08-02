<script lang="ts">
	import type { CrossingCell } from './crossing';

	// The legend: where a hue and a layer name are introduced to each other.
	// Without it the crossing strips are five coloured ticks nobody can name,
	// which is the defect this whole round exists to close — the maintainer's
	// read of the first cut: "nice to see which one(s) is / are being worked …
	// but the current version doesn't convey that correctly."
	//
	// Deliberately the same cell geometry the strips use (3px wide, crossing a
	// beam), so the legend is not a separate diagram to learn — it is one strip
	// with all its ticks lit and its names spelled out.

	interface Props {
		cells: CrossingCell[];
		/** Call signs whose layer has an item weaving right now. */
		weaving?: ReadonlySet<string>;
	}

	let { cells, weaving = new Set<string>() }: Props = $props();
</script>

{#if cells.length > 0}
	<span class="flex flex-wrap items-center gap-x-2.5 gap-y-1 font-mono text-[9px]">
		{#each cells as cell (cell.callSign)}
			{@const live = weaving.has(cell.callSign)}
			<span class="inline-flex items-center gap-1" title={`warp thread · ${cell.callSign}`}>
				<span
					class="block w-[3px] {live ? 'h-[11px]' : 'h-[9px]'}"
					style={`background-color: ${cell.color}${live ? '' : '; opacity: 0.75'}`}
					aria-hidden="true"
				></span>
				<span style={live ? `color: ${cell.color}` : ''} class={live ? '' : 'text-ink-quiet'}
					>{cell.callSign}</span
				>
				{#if live}
					<!-- Answering "which one is being worked" where the reader asks it:
					     on the warp, not only on the run. -->
					<span class="text-amber-300/90" aria-label="weaving now">↯</span>
				{/if}
			</span>
		{/each}
	</span>
{/if}
