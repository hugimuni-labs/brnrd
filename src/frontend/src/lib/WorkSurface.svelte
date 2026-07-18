<script lang="ts">
	import { fade } from 'svelte/transition';
	import { SvelteMap, SvelteSet } from 'svelte/reactivity';
	import {
		basename,
		buildNavTree,
		fileDirKey,
		fileLayer,
		headingAnchor,
		inlineTokens,
		markdownBlocks,
		splitIntoSections,
		type MarkdownBlock,
		type SurfaceResponse
	} from './surface';

	interface Props {
		data: SurfaceResponse;
	}
	let { data }: Props = $props();

	// Which layer sections are open in the nav (authored open by default; knowledge
	// and runs collapsed — the file list is too long to show all at once).
	let expandedLayers = new SvelteSet(['authored']);
	// Per-layer: which dir-group keys are open.
	let expandedDirs = new SvelteMap<string, SvelteSet<string>>();
	// Which section indices are expanded in the outline reader.
	let expandedSections = new SvelteSet<number>();
	// Fragment from a link click, resolved to a section index by the $effect below.
	let pendingAnchor = $state<string | null>(null);
	let selectedPath = $state('');

	let knownPaths = $derived(new Set(data.files.map((f) => f.path)));
	let navTree = $derived(buildNavTree(data.files));
	let selected = $derived(
		data.files.find((f) => f.path === selectedPath) ??
			data.files.find((f) => f.path === 'surface/index.md') ??
			data.files[0] ??
			null
	);
	let blocks = $derived(selected ? markdownBlocks(selected.markdown) : []);
	let sections = $derived(splitIntoSections(blocks));
	// True when every section (including any with empty tail) is open.
	let allSectionsExpanded = $derived(
		!!sections && sections.length > 0 && sections.every((_, i) => expandedSections.has(i))
	);

	// After a link navigation with a fragment, expand the matching section once
	// the new page's sections have been derived.
	$effect(() => {
		const anc = pendingAnchor;
		if (!anc) return;
		const secs = sections;
		if (!secs) {
			pendingAnchor = null;
			return;
		}
		const idx = secs.findIndex((s) => s.heading && headingAnchor(s.heading.text) === anc);
		if (idx !== -1) expandedSections.add(idx);
		pendingAnchor = null;
	});

	// Navigate to a file, auto-expanding its ancestors in the nav tree so the
	// selected row is always reachable without manual drilling.
	function select(path: string, anchor?: string | null) {
		selectedPath = path;
		const file = data.files.find((f) => f.path === path);
		if (file) {
			const layer = fileLayer(file);
			expandedLayers.add(layer);
			const dk = fileDirKey(path, layer);
			if (dk) {
				const dirSet = expandedDirs.get(layer) ?? new SvelteSet<string>();
				dirSet.add(dk);
				expandedDirs.set(layer, dirSet);
			}
		}
		// Reset section state; the $effect above will resolve the anchor once
		// sections are derived from the new page.
		expandedSections.clear();
		pendingAnchor = anchor ?? null;
	}

	function toggleLayer(layer: string) {
		if (expandedLayers.has(layer)) expandedLayers.delete(layer);
		else expandedLayers.add(layer);
	}

	function toggleDir(layer: string, dk: string) {
		const existing = expandedDirs.get(layer) ?? new SvelteSet<string>();
		if (existing.has(dk)) existing.delete(dk);
		else existing.add(dk);
		expandedDirs.set(layer, existing);
	}

	function toggleSection(i: number) {
		if (expandedSections.has(i)) expandedSections.delete(i);
		else expandedSections.add(i);
	}

	function expandAll() {
		if (sections) sections.forEach((_, i) => expandedSections.add(i));
	}

	function collapseAll() {
		expandedSections.clear();
	}
</script>

{#snippet inline(text: string)}
	{#each inlineTokens(text, selected?.path ?? '', knownPaths) as token, i (i)}
		{#if token.kind === 'strong'}<strong class="font-semibold text-stone-100">{token.text}</strong>
		{:else if token.kind === 'link' && token.target}<button
				class="text-amber-300 underline decoration-amber-700/70 underline-offset-2 hover:text-amber-100"
				onclick={() => select(token.target!, token.anchor)}>{token.text}</button
			>
		{:else if token.kind === 'link' && token.href}
			<a
				class="text-amber-300 underline decoration-amber-700/70 underline-offset-2 hover:text-amber-100"
				href={token.href}
				target="_blank"
				rel="external noreferrer">{token.text}</a
			>
		{:else if token.kind === 'link'}<span
				class="text-stone-500"
				title="corpus target is not present">{token.text}</span
			>
		{:else}{token.text}{/if}
	{/each}
{/snippet}

{#snippet renderBlock(b: MarkdownBlock)}
	{#if b.kind === 'heading'}
		{#if b.level === 1}<h2 class="mt-4 mb-2 text-lg font-semibold text-amber-100">
				{@render inline(b.text)}
			</h2>
		{:else}<h3 class="mt-4 mb-1 font-mono text-xs tracking-wide text-amber-200 uppercase">
				{@render inline(b.text)}
			</h3>{/if}
	{:else if b.kind === 'paragraph'}<p class="my-2 leading-relaxed">
			{@render inline(b.text)}
		</p>
	{:else if b.kind === 'quote'}<blockquote
			class="my-2 border-l-2 border-amber-800 pl-3 text-stone-400"
		>
			{@render inline(b.text)}
		</blockquote>
	{:else if b.kind === 'code'}<pre
			class="my-2 overflow-x-auto rounded bg-stone-950 p-3 font-mono text-xs text-stone-300"><code
				>{b.text}</code
			></pre>
	{:else if b.kind === 'list'}
		{#if b.ordered}<ol class="my-2 list-decimal space-y-1 pl-5">
				{#each b.items as item, i (i)}<li>{@render inline(item)}</li>{/each}
			</ol>
		{:else}<ul class="my-2 list-disc space-y-1 pl-5">
				{#each b.items as item, i (i)}<li>{@render inline(item)}</li>{/each}
			</ul>{/if}
	{/if}
{/snippet}

<div class="panel p-4">
	<div class="mb-3 flex items-center justify-between gap-3 text-sm">
		<span class="font-mono font-medium tracking-wide text-amber-200 uppercase">corpus</span>
		{#if data.reported_at}<span class="font-mono text-[10px] text-stone-600"
				>mirrored {new Date(data.reported_at).toLocaleString()}</span
			>{/if}
	</div>
	{#if data.files.length === 0}
		<p class="text-sm text-stone-500">No corpus mirrored yet.</p>
	{:else}
		<div class="grid gap-3 md:grid-cols-[minmax(11rem,0.28fr)_minmax(0,1fr)]">
			<!-- Nav tree: height-capped so 260-file lists don't stretch the page.
			     Each layer is a toggleable section; knowledge/runs sub-group by dir. -->
			<nav class="subpanel max-h-[70vh] overflow-y-auto p-2" aria-label="Corpus files">
				{#each navTree as navLayer (navLayer.layer)}
					{@const layerOpen = expandedLayers.has(navLayer.layer)}
					<!-- Layer header: count badge, chevron, toggleable -->
					<button
						class="mt-2 flex w-full items-center gap-1 rounded px-2 py-0.5 text-left font-mono text-[10px] tracking-wide uppercase first:mt-0 {layerOpen
							? 'text-stone-400 hover:text-stone-300'
							: 'text-stone-600 hover:text-stone-400'}"
						onclick={() => toggleLayer(navLayer.layer)}
					>
						<span class="shrink-0">{layerOpen ? '▾' : '▸'}</span>
						<span class="truncate">{navLayer.label}</span>
						<span class="ml-auto shrink-0 text-stone-700">·{navLayer.count}</span>
					</button>
					{#if layerOpen}
						{#if navLayer.dirs === null}
							<!-- Flat layer (authored surface pages) -->
							{#each navLayer.flatFiles as file (file.path)}
								<button
									class="block w-full truncate rounded px-2 py-1 text-left font-mono text-[11px] {file.path ===
									selected?.path
										? 'bg-amber-950/50 text-amber-200'
										: 'text-stone-500 hover:bg-stone-900 hover:text-stone-300'}"
									title={file.path}
									onclick={() => select(file.path)}>{basename(file.path)}</button
								>
							{/each}
						{:else}
							<!-- Dir-grouped layer (knowledge repos/<slug>, runs/<slug>/<run>) -->
							{#each navLayer.dirs as dir (dir.key)}
								{@const dirOpen = expandedDirs.get(navLayer.layer)?.has(dir.key) ?? false}
								<button
									class="mt-0.5 flex w-full items-center gap-1 rounded px-2 py-0.5 text-left font-mono text-[10px] {dirOpen
										? 'text-stone-500 hover:text-stone-400'
										: 'text-stone-600 hover:text-stone-500'}"
									onclick={() => toggleDir(navLayer.layer, dir.key)}
								>
									<span class="shrink-0">{dirOpen ? '▾' : '▸'}</span>
									<span class="truncate">{dir.key}</span>
									<span class="ml-auto shrink-0 text-stone-700">·{dir.count}</span>
								</button>
								{#if dirOpen}
									{#each dir.files as file (file.path)}
										<button
											class="block w-full truncate rounded py-1 pl-6 pr-2 text-left font-mono text-[11px] {file.path ===
											selected?.path
												? 'bg-amber-950/50 text-amber-200'
												: 'text-stone-500 hover:bg-stone-900 hover:text-stone-300'}"
											title={file.path}
											onclick={() => select(file.path)}>{basename(file.path)}</button
										>
									{/each}
								{/if}
							{/each}
						{/if}
					{/if}
				{/each}
			</nav>

			<article class="subpanel min-w-0 p-4 text-sm text-stone-300" in:fade={{ duration: 120 }}>
				<!-- Pane header: path + expand/collapse all (when outline mode is active) -->
				<div class="mb-3 flex items-baseline justify-between gap-2 border-b border-stone-800 pb-2">
					<span class="font-mono text-[10px] text-stone-600">{selected?.path}</span>
					{#if sections}
						<button
							class="shrink-0 font-mono text-[10px] text-amber-400/70 hover:text-amber-300"
							onclick={allSectionsExpanded ? collapseAll : expandAll}
							>{allSectionsExpanded ? '− collapse all' : '+ expand all'}</button
						>
					{/if}
				</div>
				{#if selected?.truncated}
					<p
						class="mb-3 rounded border border-amber-900/60 bg-amber-950/30 px-2 py-1 font-mono text-[10px] text-amber-300/80"
					>
						mirror truncated — this page is capped in the dashboard replica; the full page lives in
						the resident's knowledge.
					</p>
				{/if}
				{#if sections}
					<!-- Outline reader: each section collapses to heading + preview.
					     Preamble content (before the first split heading) is always visible. -->
					{#each sections as section, i (i)}
						<div class="mb-3">
							{#if section.heading}
								<!-- Section heading doubles as the expand/collapse toggle.
								     Plain text here — inline rendering inside a button risks nested buttons. -->
								<button
									class="flex w-full items-baseline gap-1.5 text-left"
									onclick={() => toggleSection(i)}
								>
									<span class="shrink-0 font-mono text-[10px] text-stone-600"
										>{section.tail.length > 0 ? (expandedSections.has(i) ? '▾' : '▸') : '·'}</span
									>
									{#if section.heading.level === 1}
										<span class="text-lg font-semibold text-amber-100">{section.heading.text}</span>
									{:else}
										<span class="font-mono text-xs tracking-wide text-amber-200 uppercase"
											>{section.heading.text}</span
										>
									{/if}
									{#if !expandedSections.has(i) && section.tail.length > 0}
										<span class="ml-auto shrink-0 font-mono text-[10px] text-stone-700"
											>+{section.tail.length}</span
										>
									{/if}
								</button>
							{/if}
							{#if section.preview}
								<div class:line-clamp-2={!!section.heading && !expandedSections.has(i)}>
									{@render renderBlock(section.preview)}
								</div>
							{/if}
							{#if !section.heading || expandedSections.has(i)}
								{#each section.tail as b, j (j)}
									{@render renderBlock(b)}
								{/each}
							{/if}
						</div>
					{/each}
				{:else}
					<!-- Flat reader: short pages or pages with no usable split headings. -->
					{#each blocks as b, i (i)}
						{@render renderBlock(b)}
					{/each}
				{/if}
			</article>
		</div>
	{/if}
</div>
