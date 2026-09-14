<script lang="ts">
	// The bench page (design-the-loom.md §18: "the bench is served at one
	// URL … rendered from the home's bench/ folder the way the dashboard
	// renders run pages today"). `[...rest]` swallows `<place>/<commit>` as
	// one segment — `place` may itself contain `/`, the store's own
	// nested-dir layout — and is split from the right, mirroring the
	// backend route (`dashboard_bench_file_api`) and `bench.ts`'s own
	// `benchFileHref`.
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import { onMount } from 'svelte';
	import MarkdownContent from '$lib/MarkdownContent.svelte';
	import { BenchAuthError, fetchBenchFile, type BenchFileDetail } from '$lib/bench';

	let repo = $derived(page.params.repo ?? '');
	let rest = $derived(page.params.rest ?? '');
	let place = $derived(rest.slice(0, Math.max(0, rest.lastIndexOf('/'))));
	let commit = $derived(rest.slice(rest.lastIndexOf('/') + 1));

	let file = $state<BenchFileDetail | null>(null);
	let notFound = $state(false);
	let unauthenticated = $state(false);
	let error = $state<string | null>(null);
	let loaded = $state(false);

	async function load() {
		loaded = false;
		notFound = false;
		error = null;
		try {
			const result = await fetchBenchFile(repo, place, commit);
			if (result === null) {
				notFound = true;
			} else {
				file = result;
			}
		} catch (e) {
			if (e instanceof BenchAuthError) unauthenticated = true;
			else error = e instanceof Error ? e.message : 'bench file fetch failed';
		} finally {
			loaded = true;
		}
	}

	onMount(load);
</script>

<svelte:head><title>{commit || 'bench'} · brnrd</title></svelte:head>

{#if unauthenticated}
	<div class="mx-auto max-w-xl p-6">
		<div class="panel p-4 text-sm text-stone-300">
			Session expired. <a class="text-amber-300 underline" href={resolve('/login')}>Sign in</a> to read
			this page.
		</div>
	</div>
{:else if error}
	<div class="mx-auto max-w-xl p-6">
		<div class="panel p-4 text-sm text-red-400">{error}</div>
	</div>
{:else if !loaded}
	<div class="mx-auto max-w-xl p-6 font-mono text-sm text-ink-quiet">reading the bench…</div>
{:else if notFound}
	<!-- design-the-loom.md §18: "the dashboard's 'page unavailable' is a 404
	     with a place in it" — the address itself is the diagnostic, not a
	     generic "not found" the reader has to go decode. -->
	<div class="mx-auto max-w-xl p-6">
		<div class="panel p-4 text-sm text-stone-300">
			No bench file at <span class="font-mono text-stone-400">{repo}/{place}/{commit}</span> — marked,
			moved, or never folded.
		</div>
	</div>
{:else if file}
	<div class="mx-auto max-w-xl px-6 py-6">
		<div class="panel p-4">
			<div class="mb-3 flex items-center justify-between text-sm">
				<span class="font-mono font-medium tracking-wide text-sky-200 uppercase">bench</span>
				<span class="font-mono text-ink-quiet">{file.commit.slice(0, 12)}</span>
			</div>
			<div class="mb-2 font-mono text-xs text-stone-400">{file.place}</div>
			{#if file.question}
				<div class="mb-3 text-sm text-sky-100">{file.question}</div>
			{/if}
			<MarkdownContent markdown={file.body} sourcePath={`bench/${repo}/${place}/${commit}`} />
		</div>
	</div>
{/if}
