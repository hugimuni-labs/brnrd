<script lang="ts">
	import { fade } from 'svelte/transition';
	import type { ResolvedPathname } from '$app/types';
	import MarkdownContent from './MarkdownContent.svelte';
	import { repoRunSlug, runIdSlug, runNodeHref } from './runNode';
	import { STATUS_BURNING, STATUS_COOLING, STATUS_UNKNOWN, STATUS_WARN } from './statusPalette';
	import {
		blockedItems,
		blockers,
		dependents,
		itemInTopics,
		itemRepos,
		liveTakenRuns,
		readyItems,
		resolveTopics,
		topicFaces,
		type ItemType,
		type WarpGraph,
		type WarpItem
	} from './warpGraph';

	// The warp, rendered as the maintainer asked for it: the unblocked items
	// colorful on top — glance, decide or do — and the blocked ones greyed
	// below, each expandable to the items holding it. The tree he floated
	// lives here as a *render* over flat storage: "unblocked on top" is a
	// query, and a query cannot rot the way a curated hierarchy does.

	interface Props {
		graph: WarpGraph;
		/** Canonical topic ids lit on the heddle rail; null = all. */
		selected?: ReadonlySet<string> | null;
		liveRunIds?: ReadonlySet<string>;
		knownPaths?: Set<string>;
		onOpenPage?: (path: string) => void;
		/** Tests seed an open item; the page leaves all folded. */
		initialOpenId?: string | null;
	}

	let {
		graph,
		selected = null,
		liveRunIds = new Set<string>(),
		knownPaths = new Set<string>(),
		onOpenPage = undefined,
		initialOpenId = null
	}: Props = $props();

	// svelte-ignore state_referenced_locally
	let openId = $state<string | null>(initialOpenId);

	// decision = the amber ask (the user's call), preparation = the frost of
	// work deliberately held for a hand, action = the burning dispatchable.
	// Untyped wears the unknown gray — a drift finding, visible as one.
	const TYPE_COLOR: Record<ItemType, string> = {
		decision: STATUS_WARN,
		preparation: STATUS_COOLING,
		action: STATUS_BURNING
	};
	const TYPE_MARK: Record<ItemType, string> = {
		decision: '◆',
		preparation: '◇',
		action: '●'
	};

	function typeColor(item: WarpItem): string {
		return item.type === null ? STATUS_UNKNOWN : TYPE_COLOR[item.type];
	}
	function typeMark(item: WarpItem): string {
		return item.type === null ? '▫' : TYPE_MARK[item.type];
	}

	let faces = $derived(topicFaces(graph));
	let ready = $derived(readyItems(graph).filter((item) => itemInTopics(item, graph, selected)));
	let held = $derived(blockedItems(graph).filter((item) => itemInTopics(item, graph, selected)));
	let openTotal = $derived(graph.items.filter((item) => item.state === 'open').length);
	let shown = $derived(ready.length + held.length);

	/** A `taken:` run's node href — resolvable only when the item's refs
	 *  name exactly one repo (the run route needs a repo slug; a guess that
	 *  404s is worse than plain text). */
	function takenHref(item: WarpItem, runId: string): ResolvedPathname | null {
		const repos = itemRepos(item);
		if (repos.length !== 1) return null;
		return runNodeHref(repoRunSlug(repos[0]), runIdSlug(runId));
	}

	async function copyPrompt(item: WarpItem) {
		// The ignition payload: the prompt plus the item's one address — the
		// daemon's weld scans event bodies for the id to write `taken:` back.
		const payload = `${item.prompt ?? item.headline}\n\nitem: ${item.id}`;
		try {
			await navigator.clipboard.writeText(payload);
		} catch {
			// Clipboard can be unavailable — the copy affordance simply no-ops
			// rather than breaking the row.
		}
	}
</script>

{#snippet itemRow(item: WarpItem, band: 'ready' | 'held')}
	{@const topics = resolveTopics(item, graph)}
	{@const live = liveTakenRuns(item, liveRunIds)}
	{@const edge = blockers(item, graph)}
	<li
		id={item.id}
		class="border-l-2 py-0.5 pl-2 {live.length > 0 ? 'bg-amber-100/5' : ''} {band === 'held'
			? 'border-stone-800'
			: ''}"
		style={band === 'ready' ? `border-left-color: ${typeColor(item)}` : ''}
	>
		<div class="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5">
			<span
				class="shrink-0 font-mono text-[10px]"
				style={band === 'ready' ? `color: ${typeColor(item)}` : ''}
				class:text-ink-mute={band === 'held'}
				aria-hidden="true">{typeMark(item)}</span
			>
			<button
				type="button"
				class="min-w-[9ch] flex-1 cursor-pointer break-words text-left leading-tight {band ===
				'ready'
					? 'text-amber-100'
					: 'text-ink-quiet'} hover:text-amber-50"
				aria-expanded={openId === item.id}
				onclick={() => (openId = openId === item.id ? null : item.id)}
			>
				{item.headline}
			</button>
			<span class="shrink-0 font-mono text-[9px] text-ink-mute">{item.id}</span>
			{#if item.type}
				<span
					class="shrink-0 font-mono text-[9px] tracking-wide uppercase"
					style={band === 'ready' ? `color: ${typeColor(item)}` : ''}
					class:text-ink-mute={band === 'held'}>{item.type}</span
				>
			{:else}
				<span class="shrink-0 font-mono text-[9px] tracking-wide text-ink-mute uppercase"
					>untyped</span
				>
			{/if}
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
			{#if live.length > 0}
				<span class="shrink-0 font-mono text-[9px] text-amber-300/90" title="held by a live run"
					>↯ weaving</span
				>
			{/if}
			{#if band === 'held' && edge.open.length > 0}
				<span class="shrink-0 font-mono text-[9px] text-ink-mute">
					needs {edge.open.map((blocker) => blocker.id).join(' ')}
				</span>
			{/if}
			{#if edge.dangling.length > 0}
				<span
					class="shrink-0 font-mono text-[9px] text-red-400/80"
					title="needs an item that does not exist"
				>
					{edge.dangling.join(' ')}?
				</span>
			{/if}
		</div>
		{#if openId === item.id}
			<div class="mt-1 mb-1 space-y-1.5 pl-4 text-[11px]" out:fade={{ duration: 100 }}>
				{#if item.bodyMarkdown}
					<MarkdownContent
						markdown={item.bodyMarkdown}
						sourcePath={item.path}
						{knownPaths}
						onNavigate={(target) => onOpenPage?.(target)}
					/>
				{/if}
				{#if band === 'held' && edge.open.length > 0}
					<p class="font-mono text-[10px] text-ink-quiet">
						held by
						{#each edge.open as blocker (blocker.id)}
							<a class="ml-1 text-amber-300/90 hover:text-amber-100" href={`#${blocker.id}`}
								>{blocker.id} {blocker.headline}</a
							>
						{/each}
					</p>
				{/if}
				{#if dependents(item, graph).length > 0}
					<p class="font-mono text-[10px] text-ink-quiet">
						unblocks
						{#each dependents(item, graph) as dep (dep.id)}
							<a class="ml-1 text-amber-300/90 hover:text-amber-100" href={`#${dep.id}`}>{dep.id}</a
							>
						{/each}
					</p>
				{/if}
				{#if item.refs.length > 0}
					<p class="font-mono text-[10px] text-ink-quiet">
						refs:
						{#each item.refs as ref, index (index)}
							{#if index > 0}<span class="text-ink-mute"> · </span>{/if}
							{#if ref.href}
								<!-- Forge/document refs are genuinely external destinations —
								     same escape CapabilityPanel takes: this row never assumes
								     client-side routing owns these links. -->
								<a
									href={ref.href}
									target="_blank"
									rel="noopener external"
									class="text-amber-300/90 hover:text-amber-100">{ref.label}</a
								>
							{:else}
								<span>{ref.label}</span>
							{/if}
						{/each}
					</p>
				{/if}
				{#if item.taken.length > 0}
					<p class="font-mono text-[10px] text-ink-quiet">
						taken:
						{#each item.taken as runId (runId)}
							{@const href = takenHref(item, runId)}
							{#if href}
								<a class="ml-1 text-amber-300/90 hover:text-amber-100" {href}>{runId}</a>
							{:else}
								<span class="ml-1">{runId}</span>
							{/if}
						{/each}
					</p>
				{/if}
				{#if item.prompt}
					<p class="flex flex-wrap items-baseline gap-x-2 font-mono text-[10px]">
						<span class="text-ink-quiet">{item.prompt}</span>
						<button
							type="button"
							class="shrink-0 cursor-pointer tracking-wide text-amber-300 uppercase hover:text-amber-100"
							onclick={() => copyPrompt(item)}
						>
							copy ⧉
						</button>
					</p>
				{/if}
			</div>
		{/if}
	</li>
{/snippet}

{#if shown < openTotal}
	<p class="mb-1 font-mono text-[10px] text-ink-mute">
		{shown} of {openTotal} open items · lensed by the heddles
	</p>
{/if}

{#if ready.length === 0 && held.length === 0}
	<p class="text-sm text-ink-quiet">
		the warp is bare — items are authored under <span class="font-mono">surface/warp/</span>.
	</p>
{:else}
	{#if ready.length > 0}
		<ul class="space-y-0.5 font-mono text-xs" aria-label="ready — unblocked items">
			{#each ready as item (item.id)}
				{@render itemRow(item, 'ready')}
			{/each}
		</ul>
	{/if}
	{#if held.length > 0}
		<p class="mt-2 mb-1 font-mono text-[9px] tracking-[0.16em] text-ink-mute uppercase">
			held · blocked by open items
		</p>
		<ul class="space-y-0.5 font-mono text-xs opacity-80" aria-label="held — blocked items">
			{#each held as item (item.id)}
				{@render itemRow(item, 'held')}
			{/each}
		</ul>
	{/if}
{/if}
