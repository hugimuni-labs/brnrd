<script lang="ts">
	// One letter in a column: a one-line head and two lines of body until a press selects it (wvut8 2). On the inbox an
	// internal letter folds to one line until opened (or "show internal"); a person letter opens its disposition (the
	// wishes citing it, the letters answering it) on a press.
	import type { Letter } from '../types.ts';
	import { hm, hue, splitQuote } from '../text.ts';
	import { inSide, internal, routes, sender } from '../sides.ts';
	import { stamps } from '../stamps.ts';
	import Chips from './Chips.svelte';
	import { board } from './board.svelte.ts';
	let { l, col, inbox }: { l: Letter; col: string; inbox: boolean } = $props();
	const b = board();
	const who = $derived(sender(l));
	const q = $derived(splitQuote(l, b.model?.byId || new Map()));
	const shut = $derived(inbox && internal(l) && !b.showAll && !b.open.has('l:' + l.id));
	const line = $derived(String(l.body).replace(/\s+/g, ' ').trim());
	const dis = $derived(l.person && b.open.has('d:' + l.id));
	const st = $derived(stamps(l));
	let quoteOpen = $state(false);
	function press() {
		if (shut || (internal(l) && inbox)) return b.toggle(b.open, 'l:' + l.id);
		b.select(b.isSel(l.id, col) && !l.person ? null : l.id, col);
		if (l.person) {
			if (!dis) return b.toggle(b.open, 'd:' + l.id);
			const r = routes(l, b.model!.byId, (t) => b.model!.threads.some((x) => x.id === t))[0];
			if (r)
				document
					.querySelector(`[data-column="${CSS.escape(r)}"]`)
					?.scrollIntoView({ inline: 'start' });
			else b.open.delete('d:' + l.id);
			return;
		}
		b.openBead(l.id);
	}
</script>

<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions -->
<article
	data-letter={l.id}
	class="letter {l.person ? 'person' : 'resident'}"
	class:in={!inbox && inSide(l, col, b.model?.threads || [])}
	class:notice={l.notice}
	class:internal={internal(l)}
	class:collapsed={shut}
	class:pending={l.pendingNote}
	class:sel={b.isSel(l.id, col)}
	class:linked={b.linked.has(l.id) && !b.isSel(l.id, col)}
	style:--h={hue(who)}
	onclick={press}
>
	{#if shut}
		<b>{who} → {String(l.to || '').replace(/^thread:/, '')}</b>
		{line.slice(0, 80)}{line.length > 80 ? '…' : ''}
	{:else}
		{#if inbox}{#each l.threads || [] as t, i (t)}{i ? ' → ' : ''}<button
					type="button"
					class="thread-chip"
					style:--t={hue(t)}
					onclick={(e) => {
						e.stopPropagation();
						b.openBead('thread:' + t);
					}}>{t}</button
				>{/each}{/if}
		<small title="{l.at.slice(0, 19).replace('T', ' ')} UTC"
			>{who} → {l.to || ''}{#if l.typed_by}
				· <span class="typed">typed by {l.typed_by}</span>{/if} · {hm(l.at)}</small
		>
		{#if q[0] != null}<!-- svelte-ignore a11y_no_static_element_interactions -->
			<span
				class="quote"
				class:open={quoteOpen}
				title="quoted"
				onclick={(e) => {
					e.stopPropagation();
					quoteOpen = !quoteOpen;
				}}>{q[0]}</span
			>{/if}
		<div class="body"><Chips text={q[1]} /></div>
		{#if st.length}<div class="stamps">
				{#each st as s, i (i)}<span class="stamp {s.cls}" title={s.title}>{s.text}</span>{/each}
			</div>{/if}
		{#if dis}
			<div class="dissect">
				<h4>disposition · {(l.wishes || []).length} wishes{l.chatter ? ' · chatter' : ''}</h4>
				{#each l.wishes || [] as w (w.wish)}<div class="row" style:--t={hue(w.thread || 'inbox')}>
						<b>{w.wish}</b> · {w.state}{w.thread ? ' · ' + w.thread : ''}<br />{w.line}
					</div>{:else}<div class="dim warn">none: no wish say or chatter cites it</div>{/each}
				<h4>letters answering it · {(l.answers || []).length}</h4>
				{#each (l.answers || [])
					.map((id) => b.model?.byId.get(id))
					.filter(Boolean) as x (x!.id)}<div class="row">
						<b>{sender(x!)} → {String(x!.to || '').replace(/^thread:/, '')}</b>
						{hm(x!.at)}<br />{String(x!.body).replace(/\s+/g, ' ').slice(0, 140)}
					</div>{:else}<div class="dim">none yet</div>{/each}
			</div>
		{/if}
	{/if}
</article>
