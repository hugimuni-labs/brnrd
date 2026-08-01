<script lang="ts">
	import { fade } from 'svelte/transition';
	import { SvelteSet } from 'svelte/reactivity';
	import { glitchReveal } from './transitions';
	import { loomPastWindowLabel } from './loomBand';
	import { clothSelvage, selvageParts, weaveCloth, type ClothLine } from './cloth';
	import type { RunLedgerRow } from './runLedger';

	// The cloth — the past band, v1 (design-work-layers.md). The window's
	// done work as root-run trees, one curated line each, workers folded
	// beneath and expanded on demand; the selvage (the cloth's self-finished
	// edge) runs across the top as one compact spend→produce row. The page
	// owns the rows and the window — this component only weaves.

	interface Props {
		rows: RunLedgerRow[] | null;
		now: number;
		windowMs: number;
		stale: boolean;
	}

	let { rows, now, windowMs, stale }: Props = $props();

	let weave = $derived(rows === null ? null : weaveCloth(rows, now, windowMs));
	let selvage = $derived(rows === null ? null : selvageParts(clothSelvage(rows, now, windowMs)));

	// Expansion is local UI state keyed by the root's id — not part of the
	// fetched data, so a re-poll doesn't fold an open brood.
	let expanded = new SvelteSet<string>();

	function toggle(id: string) {
		if (expanded.has(id)) expanded.delete(id);
		else expanded.add(id);
	}
</script>

{#snippet curatedLine(line: ClothLine, child: boolean)}
	{#if child}
		<span class="shrink-0 text-ink-mute" aria-hidden="true">↳</span>
	{/if}
	{#if line.href}
		<a
			href={line.href}
			class="min-w-0 truncate text-amber-100 hover:text-amber-50"
			class:opacity-60={line.bare}
		>
			{line.name}
		</a>
	{:else}
		<span class="min-w-0 truncate text-stone-200" class:opacity-60={line.bare}>{line.name}</span>
	{/if}
	{#if line.repoLabel}
		<span class="hidden shrink-0 text-ink-mute sm:inline">{line.repoLabel}</span>
	{/if}
	{#if line.chips.length > 0}
		<span class="shrink-0 text-stone-300">
			{line.chips.map((chip) => chip.label).join(' ')}
		</span>
	{/if}
	<span class="ml-auto shrink-0 text-ink-quiet">{line.duration} · {line.age}</span>
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

	{#if weave === null || selvage === null}
		<p class="font-mono text-[10px] text-ink-quiet">reading the cloth…</p>
	{:else if weave.trees.length === 0}
		<p class="font-mono text-[10px] text-ink-quiet">nothing woven in this window yet.</p>
	{:else}
		<!-- The selvage: the cloth's self-finished edge. One quiet row, first. -->
		<p
			class="mb-2 flex flex-wrap items-baseline gap-x-2 gap-y-0.5 border-b border-stone-800/70 pb-2 font-mono text-[10px] text-ink-quiet"
			aria-label="spend and produce over the window"
		>
			<span class="tracking-[0.16em] text-ink-mute uppercase">selvage</span>
			{#each selvage as part, index (index)}
				<span>{part}</span>
			{/each}
		</p>

		<!-- The weave: root runs, newest first, one curated line each; the
		     past glitches (band grammar), so lines assemble rather than fade. -->
		<div class="space-y-0.5" role="list" aria-label="closed runs in the window">
			{#each weave.trees as tree, index (tree.root.id)}
				<div role="listitem" in:glitchReveal={{ duration: 240, delay: index * 24 }}>
					<div class="flex min-w-0 items-baseline gap-2 font-mono text-xs leading-relaxed">
						{@render curatedLine(tree.root, false)}
						{#if tree.children.length > 0}
							<button
								type="button"
								class="shrink-0 cursor-pointer text-[10px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
								aria-expanded={expanded.has(tree.root.id)}
								onclick={() => toggle(tree.root.id)}
							>
								{expanded.has(tree.root.id) ? '▾' : '▸'}
								{tree.children.length} worker{tree.children.length === 1 ? '' : 's'}
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
