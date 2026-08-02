<script lang="ts">
	import type { CrossingCell } from './crossing';

	// The crossing strip: the warp threads, in their authored order, with the
	// ones this pick lifted lit. Fixed cell width on purpose — the strip is only
	// a reference if the same threads occupy the same cells wherever it is drawn,
	// so a burning pick and the cloth line it becomes three hours later make
	// legibly the same statement. Position is a bonus the pick lane can offer and
	// the (wrapping) cloth row cannot; the alphabet is the part that travels.
	//
	// The beam hairline is not decoration. The first cut was bare ticks of
	// varying brightness, and the maintainer read it within minutes as a signal
	// meter — "is this ⚡ power of the core? like fable would be max?" — which is
	// exactly what N bars of varying brightness means to everyone alive. The
	// strip was borrowing a gauge's idiom while meaning a set. Drawing the beam
	// through it makes it threads crossing a bar instead of bars in a meter:
	// same cells, same data, an idiom it can keep.

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
		class="relative inline-flex shrink-0 items-center gap-[2px]"
		title={`${label}: ${crossed.join(' · ')}`}
		aria-label={`${label}: ${crossed.join(', ')}`}
	>
		<!-- The beam: one hairline the ticks cross, drawn behind them. -->
		<span
			class="pointer-events-none absolute inset-x-[-2px] top-1/2 h-px -translate-y-1/2 bg-stone-600/70"
			aria-hidden="true"
		></span>
		{#each cells as cell (cell.callSign)}
			<span
				class="relative block w-[3px] {cell.lit
					? 'h-[11px] bg-amber-400'
					: 'h-[5px] bg-stone-700/70'}"
				aria-hidden="true"
			></span>
		{/each}
	</span>
{/if}
