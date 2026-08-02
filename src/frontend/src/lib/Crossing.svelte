<script lang="ts">
	import type { CrossingCell } from './crossing';

	// The crossing strip: the warp threads, in their authored order, with the
	// ones this pick lifted lit. Fixed cell width on purpose — the strip is only
	// a reference if the same threads occupy the same cells wherever it is drawn,
	// so a burning pick and the cloth line it becomes three hours later make
	// legibly the same statement. Position is a bonus the pick lane can offer and
	// the (wrapping) cloth row cannot; the alphabet is the part that travels.

	interface Props {
		cells: CrossingCell[];
		/** Screen-reader sentence; the ticks themselves are decoration. */
		label?: string;
	}

	let { cells, label = 'threads crossed' }: Props = $props();
	let crossed = $derived(cells.filter((cell) => cell.lit).map((cell) => cell.callSign));
</script>

{#if cells.length > 0}
	<span
		class="inline-flex shrink-0 items-center gap-[2px]"
		title={`${label}: ${crossed.join(' · ')}`}
		aria-label={`${label}: ${crossed.join(', ')}`}
	>
		{#each cells as cell (cell.callSign)}
			<span
				class="block h-[10px] w-[3px] {cell.lit ? 'bg-amber-400' : 'bg-stone-700/60'}"
				aria-hidden="true"
			></span>
		{/each}
	</span>
{/if}
