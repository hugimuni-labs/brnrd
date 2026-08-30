<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import './layout.css';
	import { markBooted } from '$lib/boot';
	import favicon from '$lib/assets/favicon.svg';
	import {
		HOME_DESCRIPTION,
		HOME_TITLE,
		SOCIAL_IMAGE,
		canonicalUrl,
		isIndexablePath,
		normalizePathname
	} from '$lib/seo';

	let { children } = $props();

	let currentPath = $derived(normalizePathname(page.url.pathname));
	let indexable = $derived(isIndexablePath(currentPath));
	let canonical = $derived(canonicalUrl(currentPath));
	let isHome = $derived(currentPath === '/');

	// Boot glitch (kb/design-brand-visual-language.md §3): a real spec,
	// named in enough detail to be checkable, never built until this pass.
	// `_` -> `b_d` -> `br_rd` -> `brnrd` -glitch-> `bRnЯd` — each frame adds
	// one letter-pair symmetrically around the underscore cursor (the
	// mirror axis `b`/`d` and `R`/`Я` already share), then the final frame
	// gets a brief chromatic flicker before the overlay lifts. Skipped
	// entirely under prefers-reduced-motion rather than just shortened —
	// the letters-converging motion *is* the content here, there's no
	// reduced-but-still-meaningful version of it.
	const FRAMES = ['_', 'b_d', 'br_rd', 'brnrd', 'bRnЯd'];
	const FRAME_MS = 190;

	let booting = $state(false);
	let frameIndex = $state(0);
	let flicker = $state(false);

	onMount(() => {
		const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
		// No curtain under reduced motion, so nothing is waiting on one.
		if (reduced) {
			markBooted();
			return;
		}
		booting = true;
		let i = 0;
		const step = () => {
			i += 1;
			if (i >= FRAMES.length) {
				flicker = true;
				setTimeout(() => {
					booting = false;
					// The text reveal is held until here: playing it behind an opaque
					// overlay is the same as not playing it (see `$lib/boot`).
					markBooted();
				}, 260);
				return;
			}
			frameIndex = i;
			if (i === FRAMES.length - 1) flicker = true;
			setTimeout(step, FRAME_MS);
		};
		setTimeout(step, FRAME_MS);
	});
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
	{#if indexable}
		<link rel="canonical" href={canonical} />
		<meta name="robots" content="index,follow,max-image-preview:large" />
		<meta property="og:url" content={canonical} />
	{:else}
		<meta name="robots" content="noindex,nofollow" />
	{/if}
	{#if isHome}
		<title>{HOME_TITLE}</title>
		<meta name="description" content={HOME_DESCRIPTION} />
		<meta property="og:title" content={HOME_TITLE} />
		<meta property="og:description" content={HOME_DESCRIPTION} />
		<meta property="og:image" content={SOCIAL_IMAGE} />
		<meta name="twitter:title" content={HOME_TITLE} />
		<meta name="twitter:description" content={HOME_DESCRIPTION} />
		<meta name="twitter:image" content={SOCIAL_IMAGE} />
	{/if}
</svelte:head>

{#if booting}
	<div
		class="fixed inset-0 z-100 flex items-center justify-center bg-[#0c0906] transition-opacity duration-300"
	>
		<span class={`boot-glitch ${flicker ? 'is-flicker' : ''}`}>{FRAMES[frameIndex]}</span>
	</div>
{/if}
{@render children()}
