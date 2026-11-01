<script lang="ts">
	// The board (design §C): every thread as a column, the inbox pinned left, the bead sheet on the right, the composer
	// below. Data is the loom's parts (design §D1), live over SSE; the order moves at most once per 10 s and holds while
	// the sheet is open or the pointer is on a thread column (order.ts arrange).
	import { onMount, untrack } from 'svelte';
	import { updated } from '$app/state';
	import './board.css';
	import { arrange, bst, byThread, SETTLED, sortThreads, wanted } from '../order.ts';
	import { INBOX } from '../sides.ts';
	import { dur, hue, secs } from '../text.ts';
	import { liveOf, what } from '../tape.ts';
	import { face, LIVE, LOG, moves, seen, type Seen } from '../rail.ts';
	import Column from './Column.svelte';
	import Stack from './Stack.svelte';
	import BeadSheet from './BeadSheet.svelte';
	import Links from './Links.svelte';
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
			if (!s || t.status === 'settled' || !LIVE.includes(s.state || '')) return [];
			return [{ t, s, lv: liveOf(s, b.model!.live) }];
		})
	);
	// A strand whose state moved since the last index flashes and logs `HH:MM:SS thread was→now` (the last six).
	let prev: Seen | null = null;
	let flash = $state(new Set<string>()),
		railLog = $state<string[]>([]),
		tick = $state(0);
	$effect(() => {
		const threads = b.model?.threads;
		if (!threads) return;
		untrack(() => {
			const now = seen(threads),
				m = moves(prev, now, new Date().toISOString());
			prev = now;
			if (!m.changed.size) return;
			railLog = [...m.lines, ...railLog].slice(0, LOG);
			flash = m.changed;
			// on, then off two frames later, so the span fades from the flash over its 3 s transition
			requestAnimationFrame(() => requestAnimationFrame(() => (flash = new Set())));
		});
	});

	// A newer page (v44hse5kpf, 4912; his steer 4a46d017): never reloaded for him. A tiny `update` mark beside the
	// connection takes it on a click, the draft and its armed wish riding sessionStorage across; a fetch failing on
	// old code makes the mark loud, and still waits for his click.
	$effect(() => {
		if (updated.current) b.newer = true;
	});
	const update = () => {
		b.keep();
		location.reload();
	};

	onMount(() => {
		// e2e reads the board's state here (src/frontend/e2e), as the board.html proofs read its globals
		(window as unknown as { __board?: Board }).__board = b;
		b.load();
		const faces = setInterval(() => tick++, 400);
		const off = b.connect();
		return () => (clearInterval(faces), off());
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
			>loom {b.heard.slice(11, 19)}Z · {b.letters.length} letters · as {b.model?.person ||
				'?'}</span
		>
		<span id="live" class="dim" class:off={b.live === 'offline'}>{b.error || b.live}</span>
		{#if b.newer}<button
				id="update"
				class:loud={!!b.error}
				title="a newer page is out: load it (the draft stays)"
				onclick={update}>update</button
			>{/if}
		<label id="internal" class="dim"
			><input type="checkbox" bind:checked={b.showAll} onchange={() => b.persist()} /> show internal</label
		>
	</header>
	<nav id="rail" aria-label="strands">
		{#each rail as r (r.s.id)}<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
			<span
				data-thread={r.t.id}
				data-strand={r.s.id}
				class:flash={flash.has(r.s.id)}
				style:--h={hue(r.t.id)}
				onclick={() => b.openBead(r.s.id)}
				>{#if r.t.board?.state === 'seated'}<b class="pulse"></b>{/if}<i data-state={r.s.state}
					>{face(b.model?.faces, r.s.state, tick)}</i
				>
				{r.t.id} · {r.s.id.slice(-6)} · {r.s.state}{#if r.lv}<em data-since={r.lv.since}
						>{` · ${r.lv.verb} ${dur(secs(r.lv.since, new Date(b.now).toISOString()))}`}</em
					>{#if r.lv.open[0]}{` · ${what(r.lv.open[0]).slice(0, 40)}`}{/if}{/if}</span
			>{/each}{#each railLog as x, i (i)}<span class="log">{x}</span>{/each}
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
	<Links />
	<BeadSheet />
	<Composer />
	<footer>
		← strands · you → / oldest → newest ↓ in every column / tap a heading to grow the plan / tap a
		letter to link it across columns / the input writes to the inbox as you
	</footer>
</div>
