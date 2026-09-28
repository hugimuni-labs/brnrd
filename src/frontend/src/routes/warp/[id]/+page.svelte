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
	import type { ResolvedPathname } from '$app/types';
	import {
		buildWarpGraph,
		itemRepos,
		resolveTopics,
		topicFaces,
		type WarpItem
	} from '$lib/warpGraph';
	import { repoRunSlug, runIdSlug, runNodeHref } from '$lib/runNode';

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

	function runHref(itemVal: WarpItem | null, runId: string): ResolvedPathname | null {
		if (!itemVal) return null;
		const repos = itemRepos(itemVal);
		if (repos.length !== 1) return null;
		return runNodeHref(repoRunSlug(repos[0]), runIdSlug(runId)) ?? null;
	}

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
	<div class="mx-auto flex max-w-2xl flex-col gap-6 p-6">
		<!-- Header: identity + navigation -->
		<header class="border-b border-stone-800 pb-4">
			<p class="eyebrow mb-1">
				{item.type ?? 'item'} · {item.id}
				<span
					class="ml-1 font-mono text-[9px] uppercase {item.state === 'open'
						? 'text-amber-300/70'
						: 'text-ink-mute'}">{item.state}</span
				>
				{#if item.stage}
					<span class="ml-2 font-mono text-[9px] text-amber-300/60 uppercase">{item.stage}</span>
				{/if}
			</p>
			<h1 class="font-mono text-xl font-semibold leading-tight text-amber-100">{item.headline}</h1>
			{#if item.returnNote}
				<p class="mt-1.5 font-mono text-[11px] text-stone-400 italic">{item.returnNote}</p>
			{/if}
			<p class="mt-2 font-mono text-[10px] text-ink-quiet">
				<a href={resolve('/warp')} class="hover:text-stone-300">← the warp</a>
			</p>
			{#if topics.length > 0}
				<p class="mt-2 flex flex-wrap gap-x-3 gap-y-1 font-mono text-[11px] text-ink-quiet">
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

		<!-- Metadata panel: says, attempts, refs, taken, prompt -->
		{#if item.says.length > 0 || item.attempts.length > 0 || item.taken.length > 0 || item.refs.length > 0 || item.prompt}
			<section class="panel space-y-2 p-3 font-mono text-[11px]" aria-label="item metadata">
				{#if item.says.length > 0}
					<div>
						<span class="font-mono text-[9px] tracking-wide text-ink-mute uppercase"
							>says · {item.says.length}</span
						>
						<div class="mt-1 flex flex-wrap gap-x-2 gap-y-1">
							{#each item.says as evtId (evtId)}
								{@const short = evtId.slice(evtId.lastIndexOf('-') + 1)}
								<span
									class="rounded border border-stone-800 bg-stone-950 px-1.5 py-0.5 font-mono text-[10px] text-stone-400"
									title={evtId}>— {short}</span
								>
							{/each}
						</div>
					</div>
				{/if}
				{#if item.attempts.length > 0 || item.taken.length > 0}
					{@const allRuns = [...new Set([...item.attempts, ...item.taken])]}
					<div>
						<span class="font-mono text-[9px] tracking-wide text-ink-mute uppercase"
							>runs · {allRuns.length}</span
						>
						<div class="mt-1 flex flex-wrap gap-x-2 gap-y-1">
							{#each allRuns as runId (runId)}
								{@const href = runHref(item, runId)}
								{#if href}
									<a
										{href}
										class="rounded border border-stone-700 bg-stone-950 px-1.5 py-0.5 font-mono text-[10px] text-amber-300/80 hover:border-stone-500 hover:text-amber-100"
										>{runId}</a
									>
								{:else}
									<span
										class="rounded border border-stone-800 bg-stone-950 px-1.5 py-0.5 font-mono text-[10px] text-stone-400"
										>{runId}</span
									>
								{/if}
							{/each}
						</div>
					</div>
				{/if}
				{#if item.refs.length > 0}
					<div>
						<span class="font-mono text-[9px] tracking-wide text-ink-mute uppercase">refs</span>
						<p class="mt-0.5">
							{#each item.refs as ref, index (index)}
								{#if index > 0}<span class="text-ink-mute"> · </span>{/if}
								{#if ref.href}
									<a
										href={ref.href}
										target="_blank"
										rel="noopener external"
										class="text-amber-300/90 hover:text-amber-100">{ref.label}</a
									>
								{:else}
									<span class="text-stone-400">{ref.label}</span>
								{/if}
							{/each}
						</p>
					</div>
				{/if}
				{#if item.prompt}
					<div class="border-t border-stone-800 pt-2">
						<span class="font-mono text-[9px] tracking-wide text-ink-mute uppercase"
							>dispatch prompt</span
						>
						<p class="mt-0.5 text-stone-300">{item.prompt}</p>
					</div>
				{/if}
			</section>
		{/if}

		<!-- Prose body: Asked / Answer / Done — the actual warp document -->
		<section aria-label="item body">
			{#if item.bodyMarkdown.trim()}
				<MarkdownContent markdown={item.bodyMarkdown} sourcePath={item.path} {knownPaths} />
			{:else}
				<p class="font-mono text-[11px] text-ink-quiet italic">no prose body yet.</p>
			{/if}
		</section>

		<!-- Touched timestamp — footer -->
		{#if item.touched}
			<footer class="border-t border-stone-800 pt-2">
				<p class="font-mono text-[9px] text-ink-mute">touched {item.touched}</p>
			</footer>
		{/if}
	</div>
{/if}
