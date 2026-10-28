<script lang="ts">
	// The bead sheet (4920): the pressed item, the chain up to its source and what came out of it, all walked in the
	// client from the beads part (design §D3). Any bead in it re-centres it; ‹ walks back; esc closes.
	import { hm, short } from '../text.ts';
	import type { Bead, Root } from '../bead.ts';
	import Chips from './Chips.svelte';
	import Pips from './Pips.svelte';
	import { board } from './board.svelte.ts';
	const b = board();
	const T = $derived(b.timeline);
	const ROOT: Record<Root, string> = {
		person: 'source · a person’s letter',
		loom: 'source · a loom fact',
		unconfirmed: 'source · a derived wish nobody confirmed yet',
		dangling: 'source · none on record'
	};
	const body = $derived.by(() => {
		if (!T) return '';
		if (T.me.kind === 'letter') return b.model?.byId.get(T.me.id)?.body ?? T.me.line;
		return T.me.line;
	});
	const wish = $derived(
		T?.me.kind === 'wish' ? b.model?.wishes.find((w) => w.wish === T.me.id) : undefined
	);
	const cls = (x: Bead) =>
		x.kind === 'letter'
			? x.from === 'loom'
				? 'k-loom'
				: String(x.from).startsWith('person:')
					? 'k-person'
					: 'k-resident'
			: 'k-' + x.kind;
	const title = (x: Bead) =>
		x.kind === 'letter'
			? `${
					x.from === 'loom'
						? 'loom noticed'
						: String(x.from || '')
								.replace(/^person:/, '')
								.split('/')[0] + ' wrote'
				} · ${hm(x.at)}`
			: x.kind === 'wish'
				? `wish · ${x.id}`
				: x.kind === 'thread'
					? `thread ${x.id.replace(/^thread:/, '')}`
					: x.kind === 'strand'
						? `strand ${short(x.id)} ran · on ${x.line}`
						: `${x.id} · not on record`;
</script>

<svelte:window onkeydown={(e) => e.key === 'Escape' && b.beadId && b.closeBead()} />

{#snippet row(x: Bead, extra: string, edge: string | null, j: number)}
	<!-- svelte-ignore a11y_click_events_have_key_events -->
	<div
		class="bd {cls(x)} {extra}"
		data-bead={x.id}
		role="button"
		tabindex="0"
		style:--j={j}
		onclick={() => b.openBead(x.id)}
	>
		<i></i>{#if edge}<span class="e">{edge} ↑</span>{/if}
		<div class="t"><span class="vb">{title(x)}</span></div>
		{#if x.line && x.kind !== 'strand'}<div class="x">{x.line}</div>{/if}
	</div>
{/snippet}

<aside
	id="bead"
	class:on={!!b.beadId}
	class:loading={!!b.beadId && !b.beads}
	aria-label="bead: where an item comes from and what came out of it"
>
	<div class="bh">
		<button
			type="button"
			class="back"
			title="back"
			disabled={!b.beadHist.length}
			onclick={() => b.back()}>‹</button
		>
		<b class="bid">{b.beadId || ''}</b><span class="rk lab">{T?.me.kind || ''}</span><span
			class="sp"
		></span>
		<button
			type="button"
			class="loc"
			title="show it on the board"
			onclick={() => b.beadId && b.locate(b.beadId)}>locate</button
		>
		<button type="button" class="x" title="close (esc)" onclick={() => b.closeBead()}>×</button>
	</div>
	<div class="bb">
		{#if T}
			<div class="up">
				<div class="src k-{T.root}">{ROOT[T.root]}</div>
				{#each T.chain as x, i (x.id)}{@render row(x, i === 0 ? 'root' : '', x.edge, i)}{/each}
				<div class="bd me {cls(T.me)}" class:root={!T.chain.length} data-bead={T.me.id}>
					<i></i>
					<div class="t"><span class="vb">{title(T.me)}</span></div>
					<div class="x"><Chips text={body} /></div>
					{#if wish}<div class="rail"><Pips w={wish} /></div>{/if}
				</div>
			</div>
			<div class="down">
				{#each T.out as x, i (x.id)}{@render row(x, 'out', null, T.chain.length + 1 + i)}{:else}<div
						class="none"
					>
						nothing came out of it yet
					</div>{/each}
			</div>
		{/if}
	</div>
</aside>
