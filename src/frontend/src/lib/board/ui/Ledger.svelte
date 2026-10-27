<script lang="ts">
	// A thread's ledger (4920): who owes the next move decides the section (waits on you, in hand, settled), one row per
	// wish with its pips, assumed wishes nested under the said wish they read from, Plan steps under the wish they cite.
	import type { Letter, Thread } from '../types.ts';
	import { LABEL, ledger, threadWishes, type LedgerRow } from '../ledger.ts';
	import { byAt, hm, stepText } from '../text.ts';
	import Chips from './Chips.svelte';
	import Pips from './Pips.svelte';
	import { board } from './board.svelte.ts';
	let { t, letters }: { t: Thread; letters: Letter[] } = $props();
	const b = board();
	const tw = $derived(threadWishes(t, letters, b.model?.wishes || []));
	const L = $derived(ledger(t, tw, b.model?.wishes || [], b.pending));
	const lettersOf = (r: LedgerRow) =>
		[...new Set([...(r.w.letters || []), ...r.extra])]
			.map((id) => b.model?.byId.get(id))
			.filter((l): l is Letter => !!l)
			.sort(byAt);
</script>

{#snippet steps(list: LedgerRow['steps'])}
	{#if list.length}<div class="stp">
			{#each list as s (s.i)}<div
					class="st {s.receipt ? 'done' : 'next'}"
					class:act={s === L.active}
				>
					<span class="sm">{s.receipt ? '✓' : '→'}</span><span class="tx"
						>{stepText(s.text)}{#if s.receipt}<span class="rc"
								>{String(s.receipt).replace(/^.*\//, '')}</span
							>{/if}</span
					>
				</div>{/each}
		</div>{/if}
{/snippet}

{#snippet row(r: LedgerRow, nested: boolean)}
	{@const key = 'w:' + t.id + '|' + r.w.wish}
	<div
		class="lg-row {r.sec} {r.hand}"
		class:hand={r.sec === 'hand'}
		class:assumed={nested}
		class:on={b.open.has(key)}
		data-wish={r.w.wish}
	>
		<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
		<div
			class="lg-h"
			onclick={() => {
				b.toggle(b.open, key);
				b.openBead(r.w.wish, 'keep');
			}}
		>
			<span class="lg-line"
				>{#if r.w.origin === 'derived'}<span class="lg-tag as">assumed</span
					>{/if}{#if r.from}<button
						type="button"
						class="ref"
						onclick={(e) => {
							e.stopPropagation();
							b.openBead(r.from!);
						}}>↰ {r.from}</button
					>
				{/if}<Chips text={r.w.line} /></span
			>
			<span class="lg-end">
				<Pips w={r.w} />
				{#if r.status}<span class="lg-st">{r.status}</span>{/if}
				{#if r.sec === 'waits' && r.call}
					{@const act = r.call.kind === 'ask' ? 'done' : 'agree'}
					<button
						type="button"
						data-act={act}
						onclick={(e) => (e.stopPropagation(), b.answer(r.w.wish, act))}>{act}</button
					>
					<button
						type="button"
						data-act="correct"
						onclick={(e) => (e.stopPropagation(), b.answer(r.w.wish, 'correct'))}>correct</button
					>
				{/if}
			</span>
		</div>
		{@render steps(r.steps)}
		{#if b.open.has(key)}
			<div class="lg-b">
				<div class="dim">
					{r.w.wish} · {r.w.origin || ''} · {r.w.state}{r.w.thread ? ' · ' + r.w.thread : ''}
				</div>
				<h4>asked · {(r.w.says || []).length}</h4>
				{#each r.w.says || [] as s (s.id + s.quote)}<div class="say {s.cites}">
						<q>{s.quote}</q>
					</div>{:else}<div class="dim">none</div>{/each}
				<h4>delivered</h4>
				{#each r.w.results || [] as res, i (i)}<div>
						{res.delivered ? 'delivered' : 'result'} · {res.kind || ''}
						<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- an external link -->
						{#if /^https?:/.test(res.ref || '')}<a href={res.ref} target="_blank" rel="noopener"
								>{res.ref}</a
							>{:else}{res.ref || ''}{/if}
						<span class="dim">{res.line || ''} {hm(res.at)}</span>
					</div>{:else}<div class="dim">nothing yet</div>{/each}
				<h4>verdict</h4>
				{#if r.w.verdict}<b>{r.w.verdict.as}</b>
					{hm(r.w.verdict.at)}
					{r.w.verdict.evidence || ''}{:else}<span class="dim">none yet</span>{/if}
				<h4>letters · {lettersOf(r).length} · oldest first</h4>
				{#each lettersOf(r) as l (l.id)}<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
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
	{#each r.kids as k (k.w.wish)}{@render row(k, true)}{/each}
{/snippet}

<div class="lg">
	{#if L.many}<div class="lg-bal">
			<b>asked {L.balance.said}</b> · assumed {L.balance.derived} · delivered {L.balance.delivered} ·
			<span class:owe={L.balance.owe}>verdict {L.balance.owe}</span>
		</div>{/if}
	{#each L.sections as s (s.k)}
		{#if L.many}<div class="lg-sec {s.k}">{LABEL[s.k]}<i>{s.n}</i></div>{/if}
		{#each s.rows as r (r.w.wish)}{@render row(r, false)}{:else}<div class="dim pad">
				nothing
			</div>{/each}
	{/each}
	{#if L.unplanned.length}
		{@const uk = 'u:' + t.id}
		<button type="button" class="stp-h" onclick={() => b.toggle(b.open, uk)}
			>{b.open.has(uk) ? '▾' : '▸'} unplanned {L.unplanned.length}</button
		>
		{#if b.open.has(uk)}{@render steps(L.unplanned)}{/if}
	{/if}
	{#if !tw.W.length && !(t.steps || []).length}<div class="dim pad">
			no wish and no Plan row on record
		</div>{/if}
	{#if L.loose.length}<div class="wc loose">
			<small>not tied to a wish</small> · {L.loose.length} letters
		</div>{/if}
</div>
