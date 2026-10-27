<script lang="ts">
	// The inbox's stack of calls: an open ask or a card he opened stands; unopened verdicts and answered cards fold into
	// one counted line (ug2om 1). done/agree sends at once, correct arms the composer.
	import type { Call } from '../types.ts';
	import { answered, stack } from '../calls.ts';
	import { hm } from '../text.ts';
	import { board } from './board.svelte.ts';
	let { calls }: { calls: Call[] } = $props();
	const b = board();
	const opened = $derived(new Set([...b.open].filter((k) => k.startsWith('c:')).map((k) => k.slice(2))));
	const S = $derived(stack(calls, b.pending, opened, b.cunfold));
</script>

{#if calls.length}<div class="calls">
		{#if S.fold}<button type="button" class="cfold" onclick={() => (b.cunfold = !b.cunfold)}
				>{b.cunfold ? '▾' : '▸'}
				{#if S.fold.verdicts}<b>{S.fold.verdicts} verdict{S.fold.verdicts == 1 ? '' : 's'} wait{S.fold.verdicts == 1 ? 's' : ''} on you</b>{/if}{S.fold
					.verdicts && S.fold.answered
					? ' · '
					: ''}{S.fold.answered ? `${S.fold.answered} answered` : ''}</button
			>{/if}
		{#each S.shown as c (c.wish)}
			{@const a = answered(c, b.pending)}
			{@const ask = c.kind === 'ask'}
			<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
			<div
				class="call"
				class:ask
				class:shut={!!a}
				class:open={opened.has(c.wish)}
				data-wish={c.wish}
				title={c.line}
				onclick={() => {
					if (!a) b.toggle(b.open, 'c:' + c.wish);
					b.openBead(c.wish, 'keep');
				}}
			>
				<span class="led"></span>
				{#if a}
					<span class="k">{ask && a.verdict === 'agree' ? 'done' : a.verdict}</span><span class="q">{c.line}</span><span
						class="dim">{a.pending ? 'sending' : hm(a.at)}</span
					>
				{:else}
					<span class="k">{ask ? 'asks you' : 'verdict'}</span><span class="q">{c.line}</span>
					<button type="button" data-act={ask ? 'done' : 'agree'} onclick={(e) => (e.stopPropagation(), b.answer(c.wish, ask ? 'done' : 'agree'))}
						>{ask ? 'done' : 'agree'}</button
					>
					<button type="button" data-act="correct" onclick={(e) => (e.stopPropagation(), b.answer(c.wish, 'correct'))}>correct</button>
					<div class="more">
						{c.wish} · {ask ? 'asked' : 'delivered'} {hm(c.at)}{#if ask} · waits on {c.waits || ''}{:else}<br
							/>{#if /^https?:/.test(c.ref || '')}<a href={c.ref} target="_blank" rel="noopener">{c.ref}</a>{:else}{c.ref || ''}{/if}
							{c.result || ''}{/if}
					</div>
				{/if}
			</div>
		{/each}
	</div>{/if}
