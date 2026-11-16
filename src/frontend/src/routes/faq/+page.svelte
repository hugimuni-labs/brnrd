<script lang="ts">
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import { FAQ } from '$lib/faq';

	const description =
		'What brnrd is, what you need to run it, where your data lives, what it costs and how far it acts on its own.';

	// Each question is a <details> with an id, so /faq#is-it-safe works as a
	// link. With JavaScript off the browser still scrolls to it and the
	// question is one click from its answer; with it on, the linked one opens.
	function openLinked() {
		const id = decodeURIComponent(window.location.hash.slice(1));
		const target = id ? document.getElementById(id) : null;
		const el = target?.closest('li')?.querySelector('details');
		if (el instanceof HTMLDetailsElement) el.open = true;
	}

	onMount(() => {
		openLinked();
		window.addEventListener('hashchange', openLinked);
		return () => window.removeEventListener('hashchange', openLinked);
	});
</script>

<svelte:head>
	<title>questions · brnrd</title>
	<meta name="description" content={description} />
	<meta property="og:title" content="questions · brnrd" />
	<meta property="og:description" content={description} />
	<meta name="twitter:title" content="questions · brnrd" />
	<meta name="twitter:description" content={description} />
	<!-- canonical + og:url come from +layout.svelte (hasCanonicalMeta) -->
</svelte:head>

<div class="mx-auto max-w-4xl p-6">
	<header class="flex items-start justify-between gap-4">
		<div>
			<a href={resolve('/')} class="font-mono text-3xl font-semibold tracking-tight text-amber-100"
				>brnrd</a
			>
			<p class="mt-1 font-mono text-[11px] tracking-wide text-ink-quiet uppercase">questions</p>
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
			<a
				href={resolve('/login')}
				class="border border-amber-700 bg-amber-950/40 px-3 py-1.5 font-mono text-[11px] tracking-wide text-amber-200 uppercase hover:bg-amber-950/70"
				>sign in</a
			>
		</nav>
	</header>

	<main>
		<section class="mt-10 max-w-3xl" aria-labelledby="faq-title">
			<p class="eyebrow">questions</p>
			<h1
				id="faq-title"
				class="mt-2 font-mono text-2xl font-semibold tracking-tight text-amber-100 md:text-3xl"
			>
				What people ask about brnrd
			</h1>
		</section>

		<section class="mt-8 max-w-3xl" aria-label="questions and answers">
			<ul class="space-y-3" data-faq-list>
				{#each FAQ as entry (entry.id)}
					<li>
						{#if entry.alias}<span id={entry.alias}></span>{/if}
						<details id={entry.id} class="panel group p-4 target:border-amber-800/60" data-faq-item>
							<summary
								class="cursor-pointer font-mono text-sm font-semibold text-amber-100 marker:text-ink-quiet"
								>{entry.question}</summary
							>
							<p class="mt-3 text-sm leading-relaxed text-stone-400">{entry.answer}</p>
							<p class="mt-3 font-mono text-[10px] text-ink-mute">
								<a class="hover:text-stone-300" href={`#${entry.id}`}>link to this question</a>
							</p>
						</details>
					</li>
				{/each}
			</ul>
		</section>
	</main>

	<footer class="mt-14 border-t border-stone-800 pt-4">
		<p class="font-mono text-[10px] text-ink-mute">
			<a class="hover:text-stone-300" href={resolve('/pricing')}>pricing</a>
			·
			<a class="hover:text-stone-300" href={resolve('/terms')}>terms</a>
			·
			<a class="hover:text-stone-300" href={resolve('/privacy')}>privacy</a>
		</p>
	</footer>
</div>
