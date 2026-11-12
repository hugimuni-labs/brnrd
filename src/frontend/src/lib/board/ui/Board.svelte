<script lang="ts">
	import RenderScope from './RenderScope.svelte';
	// The board (design §C): every thread as a column, the inbox pinned left, the bead sheet on the right, the composer
	// below. Data is the loom's parts (design §D1), live over SSE; the order moves at most once per 10 s and holds while
	// the sheet is open or the pointer is on a thread column (order.ts arrange).
	import { onMount, tick as flushed, untrack } from 'svelte';
	import { resolve } from '$app/paths';
	import { beforeNavigate } from '$app/navigation';
	import { page, updated } from '$app/state';
	import { version } from '$app/environment';
	import { site } from '$lib/site';
	import './board.css';
	import { arrange, bst, byThread, SETTLED, sortThreads, wanted } from '../order.ts';
	import { headerFuel, leadName, machinesTitle } from '../parts.ts';
	import { barGauge } from '../../loomSnapshot.ts';
	import FuelMarks from '../../FuelMarks.svelte';
	import { INBOX } from '../sides.ts';
	import { dur, hue, secs } from '../text.ts';
	import { liveOf, what } from '../tape.ts';
	import { face, LIVE, LOG, moves, seen, waitOf, type Seen } from '../rail.ts';
	import Column from './Column.svelte';
	import Topics from './Topics.svelte';
	import Stack from './Stack.svelte';
	import BeadSheet from './BeadSheet.svelte';
	import Links from './Links.svelte';
	import Composer from './Composer.svelte';
	import { Board, provide } from './board.svelte.ts';
	import type { Held } from '../cache.ts';
	// `held`: the last page's parts from the browser's cache (cache.ts), so the board paints before the network answers
	let {
		get,
		held = null,
		view = 'board'
	}: {
		get?: (path: string) => Promise<unknown>;
		held?: Held | null;
		view?: 'board' | 'topics';
	} = $props();
	const b = provide(untrack(() => new Board(get, held)));

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
	// The face tick also moves the reset wedges. Reading `tick` is what subscribes.
	const fuelNow = $derived.by(() => {
		void tick;
		return Date.now();
	});
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

	// Where he was (w-kkghfg2vo6do, his 10361): the place is written as the page hides and a second after a scroll or a
	// press; the board's horizontal scroll comes back once the columns stand.
	let main: HTMLElement | undefined = $state();
	let saver: ReturnType<typeof setTimeout> | null = null;
	const remember = () => {
		if (view === 'board') {
			b.savePlace();
			b.keep();
		}
	};
	beforeNavigate(remember);
	const save = () => {
		if (view !== 'board') return;
		if (saver) clearTimeout(saver);
		saver = setTimeout(() => ((saver = null), b.savePlace()), 1000);
	};
	$effect(() => {
		void b.beadId;
		void b.sel;
		void b.grown.size;
		void b.open.size;
		if (b.model) untrack(save);
	});
	let landedX = false;
	$effect(() => {
		if (landedX || !main || !order.length) return;
		landedX = true;
		const x = b.place.x;
		if (x) flushed().then(() => main && (main.scrollLeft = x));
	});
	onMount(() => {
		// e2e reads the board's state here (src/frontend/e2e), as the board.html proofs read its globals
		(window as unknown as { __board?: Board }).__board = b;
		b.load();
		const faces = setInterval(() => tick++, 400);
		const off = b.connect();
		const hide = () => document.hidden && remember();
		document.addEventListener('visibilitychange', hide);
		window.addEventListener('pagehide', remember);
		return () => {
			// A render boundary can unmount the board before pagehide saves the draft.
			b.keep();
			clearInterval(faces);
			if (saver) clearTimeout(saver);
			if (timer) clearTimeout(timer);
			off();
			document.removeEventListener('visibilitychange', hide);
			window.removeEventListener('pagehide', remember);
		};
	});
</script>

<div class="brd" class:sheet={!!b.beadId}>
	<header>
		{#if view === 'board'}
			<a class="topics-link" href={resolve('/topics')}>topics</a>
		{:else}
			<a class="topics-link" href={resolve('/')}>← board</a>
		{/if}
		<b>fuel</b>
		<span id="fuel" title={headerFuel(b.model?.fuel)}>
			{#each b.model?.fuel ?? [] as shell (shell.shell)}
				{@const bar = barGauge(shell.gauges)}
				<span class="shell" data-shell={shell.shell} data-bar={bar?.window ?? ''}>
					<span class="name">{shell.shell}</span>
					{#if shell.gauges?.length}
						<FuelMarks gauges={shell.gauges} now={fuelNow} />
					{:else if shell.error}
						<span class="fail">{shell.error}</span>
					{/if}
				</span>
			{/each}
		</span>
		{#if leadName(b.model?.machines)}
			<span id="lead" class="dim" title={machinesTitle(b.model?.machines)}
				>leads <b>{leadName(b.model?.machines)}</b></span
			>
		{/if}
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
	{#if view === 'board'}
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
					{r.t.id} · {r.s.id.slice(-6)} · {waitOf(r.t, r.s.state)}{#if r.lv}<em
							data-since={r.lv.since}
							>{` · ${r.lv.verb}${r.lv.since ? ' ' + dur(secs(r.lv.since, new Date(b.now).toISOString())) : ''}`}</em
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
		<main id="board" bind:this={main} onscrollcapture={save}>
			{#if !b.model}<div class="empty">{b.error || 'loading the board'}</div>{/if}
			{#each order as id (id)}
				{#if id === INBOX}<RenderScope name="Column"
						><Column t={inbox} inbox letters={b.letters} /></RenderScope
					>
				{:else if id === SETTLED}<RenderScope name="Stack"><Stack {settled} {by} /></RenderScope>
				{:else if byId.get(id)}<RenderScope name="Column"
						><Column t={byId.get(id)!} letters={by.get(id) || []} /></RenderScope
					>{/if}
			{/each}
		</main>
		<RenderScope name="Links"><Links /></RenderScope>
	{:else}
		<main class="topics-page"><RenderScope name="Topics"><Topics /></RenderScope></main>
	{/if}
	<RenderScope name="BeadSheet"><BeadSheet /></RenderScope>
	{#if view === 'board'}<RenderScope name="Composer"><Composer /></RenderScope>{/if}
	<footer>
		{#if view === 'board'}← strands · you → / oldest → newest ↓ in every column / tap a heading to
			grow the plan / tap a letter to link it across columns / the input writes to the inbox as you
			/
		{:else}topics · tap a page to unfold its newest entry /
		{/if} <span data-page-version>{version.slice(0, 8)}</span>
		{#if $site?.tree && $site.tree !== version}
			/
			<!-- eslint-disable svelte/no-navigation-without-resolve -- reload this document, including its query -->
			<a
				href={page.url.href}
				onclick={(e) => {
					e.preventDefault();
					window.location.reload();
				}}>newer page on the server, reload</a
			>
			<!-- eslint-enable svelte/no-navigation-without-resolve -->
		{/if}
	</footer>
</div>
