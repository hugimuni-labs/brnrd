<script lang="ts">
	import { boltSummonsLabel, type BoltRow } from './bolts';

	// The summons strip (design-the-bolt.md §The cloth side, fork 2 signed:
	// "compact summons strip at the page head → tap jumps to the cloth-head
	// lane → the lane glows on arrival"). Moving sections, force-scroll, and
	// a modal are all explicitly rejected — this renders once, at rest,
	// wherever the page places it, and never moves itself.
	//
	// Rhymes with WarpBand's own needs-you strip (compact bar, count chip,
	// amber accent family) but slimmer still: one line, no accordion — the
	// lane it points at *is* the accordion.

	interface Props {
		/** Null while the run-ledger feed hasn't resolved yet. The count
		 *  doctrine (`backchannel.ts`): never render a partial sum — hold the
		 *  strip rather than show a count that might grow the moment the feed
		 *  lands. Empty once resolved renders nothing at all (steady state). */
		unacked: BoltRow[] | null;
		onView?: () => void;
		onTakeAll?: () => void;
	}

	let { unacked, onView, onTakeAll }: Props = $props();
</script>

{#if unacked !== null && unacked.length > 0}
	<div
		class="subpanel mb-3 flex flex-wrap items-baseline gap-x-2 gap-y-0.5 px-3 py-1.5 text-xs"
		role="status"
	>
		<span aria-hidden="true">⚡</span>
		<span class="font-mono text-[11px] tracking-wide text-amber-200">
			{boltSummonsLabel(unacked.length)}
		</span>
		<button
			type="button"
			class="ml-auto cursor-pointer font-mono text-[10px] tracking-wide text-amber-300 uppercase hover:text-amber-100"
			onclick={() => onTakeAll?.()}
		>
			take all
		</button>
		<span class="text-ink-mute" aria-hidden="true">·</span>
		<button
			type="button"
			class="cursor-pointer font-mono text-[10px] tracking-wide text-amber-300 uppercase hover:text-amber-100"
			onclick={() => onView?.()}
		>
			view
		</button>
	</div>
{/if}
