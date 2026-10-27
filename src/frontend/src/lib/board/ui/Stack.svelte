<script lang="ts">
	// Settled threads fold into one stack at the far right; a press opens one as its own column beside it.
	import type { Letter, Thread } from '../types.ts';
	import { hm, hue } from '../text.ts';
	import { lastAt } from '../order.ts';
	import { board } from './board.svelte.ts';
	let { settled, by }: { settled: Thread[]; by: Map<string, Letter[]> } = $props();
	const b = board();
	const verdicts = (s: Thread) =>
		(s.opened_for || [])
			.map((o) => b.model?.wishes.find((w) => w.wish === o.wish))
			.filter((w) => !!w)
			.map((w) => (w!.verdict ? `verdict · ${w!.verdict.as}` : `${w!.state} · no verdict`))
			.join(' · ');
</script>

<section class="column stack" data-column="~settled">
	<div class="heading">
		<h2>settled</h2>
		<div class="state">{settled.length} threads · tap one to open it here</div>
	</div>
	<div class="messages">
		<div class="chat">
			{#each settled as s (s.id)}<button
					type="button"
					class="srow"
					class:on={b.settledOpen.has(s.id)}
					style:--t={hue(s.id)}
					onclick={() => (b.toggle(b.settledOpen, s.id), b.persist())}
					>{s.id}<small>{verdicts(s) || 'no wish on record'} · {hm(lastAt(s, by))}</small></button
				>{/each}
		</div>
	</div>
</section>
