<script lang="ts">
	import { resolve } from '$app/paths';
	import { SOCIAL_IMAGE, canonicalUrl } from '$lib/seo';
	import JsonLd from '$lib/JsonLd.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	let entry = $derived(data.entry);
	let pageTitle = $derived(`${entry.title} · brnrd build log`);

	let jsonLd = $derived({
		'@context': 'https://schema.org',
		'@type': 'BlogPosting',
		'@id': `${canonicalUrl(`/log/${entry.slug}`)}#post`,
		headline: entry.title,
		datePublished: entry.date,
		dateModified: entry.date,
		description: entry.summary,
		url: canonicalUrl(`/log/${entry.slug}`),
		mainEntityOfPage: canonicalUrl(`/log/${entry.slug}`),
		author: { '@id': 'https://brnrd.dev/#organization' },
		publisher: { '@id': 'https://brnrd.dev/#organization' }
	});
</script>

<svelte:head>
	<title>{pageTitle}</title>
	<meta name="description" content={entry.summary} />
	<meta property="og:title" content={pageTitle} />
	<meta property="og:description" content={entry.summary} />
	<meta property="og:image" content={SOCIAL_IMAGE} />
	<meta property="article:published_time" content={entry.date} />
	<JsonLd data={jsonLd} />
</svelte:head>

<div class="mx-auto max-w-3xl p-5 sm:p-6">
	<header class="flex items-start justify-between gap-4">
		<div>
			<a href={resolve('/')} class="font-mono text-2xl font-semibold tracking-tight text-amber-100"
				>brnrd</a
			>
			<p class="mt-1 font-mono text-[10px] tracking-wide text-ink-quiet uppercase">log</p>
		</div>
		<nav class="flex items-center gap-4 pt-1">
			<a
				href={resolve('/log')}
				class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
				>all entries</a
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
				<p class="eyebrow">build log</p>
				<time
					datetime={entry.date}
					class="mt-2 block font-mono text-[11px] tracking-wide text-amber-200/70 uppercase"
					>{entry.date}</time
				>
				<h1 class="mt-2 font-mono text-2xl font-semibold tracking-tight text-amber-100 sm:text-3xl">
					{entry.title}
				</h1>
				<p class="mt-5 text-base leading-relaxed text-stone-300">
					<strong class="text-amber-100">Measured:</strong>
					{entry.measured}
				</p>
			</header>

			<div class="mt-10 space-y-4 text-sm leading-relaxed text-stone-400 sm:text-base">
				{#each entry.body as paragraph, pi (pi)}
					<p>{paragraph}</p>
				{/each}
			</div>

			{#if entry.links.length > 0}
				<section class="mt-10 border-t border-stone-800 pt-5" aria-label="receipts">
					<p class="font-mono text-[10px] tracking-wide text-amber-200/70 uppercase">receipts</p>
					<ul class="mt-3 space-y-2">
						{#each entry.links as link (link.url)}
							<li>
								<!-- Every entry.links.url is an absolute, hand-authored
								     receipt URL (code/PR/issue), never an internal route —
								     rel="external" is the same honest opt-out
								     CapabilityPanel.svelte takes for the same reason: a
								     plain string here isn't a branded ResolvedPathname the
								     no-navigation-without-resolve rule can see through. -->
								<a
									class="font-mono text-[11px] tracking-wide text-sky-400 underline underline-offset-2"
									href={link.url}
									target="_blank"
									rel="external noopener noreferrer">{link.label} →</a
								>
							</li>
						{/each}
					</ul>
				</section>
			{/if}

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
					<a class="text-sky-400 underline underline-offset-2" href={resolve('/log')}
						>more entries →</a
					>
				</div>
			</aside>
		</article>
	</main>

	<footer class="mt-12 border-t border-stone-800 pt-4">
		<p class="font-mono text-[10px] text-ink-mute">brnrd · local execution · durable continuity</p>
	</footer>
</div>
