<script lang="ts">
	// The resident dashboard — the main UI, in one place. The page body moved
	// to `$lib/Dashboard.svelte` on 2026-08-31 so `/daily` could wear the same
	// interface instead of growing a second, half-shaped one beside it
	// (maintainer: "have on the daily the whole main user interface, the
	// visual, like exactly as it is"). This route is the URL and nothing else;
	// its render is the component's default (`liveView="field"`), which is the
	// exact shape this file held before the extraction.
	import { resolve } from '$app/paths';
	import Dashboard from '$lib/Dashboard.svelte';
	import { HOME_DESCRIPTION, HOME_TITLE, SOCIAL_IMAGE } from '$lib/seo';
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

<div class="mx-auto max-w-4xl px-5 pt-5 sm:px-6 sm:pt-6">
	<a
		href={resolve('/websummit')}
		class="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 border-y border-amber-900/50 py-2.5 font-mono text-[10px] tracking-wide uppercase transition-colors hover:border-amber-700/70"
	>
		<span class="text-ink-quiet">HugiMuni / brnrd · Web Summit Lisbon 2026</span>
		<span class="text-amber-200">Meet us · Nov 9–12 →</span>
	</a>
</div>

<Dashboard />
