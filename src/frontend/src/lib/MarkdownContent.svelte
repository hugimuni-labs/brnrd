<script lang="ts">
	// The one renderer for mirrored corpus Markdown. Both readers use it: the
	// corpus browser (WorkSurface, which drives its own outline and hands over
	// pre-split blocks) and the Wyrd run node page (which hands over raw
	// markdown). Typography and link resolution live here so the two cannot
	// drift apart.
	import { inlineTokens, markdownBlocks, type MarkdownBlock } from './surface';
	import { runNodeHrefForPath } from './runNode';
	import { getContext } from 'svelte';
	import {
		REVEAL_LEDGER,
		revealBudgetMask,
		revealTimeline,
		typeReveal,
		type RevealLedger,
		type TypeRevealParams
	} from './transitions';

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
		 * Stream this document's text in with the dashboard's `typeReveal`
		 * instead of painting it at once.
		 *
		 * The reveal is how these surfaces say *this text just arrived*, which
		 * on a card rewritten mid-run is the whole point — and, per the
		 * maintainer 2026-07-19, is wanted on the read surfaces too, not only
		 * the live ones. Two earlier limits are gone with it:
		 *
		 * - It no longer skips blocks carrying links or bold. `typeReveal` owns
		 *   its node's DOM, so the old cut applied it to whole blocks and had to
		 *   refuse any block with markup in it — which on a run body or a kb
		 *   page is most of them, so the motion read as patchy rather than
		 *   absent. It now runs per inline *token*, inside a plain span nested in
		 *   the `<a>`/`<strong>`, and the tokens share one sweep
		 *   (`revealTimeline`) so a single head crosses the line. Links keep
		 *   their href; bold keeps its weight; the prose still streams.
		 * - It is no longer gated per page. `revealBudgetMask` bounds it to the
		 *   first screenful of characters, which is the honest form of the old
		 *   "a long page is noise" worry.
		 *
		 * Code blocks never reveal: `typeReveal` rebuilds text into per-word
		 * nowrap spans, which is exactly wrong for whitespace-significant
		 * content.
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

	let blocks = $derived(providedBlocks ?? markdownBlocks(markdown));

	/** Revealable characters in a block — code is excluded, so it costs none. */
	function blockLength(block: MarkdownBlock): number {
		if (block.kind === 'code') return 0;
		if (block.kind === 'list') return block.items.reduce((sum, item) => sum + item.length, 0);
		return block.text.length;
	}

	// A page that mounts several of these shares one budget between them (see
	// `revealLedger`); a lone renderer with no page around it falls back to its
	// own. Without the ledger the run node's ten documents each passed their own
	// cap and together animated 14,500 cells.
	const ledger = getContext<RevealLedger | undefined>(REVEAL_LEDGER);

	let revealing = $derived.by(() => {
		if (!reveal) return blocks.map(() => false);
		const lengths = blocks.map(blockLength);
		return ledger ? ledger.claim(sourcePath, lengths) : revealBudgetMask(lengths);
	});

	/**
	 * Blocks enter as a chorus, not in unison. The stagger is per block and
	 * uniform *within* one — the tokens of a line must share a start instant or
	 * their shared sweep stops agreeing about where the head is.
	 */
	const BLOCK_STAGGER_MS = 55;
	const BLOCK_STAGGER_CAP_MS = 640;
	function blockDelay(index: number): number {
		return Math.min(BLOCK_STAGGER_CAP_MS, index * BLOCK_STAGGER_MS);
	}
</script>

<!-- One token's text, streaming or painted. Splitting this out keeps every
     token kind below to a single branch instead of a revealed/plain pair. -->
{#snippet label(text: string, stream: TypeRevealParams | null)}
	{#if stream}<span use:typeReveal={stream}>{text}</span>{:else}{text}{/if}
{/snippet}

{#snippet inline(text: string, streams: boolean, delay: number)}
	{@const tokens = inlineTokens(text, sourcePath, knownPaths)}
	{@const sweep = revealTimeline(tokens.map((token) => token.text.length))}
	{#each tokens as token, i (i)}
		{@const stream = streams ? { text: token.text, delay, ...sweep[i] } : null}
		{#if token.kind === 'strong'}<strong class="font-semibold text-stone-100"
				>{@render label(token.text, stream)}</strong
			>
		{:else if token.kind === 'link' && token.target && onNavigate}<button
				class="cursor-pointer text-amber-300 underline decoration-amber-700/70 underline-offset-2 hover:text-amber-100"
				onclick={() => onNavigate(token.target!, token.anchor)}
				>{@render label(token.text, stream)}</button
			>
		{:else if token.kind === 'link' && token.target && runNodeHrefForPath(token.target)}<a
				class="text-amber-300 underline decoration-amber-700/70 underline-offset-2 hover:text-amber-100"
				href={runNodeHrefForPath(token.target)}>{@render label(token.text, stream)}</a
			>
		{:else if token.kind === 'link' && token.href}<a
				class="text-amber-300 underline decoration-amber-700/70 underline-offset-2 hover:text-amber-100"
				href={token.href}
				target="_blank"
				rel="external noreferrer">{@render label(token.text, stream)}</a
			>
		{:else if token.kind === 'link'}<span
				class="text-ink-quiet"
				title="corpus target is not present">{@render label(token.text, stream)}</span
			>
		{:else}{@render label(token.text, stream)}{/if}
	{/each}
{/snippet}

{#snippet renderBlock(block: MarkdownBlock, streams: boolean, delay: number)}
	{#if block.kind === 'heading'}
		{#if block.level === 1}<h2 class="mt-4 mb-2 text-lg font-semibold text-amber-100">
				{@render inline(block.text, streams, delay)}
			</h2>
		{:else}<h3 class="mt-4 mb-1 font-mono text-xs tracking-wide text-amber-200 uppercase">
				{@render inline(block.text, streams, delay)}
			</h3>{/if}
	{:else if block.kind === 'paragraph'}<p class="my-2 leading-relaxed">
			{@render inline(block.text, streams, delay)}
		</p>
	{:else if block.kind === 'quote'}<blockquote
			class="my-2 border-l-2 border-amber-800 pl-3 text-stone-400"
		>
			{@render inline(block.text, streams, delay)}
		</blockquote>
	{:else if block.kind === 'code'}<pre
			class="my-2 overflow-x-auto rounded bg-stone-950 p-3 font-mono text-xs text-stone-300"><code
				>{block.text}</code
			></pre>
	{:else if block.kind === 'list'}
		{#if block.ordered}<ol class="my-2 list-decimal space-y-1 pl-5">
				{#each block.items as item, i (i)}<li>{@render inline(item, streams, delay)}</li>{/each}
			</ol>
		{:else}<ul class="my-2 list-disc space-y-1 pl-5">
				{#each block.items as item, i (i)}<li>{@render inline(item, streams, delay)}</li>{/each}
			</ul>{/if}
	{/if}
{/snippet}

{#each blocks as block, i (i)}
	{@render renderBlock(block, revealing[i], blockDelay(i))}
{/each}
