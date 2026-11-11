<script lang="ts">
	import { INSTALL_LINE } from '$lib/install';
	import { GITHUB_REPO } from '$lib/publicStats';
	import { SOCIAL_IMAGE } from '$lib/seo';
	import { resolve } from '$app/paths';

	const BOOTH = {
		meet: 'https://meet.google.com/meet/ifp-ymqz-gaq',
		hours: 'Today, 15:00–15:30 (Lisbon time)'
	};
	const VIDEO_URL = '';
	const title = 'The brnrd booth, virtual today';
	const description = 'The Web Summit venue is closed because of the storm, so the booth is here.';
</script>

<svelte:head>
	<title>{title}</title>
	<meta name="description" content={description} />
	<meta property="og:title" content={title} />
	<meta property="og:description" content={description} />
	<meta property="og:image" content={SOCIAL_IMAGE} />
	<meta name="twitter:title" content={title} />
	<meta name="twitter:description" content={description} />
	<meta name="twitter:image" content={SOCIAL_IMAGE} />
</svelte:head>

<div class="mx-auto max-w-4xl p-5 sm:p-6">
	<header>
		<a href={resolve('/')} class="font-mono text-3xl font-semibold tracking-tight text-amber-100"
			>brnrd</a
		>
	</header>
	<main class="mt-10 max-w-3xl sm:mt-16">
		<h1
			class="font-mono text-4xl leading-[1.08] font-semibold tracking-tight text-amber-100 sm:text-5xl"
		>
			{title}
		</h1>
		<p class="mt-5 text-base leading-relaxed text-stone-300 sm:text-lg">{description}</p>
		<p class="mt-5 text-base leading-relaxed text-stone-300 sm:text-lg">
			brnrd turns the coding agent you already use (Claude Code, Codex…) into a teammate that keeps
			working after you close the laptop: you message it from Telegram, it remembers the project,
			and it comes back with pull requests.
		</p>

		<section class="panel mt-8 p-5 sm:p-6" aria-labelledby="demo-title">
			<h2 id="demo-title" class="font-mono text-xl leading-snug font-semibold text-amber-100">
				Drop in for a live demo: ask it something yourself and watch it work
			</h2>
			{#if BOOTH.meet && BOOTH.hours}
				<p class="mt-4 text-stone-300">{BOOTH.hours}</p>
				<a
					href={BOOTH.meet}
					rel="external"
					class="mt-4 inline-flex min-h-11 items-center border border-amber-700 bg-amber-950/40 px-4 py-3 font-mono text-sm text-amber-200 hover:bg-amber-950/70"
					>Join on Google Meet →</a
				>
			{:else}
				<p class="mt-4 text-stone-300">Room link and hours here shortly</p>
			{/if}
		</section>

		<section class="mt-8" aria-labelledby="install-title">
			<h2 id="install-title" class="font-mono text-xl font-semibold text-amber-100">
				Try brnrd on your machine
			</h2>
			<pre
				class="panel mt-4 overflow-x-auto px-4 py-3 font-mono text-sm whitespace-pre-wrap text-stone-200"
				aria-label="install brnrd"><code>{INSTALL_LINE}</code></pre>
			<a
				href={`https://github.com/${GITHUB_REPO}`}
				rel="external"
				class="mt-4 inline-flex min-h-11 items-center font-mono text-sm text-amber-200 underline underline-offset-4"
				>brnrd on GitHub →</a
			>
		</section>

		{#if VIDEO_URL}
			<video
				class="mt-8 w-full"
				controls
				preload="metadata"
				src={VIDEO_URL}
				aria-label="brnrd trailer"
			>
				<track kind="captions" />
				<a href={VIDEO_URL} rel="external">Watch the brnrd trailer</a>
			</video>
		{/if}
	</main>
</div>
