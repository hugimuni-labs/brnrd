<script lang="ts">
	// The warp's own page (2026-08-11 round): every work item, addressable —
	// `/warp#w-42` is an item's one URL, quotable in a reply and stable
	// across renames. Two tabs: live (the same ready/held graph the home
	// page lenses) and completed (the done/retired ledger the decisions
	// file used to be). Feeds are ones the dashboard already publishes —
	// the corpus mirror and the live-runs snapshot; no new endpoint.
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import HeddleRail from '$lib/HeddleRail.svelte';
	import WarpGraphView from '$lib/WarpGraphView.svelte';
	import WithheldNotice from '$lib/WithheldNotice.svelte';
	import { LiveRunsAuthError, fetchLiveRuns } from '$lib/liveRuns';
	import { SurfaceAuthError, fetchSurface, type SurfaceResponse } from '$lib/surface';
	import {
		buildWarpGraph,
		completedItems,
		itemInTopics,
		resolveTopics,
		topicCounts,
		topicFaces,
		topicThreads,
		weavingRows
	} from '$lib/warpGraph';

	let data = $state<SurfaceResponse | null>(null);
	let error = $state<string | null>(null);
	let unauthenticated = $state(false);
	let liveRunIds = $state<ReadonlySet<string>>(new Set());
	let tab = $state<'live' | 'completed'>('live');
	let selected = $state<Set<string> | null>(null);

	let graph = $derived(buildWarpGraph(data?.files ?? []));
	let threads = $derived(topicThreads(graph));
	let counts = $derived(topicCounts(graph));
	let weavingTopics = $derived(
		new Set(
			weavingRows(graph, liveRunIds)
				.map((row) => row.callSign)
				.filter(Boolean)
		)
	);
	let completed = $derived(
		completedItems(graph).filter((item) => itemInTopics(item, graph, selected))
	);
	let faces = $derived(topicFaces(graph));

	function toggleTopic(id: string) {
		const all = new Set(threads.map((thread) => thread.canonicalId));
		let next: Set<string>;
		if (selected === null) {
			next = all;
			next.delete(id);
		} else {
			next = new Set(selected);
			if (next.has(id)) next.delete(id);
			else next.add(id);
		}
		selected = next.size >= threads.length ? null : next;
	}

	onMount(async () => {
		try {
			data = await fetchSurface();
		} catch (e) {
			if (e instanceof SurfaceAuthError) unauthenticated = true;
			else error = e instanceof Error ? e.message : 'surface fetch failed';
		}
		try {
			const live = await fetchLiveRuns();
			liveRunIds = new Set(live.runs.map((run) => run.run_id || run.id));
		} catch (e) {
			// Live framing is a supplement — the item space reads fine without
			// it, and a 401 is already carried by the surface fetch.
			if (e instanceof LiveRunsAuthError) unauthenticated = true;
		}
	});
</script>

<svelte:head><title>the warp · brnrd</title></svelte:head>

{#if unauthenticated}
	<div class="mx-auto max-w-xl p-6">
		<div class="panel p-4 text-sm text-stone-300">
			Session expired. <a class="text-amber-300 underline" href={resolve('/login')}>Sign in</a> to read
			the warp.
		</div>
	</div>
{:else if error}
	<div class="mx-auto max-w-xl p-6">
		<div class="panel p-4 text-sm text-red-400">{error}</div>
	</div>
{:else if data === null}
	<div class="mx-auto max-w-xl p-6 font-mono text-sm text-ink-quiet">stringing the warp…</div>
{:else}
	<div class="mx-auto flex max-w-2xl flex-col p-6">
		<header class="mb-4">
			<p class="eyebrow">the warp · every item</p>
			<h1 class="font-mono text-lg font-semibold text-amber-100">what is asked, and what became</h1>
			<p class="mt-1 font-mono text-[10px] text-ink-quiet">
				<a href={resolve('/')} class="hover:text-stone-300">← dashboard</a>
			</p>
		</header>

		{#if data.files.length === 0 && data.withheld}
			<div class="panel p-4"><WithheldNotice withheld={data.withheld} /></div>
		{:else}
			<div class="mb-3">
				<HeddleRail
					{threads}
					{counts}
					{selected}
					weaving={weavingTopics}
					onToggle={toggleTopic}
					onAll={() => (selected = null)}
				/>
			</div>

			<div class="mb-3 flex gap-3 font-mono text-[11px] tracking-wide uppercase" role="tablist">
				<button
					type="button"
					role="tab"
					aria-selected={tab === 'live'}
					class="cursor-pointer {tab === 'live' ? 'text-amber-200' : 'text-ink-mute hover:text-stone-300'}"
					onclick={() => (tab = 'live')}>live</button
				>
				<button
					type="button"
					role="tab"
					aria-selected={tab === 'completed'}
					class="cursor-pointer {tab === 'completed'
						? 'text-amber-200'
						: 'text-ink-mute hover:text-stone-300'}"
					onclick={() => (tab = 'completed')}>completed · {completedItems(graph).length}</button
				>
			</div>

			{#if tab === 'live'}
				<WarpGraphView {graph} {selected} {liveRunIds} />
			{:else if completed.length === 0}
				<p class="text-sm text-ink-quiet">nothing completed yet — receipts land here.</p>
			{:else}
				<ul class="space-y-0.5 font-mono text-xs" aria-label="completed items">
					{#each completed as item (item.id)}
						<li id={item.id} class="flex min-w-0 flex-wrap items-baseline gap-x-2 py-0.5">
							<span class="shrink-0 text-ink-mute" aria-hidden="true"
								>{item.state === 'retired' ? '✕' : '✓'}</span
							>
							<span class="min-w-[9ch] flex-1 break-words text-stone-300">{item.headline}</span>
							<span class="shrink-0 font-mono text-[9px] text-ink-mute">{item.id}</span>
							{#if item.type}
								<span class="shrink-0 font-mono text-[9px] tracking-wide text-ink-quiet uppercase"
									>{item.type}</span
								>
							{/if}
							{#each resolveTopics(item, graph) as topic (topic.canonicalId)}
								{@const face = faces.get(topic.canonicalId)}
								{#if face}
									<span
										class="shrink-0 font-mono text-[11px]"
										style={`color: ${face.color}`}
										title={topic.title}>{face.glyph}</span
									>
								{/if}
							{/each}
							<span class="ml-auto shrink-0 text-[10px] whitespace-nowrap text-ink-mute">
								{item.state === 'retired'
									? (item.retiredNote ?? 'retired')
									: [item.doneDate, item.doneRun].filter(Boolean).join(' · ')}
							</span>
						</li>
					{/each}
				</ul>
			{/if}
		{/if}
	</div>
{/if}
