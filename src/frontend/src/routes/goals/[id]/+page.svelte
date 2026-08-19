<script lang="ts">
	// A goal's own page (design-goal-oriented-engineering.md §"The finding":
	// "trajectory — the metric over time, on the goal's own page (/goals/g-N).
	// This page *is* the 'continuous multi-run view' the memo asks for; a
	// goal outlives every run that advances it."). Same feed the warp already
	// publishes — the corpus mirror — joined client-side; no new endpoint.
	//
	// Three derived views, none authored (the design's own rule):
	//  - trajectory: the readings series off the goal's own sibling file
	//    (`g-<N>.readings.jsonl`, same corpus mirror as the item file, joined
	//    client-side by path — `findGoalReadingsFile`). No source connected
	//    yet ⇒ a visibly turned-off part naming its own enable path, never a
	//    silently missing section (design-capability-panel.md /
	//    design-run-route.md "the off parts stay visible"); a goal *with* a
	//    readings file but zero parseable lines renders the same off state,
	//    text-identical to "never recorded" — the reader's question is "is
	//    there anything to see", not "why is the file empty".
	//  - contributing work: the item cone under `advances:` (`contributingCone`).
	//  - blockers-on-you: the open decision/preparation items inside that cone
	//    (`blockersOnYou`) — a query, not a curated list.
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import WithheldNotice from '$lib/WithheldNotice.svelte';
	import { SurfaceAuthError, fetchSurface, type SurfaceResponse } from '$lib/surface';
	import {
		blockersOnYou,
		buildWarpGraph,
		contributingCone,
		findGoalReadingsFile,
		formatReadingDelta,
		formatReadingValue,
		parseGoalReadings,
		readingsNewestFirst,
		resolveTopics,
		summarizeGoalReadings,
		topicFaces,
		type ItemType,
		type WarpItem
	} from '$lib/warpGraph';

	let data = $state<SurfaceResponse | null>(null);
	let error = $state<string | null>(null);
	let unauthenticated = $state(false);

	let goalId = $derived(page.params.id ?? '');
	let graph = $derived(buildWarpGraph(data?.files ?? []));
	let goal = $derived(graph.itemById.get(goalId) ?? null);
	let cone = $derived(goal ? contributingCone(goal.id, graph) : []);
	let callback = $derived(goal ? blockersOnYou(goal.id, graph) : []);
	let faces = $derived(topicFaces(graph));
	let readingsFile = $derived(goal ? findGoalReadingsFile(goal.id, data?.files ?? []) : null);
	let readings = $derived(readingsFile ? parseGoalReadings(readingsFile.markdown) : []);
	let readingsTable = $derived(readingsNewestFirst(readings));
	let readingsSummary = $derived(summarizeGoalReadings(readings));

	// Same marks the item lanes wear (WarpGraphView.svelte) — the design asks
	// to reuse the existing heddle/lane visual grammar for item rows, never
	// mint a new idiom for them; this page's contributing/blockers lists are
	// a simpler read-only cut of the same rows, not a different language.
	const TYPE_MARK: Record<ItemType, string> = {
		decision: '◆',
		preparation: '◇',
		action: '●',
		goal: '◎'
	};
	function typeMark(item: WarpItem): string {
		return item.type === null ? '▫' : TYPE_MARK[item.type];
	}

	// The lint rule type-checks an `<a href>` expression itself and only
	// recognizes a bare `resolve()` call there — it cannot see through a
	// helper or a template literal, and (unlike a `// eslint-disable`
	// comment inside `<script>`, the working pattern repos/+page.svelte
	// uses for the identical goto()-plus-fragment shape) an HTML comment
	// in markup isn't honoured as a suppression here. So: `href` stays a
	// bare `resolve('/warp')` (real navigation even with JS disabled or a
	// right-click/open-in-new-tab), and a primary click additionally
	// `goto()`s with the item's own #fragment appended — same "resolve()
	// owns the route, the fragment is appended once after" idiom, routed
	// through the call site where the disable comment actually works.
	function openItem(event: MouseEvent, item: WarpItem): void {
		event.preventDefault();
		// eslint-disable-next-line svelte/no-navigation-without-resolve
		void goto(`${resolve('/warp')}#${item.id}`);
	}

	const METRIC_SPINE: Array<[string, 'metric' | 'target' | 'horizon']> = [
		['metric', 'metric'],
		['target', 'target'],
		['horizon', 'horizon']
	];

	onMount(async () => {
		try {
			data = await fetchSurface();
		} catch (e) {
			if (e instanceof SurfaceAuthError) unauthenticated = true;
			else error = e instanceof Error ? e.message : 'surface fetch failed';
		}
	});
</script>

<svelte:head><title>{goal ? goal.headline : goalId} · brnrd</title></svelte:head>

{#snippet itemRow(item: WarpItem)}
	{@const topics = resolveTopics(item, graph)}
	<li class="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5 py-0.5">
		<span class="shrink-0 font-mono text-[10px] text-ink-mute" aria-hidden="true"
			>{typeMark(item)}</span
		>
		<a
			href={resolve('/warp')}
			onclick={(event) => openItem(event, item)}
			class="min-w-[9ch] flex-1 break-words text-left leading-tight text-amber-100 hover:text-amber-50"
			>{item.headline}</a
		>
		<span class="shrink-0 font-mono text-[9px] text-ink-mute">{item.id}</span>
		<span class="shrink-0 font-mono text-[9px] tracking-wide text-ink-quiet uppercase"
			>{item.type ?? 'untyped'}</span
		>
		{#if topics.length > 0}
			<span class="shrink-0 font-mono text-[11px]" aria-label="topics">
				{#each topics as topic (topic.canonicalId)}
					{@const face = faces.get(topic.canonicalId)}
					{#if face}
						<span style={`color: ${face.color}`} title={topic.title}>{face.glyph}</span>
					{/if}
				{/each}
			</span>
		{/if}
	</li>
{/snippet}

{#if unauthenticated}
	<div class="mx-auto max-w-xl p-6">
		<div class="panel p-4 text-sm text-stone-300">
			Session expired. <a class="text-amber-300 underline" href={resolve('/login')}>Sign in</a> to read
			this goal.
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
{:else if !goal}
	<div class="mx-auto max-w-xl p-6">
		<p class="font-mono text-sm text-ink-quiet">
			no goal named <span class="text-stone-300">{goalId}</span> —
			<a href={resolve('/warp')} class="text-amber-300 hover:text-amber-100">back to the warp</a>
		</p>
	</div>
{:else}
	<div class="mx-auto flex max-w-2xl flex-col gap-5 p-6">
		<header>
			<p class="eyebrow">goal · {goal.id}</p>
			<h1 class="font-mono text-lg font-semibold text-amber-100">{goal.headline}</h1>
			<p class="mt-1 font-mono text-[10px] text-ink-quiet">
				<a href={resolve('/warp')} class="hover:text-stone-300">← the warp</a>
			</p>
			{#if goal.metric || goal.target || goal.horizon}
				<p class="mt-2 flex flex-wrap gap-x-3 gap-y-1 font-mono text-[11px] text-stone-300">
					{#each METRIC_SPINE as [label, key] (label)}
						{#if goal[key]}
							<span><span class="text-ink-mute">{label}:</span> {goal[key]}</span>
						{/if}
					{/each}
				</p>
			{/if}
		</header>

		<section aria-labelledby="trajectory-heading">
			<h2
				id="trajectory-heading"
				class="font-mono text-[10px] tracking-[0.16em] text-ink-mute uppercase"
			>
				trajectory{#if readingsTable.length > 0}
					· {readingsTable.length}{/if}
			</h2>
			{#if readingsTable.length === 0}
				<!-- The off-part-visible rule: a capability with no data source yet
				     renders as a visibly turned-off row naming its own enable path,
				     never as a missing section — same doctrine CapabilityPanel.svelte
				     already renders for `unobservable`/`waiting` lamps. -->
				<div class="mt-1 flex items-start gap-2 py-1 opacity-70">
					<span
						class="mt-1 inline-block h-2 w-2 shrink-0 rounded-full border border-dashed border-stone-600"
						aria-hidden="true"
					></span>
					<p class="font-mono text-[11px] text-ink-quiet">
						no readings yet — record one with <span class="text-stone-300"
							>brnrd goal record {goal.id} &lt;key&gt; &lt;value&gt;</span
						>; collectors (analytics, X read) are the automated enable path.
					</p>
				</div>
			{:else}
				<div class="mt-1 overflow-x-auto">
					<table class="w-full border-collapse text-left font-mono text-[11px]">
						<thead>
							<tr class="text-ink-mute">
								<th class="pr-3 pb-1 font-normal">ts</th>
								<th class="pr-3 pb-1 font-normal">key</th>
								<th class="pr-3 pb-1 font-normal">value</th>
								<th class="pb-1 font-normal">source</th>
							</tr>
						</thead>
						<tbody>
							{#each readingsTable as r, i (i)}
								<tr class="border-t border-stone-800">
									<td class="pr-3 py-0.5 text-ink-quiet">{r.ts}</td>
									<td class="pr-3 py-0.5 text-stone-300">{r.key}</td>
									<td class="pr-3 py-0.5 text-amber-100">{formatReadingValue(r.value)}</td>
									<td class="py-0.5 text-ink-quiet">{r.source || '—'}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
				<ul class="mt-2 flex flex-wrap gap-x-3 gap-y-1 font-mono text-[10px] text-ink-quiet">
					{#each [...readingsSummary] as [key, info] (key)}
						<li>
							<span class="text-stone-300">{key}:</span> latest {formatReadingValue(
								info.latest.value
							)}{#if info.delta !== null}
								<!-- comparable — latest and previous share a measurement basis -->
								<span>&nbsp;(Δ{formatReadingDelta(info.delta)} vs previous)</span>
							{:else if info.basisMismatch}
								<!-- refused, not silent: latest and previous exist but measure
								     different populations (design-goal-oriented-engineering.md,
								     mirrors items.py's readings_index_line / `goal show`) — a
								     blank here would read as "no change", which is the failure
								     this branch exists to stop rendering. -->
								<span>&nbsp;(Δ refused: basis differs from previous sample)</span>
							{/if} · min {formatReadingValue(info.min)} · max {formatReadingValue(info.max)}
						</li>
					{/each}
				</ul>
			{/if}
		</section>

		<section aria-labelledby="contributing-heading">
			<h2
				id="contributing-heading"
				class="font-mono text-[10px] tracking-[0.16em] text-ink-mute uppercase"
			>
				contributing work · {cone.length}
			</h2>
			{#if cone.length === 0}
				<p class="mt-1 font-mono text-[11px] text-ink-quiet">
					nothing advances this goal yet — an item wears <span class="text-stone-300"
						>advances: {goal.id}</span
					> to join the cone.
				</p>
			{:else}
				<ul class="mt-1 space-y-0.5 font-mono text-xs" aria-label="contributing work">
					{#each cone as item (item.id)}
						{@render itemRow(item)}
					{/each}
				</ul>
			{/if}
		</section>

		<section aria-labelledby="callback-heading">
			<h2
				id="callback-heading"
				class="font-mono text-[10px] tracking-[0.16em] text-ink-mute uppercase"
			>
				blockers-on-you · {callback.length}
			</h2>
			{#if callback.length === 0}
				<p class="mt-1 font-mono text-[11px] text-ink-quiet">
					nothing waiting on you for this goal right now.
				</p>
			{:else}
				<ul class="mt-1 space-y-0.5 font-mono text-xs" aria-label="blockers on you">
					{#each callback as item (item.id)}
						{@render itemRow(item)}
					{/each}
				</ul>
			{/if}
		</section>
	</div>
{/if}
