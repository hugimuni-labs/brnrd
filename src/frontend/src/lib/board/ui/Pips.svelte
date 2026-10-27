<script lang="ts">
	// Five stages of a wish's life in one marker language (m435g): filled happened, hollow not yet, amber current,
	// a red ring on the stage that waits on him. The pips are the door to the wish's bead.
	import type { Wish } from '../types.ts';
	import { stages, stageText, stageWord } from '../stages.ts';
	import { allCalls, answered, openAsks } from '../calls.ts';
	import { board } from './board.svelte.ts';
	let { w }: { w: Wish } = $props();
	const b = board();
	const calls = $derived(allCalls(b.model?.threads || []));
	const st = $derived(
		stages(w, openAsks(calls), calls.some((c) => c.wish === w.wish && answered(c, b.pending)))
	);
	const n = $derived(st.filter((s) => s.on).length);
</script>

<button
	type="button"
	class="pips"
	data-pip={w.wish}
	title={`${n} of 5: ${st.map((s) => (s.on ? '● ' : '○ ') + stageText(s)).join(' · ')}. Open the wish`}
	onclick={(e) => {
		e.stopPropagation();
		b.openBead(w.wish);
	}}
>
	{#each st as s (s.k)}<i
			class="pip mk {s.k}"
			class:on={s.on}
			class:cur={s.cur}
			class:wait={s.wait}
			class:x={s.mark === 'x'}
			class:c={s.mark === 'c'}
			data-stage={s.k}
			title={stageText(s)}
		></i>{/each}
	<span class="pst">{stageWord(w)}</span><span class="go">›</span>
</button>
