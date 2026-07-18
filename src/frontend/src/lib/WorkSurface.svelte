<script lang="ts">
	import { fade } from 'svelte/transition';
	import { groupByLayer, inlineTokens, markdownBlocks, type SurfaceResponse } from './surface';

	interface Props {
		data: SurfaceResponse;
	}
	let { data }: Props = $props();
	// A large knowledge page (the 890KB log) renders collapsed until asked —
	// the corpus browser stays responsive when a huge file is selected.
	const COLLAPSE_BLOCKS = 60;
	let selectedPath = $state('');
	let expanded = $state(false);
	let knownPaths = $derived(new Set(data.files.map((file) => file.path)));
	let groups = $derived(groupByLayer(data.files));
	let selected = $derived(
		data.files.find((file) => file.path === selectedPath) ??
			data.files.find((file) => file.path === 'surface/index.md') ??
			data.files[0] ??
			null
	);
	let blocks = $derived(selected ? markdownBlocks(selected.markdown) : []);
	let collapsed = $derived(!expanded && blocks.length > COLLAPSE_BLOCKS);
	let shownBlocks = $derived(collapsed ? blocks.slice(0, COLLAPSE_BLOCKS) : blocks);
	function select(path: string) {
		selectedPath = path;
		expanded = false;
	}
</script>

{#snippet inline(text: string)}
	{#each inlineTokens(text, selected?.path ?? '', knownPaths) as token}
		{#if token.kind === 'strong'}<strong class="font-semibold text-stone-100">{token.text}</strong>
		{:else if token.kind === 'link' && token.target}<button
				class="text-amber-300 underline decoration-amber-700/70 underline-offset-2 hover:text-amber-100"
				onclick={() => select(token.target!)}>{token.text}</button
			>
		{:else if token.kind === 'link' && token.href}<a
				class="text-amber-300 underline decoration-amber-700/70 underline-offset-2 hover:text-amber-100"
				href={token.href}
				target="_blank"
				rel="noreferrer">{token.text}</a
			>
		{:else if token.kind === 'link'}<span
				class="text-stone-500"
				title="corpus target is not present">{token.text}</span
			>
		{:else}{token.text}{/if}
	{/each}
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
			<nav class="subpanel h-fit p-2" aria-label="Corpus files">
				{#each groups as group (group.layer)}
					<p
						class="mt-2 mb-1 px-2 font-mono text-[10px] tracking-wide text-stone-600 uppercase first:mt-0"
					>
						{group.label}
					</p>
					{#each group.files as file (file.path)}
						<button
							class="block w-full truncate rounded px-2 py-1 text-left font-mono text-[11px] {file.path ===
							selected?.path
								? 'bg-amber-950/50 text-amber-200'
								: 'text-stone-500 hover:bg-stone-900 hover:text-stone-300'}"
							onclick={() => select(file.path)}>{file.path}</button
						>
					{/each}
				{/each}
			</nav>
			<article class="subpanel min-w-0 p-4 text-sm text-stone-300" in:fade={{ duration: 120 }}>
				<p class="mb-3 border-b border-stone-800 pb-2 font-mono text-[10px] text-stone-600">
					{selected?.path}
				</p>
				{#if selected?.truncated}
					<p
						class="mb-3 rounded border border-amber-900/60 bg-amber-950/30 px-2 py-1 font-mono text-[10px] text-amber-300/80"
					>
						mirror truncated — this page is capped in the dashboard replica; the full page
						lives in the resident's knowledge.
					</p>
				{/if}
				{#each shownBlocks as block}
					{#if block.kind === 'heading'}
						{#if block.level === 1}<h2 class="mt-4 mb-2 text-lg font-semibold text-amber-100">
								{@render inline(block.text)}
							</h2>
						{:else}<h3 class="mt-4 mb-1 font-mono text-xs tracking-wide text-amber-200 uppercase">
								{@render inline(block.text)}
							</h3>{/if}
					{:else if block.kind === 'paragraph'}<p class="my-2 leading-relaxed">
							{@render inline(block.text)}
						</p>
					{:else if block.kind === 'quote'}<blockquote
							class="my-2 border-l-2 border-amber-800 pl-3 text-stone-400"
						>
							{@render inline(block.text)}
						</blockquote>
					{:else if block.kind === 'code'}<pre
							class="my-2 overflow-x-auto rounded bg-stone-950 p-3 font-mono text-xs text-stone-300"><code
								>{block.text}</code
							></pre>
					{:else if block.kind === 'list'}
						{#if block.ordered}<ol class="my-2 list-decimal space-y-1 pl-5">
								{#each block.items as item}<li>{@render inline(item)}</li>{/each}
							</ol>
						{:else}<ul class="my-2 list-disc space-y-1 pl-5">
								{#each block.items as item}<li>{@render inline(item)}</li>{/each}
							</ul>{/if}
					{/if}
				{/each}
				{#if collapsed}
					<button
						class="mt-2 rounded border border-stone-800 px-2 py-1 font-mono text-[10px] text-amber-300 hover:bg-stone-900"
						onclick={() => (expanded = true)}
						>show {blocks.length - COLLAPSE_BLOCKS} more block(s)</button
					>
				{/if}
			</article>
		</div>
	{/if}
</div>
