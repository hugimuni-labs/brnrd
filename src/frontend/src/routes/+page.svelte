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
	import { BASE, getJSON } from '$lib/board/parts';

	// 2026-10-27 (web-main): `/` becomes the board. 2026-10-28: on brnrd.dev too, for a signed-in person whose loom
	// pushes its parts (/v1/loom/* in the cloud). It is shown only when /v1/loom/index answers a board: signed out
	// (401), no parts pushed (404), or a dev server on localhost with no loom behind it keeps the page below.
	// localStorage `board` = 0 skips the board.
	// Nothing renders until that is decided: mounting the page below first would fetch /v1/dashboard/* (404s on a
	// loopback loom) before the board replaced it. The board then drops the app's body chrome (layout.css grain).
	let mode = $state<'?' | 'board' | 'home'>('?');
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
		const probe = flag === '0' ? Promise.resolve(null) : getJSON(`${BASE}/index`).catch(() => null);
		probe.then((j) => {
			if (gone) return;
			index = j && typeof j === 'object' && 'parts' in j ? j : null;
			mode = index ? 'board' : 'home';
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

{#if mode === 'board'}
	<Board {get} />
{:else if mode === 'home'}
	<LoomHome />
{/if}
