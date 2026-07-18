<script lang="ts">
	import { page } from '$app/state';
	import { onMount } from 'svelte';
	import RunNode from '$lib/RunNode.svelte';
	import { SurfaceAuthError, fetchSurface, type SurfaceResponse } from '$lib/surface';

	let data = $state<SurfaceResponse | null>(null);
	let error = $state<string | null>(null);
	let unauthenticated = $state(false);
	let repoSlug = $derived(page.params.repo);
	let runId = $derived(page.params.run);

	onMount(async () => {
		try {
			data = await fetchSurface();
		} catch (e) {
			if (e instanceof SurfaceAuthError) unauthenticated = true;
			else error = e instanceof Error ? e.message : 'run node fetch failed';
		}
	});
</script>

<svelte:head><title>{runId} · brnrd</title></svelte:head>

{#if unauthenticated}
	<div class="mx-auto max-w-xl p-6">
		<div class="panel p-4 text-sm text-stone-300">
			Session expired. <a class="text-amber-300 underline" href="/login">Sign in</a> to read this run.
		</div>
	</div>
{:else if error}
	<div class="mx-auto max-w-xl p-6">
		<div class="panel p-4 text-sm text-red-400">{error}</div>
	</div>
{:else if data === null}
	<div class="mx-auto max-w-xl p-6 font-mono text-sm text-stone-500">reading run node…</div>
{:else}
	<RunNode {data} {repoSlug} {runId} />
{/if}
