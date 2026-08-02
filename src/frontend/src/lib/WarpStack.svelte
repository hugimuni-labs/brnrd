<script lang="ts">
	import { fade, fly } from 'svelte/transition';
	import { flip } from 'svelte/animate';
	import MarkdownContent from './MarkdownContent.svelte';
	import { STATUS_BURNING, STATUS_COOLING, STATUS_UNKNOWN } from './statusPalette';
	import { ignitionPayload, type WarpLayer, type WarpHeat } from './warp';
	import type { AuthoredBackchannelItem } from './backchannelPage';

	interface Props {
		layers: WarpLayer[];
		/** Corpus paths, for resolving internal links inside item bodies. */
		knownPaths?: Set<string>;
		/** Tests seed the open layer fold; the page leaves it null (all folded). */
		initialOpenCallSign?: string | null;
	}

	let { layers, knownPaths = new Set<string>(), initialOpenCallSign = null }: Props = $props();

	// Heat wears the existing thermal palette — no new hue enters for the
	// warp (design-work-layers.md: heat extends the loom's thermal grammar
	// one tense earlier). ember burns amber; banked is the frost blue the
	// maintainer's "blue" always was; cold is the unknown gray.
	const HEAT_COLOR: Record<WarpHeat, string> = {
		ember: STATUS_BURNING,
		banked: STATUS_COOLING,
		cold: STATUS_UNKNOWN
	};

	// The stack is the standing body now, and it stands *open*: every layer
	// shows its ember items inline — the section header's "7 ember" must be
	// visible without a click, not a promise behind five shut drawers. What
	// folds is the held remainder: the definition block and the banked/cold
	// items, behind the layer's own disclosure, with the counts on the band
	// saying what the fold holds. One open fold, one open item.
	// svelte-ignore state_referenced_locally
	let openCallSign = $state<string | null>(initialOpenCallSign);
	let openItemKey = $state<string | null>(null);

	function toggleLayer(callSign: string) {
		openCallSign = openCallSign === callSign ? null : callSign;
		openItemKey = null;
	}

	function heldItems(layer: WarpLayer): AuthoredBackchannelItem[] {
		return layer.items.filter((item) => item.state !== 'ember');
	}

	function emberItems(layer: WarpLayer): AuthoredBackchannelItem[] {
		return layer.items.filter((item) => item.state === 'ember');
	}

	// Copy-to-chat is the shipping ignition affordance (#876 deferred as a
	// consent liability): copying the item's `prompt:` into the configured
	// chat rides an already-consented channel. The payload carries the item's
	// resolver address (`layer#slug`) as its last line — the daemon scans
	// ignition event bodies for it to weld the run back onto the item
	// (`ignitionPayload` in warp.ts). Same honest collapse as the
	// backchannel's copy chip, same wording contract.
	let copiedKey = $state<string | null>(null);
	let copyTimer: ReturnType<typeof setTimeout> | null = null;

	async function copyPrompt(key: string, payload: string) {
		try {
			await navigator.clipboard.writeText(payload);
		} catch {
			return;
		}
		copiedKey = key;
		if (copyTimer) clearTimeout(copyTimer);
		copyTimer = setTimeout(() => {
			copiedKey = null;
		}, 1600);
	}
</script>

{#snippet itemRow(layer: WarpLayer, item: AuthoredBackchannelItem)}
	{@const heat = item.state}
	{@const heatColor = heat ? HEAT_COLOR[heat] : STATUS_UNKNOWN}
	{@const itemOpen = openItemKey === item.key}
	{@const foldId = `warp-item-${layer.callSign}-${item.key}`}
	<li class="border border-stone-900/70 bg-stone-950/30 px-2.5 py-1.5">
		<button
			type="button"
			class="flex w-full cursor-pointer flex-wrap items-baseline gap-x-2 gap-y-0.5 text-left"
			aria-expanded={itemOpen}
			aria-controls={foldId}
			onclick={() => (openItemKey = itemOpen ? null : item.key)}
		>
			<span
				class="inline-block h-2 w-2 shrink-0 self-center rounded-full"
				style={`background-color: ${heatColor}`}
				aria-hidden="true"
			></span>
			<!-- The headline wraps whole — text-sm, tight leading — rather
			     than ellipsizing: a second line beats an amputated clause
			     (maintainer: "just looks cut"). -->
			<span class="min-w-0 flex-1 text-sm leading-tight font-medium break-words text-amber-100"
				>{item.headline}</span
			>
			<span
				class="shrink-0 font-mono text-[10px] tracking-wide uppercase"
				style={`color: ${heatColor}`}>{heat ?? '—'}</span
			>
			{#if item.taken.length > 0}
				<!-- THE WELD (#972): this item has crossed into the shed — the
				     count marks the ancestry; the run ids ride the fold. -->
				<span class="shrink-0 font-mono text-[10px] text-amber-300/80">↯ {item.taken.length}</span>
			{/if}
		</button>
		{#if item.prompt && heat === 'ember'}
			<!-- Ignition, inline: an ember is dispatchable *now*, so its mandate
			     rides the standing row, not an expansion (the play affordance
			     renders on dispatchable items only, per the design). A banked or
			     cold item's prompt is context and stays behind its fold. -->
			<button
				type="button"
				class="mt-1 flex w-full max-w-full cursor-pointer items-baseline gap-1.5 border border-amber-800/60 bg-amber-950/30 px-2 py-1 text-left hover:border-amber-600/70 hover:bg-amber-950/50"
				title="Copy this item's dispatch mandate — paste it wherever you message the resident to send it. No auto-dispatch."
				onclick={() => copyPrompt(item.key, ignitionPayload(layer.callSign, item))}
			>
				<span class="shrink-0 font-mono text-[10px] tracking-wide text-amber-200 uppercase"
					>{copiedKey === item.key ? 'copied ✓' : 'ignite · copy'}</span
				>
				<!-- The mandate renders whole — wrapping costs one line and an
				     ellipsis would cut it mid-clause. -->
				<span class="min-w-0 flex-1 break-words text-ink-quiet italic">{item.prompt}</span>
			</button>
		{/if}
		{#if itemOpen}
			<div class="mt-1.5" id={foldId} transition:fade={{ duration: 150 }}>
				{#if item.taken.length > 0}
					<!-- The weld's back-pointer: the runs this item ignited, by
					     address — referencing, never re-listing (#972). -->
					<div class="font-mono text-[10px]">
						<span class="tracking-wide text-amber-300 uppercase">taken</span>
						<span class="text-ink-quiet"> → {item.taken.join(' · ')}</span>
					</div>
				{/if}
				{#if item.needs}
					<!-- The named missing thing renders first: an open item's
					     job is to say what resolving it takes. -->
					<div class="font-mono text-[10px]">
						<span class="tracking-wide text-sky-300 uppercase">needs</span>
						<span class="text-ink-quiet"> {item.needs}</span>
					</div>
				{/if}
				{#if item.refs.length > 0}
					<div class="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 font-mono text-[10px]">
						{#each item.refs as ref, i (i)}
							{#if ref.href}
								<a
									class="text-sky-400 underline hover:text-sky-300"
									href={ref.href}
									target="_blank"
									rel="external noreferrer">{ref.label}</a
								>
							{:else}
								<span class="text-ink-quiet">{ref.label}</span>
							{/if}
						{/each}
					</div>
				{/if}
				{#if item.prompt && heat !== 'ember'}
					<!-- A held item's mandate is context, not an invitation — it
					     renders as prose inside the fold, no copy affordance. -->
					<div class="mt-1 font-mono text-[10px] break-words text-ink-quiet italic">
						{item.prompt}
					</div>
				{/if}
				{#if item.bodyMarkdown}
					<div class="mt-1.5">
						<MarkdownContent markdown={item.bodyMarkdown} sourcePath={layer.path} {knownPaths} />
					</div>
				{/if}
			</div>
		{/if}
	</li>
{/snippet}

<div class="panel p-4">
	<div class="mb-3 flex items-center justify-between text-sm">
		<span class="font-mono font-medium tracking-wide text-amber-200 uppercase">the warp</span>
		<span class="font-mono text-[10px] tracking-wide text-ink-quiet uppercase"
			>standing intent · {layers.length} {layers.length === 1 ? 'layer' : 'layers'}</span
		>
	</div>

	{#if layers.length === 0}
		<p class="text-sm text-ink-quiet">
			No layers are strung — the warp is authored under <span class="font-mono"
				>surface/layers/</span
			>.
		</p>
	{:else}
		<ul class="space-y-1.5">
			{#each layers as layer (layer.callSign)}
				{@const open = openCallSign === layer.callSign}
				{@const bandId = `warp-band-${layer.callSign}`}
				{@const embers = emberItems(layer)}
				{@const held = heldItems(layer)}
				<li
					class="subpanel px-3 py-2 text-xs"
					in:fly={{ y: -8, duration: 220 }}
					out:fade={{ duration: 150 }}
					animate:flip={{ duration: 220 }}
				>
					<!-- The band: disclosure · call sign · heat counts. The counts are
					     the band's one-line truth — how much is ready, held, undefined
					     — and the disclosure opens the *held* remainder (definition +
					     banked/cold); the embers below never fold. -->
					<button
						type="button"
						class="flex w-full cursor-pointer flex-wrap items-baseline gap-x-2 gap-y-0.5 text-left"
						aria-expanded={open}
						aria-controls={bandId}
						onclick={() => toggleLayer(layer.callSign)}
					>
						<span class="font-mono text-[10px] text-ink-quiet" aria-hidden="true"
							>{open ? '▾' : '▸'}</span
						>
						<span class="min-w-0 flex-1 font-mono font-medium text-amber-100">{layer.callSign}</span
						>
						<span class="flex shrink-0 items-center gap-2 font-mono text-[10px]">
							{#if layer.counts.ember > 0}
								<span style={`color: ${HEAT_COLOR.ember}`}>{layer.counts.ember} ember</span>
							{/if}
							{#if layer.counts.banked > 0}
								<span style={`color: ${HEAT_COLOR.banked}`}>{layer.counts.banked} banked</span>
							{/if}
							{#if layer.counts.cold + layer.counts.unstated > 0}
								<span style={`color: ${HEAT_COLOR.cold}`}
									>{layer.counts.cold + layer.counts.unstated} cold</span
								>
							{/if}
						</span>
					</button>

					{#if embers.length > 0}
						<!-- The live threads, standing open: the section header's ember
						     count is visible work, not a number behind a click. -->
						<ul class="mt-1.5 space-y-1">
							{#each embers as item (item.key)}
								{@render itemRow(layer, item)}
							{/each}
						</ul>
					{/if}

					{#if open}
						<div class="mt-2" id={bandId} transition:fade={{ duration: 150 }}>
							{#if layer.definitionMarkdown}
								<div class="mb-2 border-l border-stone-800 pl-2 opacity-80">
									<MarkdownContent
										markdown={layer.definitionMarkdown}
										sourcePath={layer.path}
										{knownPaths}
									/>
								</div>
							{/if}
							{#if held.length > 0}
								<ul class="space-y-1">
									{#each held as item (item.key)}
										{@render itemRow(layer, item)}
									{/each}
								</ul>
							{:else if !layer.definitionMarkdown}
								<p class="text-ink-quiet">nothing is held back — every item here is ember.</p>
							{/if}
						</div>
					{/if}
				</li>
			{/each}
		</ul>
	{/if}
</div>
