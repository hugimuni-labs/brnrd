<script lang="ts">
	// A wish card in the inbox's wish view (board.html card): state, origin, id and thread, the line, the pips, results
	// and verdict; opened, its says and every letter tied to it, oldest first.
	import type { Letter, Wish } from '../types.ts';
	import { DONE } from '../stages.ts';
	import { byAt, hm, hue } from '../text.ts';
	import Chips from './Chips.svelte';
	import Pips from './Pips.svelte';
	import { board } from './board.svelte.ts';
	let { w }: { w: Wish } = $props();
	const b = board();
	const key = $derived('c:' + w.wish);
	const on = $derived(b.open.has(key));
	const says = $derived(w.says || []);
	const ls = $derived(
		(w.letters || [])
			.map((id) => b.model?.byId.get(id))
			.filter((l): l is Letter => !!l)
			.sort(byAt)
	);
</script>

<div
	class="wc"
	class:done={DONE.includes(w.state)}
	class:open={on}
	data-wish={w.wish}
	style:--t={hue(w.thread || 'inbox')}
>
	<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
	<div
		class="wch"
		onclick={(e) => !(e.target as HTMLElement).closest('a,button') && b.toggle(b.open, key)}
	>
		<small
			>{w.state} · {w.origin || ''} · {w.wish}{#if w.thread}
				· <button type="button" class="ref" onclick={() => b.openBead(w.thread!)}>{w.thread}</button
				>{/if}</small
		>
		<div class="wline"><Chips text={w.line} /></div>
		<Pips {w} />
		<div class="res">
			{#each w.results || [] as r, i (i)}<div>
					{r.delivered ? 'delivered' : 'result'} · {r.kind || ''}
					<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- an external link -->
					{#if /^https?:/.test(r.ref || '')}<a href={r.ref} target="_blank" rel="noopener"
							>{r.ref}</a
						>{:else}{r.ref || ''}{/if}
				</div>{/each}
			{#if w.verdict}<div>
					verdict · <b>{w.verdict.as}</b>
					{w.verdict.evidence || ''}
				</div>{:else}<div class="dim">verdict · none yet</div>{/if}
		</div>
		<div class="count">
			{on ? '▾' : '▸'}
			{says.length} say{says.length === 1 ? '' : 's'} · {ls.length} letter{ls.length === 1
				? ''
				: 's'}
		</div>
	</div>
	{#if on}
		<div class="insp">
			<h4>says · {says.length}</h4>
			{#each says as s (s.id + s.quote)}<div class="say {s.cites}"><q>{s.quote}</q></div>{:else}<div
					class="dim"
				>
					none
				</div>{/each}
			<h4>letters · {ls.length} · oldest first</h4>
			{#each ls as l (l.id)}<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
				<div class="lrow" class:quiet={l.chatter} onclick={() => b.openBead(l.id)}>
					<b
						>{l.notice ? 'loom' : l.from.replace('person:', '').split('/')[0]} → {String(
							l.to || ''
						).replace(/^thread:/, '')}</b
					>
					<span class="dim">{hm(l.at)}</span><br />{String(l.body)
						.replace(/\s+/g, ' ')
						.slice(0, 140)}
				</div>{:else}<div class="dim">none</div>{/each}
		</div>
	{/if}
</div>
