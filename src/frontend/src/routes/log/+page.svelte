<script lang="ts">
	import { resolve } from '$app/paths';
	import { SOCIAL_IMAGE, canonicalUrl } from '$lib/seo';
	import { buildLogEntriesSorted } from '$lib/buildLog';
	import JsonLd from '$lib/JsonLd.svelte';

	const title = 'Build log · brnrd';
	const description =
		'Dated findings from building brnrd, with receipts: what was measured, what it means, and links to the code.';

	const entries = buildLogEntriesSorted();

	const jsonLd = {
		'@context': 'https://schema.org',
		'@type': 'Blog',
		'@id': `${canonicalUrl('/log')}#blog`,
		url: canonicalUrl('/log'),
		name: title,
		description,
		publisher: { '@id': 'https://brnrd.dev/#organization' },
		blogPost: entries.map((entry) => ({
			'@type': 'BlogPosting',
			headline: entry.title,
			datePublished: entry.date,
			url: canonicalUrl(`/log/${entry.slug}`)
		}))
	};
</script>

<svelte:head>
	<title>{title}</title>
	<meta name="description" content={description} />
	<meta property="og:title" content={title} />
	<meta property="og:description" content={description} />
	<meta property="og:image" content={SOCIAL_IMAGE} />
	<JsonLd data={jsonLd} />
</svelte:head>

<div class="mx-auto max-w-4xl p-5 sm:p-6">
	<header class="flex items-start justify-between gap-4">
		<div>
			<a href={resolve('/')} class="font-mono text-3xl font-semibold tracking-tight text-amber-100"
				>brnrd</a
			>
			<p class="mt-1 font-mono text-[11px] tracking-wide text-ink-quiet uppercase">log</p>
		</div>
		<nav class="flex items-center gap-4 pt-2">
			<a
				href={resolve('/learn')}
				class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
				>learn</a
			>
			<a
				href={resolve('/')}
				class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
				>home</a
			>
		</nav>
	</header>

	<main class="mt-12">
		<section class="max-w-3xl" aria-labelledby="log-title">
			<p class="eyebrow">build log</p>
			<h1
				id="log-title"
				class="mt-2 font-mono text-2xl font-semibold tracking-tight text-amber-100 sm:text-3xl"
			>
				What we measured, in order.
			</h1>
			<p class="mt-4 max-w-2xl text-sm leading-relaxed text-stone-400 sm:text-base">
				Dated findings from building brnrd, on ground we own: a title, the thing that was measured,
				and a link to the code so a reader can check the claim rather than take it on faith. Nothing
				that expires silently.
			</p>
		</section>

		{#if entries.length === 0}
			<section
				class="mt-10 max-w-2xl border-t border-stone-800 pt-8 text-center"
				aria-label="no entries yet"
			>
				<p class="font-mono text-[11px] tracking-wide text-ink-mute uppercase">
					nothing logged yet
				</p>
				<p class="mt-3 text-sm leading-relaxed text-stone-400">
					The first dated finding lands here as soon as one clears review. Check back, or read the
					<a class="text-sky-400 underline underline-offset-2" href={resolve('/learn')}
						>field notes</a
					>
					in the meantime.
				</p>
			</section>
		{:else}
			<section class="mt-10 space-y-8" aria-label="build log entries">
				{#each entries as entry (entry.slug)}
					<article class="border-t border-stone-800 pt-5">
						<div class="flex flex-wrap items-baseline gap-x-3 gap-y-1">
							<time
								datetime={entry.date}
								class="font-mono text-[11px] tracking-wide text-amber-200/70 uppercase"
								>{entry.date}</time
							>
						</div>
						<h2 class="mt-2 font-mono text-lg font-semibold tracking-tight text-amber-100">
							<a class="hover:text-amber-200" href={resolve(`/log/${entry.slug}`)}>{entry.title}</a>
						</h2>
						<p class="mt-3 text-sm leading-relaxed text-stone-400">{entry.summary}</p>
						<a
							class="mt-4 inline-flex font-mono text-[11px] tracking-wide text-sky-400 underline underline-offset-2"
							href={resolve(`/log/${entry.slug}`)}>read the finding →</a
						>
					</article>
				{/each}
			</section>
		{/if}
	</main>

	<footer class="mt-16 border-t border-stone-800 pt-4">
		<p class="font-mono text-[10px] text-ink-mute">
			brnrd · open source · local execution · persistent resident
		</p>
	</footer>
</div>
