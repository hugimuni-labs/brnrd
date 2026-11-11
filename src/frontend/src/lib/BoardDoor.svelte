<script lang="ts">
	import LoomHome from '$lib/LoomHome.svelte';
	import { site, loadSite } from '$lib/site';
	import { HOME_DESCRIPTION, HOME_TITLE, SOCIAL_IMAGE } from '$lib/seo';
	import { onMount } from 'svelte';
	import Board from '$lib/board/ui/Board.svelte';
	import Landing from '$lib/Landing.svelte';
	import BoardConsent from '$lib/BoardConsent.svelte';
	import { BASE, getJSON } from '$lib/board/parts';
	import { cached, forget, restore, type Held } from '$lib/board/cache';

	// One bundle, two doors. /v1/site declares the loopback: it always draws the board, whose own loading state
	// handles missing parts. Cloud and older backends retain the index/sign-in/consent flow. The last page's cached
	// parts paint independently of the declaration; a cloud 401 still forgets them.
	let { view = 'board' }: { view?: 'board' | 'topics' } = $props();
	type Step = 'ask' | 'declined' | 'waiting';
	let mode = $state<'?' | 'anon' | 'board' | 'home' | Step>('?');
	let holding = $state(cached());
	let held = $state<Held | null>(null);
	let pending: Promise<unknown> | null = null;
	let lastSeen = $state<string | null>(null);
	let slow = $state(false);
	let busy = $state(false);
	let failed = $state(false);
	let gone = false;
	let timers: ReturnType<typeof setTimeout>[] = [];
	const STEPS: Record<string, Step> = { ask: 'ask', declined: 'declined', granted: 'waiting' };
	const show = (j: unknown) => {
		index = j;
		mode = 'board';
		document.documentElement.classList.add('board');
	};
	const isBoard = (j: unknown) => !!j && typeof j === 'object' && 'parts' in j;
	// after a yes: the index every 2 s until it answers a board; past 60 s the page says when the loom was last seen
	const wait = () => {
		mode = 'waiting';
		slow = false;
		const began = Date.now();
		const poll = () =>
			fetch(`${BASE}/index`, { cache: 'no-store' })
				.then((r) => (r.ok ? r.json() : null))
				.catch(() => null)
				.then(async (j) => {
					if (gone || mode !== 'waiting') return;
					if (isBoard(j)) return show(j);
					if (!slow && Date.now() - began >= 60_000) {
						slow = true;
						const c = await consent();
						lastSeen = c?.last_seen_at ?? null;
					}
					timers.push(setTimeout(poll, 2000));
				});
		poll();
	};
	const consent = () =>
		fetch(`${BASE}/consent`, { cache: 'no-store' })
			.then((r) => (r.ok ? r.json() : null))
			.catch(() => null);
	const answer = async (send: boolean) => {
		busy = true;
		failed = false;
		const r = await fetch(`${BASE}/consent`, {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify({ send })
		}).catch(() => null);
		busy = false;
		if (!r?.ok) return void (failed = true);
		if (send) wait();
		else mode = 'declined';
	};
	let index: unknown = null;
	// the board's first read of the index is the answer the probe already holds, or the probe still in flight
	const get = (path: string) => {
		if (path !== `${BASE}/index`) return getJSON(path);
		const had = index;
		index = null;
		if (had) return Promise.resolve(had);
		const p = pending;
		pending = null;
		if (!p) return getJSON(path);
		return p.then((j) => {
			if (isBoard(j)) return j;
			throw new Error(`${path}: ${j === 'anon' ? 'HTTP 401' : 'no board'}`);
		});
	};
	onMount(() => {
		const flag = localStorage.getItem('board');
		gone = false;
		const deployment = loadSite();
		// in parallel with /v1/site: waiting for it would put a second round trip before a signed-in phone's board
		const readIndex = () =>
			fetch(`${BASE}/index`, { cache: 'no-store' })
				.then((r) => (r.status === 401 ? 'anon' : r.ok ? r.json() : null))
				.catch(() => null);
		const probe =
			flag === '0'
				? deployment.then((s) => (s?.deployment === 'loopback' ? readIndex() : null))
				: readIndex();
		// The last page's parts, when the browser holds them (a few ms to read). Whichever answers first paints the
		// board: the cache alone (the probe becomes the board's first index read), or the probe with the cache primed
		// behind it, so the thread parts are fetched only where their hash moved. The parts, not the index, are the
		// seconds on a phone.
		const fromCache: Promise<Held | null> =
			flag !== '0' && cached() ? restore().catch(() => null) : Promise.resolve(null);
		fromCache.then((h) => {
			if (gone || mode !== '?') return;
			if (!h) return void (holding = false);
			held = h;
			pending = probe;
			mode = 'board';
			document.documentElement.classList.add('board');
		});
		deployment.then(async (s) => {
			if (s?.deployment !== 'loopback') return;
			const h = await fromCache;
			if (gone || mode === 'board') return;
			held = h;
			pending = probe;
			mode = 'board';
			document.documentElement.classList.add('board');
		});
		probe.then(async (j) => {
			const s = await deployment;
			if (gone || s?.deployment === 'loopback') return;
			if (isBoard(j)) {
				const h = await fromCache;
				if (gone || mode === 'board') return;
				held = h;
				return show(j);
			}
			// not his board: whatever the cache held is not to be shown again on this browser
			held = null;
			holding = false;
			if (mode === 'board') mode = '?';
			void forget();
			if (j === 'anon') return void (mode = 'anon');
			const c = flag === '0' ? null : await consent();
			if (gone) return;
			const step = STEPS[c?.state];
			lastSeen = c?.last_seen_at ?? null;
			if (step === 'waiting') wait();
			else mode = step ?? 'home';
		});
		return () => {
			gone = true;
			timers.forEach(clearTimeout);
			document.documentElement.classList.remove('board');
		};
	});
</script>

<svelte:head>
	<title>{HOME_TITLE}</title>
	<meta name="description" content={HOME_DESCRIPTION} />
	<meta property="og:title" content={HOME_TITLE} />
	<meta property="og:description" content={HOME_DESCRIPTION} />
	<meta property="og:image" content={SOCIAL_IMAGE} />
	<meta name="twitter:title" content={HOME_TITLE} />
	<meta name="twitter:description" content={HOME_DESCRIPTION} />
	<meta name="twitter:image" content={SOCIAL_IMAGE} />
</svelte:head>

{#if mode === 'anon' || (mode === '?' && !holding && $site?.deployment !== 'loopback')}
	<div data-landing class="contents"><Landing live={mode === 'anon'} /></div>
{:else if mode === 'board'}
	<Board {get} {held} {view} />
{:else if mode === 'ask' || mode === 'declined' || mode === 'waiting'}
	<BoardConsent step={mode} {lastSeen} {slow} {busy} {failed} {answer} />
{:else if mode === 'home'}
	<div data-home class="contents"><LoomHome /></div>
{/if}
