<script lang="ts">
	import BackchannelQueue from './BackchannelQueue.svelte';
	import WarpStack from './WarpStack.svelte';
	import {
		backchannelChip,
		backchannelShowClear,
		buildBackchannelItems,
		needsPreview
	} from './backchannel';
	import type { AuthoredBackchannelItem, BackchannelItemKind } from './backchannelPage';
	import type { ConfigChangeRequestItem } from './configRequests';
	import type { PRReviewItem } from './prReviewQueue';
	import { STATUS_GOOD, STATUS_SPENT, STATUS_UNKNOWN, STATUS_WARN } from './statusPalette';
	import type { WarpLayer } from './warp';
	import type { WithheldLane } from './withheld';

	// The warp band (the flip's grave, 2026-08-02): the layer stack is the
	// standing body and renders ALWAYS — no state, feed, or tab removes it.
	// The old shape defaulted a heddle to the needs-you list whenever items
	// waited, so a daemon restart that resolved the feeds made the stack
	// visually vanish behind a tab. The needs-you queue is now a compact
	// strip *above* the stack: count + top asks collapsed, the full list
	// (authored + derived sub-lenses, content and counts intact) behind an
	// in-place accordion. Feed state only ever touches the strip's own chip.

	interface Props {
		/** False while the surface fetch is still in flight — the stack slot
		 *  renders "stringing…" rather than a premature bare-warp verdict. */
		surfaceLoaded: boolean;
		layers: WarpLayer[];
		/** Corpus paths, for resolving internal links inside item bodies. */
		knownPaths?: Set<string>;
		authoredItems?: AuthoredBackchannelItem[];
		prs: PRReviewItem[] | null;
		requests: ConfigChangeRequestItem[] | null;
		/** All backchannel feeds resolved (loaded or errored) — until then the
		 *  strip's chip says "counting…" instead of presenting a partial sum
		 *  as a verdict (the #480 tensed-absence rule). */
		feedsResolved: boolean;
		stale: boolean;
		now: number;
		withheld?: WithheldLane | null;
		prError?: string | null;
		configError?: string | null;
		/** Tests seed the strip open; the page leaves it false (collapsed). */
		initialNeedsOpen?: boolean;
	}

	let {
		surfaceLoaded,
		layers,
		knownPaths = new Set<string>(),
		authoredItems = [],
		prs,
		requests,
		feedsResolved,
		stale,
		now,
		withheld = null,
		prError = null,
		configError = null,
		initialNeedsOpen = false
	}: Props = $props();

	// svelte-ignore state_referenced_locally
	let needsOpen = $state(initialNeedsOpen);

	const NEEDS_FOLD_ID = 'warp-needs-fold';
	const PREVIEW_LIMIT = 3;

	// Same four-kind palette the queue itself wears — no new hue for the strip.
	const KIND_COLOR: Record<BackchannelItemKind, string> = {
		decide: STATUS_WARN,
		review: STATUS_GOOD,
		read: STATUS_UNKNOWN,
		act: STATUS_SPENT
	};

	let derivedItems = $derived(buildBackchannelItems(prs ?? [], requests ?? []));
	let pendingCount = $derived(authoredItems.length + derivedItems.length);

	// The strip's whole read of feed state lives in this one chip: counting
	// while unresolved, "withheld" quietly when the only truth is a withheld
	// lane, the attributed "N authored · M derived" otherwise. The stack
	// below never hears about any of it.
	let chip = $derived(
		feedsResolved && pendingCount === 0 && withheld !== null
			? 'withheld'
			: backchannelChip(feedsResolved, authoredItems.length, derivedItems.length)
	);

	// Top asks: decision/action items lead (his 08-02 read: "the top item in
	// all that should be a decision/action ask") — the ordering is
	// `needsPreview`'s contract, unit-pinned in backchannel.test.ts.
	let preview = $derived(needsPreview(authoredItems, derivedItems, PREVIEW_LIMIT));
</script>

<!-- needs you: a compact strip above the stack, never a replacement for it. -->
<div class="subpanel px-3 py-2 text-xs">
	<button
		type="button"
		class="flex w-full cursor-pointer flex-wrap items-baseline gap-x-2 gap-y-0.5 text-left"
		aria-expanded={needsOpen}
		aria-controls={NEEDS_FOLD_ID}
		onclick={() => (needsOpen = !needsOpen)}
	>
		<span class="font-mono text-[10px] text-ink-quiet" aria-hidden="true"
			>{needsOpen ? '▾' : '▸'}</span
		>
		<span
			class="font-mono text-[11px] tracking-wide uppercase {pendingCount > 0
				? 'text-amber-200'
				: 'text-ink-quiet'}">needs you</span
		>
		<span class="font-mono text-[10px] text-ink-quiet">· {chip}</span>
	</button>
	{#if !needsOpen && preview.length > 0}
		<!-- Collapsed: the top asks by headline only — enough to answer "what
		     does the resident need from me?" without unfolding anything. -->
		<ul class="mt-1 space-y-0.5">
			{#each preview as row (row.key)}
				<li class="flex flex-wrap items-baseline gap-x-2">
					<!-- Headlines wrap whole; never a mid-word ellipsis. -->
					<span class="min-w-0 flex-1 leading-tight break-words text-amber-100/90"
						>{row.headline}</span
					>
					{#if row.kind}
						<span
							class="shrink-0 font-mono text-[10px] tracking-wide uppercase"
							style={`color: ${KIND_COLOR[row.kind]}`}>{row.kind}</span
						>
					{/if}
				</li>
			{/each}
			{#if pendingCount > preview.length}
				<li class="font-mono text-[10px] text-ink-quiet">+{pendingCount - preview.length} more</li>
			{/if}
		</ul>
	{/if}
	{#if needsOpen}
		<div class="mt-2" id={NEEDS_FOLD_ID}>
			{#if prError}
				<p class="mb-2 text-sm text-red-400">{prError}</p>
			{/if}
			{#if configError}
				<p class="mb-2 text-sm text-red-400">{configError}</p>
			{/if}
			{#if backchannelShowClear(feedsResolved, pendingCount, withheld !== null)}
				<!-- The clear verdict only once every feed has answered; before
				     that an empty sum is an unmeasured absence, not a zero. -->
				<p class="text-sm text-ink-quiet">nothing waits on you — the queue is clear.</p>
			{:else if pendingCount === 0 && !feedsResolved}
				<p class="text-sm text-ink-quiet">counting…</p>
			{:else}
				<!-- The full list, authored + derived sub-lenses intact. -->
				<BackchannelQueue
					{authoredItems}
					{knownPaths}
					prs={prs ?? []}
					requests={requests ?? []}
					{stale}
					{now}
					{withheld}
				/>
			{/if}
		</div>
	{/if}
</div>

<!-- The standing body: the layer stack, always. -->
<div class="mt-3">
	{#if !surfaceLoaded}
		<p class="text-sm text-ink-quiet">stringing…</p>
	{:else if layers.length === 0}
		<p class="text-sm text-ink-quiet">
			the warp is bare — layers are authored under
			<span class="font-mono">surface/layers/</span>.
		</p>
	{:else}
		<WarpStack {layers} {knownPaths} />
	{/if}
</div>
