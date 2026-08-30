<script lang="ts">
	import { resolve } from '$app/paths';
	import { SEARCH_TOPICS } from '$lib/searchTopics';
	import { SOCIAL_IMAGE } from '$lib/seo';

	const title = 'Learn · brnrd';
	const description =
		'Technical notes on AI harnesses, coding agents, persistence, orchestration and remote local-first execution.';
</script>

<svelte:head>
	<title>{title}</title>
	<meta name="description" content={description} />
	<meta property="og:title" content={title} />
	<meta property="og:description" content={description} />
	<meta property="og:image" content={SOCIAL_IMAGE} />
</svelte:head>

<div class="mx-auto max-w-4xl p-5 sm:p-6">
	<header class="flex items-start justify-between gap-4">
		<div>
			<a href={resolve('/')} class="font-mono text-3xl font-semibold tracking-tight text-amber-100"
				>brnrd</a
			>
			<p class="mt-1 font-mono text-[11px] tracking-wide text-ink-quiet uppercase">learn</p>
		</div>
		<nav class="flex items-center gap-4 pt-2">
			<a
				href={resolve('/')}
				class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
				>home</a
			>
			<a
				href={resolve('/pricing')}
				class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
				>pricing</a
			>
		</nav>
	</header>

	<main class="mt-12">
		<section class="max-w-3xl" aria-labelledby="learn-title">
			<p class="eyebrow">field notes</p>
			<h1
				id="learn-title"
				class="mt-2 font-mono text-2xl font-semibold tracking-tight text-amber-100 sm:text-3xl"
			>
				The system around the model.
			</h1>
			<p class="mt-4 max-w-2xl text-sm leading-relaxed text-stone-400 sm:text-base">
				Durable explanations of the vocabulary and architecture behind coding agents. No trend
				recaps, no SEO slurry: these pages exist to make the execution model legible.
			</p>
		</section>

		<section class="mt-10 grid grid-cols-1 gap-6 md:grid-cols-2" aria-label="technical topics">
			{#each SEARCH_TOPICS as topic (topic.slug)}
				<article class="border-t border-stone-800 pt-4">
					<p class="font-mono text-[10px] tracking-wide text-amber-200/70 uppercase">{topic.kicker}</p>
					<h2 class="mt-2 font-mono text-lg font-semibold tracking-tight text-amber-100">
						<a class="hover:text-amber-200" href={resolve(`/learn/${topic.slug}`)}>{topic.title}</a>
					</h2>
					<p class="mt-3 text-sm leading-relaxed text-stone-400">{topic.description}</p>
					<a
						class="mt-4 inline-flex font-mono text-[11px] tracking-wide text-sky-400 underline underline-offset-2"
						href={resolve(`/learn/${topic.slug}`)}>read note →</a
					>
				</article>
			{/each}
		</section>
	</main>

	<footer class="mt-16 border-t border-stone-800 pt-4">
		<p class="font-mono text-[10px] text-ink-mute">
			brnrd · open source · local execution · persistent resident
		</p>
	</footer>
</div>
