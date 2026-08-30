<script lang="ts">
	import { resolve } from '$app/paths';
	import { SOCIAL_IMAGE } from '$lib/seo';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	let topic = $derived(data.topic);
	let pageTitle = $derived(`${topic.title} · brnrd`);
</script>

<svelte:head>
	<title>{pageTitle}</title>
	<meta name="description" content={topic.description} />
	<meta property="og:title" content={pageTitle} />
	<meta property="og:description" content={topic.description} />
	<meta property="og:image" content={SOCIAL_IMAGE} />
</svelte:head>

<div class="mx-auto max-w-3xl p-5 sm:p-6">
	<header class="flex items-start justify-between gap-4">
		<div>
			<a href={resolve('/')} class="font-mono text-2xl font-semibold tracking-tight text-amber-100"
				>brnrd</a
			>
			<p class="mt-1 font-mono text-[10px] tracking-wide text-ink-quiet uppercase">learn</p>
		</div>
		<nav class="flex items-center gap-4 pt-1">
			<a
				href={resolve('/learn')}
				class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
				>all notes</a
			>
			<a
				href={resolve('/')}
				class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
				>home</a
			>
		</nav>
	</header>

	<main class="mt-12">
		<article>
			<header class="max-w-2xl">
				<p class="eyebrow">{topic.kicker}</p>
				<h1 class="mt-2 font-mono text-2xl font-semibold tracking-tight text-amber-100 sm:text-3xl">
					{topic.title}
				</h1>
				<p class="mt-5 text-base leading-relaxed text-stone-300">{topic.lede}</p>
			</header>

			<div class="mt-10 space-y-10">
				{#each topic.sections as section (section.heading)}
					<section
						aria-labelledby={`section-${section.heading.toLowerCase().replaceAll(/[^a-z0-9]+/g, '-')}`}
					>
						<h2
							id={`section-${section.heading.toLowerCase().replaceAll(/[^a-z0-9]+/g, '-')}`}
							class="border-t border-stone-800 pt-4 font-mono text-lg font-semibold tracking-tight text-amber-100"
						>
							{section.heading}
						</h2>
						<div class="mt-4 space-y-4 text-sm leading-relaxed text-stone-400 sm:text-base">
							{#each section.paragraphs as paragraph}
								<p>{paragraph}</p>
							{/each}
						</div>
						{#if section.bullets}
							<ul class="mt-5 space-y-2 border-l border-amber-900/60 pl-5 text-sm text-stone-400">
								{#each section.bullets as bullet}
									<li>{bullet}</li>
								{/each}
							</ul>
						{/if}
					</section>
				{/each}
			</div>

			<aside class="mt-12 border-y border-amber-900/60 py-5" aria-label="brnrd relationship">
				<p class="font-mono text-[10px] tracking-wide text-amber-200/70 uppercase">
					where brnrd fits
				</p>
				<p class="mt-2 text-sm leading-relaxed text-stone-400">
					brnrd keeps persistent identity, project context and routing above bounded coding-harness
					runs. The open-source resident executes on your machine; brnrd.dev is an optional control
					plane for hosted reach and coordination.
				</p>
				<div class="mt-4 flex flex-wrap gap-4 font-mono text-[11px]">
					<a class="text-sky-400 underline underline-offset-2" href={resolve('/')}>see brnrd →</a>
					<a class="text-sky-400 underline underline-offset-2" href={resolve('/learn')}
						>more notes →</a
					>
				</div>
			</aside>
		</article>
	</main>

	<footer class="mt-12 border-t border-stone-800 pt-4">
		<p class="font-mono text-[10px] text-ink-mute">brnrd · local execution · durable continuity</p>
	</footer>
</div>
