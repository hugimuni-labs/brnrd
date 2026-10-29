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
	import { BASE, getJSON } from '$lib/board/parts';

	// 2026-10-27 (web-main): `/` becomes the board. 2026-10-28: on brnrd.dev too, for a signed-in person whose loom
	// pushes its parts (/v1/loom/* in the cloud). It is shown only when /v1/loom/index answers a board: signed out
	// (401), no parts pushed (404), or a dev server on localhost with no loom behind it keeps the page below.
	// localStorage `board` = 0 skips the board.
	// 2026-10-29: until that is decided the page draws the landing, so the prerendered body carries it (h1, install
	// line) instead of nothing; a signed-in reader sees it for one probe (accepted, inbox xaj18). Signed out (401) it
	// stays mounted: the old route through LoomHome and Dashboard repainted it after two "reaching" waits.
	// The page below is mounted only once decided: it fetches /v1/dashboard/* (404s on a loopback loom).
	// The board then drops the app's body chrome (layout.css grain).
	let mode = $state<'?' | 'anon' | 'board' | 'home'>('?');
	let index: unknown = null;
	// the board's first read of the index is the answer the probe already holds
	const get = (path: string) => {
		const held = path === `${BASE}/index` ? index : null;
		index = null;
		return held ? Promise.resolve(held) : getJSON(path);
	};
	onMount(() => {
		const flag = localStorage.getItem('board');
		let gone = false;
		const probe =
			flag === '0'
				? Promise.resolve(null)
				: fetch(`${BASE}/index`, { cache: 'no-store' })
						.then((r) => (r.status === 401 ? 'anon' : r.ok ? r.json() : null))
						.catch(() => null);
		probe.then((j) => {
			if (gone) return;
			index = j && typeof j === 'object' && 'parts' in j ? j : null;
			mode = index ? 'board' : j === 'anon' ? 'anon' : 'home';
			if (index) document.documentElement.classList.add('board');
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

{#if mode === '?' || mode === 'anon'}
	<div data-landing class="contents"><Landing live={mode === 'anon'} /></div>
{:else if mode === 'board'}
	<Board {get} />
{:else if mode === 'home'}
	<div data-home class="contents"><LoomHome /></div>
{/if}
