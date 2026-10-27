<script lang="ts">
	// One column. The inbox: heading, the call stack, every letter. A thread (6lozd 3, 4958) has no mode: its ledger pinned
	// on top (a press on the heading grows it over the column), the history with its tapes, the now-line while a body runs.
	import { tick } from 'svelte';
	import type { Letter, Thread } from '../types.ts';
	import { bst } from '../order.ts';
	import { dur, hue, secs, short, stepText } from '../text.ts';
	import { liveOf, what, withTools } from '../tape.ts';
	import LetterCard from './LetterCard.svelte';
	import Tape from './Tape.svelte';
	import Ledger from './Ledger.svelte';
	import Calls from './Calls.svelte';
	import WishCard from './WishCard.svelte';
	import { inboxCards } from '../ledger.ts';
	import { board } from './board.svelte.ts';
	let { t, inbox = false, letters }: { t: Thread; inbox?: boolean; letters: Letter[] } = $props();
	const b = board();
	const B = $derived(t.board || {});
	const strand = $derived((t.strands || []).slice(-1)[0]);
	// The inbox opens on its wishes (board.html's default); a press on its heading turns it to letters and back.
	const wv = $derived(inbox && !b.letterView.has('inbox'));
	const W = $derived(wv ? inboxCards(b.model?.wishes || []) : null);
	const rows = $derived(
		inbox
			? wv
				? []
				: letters
			: withTools(letters, t, b.model?.tools || {}, b.model?.live || {}).filter(
					(r) => !('live' in r)
				)
	);
	const now = $derived(
		strand && strand.state === 'working' ? liveOf(strand, b.model?.live || {}) : null
	);
	const steps = $derived(t.steps || []);
	const ai = $derived(steps.findIndex((s) => !s.receipt));
	const grown = $derived(b.grown.has(t.id));

	let pane: HTMLElement | undefined = $state();
	let first = true;
	// Stick to the bottom when the reader is there (or on first paint); a reader scrolled up keeps the place.
	$effect.pre(() => {
		void rows.length;
		void W?.cards.length;
		if (!pane) return;
		const bottom = first || pane.scrollHeight - pane.clientHeight - pane.scrollTop < 40;
		first = false;
		if (bottom) tick().then(() => pane && (pane.scrollTop = pane.scrollHeight));
	});
</script>

<!-- eslint-disable svelte/no-navigation-without-resolve -- the forge url -->
<section
	class="column"
	class:inbox
	class:threadcol={!inbox}
	class:wv
	class:red={B.red}
	class:stall={B.stall}
	data-column={t.id}
	data-b={inbox ? '' : bst(t)}
	style:--h={hue(t.id)}
	onmouseenter={() => !inbox && (b.hovered = t.id)}
	onmouseleave={() => b.hovered === t.id && (b.hovered = null)}
>
	<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
	<div
		class="heading"
		onclick={(e) => {
			if ((e.target as HTMLElement).closest('a,button')) return;
			if (inbox) (b.toggle(b.letterView, 'inbox'), b.persist(), (first = true));
			else b.toggle(b.grown, t.id);
		}}
	>
		<h2>
			{#if B.state === 'settled'}<button
					type="button"
					class="unsettle"
					title="back into the settled stack"
					onclick={() => (b.settledOpen.delete(t.id), b.persist())}>×</button
				>{/if}
			{t.id}{#if t.url}<a
					class="forge"
					href={t.url}
					target="_blank"
					rel="noopener"
					title="open on the forge">↗</a
				>{/if}
			{#if B.state === 'seated'}<span
					class="pulse"
					class:hold={B.strand === 'holding'}
					title="seated · {B.strand}"
				></span>{/if}
			{#if B.open || B.waits}<span class="badge">verdict</span>{:else if B.state === 'mine'}<span
					class="badge mine"
					class:stall={B.stall}>{B.stall ? 'no body' : 'mine'}</span
				>{/if}
			{#if B.ready}<span
					class="badge ready"
					title="every wish on this thread is delivered or past it">ready to settle</span
				>{/if}
		</h2>
		{#if B.why}<div class="why {B.waits ? 'call' : B.state === 'waiting' ? 'waiting' : 'mine'}">
				{B.why}
			</div>{/if}
		{#if (B.unanswered || []).length}<div class="why unans">
				{B.unanswered!.length} letter{B.unanswered!.length > 1 ? 's' : ''} of yours unanswered:
				{#each B.unanswered || [] as id (id)}<button
						type="button"
						class="ref"
						onclick={() => b.openBead(id)}>{short(id)}</button
					>
				{/each}
			</div>{/if}
		<div class="state">
			{inbox ? 'the strand that weaves all of it' : t.status || ''}{#if strand}
				· {strand.id.slice(-6)} {strand.body || ''} {strand.state || ''}{/if}{#if t.plan?.total}
				· <span class="plan">plan {t.plan.done}/{t.plan.total}</span
				>{/if}{#if inbox && t.plan?.bytes != null && t.plan?.cap}
				· <span
					class="bytes"
					class:over={t.plan.bytes > t.plan.cap * 0.9}
					title="Plan bytes, carried at every wake; the inbox README keeps it under {t.plan.cap}"
					>{t.plan.bytes}/{t.plan.cap} B</span
				>{/if}{#if inbox}
				· <span class="tog"
					>{#if W}wishes {W.open} open of {W.cards.length} · ▸ letters{:else}letters · ▸ wishes {(
							b.model?.wishes || []
						).length}{/if}</span
				>{/if}
		</div>
		{#if inbox}<div class="wish">
				{#if W}done above, open below · newest at the bottom<br />heard → read → granting →
					delivered → agreed{:else}Every thread · {letters.length} letters<br />Time order · thread
					colours{/if}
			</div>{/if}
	</div>
	{#if inbox}
		<Calls calls={B.calls || []} />
	{:else}
		<div class="band" class:ex={grown}>
			<div class="wishpane"><Ledger {t} {letters} /></div>
		</div>
	{/if}
	<div class="messages" bind:this={pane}>
		{#if W}<div class="wishpane">
				{#each W.cards as w (w.wish)}<WishCard {w} />{:else}<div class="empty">
						no wishes
					</div>{/each}
			</div>{/if}
		<div class="chat">
			{#each rows as r (r.id)}
				{#if 'fold' in r}<Tape f={r} />{:else if 'body' in r}<LetterCard
						l={r}
						col={t.id}
						{inbox}
					/>{/if}
			{:else}<div class="empty">no letters</div>{/each}
		</div>
	</div>
	{#if now}
		<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
		<div class="nowbar" onclick={() => pane && (pane.scrollTop = pane.scrollHeight)}>
			<b
				>now{#if ai >= 0}<span class="stn" title={stepText(steps[ai].text)}
						>{` · step ${ai + 1}`}</span
					>{/if}{now.say ? ' · ' + now.say : ''}<span class="t"
					>{` · ${now.verb} ${now.abs ? now.since.slice(11, 16) + 'Z' : dur(secs(now.since, new Date(b.now).toISOString()))}`}</span
				></b
			>{#if now.open[0]}<span class="oc">{` · ${what(now.open[0])}`}</span>{/if}
		</div>
	{/if}
</section>
