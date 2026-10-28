<script lang="ts">
	// A text with its &refs and bare ids as chips; each opens its bead. Runs, never HTML.
	import { chips } from '../text.ts';
	import { board } from './board.svelte.ts';
	let { text }: { text: string } = $props();
	const b = board();
</script>

{#each chips(text) as r, i (i)}{#if 'bead' in r}<button
			type="button"
			class="ref"
			title={r.amp ? `trace ${r.text}` : `open ${r.bead}`}
			onclick={(e) => {
				e.stopPropagation();
				b.openBead(r.bead);
			}}>{r.text}</button
		>{:else}{r.text}{/if}{/each}
