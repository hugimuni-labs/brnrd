<script lang="ts">
	// A strand on a card: its body and live state. Working pulses; holding is
	// lit and still; everything past the live states goes quiet.
	import type { LoomStrand } from './loomSnapshot.ts';

	let { strand }: { strand: LoomStrand } = $props();

	const dot: Record<string, string> = {
		working: 'bg-sky-300 shadow-[0_0_6px_rgba(125,211,252,0.7)] motion-safe:animate-pulse',
		holding: 'bg-sky-300/70',
		idle: 'bg-stone-500',
		parked: 'bg-orange-400/80',
		failed: 'bg-red-500'
	};
</script>

<span
	class="inline-flex items-center gap-1.5 border border-stone-800 bg-stone-900/40 px-1.5 py-0.5 font-mono text-[10px] text-stone-300"
	title={strand.id}
	data-state={strand.state}
>
	<span class={`size-1.5 shrink-0 rounded-full ${dot[strand.state ?? ''] ?? 'bg-stone-700'}`}
	></span>
	{#if strand.body}<span>{strand.body}</span>{/if}
	{#if strand.state}<span class="text-ink-mute">{strand.state}</span>{/if}
</span>
