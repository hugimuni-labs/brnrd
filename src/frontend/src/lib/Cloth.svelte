<script lang="ts">
	import { fade } from 'svelte/transition';
	import { SvelteSet } from 'svelte/reactivity';
	import { glitchReveal } from './transitions';
	import { loomPastWindowLabel } from './loomBand';
	import {
		clothSelvage,
		groupClothDays,
		inClothWindow,
		selvageParts,
		weaveCloth,
		type ClothLine,
		type ClothTree
	} from './cloth';
	import { LENS_ALL, applyLens, availableLenses, reconcileLens } from './loomLens';
	import type { RunLedgerRow } from './runLedger';

	// The cloth — the past band, v1 (design-work-layers.md). The window's
	// done work as root-run trees, one curated line each, strand strands
	// folded beneath and expanded on demand; the selvage (the cloth's
	// self-finished edge) runs across the top as one compact spend→produce
	// row. The page owns the rows and the window — this component only
	// weaves.

	interface Props {
		rows: RunLedgerRow[] | null;
		now: number;
		windowMs: number;
		stale: boolean;
	}

	let { rows, now, windowMs, stale }: Props = $props();

	// The lens rail (the dissolution, 2026-08-02). The chips lens the *past
	// inventory*, and the cloth is the past's one object now, so the rail
	// moved here from the loom band. The vocabulary is still derived, never
	// declared (`loomLens.ts`), read off the same window of rows the weave
	// renders. The lens is local view state, like a fold: it slices what
	// this cloth shows, and nothing outside the cloth ever asks about it.
	let windowRows = $derived((rows ?? []).filter((row) => inClothWindow(row, now, windowMs)));
	let lenses = $derived(availableLenses(windowRows));
	let lens = $state<string>(LENS_ALL);
	// A selection can outlive its lens (rows aged out, the vocabulary moved).
	// Reconciling here keeps the weave and the chip row from disagreeing.
	let activeLens = $derived(reconcileLens(lens, lenses));

	// Day rules, folds, repo chips and the drop count all recompute on the
	// lensed set — the weave *is* the lensed view. The selvage does not: it
	// is the hem of the whole cloth, not of a lens, so it sums the whole
	// window regardless of which chip is lit.
	let weave = $derived(
		rows === null ? null : weaveCloth(applyLens(windowRows, activeLens), now, windowMs)
	);
	let days = $derived(weave === null ? null : groupClothDays(weave.trees));
	let selvage = $derived(rows === null ? null : selvageParts(clothSelvage(rows, now, windowMs)));

	// Expansion is local UI state keyed by the root's id — not part of the
	// fetched data, so a re-poll doesn't fold an open brood. The unnamed
	// fold keys by the day instead: one quiet line per day, opened per day.
	let expanded = new SvelteSet<string>();
	let unfolded = new SvelteSet<string>();

	function toggle(set: SvelteSet<string>, id: string) {
		if (set.has(id)) set.delete(id);
		else set.add(id);
	}
</script>

{#snippet curatedLine(line: ClothLine, child: boolean)}
	<!-- The band's bar language at the cloth's zoom: a slim leading duration
	     bar — width from `loomBarFraction` against the window-wide max (bars
	     compare across days), color from the shelf's thermal-age stops, bare
	     runs dimmed the way the shelf dims them. An accent, not a background:
	     the text line stays the row's voice. Strand rows recede like the
	     band's nested children — a shorter, thinner, dimmer bar. -->
	<span
		class="shrink-0 self-center overflow-hidden rounded-[1px] bg-stone-900/60 {child
			? 'h-[2px] w-7 opacity-70'
			: 'h-[3px] w-10'}"
		aria-hidden="true"
	>
		<span
			class="block h-full rounded-[1px]"
			class:opacity-40={line.bare}
			style={`width: ${(line.barFraction * 100).toFixed(2)}%; background-color: ${line.color}`}
		></span>
	</span>
	{#if child}
		<span class="shrink-0 text-ink-mute" aria-hidden="true">↳</span>
	{/if}
	<!-- The title wraps whole (min-w-0 + break-words) — same call the warp's
	     headlines took in #978: a second line beats an amputated clause. -->
	{#if line.href}
		<a
			href={line.href}
			class="min-w-0 break-words text-amber-100 hover:text-amber-50"
			class:opacity-60={line.bare}
		>
			{line.name}
		</a>
	{:else}
		<span class="min-w-0 break-words text-stone-200" class:opacity-60={line.bare}>{line.name}</span>
	{/if}
	<!-- Repo chip only when this row is off the window's dominant repo —
	     a single-repo window says nothing per row (the whole cloth is that
	     repo). Short name in the row; the full label rides the hover. -->
	{#if line.repoChip}
		<span class="hidden shrink-0 text-[10px] text-ink-mute sm:inline" title={line.repoChip.full}>
			{line.repoChip.short}
		</span>
	{/if}
	{#if line.chips.length > 0}
		<span class="shrink-0 text-stone-300">
			{line.chips.map((chip) => chip.label).join(' ')}
		</span>
	{/if}
	{#if line.items.length > 0}
		<!-- THE WELD (#972): the warp item this run was ignited from — the
		     address is the reference; the item's `taken:` row points back. -->
		<span
			class="hidden shrink-0 font-mono text-[10px] text-amber-300/80 sm:inline"
			title="ignited from the warp"
		>
			🧵 {line.items.join(' · ')}
		</span>
	{/if}
	<span class="ml-auto shrink-0 text-[10px] whitespace-nowrap text-ink-mute">
		{line.duration} · {line.age}
	</span>
{/snippet}

{#snippet runRow(tree: ClothTree, index: number)}
	<div role="listitem" in:glitchReveal={{ duration: 240, delay: index * 24 }}>
		<div class="flex min-w-0 items-baseline gap-2 font-mono text-xs leading-relaxed">
			{@render curatedLine(tree.root, false)}
			{#if tree.children.length > 0}
				<button
					type="button"
					class="shrink-0 cursor-pointer text-[10px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
					aria-expanded={expanded.has(tree.root.id)}
					onclick={() => toggle(expanded, tree.root.id)}
				>
					{expanded.has(tree.root.id) ? '▾' : '▸'}
					{tree.children.length} strand{tree.children.length === 1 ? '' : 's'}
				</button>
			{/if}
		</div>
		{#if expanded.has(tree.root.id)}
			<div class="mt-0.5 space-y-0.5" out:fade={{ duration: 100 }}>
				{#each tree.children as child, childIndex (child.id)}
					<div
						class="ml-4 flex min-w-0 items-baseline gap-2 font-mono text-[11px] leading-relaxed"
						in:glitchReveal={{ duration: 240, delay: childIndex * 24 }}
					>
						{@render curatedLine(child, true)}
					</div>
				{/each}
			</div>
		{/if}
	</div>
{/snippet}

<div class="panel p-4">
	<div class="mb-3 flex items-center justify-between gap-2 text-sm">
		<span class="font-mono font-medium tracking-wide text-amber-200 uppercase">the cloth</span>
		<span class="flex items-center gap-2">
			{#if stale}
				<span
					class="border border-sky-900/60 bg-sky-950/40 px-1.5 py-0.5 font-mono text-[10px] tracking-wide text-sky-300 uppercase"
					>stale report</span
				>
			{/if}
			<span class="font-mono text-[10px] tracking-[0.16em] text-ink-mute uppercase">
				past · {loomPastWindowLabel(windowMs)}
			</span>
		</span>
	</div>

	<!-- The lens rail, above the weave it slices. Every chip is derived from
	     the rows in this window — origins from `source_system`, shapes from
	     the relic manifests, the strand stack from `is_subspawn`. It renders
	     even over an empty weave: a lit chip over zero rows must stay
	     un-clickable-off or the reader is trapped in an empty slice. -->
	{#if lenses.length > 1}
		<div
			class="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1 font-mono text-[9px] leading-none"
			role="group"
			aria-label="lenses over the cloth"
		>
			{#each lenses as candidate (candidate.id)}
				<button
					type="button"
					class="cursor-pointer tracking-[0.08em] uppercase transition-colors"
					class:text-amber-200={activeLens === candidate.id}
					class:text-ink-mute={activeLens !== candidate.id}
					class:hover:text-stone-400={activeLens !== candidate.id}
					aria-pressed={activeLens === candidate.id}
					title={`${candidate.count} run${candidate.count === 1 ? '' : 's'} · ${candidate.facet}`}
					onclick={() => (lens = activeLens === candidate.id ? LENS_ALL : candidate.id)}
				>
					{candidate.label}<span class="ml-1 text-ink-mute">{candidate.count}</span>
				</button>
			{/each}
		</div>
	{/if}

	{#if weave === null || days === null || selvage === null}
		<p class="font-mono text-[10px] text-ink-quiet">reading the cloth…</p>
	{:else}
		<!-- The selvage: the cloth's self-finished edge. One quiet row, first.
		     It renders whenever the window holds anything — even under a lens
		     that empties the weave — because it hems the whole cloth, never
		     the lensed slice. -->
		{#if windowRows.length > 0}
			<p
				class="mb-2 flex flex-wrap items-baseline gap-x-2 gap-y-0.5 border-b border-stone-800/70 pb-2 font-mono text-[10px] text-ink-quiet"
				aria-label="spend and produce over the window"
			>
				<span class="tracking-[0.16em] text-ink-mute uppercase">selvage</span>
				{#each selvage as part, index (index)}
					<span>{part}</span>
				{/each}
			</p>
		{/if}

		{#if weave.trees.length === 0}
			<!-- An empty weave under an active lens means something different
			     from an empty window — saying "nothing woven" while rows sit
			     one chip away would be the cloth lying about its own contents. -->
			<p class="font-mono text-[10px] text-ink-quiet">
				{activeLens === LENS_ALL
					? 'nothing woven in this window yet.'
					: 'nothing in this window matches this lens.'}
			</p>
		{/if}

		<!-- The weave: root runs day by day, newest day first, one curated
		     line each; a slim quiet rule gives ~30 rows their day rhythm. The
		     past glitches (band grammar), so lines assemble rather than fade. -->
		<div class="space-y-2">
			{#each days as day (day.key)}
				<div>
					<div
						class="mb-1 flex items-center gap-2 font-mono text-[10px] tracking-[0.16em] text-ink-mute uppercase"
					>
						<span class="h-px w-4 bg-stone-800/70" aria-hidden="true"></span>
						<span>{day.dayLabel} · {day.runCount} run{day.runCount === 1 ? '' : 's'}</span>
						<span class="h-px min-w-4 flex-1 bg-stone-800/70" aria-hidden="true"></span>
					</div>
					<div class="space-y-0.5" role="list" aria-label={`runs closed on ${day.dayLabel}`}>
						{#each day.trees as tree, index (tree.root.id)}
							{@render runRow(tree, index)}
						{/each}
						{#if day.unnamed}
							<!-- The nameless fold: runs whose only title would be a raw
							     id collapse into one quiet line per day — expandable,
							     never dropped. A named run never lands here. -->
							<div role="listitem">
								<button
									type="button"
									class="flex cursor-pointer items-baseline gap-2 font-mono text-[11px] leading-relaxed text-ink-quiet hover:text-stone-300"
									aria-expanded={unfolded.has(day.key)}
									onclick={() => toggle(unfolded, day.key)}
								>
									<span class="shrink-0" aria-hidden="true">▫</span>
									<span>{day.unnamed.label}</span>
								</button>
								{#if unfolded.has(day.key)}
									<div
										class="mt-0.5 ml-4 space-y-0.5"
										role="list"
										aria-label={`unnamed runs closed on ${day.dayLabel}`}
										out:fade={{ duration: 100 }}
									>
										{#each day.unnamed.trees as tree, index (tree.root.id)}
											{@render runRow(tree, index)}
										{/each}
									</div>
								{/if}
							</div>
						{/if}
					</div>
				</div>
			{/each}
		</div>

		<!-- The honest hem: the cap bounds the DOM, never the truth. -->
		{#if weave.dropped > 0}
			<p class="mt-2 font-mono text-[10px] text-ink-mute">
				+ {weave.dropped} older in the window
			</p>
		{/if}
	{/if}
</div>
