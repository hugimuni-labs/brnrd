<script lang="ts">
	import { onMount } from 'svelte';
	import { afterNavigate } from '$app/navigation';
	import { page } from '$app/state';
	import './layout.css';
	import { markBooted } from '$lib/boot';
	import { countPublicPageview, isAnalyticsPath } from '$lib/analytics';
	import favicon from '$lib/assets/favicon.svg';
	import { canonicalUrl, hasCanonicalMeta, isIndexablePath, normalizePathname } from '$lib/seo';

	let { children } = $props();

	let currentPath = $derived(normalizePathname(page.url.pathname));
	let indexable = $derived(isIndexablePath(currentPath));
	let canonicalMeta = $derived(hasCanonicalMeta(currentPath));
	let canonical = $derived(canonicalUrl(currentPath));

	afterNavigate(({ from }) => {
		const pathname = normalizePathname(page.url.pathname);
		if (!isAnalyticsPath(pathname)) return;

		// Initial loads keep the browser's real external referrer. SPA hops use
		// only a sanitized public pathname, and a hop from any private route
		// deliberately sends an empty referrer rather than leaking that route.
		const referrer = from
			? isAnalyticsPath(from.url.pathname)
				? normalizePathname(from.url.pathname)
				: ''
			: undefined;
		void countPublicPageview(pathname, { title: document.title, referrer });
	});

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
	{#if canonicalMeta}
		<link rel="canonical" href={canonical} />
		<meta property="og:url" content={canonical} />
	{/if}
	{#if indexable}
		<meta name="robots" content="index,follow,max-image-preview:large" />
	{:else}
		<meta name="robots" content="noindex,nofollow" />
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
