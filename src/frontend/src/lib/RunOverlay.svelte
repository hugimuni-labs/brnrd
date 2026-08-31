<script lang="ts">
	// The overlay placement of the selected run (maintainer, 2026-08-25:
	// "regardless if it's the compacted card of the resident run… or the
	// strand — they are all shown on this compacted view, and when you press
	// it, the overlay renders them, and you can close it"). This is a
	// *placement*, not a fourth rendering: the content inside is the same
	// single node panel the machine lane unfolds in-flow, so "one run, one
	// panel" survives the restructure — the panel just gains a stage that
	// covers the page instead of costing the reader their scroll position.
	//
	// Phone-first: a bottom sheet capped at 92svh with its own scroll;
	// desktop centers the same sheet. Closes on ✕, backdrop, or Escape —
	// closing never discards the underlying selection, so the reader lands
	// back exactly where they pressed.
	import type { Snippet } from 'svelte';
	import { fade, fly } from 'svelte/transition';

	// Two sizes since 2026-08-31. `sheet` is everything above, unchanged and
	// still the default. `full` is the stage `/daily` opens the ascii map
	// onto: the same sheet mechanics — backdrop, Escape, tap-out — widened to
	// the viewport, because a map read through a 2xl column is a map you
	// cannot read. The close control takes its own words with it: pressing a
	// run's card *opens* something, so `✕ close` is right there; pressing a
	// compact map *expands* it, so the way back is `↙ collapse`, which is the
	// affordance the maintainer asked for by name ("a button to collapse and
	// go back").
	interface Props {
		onClose: () => void;
		/** Accessible name for the dialog — the run's own name when known. */
		label?: string;
		/** `sheet` = the phone-first bottom sheet; `full` = the full-bleed stage. */
		size?: 'sheet' | 'full';
		/** The close control's own words. */
		closeLabel?: string;
		/** The backdrop's accessible name — the dismiss verb, spelled out. */
		dismissLabel?: string;
		children?: Snippet;
	}

	let {
		onClose,
		label = 'run detail',
		size = 'sheet',
		closeLabel = '✕ close',
		dismissLabel = 'close run detail',
		children
	}: Props = $props();

	function onKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape') {
			event.stopPropagation();
			onClose();
		}
	}
</script>

<svelte:window onkeydown={onKeydown} />

<!-- The backdrop is a real button: tap-out is the phone's native "close". -->
<div
	class="fixed inset-0 z-50 flex items-end justify-center sm:items-center sm:p-6"
	role="dialog"
	aria-modal="true"
	aria-label={label}
	transition:fade={{ duration: 200 }}
>
	<button
		type="button"
		class="absolute inset-0 cursor-pointer bg-black/70"
		aria-label={dismissLabel}
		onclick={onClose}
	></button>
	<div
		class="relative z-10 max-h-[92svh] w-full overflow-y-auto overscroll-contain {size === 'full'
			? 'sm:max-w-none'
			: 'sm:max-w-2xl'}"
		transition:fly={{ y: 48, duration: 260 }}
	>
		<div class="flex justify-end pb-1 pr-1">
			<button
				type="button"
				class="cursor-pointer border border-stone-800 bg-stone-950/90 px-2 py-1 font-mono text-[10px] tracking-wide text-ink-quiet uppercase hover:text-stone-200"
				onclick={onClose}
			>
				{closeLabel}
			</button>
		</div>
		{@render children?.()}
	</div>
</div>
