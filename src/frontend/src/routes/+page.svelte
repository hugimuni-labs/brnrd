<script lang="ts">
	// The resident dashboard — the main UI, in one place. The page body moved
	// to `$lib/Dashboard.svelte` on 2026-08-31 so `/daily` could wear the same
	// interface instead of growing a second, half-shaped one beside it
	// (maintainer: "have on the daily the whole main user interface, the
	// visual, like exactly as it is"). This route is the URL and nothing else;
	// its render is the component's default (`liveView="field"`), which is the
	// exact shape this file held before the extraction.
	//
	// 2026-10-19: `/` is the loom's home board when the loom has published a
	// snapshot, and this same dashboard otherwise (`$lib/LoomHome.svelte`
	// decides). The dashboard with the command deck stays at /bridge.
	import LoomHome from '$lib/LoomHome.svelte';
	import { HOME_DESCRIPTION, HOME_TITLE, SOCIAL_IMAGE } from '$lib/seo';
	import { onMount } from 'svelte';
	import Board from '$lib/board/ui/Board.svelte';
	import Landing from '$lib/Landing.svelte';
	import BoardConsent from '$lib/BoardConsent.svelte';
	import { BASE, getJSON } from '$lib/board/parts';
	import { cached, forget, restore, type Held } from '$lib/board/cache';

	// 2026-10-27 (web-main): `/` becomes the board. 2026-10-28: on brnrd.dev too, for a signed-in person whose loom
	// pushes its parts (/v1/loom/* in the cloud). It is shown only when /v1/loom/index answers a board: signed out
	// (401), no parts pushed (404), or a dev server on localhost with no loom behind it keeps the page below.
	// localStorage `board` = 0 skips the board.
	// 2026-10-29: until that is decided the page draws the landing, so the prerendered body carries it (h1, install
	// line) instead of nothing; a signed-in reader sees it for one probe (accepted, inbox xaj18). Signed out (401) it
	// stays mounted: the old route through LoomHome and Dashboard repainted it after two "reaching" waits.
	// The page below is mounted only once decided: it fetches /v1/dashboard/* (404s on a loopback loom).
	// The board then drops the app's body chrome (layout.css grain).
	// 2026-10-29 (w-g98svlo7bhrb): signed in with no board, /v1/loom/consent decides. A loom never asked shows the
	// prompt, a no shows a plain page, a yes waits for the first push; none of them is the old app. Only an account
	// with no loom (state `none`), or a server without the route (a loopback loom, a dev server), keeps the page below.
	// 2026-11-06 (w-kkghfg2vo6do, his 10361): the phone drops the tab on every switch to Telegram. The last page's parts
	// are in the browser's cache (lib/board/cache.ts): when it holds a board, the board paints from it before the probe
	// answers, and the board's first read of the index is the probe itself. The probe still decides: a 401 or no board
	// forgets the cache and the page goes where it always went. `holding` keeps the landing off that first frame.
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
		const probe =
			flag === '0'
				? Promise.resolve(null)
				: fetch(`${BASE}/index`, { cache: 'no-store' })
						.then((r) => (r.status === 401 ? 'anon' : r.ok ? r.json() : null))
						.catch(() => null);
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
		probe.then(async (j) => {
			if (gone) return;
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

{#if mode === 'anon' || (mode === '?' && !holding)}
	<div data-landing class="contents"><Landing live={mode === 'anon'} /></div>
{:else if mode === 'board'}
	<Board {get} {held} />
{:else if mode === 'ask' || mode === 'declined' || mode === 'waiting'}
	<BoardConsent step={mode} {lastSeen} {slow} {busy} {failed} {answer} />
{:else if mode === 'home'}
	<div data-home class="contents"><LoomHome /></div>
{/if}
