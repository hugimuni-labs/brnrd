<script lang="ts">
	import RenderScope from './board/ui/RenderScope.svelte';
	import HomeAccess from '$lib/HomeAccess.svelte';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { site, loadSite } from '$lib/site';
	import { HOME_DESCRIPTION, HOME_TITLE, SOCIAL_IMAGE } from '$lib/seo';
	import { onMount } from 'svelte';
	import Board from '$lib/board/ui/Board.svelte';
	import BoardRecovery from '$lib/board/ui/BoardRecovery.svelte';
	import Landing from '$lib/Landing.svelte';
	import { BASE, leaving, probeBoard } from '$lib/board/parts';
	import { cached, forget, restore, type Held } from '$lib/board/cache';

	// One bundle, two doors. /v1/site declares the loopback: it always draws the board, whose own loading state
	// handles missing parts. Cloud and older backends retain the index/sign-in/consent flow. The last page's cached
	// parts paint independently of the declaration; a cloud 401 still forgets them.
	let { view = 'board' }: { view?: 'board' | 'topics' } = $props();
	let renderError = $state('');
	let mode = $state<'?' | 'anon' | 'board' | 'refused' | 'error' | 'account'>('?');
	let holding = $state(cached());
	let held = $state<Held | null>(null);
	let pending: ReturnType<typeof probeBoard> | null = null;
	let gone = false;
	function refuse() {
		if (gone) return;
		held = null;
		void forget();
		mode = 'refused';
		document.documentElement.classList.add('board');
	}
	const show = (j: unknown) => {
		index = j;
		mode = 'board';
		document.documentElement.classList.add('board');
	};
	async function readPart(path: string) {
		const response = await fetch(path, { cache: 'no-store', signal: leaving() });
		if (response.status === 402) refuse();
		if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
		return response.json();
	}
	let index: unknown = null;
	// the board's first read of the index is the answer the probe already holds, or the probe still in flight
	const get = (path: string) => {
		if (path !== `${BASE}/index`) return readPart(path);
		const had = index;
		index = null;
		if (had) return Promise.resolve(had);
		const p = pending;
		pending = null;
		if (!p) return readPart(path);
		return p.then((j) => {
			if (j.kind === 'ready') return j.index;
			throw new Error(`${path}: ${j.kind === 'anon' ? 'HTTP 401' : 'no board'}`);
		});
	};
	onMount(() => {
		gone = false;
		const deployment = loadSite();
		// in parallel with /v1/site: waiting for it would put a second round trip before a signed-in phone's board
		const probe = probeBoard();
		// The last page's parts, when the browser holds them (a few ms to read). Whichever answers first paints the
		// board: the cache alone (the probe becomes the board's first index read), or the probe with the cache primed
		// behind it, so the thread parts are fetched only where their hash moved. The parts, not the index, are the
		// seconds on a phone.
		const fromCache: Promise<Held | null> = cached()
			? restore().catch(() => null)
			: Promise.resolve(null);
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
			if (j.kind === 'ready') {
				const h = await fromCache;
				if (gone || mode === 'board') return;
				held = h;
				return show(j.index);
			}
			// not his board: whatever the cache held is not to be shown again on this browser
			held = null;
			holding = false;
			if (mode === 'board') mode = '?';
			void forget();
			if (j.kind === 'anon') return void (mode = 'anon');
			if (j.kind === 'refused') return refuse();
			if (j.kind === 'missing') {
				mode = 'account';
				void goto(resolve('/account'), { replaceState: true });
				return;
			}
			renderError = 'Could not load your board.';
			mode = 'error';
		});
		return () => {
			gone = true;
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
	<svelte:boundary
		onerror={(error) => {
			console.error('[board render]', error, error instanceof Error ? error.stack : '');
			renderError = error instanceof Error ? error.message : String(error);
		}}
	>
		<RenderScope name="Board"><Board {get} {held} {view} onRefused={refuse} /></RenderScope>
		{#snippet failed()}<BoardRecovery message={renderError} />{/snippet}
	</svelte:boundary>
{:else if mode === 'refused'}
	<main class="mx-auto max-w-xl p-6"><HomeAccess refused /></main>
{:else if mode === 'error'}
	<BoardRecovery message={renderError} />
{/if}
