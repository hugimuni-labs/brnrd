<script lang="ts">
	// A warp item's own page — renders the prose body (Asked / Answer / Done
	// sections) as Markdown alongside the item's frontmatter properties.
	// The corpus mirror (`fetchSurface`) already carries the full file text;
	// no new endpoint is needed — same feed the `/warp` list page uses,
	// narrowed by `page.params.id`.
	//
	// Design constraints (w-103):
	//  - Render Asked / Answer / Done from `bodyMarkdown` (already on WarpItem)
	//  - No new backend endpoint
	//  - Uses MarkdownContent, the existing Markdown renderer
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import MarkdownContent from '$lib/MarkdownContent.svelte';
	import WithheldNotice from '$lib/WithheldNotice.svelte';
	import { SurfaceAuthError, fetchSurface, type SurfaceResponse } from '$lib/surface';
	import { buildWarpGraph, resolveTopics, topicFaces } from '$lib/warpGraph';

	let data = $state<SurfaceResponse | null>(null);
	let error = $state<string | null>(null);
	let unauthenticated = $state(false);

	let itemId = $derived(page.params.id ?? '');
	let graph = $derived(buildWarpGraph(data?.files ?? []));
	let item = $derived(graph.itemById.get(itemId) ?? null);
	let topics = $derived(item ? resolveTopics(item, graph) : []);
	let faces = $derived(topicFaces(graph));
	/** Corpus paths in this surface — needed for MarkdownContent's link resolver. */
	let knownPaths = $derived(new Set((data?.files ?? []).map((f) => f.path)));

	onMount(async () => {
		try {
			data = await fetchSurface();
		} catch (e) {
			if (e instanceof SurfaceAuthError) unauthenticated = true;
			else error = e instanceof Error ? e.message : 'surface fetch failed';
		}
	});
</script>

<svelte:head><title>{item ? item.headline : itemId} · brnrd</title></svelte:head>

{#if unauthenticated}
	<div class="mx-auto max-w-xl p-6">
		<div class="panel p-4 text-sm text-stone-300">
			Session expired. <a class="text-amber-300 underline" href={resolve('/login')}>Sign in</a> to read
			this item.
		</div>
	</div>
{:else if error}
	<div class="mx-auto max-w-xl p-6">
		<div class="panel p-4 text-sm text-red-400">{error}</div>
	</div>
{:else if data === null}
	<div class="mx-auto max-w-xl p-6 font-mono text-sm text-ink-quiet">stringing the warp…</div>
{:else if data.files.length === 0 && data.withheld}
	<div class="mx-auto max-w-xl p-6">
		<div class="panel p-4"><WithheldNotice withheld={data.withheld} /></div>
	</div>
{:else if !item}
	<div class="mx-auto max-w-xl p-6">
		<p class="font-mono text-sm text-ink-quiet">
			no item named <span class="text-stone-300">{itemId}</span> —
			<a href={resolve('/warp')} class="text-amber-300 hover:text-amber-100">back to the warp</a>
		</p>
	</div>
{:else}
	<div class="mx-auto flex max-w-2xl flex-col gap-5 p-6">
		<header>
			<p class="eyebrow">
				{item.type ?? 'item'} · {item.id}
				<span
					class="ml-1 font-mono text-[9px] uppercase {item.state === 'open'
						? 'text-amber-300/70'
						: 'text-ink-mute'}">{item.state}</span
				>
			</p>
			<h1 class="font-mono text-lg font-semibold text-amber-100">{item.headline}</h1>
			<p class="mt-1 font-mono text-[10px] text-ink-quiet">
				<a href={resolve('/warp')} class="hover:text-stone-300">← the warp</a>
			</p>
			{#if topics.length > 0}
				<p class="mt-2 flex flex-wrap gap-x-2 gap-y-1 font-mono text-[11px] text-ink-quiet">
					{#each topics as topic (topic.canonicalId)}
						{@const face = faces.get(topic.canonicalId)}
						<span class="flex items-center gap-1">
							{#if face}
								<span style={`color: ${face.color}`} aria-hidden="true">{face.glyph}</span>
							{/if}
							<span>{topic.title ?? topic.canonicalId}</span>
						</span>
					{/each}
				</p>
			{/if}
		</header>

		<!-- Prose body: Asked / Answer / Done rendered via the shared MarkdownContent
		     renderer — same typographic register as run nodes and the corpus browser. -->
		<section aria-label="item body">
			{#if item.bodyMarkdown.trim()}
				<MarkdownContent markdown={item.bodyMarkdown} sourcePath={item.path} {knownPaths} />
			{:else}
				<p class="font-mono text-[11px] text-ink-quiet italic">no prose body yet.</p>
			{/if}
		</section>
	</div>
{/if}
