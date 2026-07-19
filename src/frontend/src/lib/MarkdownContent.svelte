<script lang="ts">
	// The one renderer for mirrored corpus Markdown. Both readers use it: the
	// corpus browser (WorkSurface, which drives its own outline and hands over
	// pre-split blocks) and the Wyrd run node page (which hands over raw
	// markdown). Typography and link resolution live here so the two cannot
	// drift apart.
	import { inlineTokens, markdownBlocks, type MarkdownBlock } from './surface';
	import { runNodeHrefForPath } from './runNode';
	import { typeReveal } from './transitions';

	interface Props {
		/** Raw page text. Ignored when `blocks` is supplied. */
		markdown?: string;
		/** Pre-parsed blocks, for callers that split a page themselves. */
		blocks?: MarkdownBlock[];
		/** Corpus path this text came from — resolves relative links. */
		sourcePath?: string;
		knownPaths?: Set<string>;
		/**
		 * In-page handler for a link that resolves to another corpus file. When
		 * a caller owns a reading pane (WorkSurface) it selects there. Without
		 * one, a link into a run node still navigates — as a real route — and
		 * anything else renders as inert text rather than a dead link.
		 */
		onNavigate?: (target: string, anchor: string | null) => void;
		/**
		 * Stream plain-text blocks in with the dashboard's `typeReveal` instead
		 * of painting them at once.
		 *
		 * Opt-in, and deliberately not the default: the corpus browser renders
		 * whole documents, where per-character reveal on a long page is noise.
		 * The run node is the opposite case — short, live, rewritten while you
		 * watch — and it was the one surface on this dashboard with no motion
		 * language at all (reported 2026-07-19: "no glitch typeReveal"). The
		 * node panel absorbed the LiveRuns card, whose every text line streamed,
		 * and did not carry the motion across; the same absorption had already
		 * dropped the relic links (#486) and the liveness dot (#495). Motion is
		 * an affordance here, not decoration: it is how this surface says *this
		 * text just arrived*, which on a card rewritten mid-run is the whole
		 * point.
		 *
		 * Only blocks that are entirely plain text reveal. `typeReveal` owns its
		 * node's DOM — it rebuilds the subtree into per-character cells — so a
		 * block carrying links or bold would have its markup flattened to
		 * characters and its affordances destroyed. Those render normally; a
		 * mixed page streams its prose and paints its links, which is the right
		 * trade and not a limitation worth engineering around.
		 */
		reveal?: boolean;
	}

	let {
		markdown = '',
		blocks: providedBlocks,
		sourcePath = '',
		knownPaths = new Set<string>(),
		onNavigate,
		reveal = false
	}: Props = $props();

	/** A block streams only if nothing in it would be destroyed by doing so. */
	function plain(text: string): boolean {
		return reveal && inlineTokens(text, sourcePath, knownPaths).every((t) => t.kind === 'text');
	}

	let blocks = $derived(providedBlocks ?? markdownBlocks(markdown));
</script>

{#snippet inline(text: string)}
	{#each inlineTokens(text, sourcePath, knownPaths) as token, i (i)}
		{#if token.kind === 'strong'}<strong class="font-semibold text-stone-100">{token.text}</strong>
		{:else if token.kind === 'link' && token.target && onNavigate}<button
				class="cursor-pointer text-amber-300 underline decoration-amber-700/70 underline-offset-2 hover:text-amber-100"
				onclick={() => onNavigate(token.target!, token.anchor)}>{token.text}</button
			>
		{:else if token.kind === 'link' && token.target && runNodeHrefForPath(token.target)}<a
				class="text-amber-300 underline decoration-amber-700/70 underline-offset-2 hover:text-amber-100"
				href={runNodeHrefForPath(token.target)}>{token.text}</a
			>
		{:else if token.kind === 'link' && token.href}<a
				class="text-amber-300 underline decoration-amber-700/70 underline-offset-2 hover:text-amber-100"
				href={token.href}
				target="_blank"
				rel="external noreferrer">{token.text}</a
			>
		{:else if token.kind === 'link'}<span
				class="text-ink-quiet"
				title="corpus target is not present">{token.text}</span
			>
		{:else}{token.text}{/if}
	{/each}
{/snippet}

{#snippet listItem(text: string)}
	{#if plain(text)}<span use:typeReveal={{ text }}>{text}</span>
	{:else}{@render inline(text)}{/if}
{/snippet}

{#snippet renderBlock(block: MarkdownBlock)}
	{#if block.kind === 'heading'}
		{#if block.level === 1}<h2 class="mt-4 mb-2 text-lg font-semibold text-amber-100">
				{@render inline(block.text)}
			</h2>
		{:else if plain(block.text)}<h3
				class="mt-4 mb-1 font-mono text-xs tracking-wide text-amber-200 uppercase"
				use:typeReveal={{ text: block.text }}
			>
				{block.text}
			</h3>
		{:else}<h3 class="mt-4 mb-1 font-mono text-xs tracking-wide text-amber-200 uppercase">
				{@render inline(block.text)}
			</h3>{/if}
	{:else if block.kind === 'paragraph'}
		{#if plain(block.text)}<p class="my-2 leading-relaxed" use:typeReveal={{ text: block.text }}>
				{block.text}
			</p>
		{:else}<p class="my-2 leading-relaxed">
				{@render inline(block.text)}
			</p>{/if}
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
				{#each block.items as item, i (i)}<li>{@render listItem(item)}</li>{/each}
			</ol>
		{:else}<ul class="my-2 list-disc space-y-1 pl-5">
				{#each block.items as item, i (i)}<li>{@render listItem(item)}</li>{/each}
			</ul>{/if}
	{/if}
{/snippet}

{#each blocks as block, i (i)}
	{@render renderBlock(block)}
{/each}
