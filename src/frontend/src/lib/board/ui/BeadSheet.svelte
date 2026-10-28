<script lang="ts">
	// The bead sheet (4920): the pressed item, the chain up to its source and what came out of it, all walked in the
	// client from the beads part (design §D3). Any bead in it re-centres it; ‹ walks back; esc closes.
	import { hm, short } from '../text.ts';
	import { refOf, trace, type Bead, type Root } from '../bead.ts';
	import { stages, stageText } from '../stages.ts';
	import { allCalls, answered, openAsks } from '../calls.ts';
	import Chips from './Chips.svelte';
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
	// a wish's five stages as one rail in pip order (m435g), then its trace by time under it
	const calls = $derived(allCalls(b.model?.threads || []));
	const asks = $derived(openAsks(calls));
	const rail = $derived(
		wish
			? stages(
					wish,
					asks,
					calls.some((c) => c.wish === wish.wish && answered(c, b.pending))
				)
			: []
	);
	const rows = $derived(T ? trace(wish, T) : []);
	const steps = $derived((b.model?.threads || []).flatMap((t) => t.steps || []));
	const ref = (r: string) => refOf(r, b.beads || {}, steps);
	// a rail time drops its day when the stage before it fell on the same one
	const when = $derived.by(() => {
		let day = '';
		return rail.map((s) => {
			const [d, h] = hm(s.at).split(' ');
			if (!h) return d;
			const r = d === day ? h : d + ' ' + h;
			day = d;
			return r;
		});
	});
	const STAGE = {
		heard: 'heard',
		read: 'read',
		granting: 'granting',
		delivered: 'delivered',
		agreed: 'agreed'
	};
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

{#snippet link(r: string)}
	{@const x = ref(r)}
	<!-- eslint-disable svelte/no-navigation-without-resolve -- a forge receipt -->
	{#if x.kind === 'url'}<a href={x.href} target="_blank" rel="noopener">{x.text}</a>
		<!-- eslint-enable svelte/no-navigation-without-resolve -->
	{:else if x.kind === 'bead'}<!-- svelte-ignore a11y_click_events_have_key_events --><span
			class="ref"
			data-bead={x.id}
			role="button"
			tabindex="0"
			title={x.id}
			onclick={() => b.openBead(x.id)}>{x.text}</span
		>
	{:else if x.kind === 'step'}<span class="sha">{x.sha}</span> {x.text}
	{:else}{x.text}{/if}
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
				</div>
				{#if wish}<div class="rail">
						{#each rail as s, i (s.k)}<div
								class="bd stage"
								class:not={!s.on}
								class:waitv={s.wait}
								data-stage={s.k}
								title={stageText(s)}
								style:--j={T.chain.length + 1 + i}
							>
								<i
									class="mk"
									class:on={s.on}
									class:cur={s.cur}
									class:wait={s.wait}
									class:x={s.mark === 'x'}
									class:c={s.mark === 'c'}
								></i><span class="vb"
									>{STAGE[s.k] + (s.mark === 'x' ? ' ✕' : s.mark === 'c' ? ' ↺' : '')}</span
								>
								<em
									>{s.at
										? when[i]
										: s.on
											? 'implied'
											: s.wait
												? asks.has(wish.wish)
													? 'ask'
													: 'verdict'
												: '·'}</em
								>
							</div>{/each}
					</div>{/if}
			</div>
			<div class="down">
				{#each rows as r, i (r.t === 'bead' ? r.bead.id : r.t + i)}
					{#if r.t === 'bead'}{@render row(r.bead, 'out', null, T.chain.length + 6 + i)}
					{:else}<div
							class="bd out trace {r.t === 'verdict' ? 'k-person' : 'k-loom'}"
							data-trace={r.t === 'stamp' ? r.k : r.t}
							style:--j={T.chain.length + 6 + i}
						>
							<i></i>
							<div class="t">
								<span class="vb"
									>{r.t === 'stamp'
										? r.k
										: r.t === 'verdict'
											? 'verdict · ' + r.as
											: 'result'}</span
								>{#if r.t !== 'result'}
									<em>· {hm(r.at)}{r.t === 'stamp' && r.by ? ' · ' + r.by : ''}</em>{/if}
							</div>
							{#if r.ref}<div class="x">{@render link(r.ref)}</div>{/if}
							{#if r.t === 'stamp' && r.line}<div class="x sl">{r.line}</div>{/if}
						</div>{/if}
				{:else}<div class="none">nothing came out of it yet</div>{/each}
			</div>
		{/if}
	</div>
</aside>
