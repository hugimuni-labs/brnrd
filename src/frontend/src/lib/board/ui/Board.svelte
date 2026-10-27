<script lang="ts">
	// The board (design §C): every thread as a column, the inbox pinned left, the bead sheet on the right, the composer
	// below. Data is the loom's parts (design §D1), live over SSE; the order moves at most once per 10 s and holds while
	// the sheet is open or the pointer is on a thread column (order.ts arrange).
	import { onMount, untrack } from 'svelte';
	import { updated } from '$app/state';
	import './board.css';
	import { arrange, bst, byThread, SETTLED, sortThreads, wanted } from '../order.ts';
	import { INBOX } from '../sides.ts';
	import { hue } from '../text.ts';
	import { liveOf } from '../tape.ts';
	import Column from './Column.svelte';
	import Stack from './Stack.svelte';
	import BeadSheet from './BeadSheet.svelte';
	import Composer from './Composer.svelte';
	import { Board, provide } from './board.svelte.ts';
	let { get }: { get?: (path: string) => Promise<unknown> } = $props();
	const b = provide(untrack(() => new Board(get)));

	const by = $derived(byThread(b.letters));
	const sorted = $derived(sortThreads(b.model?.threads || [], by));
	const byId = $derived(new Map(sorted.map((t) => [t.id, t])));
	const inbox = $derived(b.model?.threads.find((t) => t.id === INBOX) || { id: INBOX });
	const settled = $derived(sorted.filter((t) => bst(t) === 'settled'));

	let order = $state<string[]>([]);
	let last = 0,
		timer: ReturnType<typeof setTimeout> | null = null,
		first = true;
	const want = $derived(wanted(sorted, b.settledOpen));
	function place() {
		const r = arrange(want, order, {
			first,
			held: !!b.beadId || !!b.hovered,
			now: Date.now(),
			last
		});
		first = false;
		last = r.last;
		if (r.ids.join() !== order.join()) order = r.ids;
		if (r.wait != null && !timer) timer = setTimeout(() => ((timer = null), place()), r.wait);
	}
	$effect(() => {
		void want;
		void b.beadId;
		void b.hovered;
		if (b.model) untrack(place);
	});

	const rail = $derived(
		(b.model?.threads || []).flatMap((t) => {
			const s = (t.strands || []).slice(-1)[0];
			if (
				!s ||
				t.status === 'settled' ||
				!['working', 'holding', 'idle', 'parked', 'failed'].includes(s.state || '')
			)
				return [];
			return [{ t, s, lv: liveOf(s, b.model!.live) }];
		})
	);

	// A newer page (v44hse5kpf, 4912): reload at once, unless the box has focus and holds text; then say so, and
	// leaving the box takes it. The draft and its armed wish ride sessionStorage across the reload either way.
	$effect(() => {
		if (updated.current) b.newer = true;
	});
	$effect(() => {
		if (b.newer && !(b.writing && b.draft.trim())) {
			b.keep();
			location.reload();
		}
	});

	onMount(() => {
		// e2e reads the board's state here (src/frontend/e2e), as the board.html proofs read its globals
		(window as unknown as { __board?: Board }).__board = b;
		b.load();
		return b.connect();
	});
</script>

<div class="brd" class:sheet={!!b.beadId}>
	<header>
		<b>fuel</b><span id="fuel"
			>{(b.model?.fuel || [])
				.map(
					(f) => `${f.shell} ${f.gauges.map((g) => g.window + ' ' + g.left_pct + '%').join(' ')}`
				)
				.join('   ')}</span
		>
		<span id="open" style="color:#d33">{b.letters.filter((l) => l.open).length} open</span>
		<span class="dim" id="at"
			>{b.letters.length} letters · as {b.model?.person || '?'}{#if b.model?.via}
				via {b.model.via}{/if}</span
		>
		<span id="live" class="dim" class:off={b.live === 'offline'}
			>{b.error || (b.newer ? 'a newer page · leave the box to take it' : b.live)}</span
		>
		<label id="internal" class="dim"
			><input type="checkbox" bind:checked={b.showAll} onchange={() => b.persist()} /> show internal</label
		>
	</header>
	<nav id="rail" aria-label="strands">
		{#each rail as r (r.s.id)}<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
			<span
				data-thread={r.t.id}
				data-strand={r.s.id}
				style:--h={hue(r.t.id)}
				onclick={() => b.openBead(r.s.id)}
				>{#if r.t.board?.state === 'seated'}<b class="pulse"></b>{/if}{r.t.id} · {r.s.id.slice(-6)} ·
				{r.s.state}{#if r.lv}<em> · {r.lv.verb}</em>{/if}</span
			>{/each}
	</nav>
	<nav id="tabs" aria-label="columns">
		{#each order as id (id)}<a
				href="#{id}"
				style:--h={hue(id)}
				onclick={(e) => (
					e.preventDefault(),
					document
						.querySelector(`[data-column="${CSS.escape(id)}"]`)
						?.scrollIntoView({ inline: 'start', behavior: 'smooth' })
				)}>{id === SETTLED ? 'settled' : id}</a
			>{/each}
	</nav>
	<main id="board">
		{#if !b.model}<div class="empty">{b.error || 'loading the board'}</div>{/if}
		{#each order as id (id)}
			{#if id === INBOX}<Column t={inbox} inbox letters={b.letters} />
			{:else if id === SETTLED}<Stack {settled} {by} />
			{:else if byId.get(id)}<Column t={byId.get(id)!} letters={by.get(id) || []} />{/if}
		{/each}
	</main>
	<BeadSheet />
	<Composer />
	<footer>
		← strands · you → / oldest → newest ↓ in every column / tap a heading to grow the plan / the
		input writes to the inbox as you
	</footer>
</div>
