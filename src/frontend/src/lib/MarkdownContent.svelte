<script lang="ts">
	import { inlineTokens, markdownBlocks, type MarkdownBlock } from './surface';

	interface Props {
		markdown: string;
		sourcePath?: string;
		knownPaths?: Set<string>;
	}

	let { markdown, sourcePath = '', knownPaths = new Set<string>() }: Props = $props();
	let blocks = $derived(markdownBlocks(markdown));
</script>

{#snippet inline(text: string)}
	{#each inlineTokens(text, sourcePath, knownPaths) as token, i (i)}
		{#if token.kind === 'strong'}<strong class="font-semibold text-stone-100">{token.text}</strong>
		{:else if token.kind === 'link' && token.href}<a
				class="text-amber-300 underline decoration-amber-700/70 underline-offset-2 hover:text-amber-100"
				href={token.href}
				target="_blank"
				rel="external noreferrer">{token.text}</a
			>
		{:else if token.kind === 'link'}<span class="text-stone-400">{token.text}</span>
		{:else}{token.text}{/if}
	{/each}
{/snippet}

{#snippet renderBlock(block: MarkdownBlock)}
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
				{#each block.items as item, i (i)}<li>{@render inline(item)}</li>{/each}
			</ol>
		{:else}<ul class="my-2 list-disc space-y-1 pl-5">
				{#each block.items as item, i (i)}<li>{@render inline(item)}</li>{/each}
			</ul>{/if}
	{/if}
{/snippet}

{#each blocks as block, i (i)}
	{@render renderBlock(block)}
{/each}
