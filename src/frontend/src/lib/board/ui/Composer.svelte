<script lang="ts">
	// The input writes to the inbox as the person, through the loom's POST /v1/loom/letters. Its &refs are checked
	// against the beads as he types; the draft outlives a reload (v44hse5kpf).
	import { hue } from '../text.ts';
	import { board } from './board.svelte.ts';
	const b = board();
	let ta: HTMLTextAreaElement | undefined = $state();
	$effect(() => {
		if (b.armed && ta && document.activeElement !== ta) {
			ta.focus();
			ta.setSelectionRange(ta.value.length, ta.value.length);
		}
	});
</script>

{#if b.refs.length}<div id="refs">
		<span class="dim">refs</span>
		{#each b.refs as r (r.t)}{#if r.id}<button type="button" class="ref" onclick={() => b.openBead(r.id!)}>&{r.t} → {r.id}</button>{:else}<span
					class="ref miss"
					title="the loom will send it unlinked and tell the thread">&{r.t} · not in the ledger</span
				>{/if}{/each}
	</div>{/if}
<form
	id="compose"
	style:--h={hue('inbox')}
	onsubmit={(e) => {
		e.preventDefault();
		b.send();
	}}
>
	<label for="say">→ inbox</label>
	<textarea
		id="say"
		bind:this={ta}
		bind:value={b.draft}
		placeholder="write to the inbox as you · ⌘↩ sends"
		oninput={() => {
			b.force = false;
			b.keep();
			if (!b.beads) b.store.loadBeads().then((x) => (b.beads = x));
		}}
		onkeydown={(e) => {
			if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
				e.preventDefault();
				b.send();
			}
		}}
	></textarea>
	<button>send</button>
	{#if b.sendError}<span class="err">{b.sendError}</span>{/if}
</form>
