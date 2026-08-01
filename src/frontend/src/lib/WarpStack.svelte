<script lang="ts">
	import { fade, fly } from 'svelte/transition';
	import { flip } from 'svelte/animate';
	import MarkdownContent from './MarkdownContent.svelte';
	import { STATUS_BURNING, STATUS_COOLING, STATUS_UNKNOWN } from './statusPalette';
	import type { WarpLayer, WarpHeat } from './warp';

	interface Props {
		layers: WarpLayer[];
		/** Corpus paths, for resolving internal links inside item bodies. */
		knownPaths?: Set<string>;
		/** Tests seed the open layer; the page leaves it null (all folded). */
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

	// One open layer, one open item — the Photoshop-layers read: the stack
	// stays a stack; the selected band expands. Accordion state is two keys.
	// svelte-ignore state_referenced_locally
	let openCallSign = $state<string | null>(initialOpenCallSign);
	let openItemKey = $state<string | null>(null);

	function toggleLayer(callSign: string) {
		openCallSign = openCallSign === callSign ? null : callSign;
		openItemKey = null;
	}

	// Copy-to-chat is the shipping ignition affordance (#876 deferred as a
	// consent liability): copying the item's `prompt:` into the configured
	// chat rides an already-consented channel. Same honest collapse as the
	// backchannel's copy chip, same wording contract.
	let copiedKey = $state<string | null>(null);
	let copyTimer: ReturnType<typeof setTimeout> | null = null;

	async function copyPrompt(key: string, prompt: string) {
		try {
			await navigator.clipboard.writeText(prompt);
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
				<li
					class="subpanel px-3 py-2 text-xs"
					in:fly={{ y: -8, duration: 220 }}
					out:fade={{ duration: 150 }}
					animate:flip={{ duration: 220 }}
				>
					<!-- Folded band: disclosure · call sign · heat counts. The counts
					     are the band's one-line truth — how much is ready, held,
					     undefined — so the stack reads as a supply gauge unopened. -->
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
							<ul class="space-y-1">
								{#each layer.items as item (item.key)}
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
											<span
												class="min-w-0 flex-1 text-sm leading-tight font-medium break-words text-amber-100"
												>{item.headline}</span
											>
											<span
												class="shrink-0 font-mono text-[10px] tracking-wide uppercase"
												style={`color: ${heatColor}`}>{heat ?? '—'}</span
											>
										</button>
										{#if itemOpen}
											<div class="mt-1.5" id={foldId} transition:fade={{ duration: 150 }}>
												{#if item.needs}
													<!-- The named missing thing renders first: an open item's
													     job is to say what resolving it takes. -->
													<div class="font-mono text-[10px]">
														<span class="tracking-wide text-sky-300 uppercase">needs</span>
														<span class="text-ink-quiet"> {item.needs}</span>
													</div>
												{/if}
												{#if item.refs.length > 0}
													<div
														class="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 font-mono text-[10px]"
													>
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
												{#if item.prompt && heat === 'ember'}
													<!-- Ignition, honest version: only an ember offers its
													     mandate — a banked or cold item's prompt is context,
													     not an invitation (the play affordance renders on
													     dispatchable items only, per the design). -->
													<button
														type="button"
														class="mt-1 flex w-full max-w-full cursor-pointer items-baseline gap-1.5 border border-amber-800/60 bg-amber-950/30 px-2 py-1 text-left hover:border-amber-600/70 hover:bg-amber-950/50"
														title="Copy this item's dispatch mandate — paste it wherever you message the resident to send it. No auto-dispatch."
														onclick={() => copyPrompt(item.key, item.prompt!)}
													>
														<span
															class="shrink-0 font-mono text-[10px] tracking-wide text-amber-200 uppercase"
															>{copiedKey === item.key ? 'copied ✓' : 'ignite · copy'}</span
														>
														<!-- The mandate renders whole — it lives behind the
														     expansion already, so wrapping costs nothing and an
														     ellipsis would cut it mid-clause. -->
														<span class="min-w-0 flex-1 break-words text-ink-quiet italic"
															>{item.prompt}</span
														>
													</button>
												{:else if item.prompt}
													<div class="mt-1 font-mono text-[10px] break-words text-ink-quiet italic">
														{item.prompt}
													</div>
												{/if}
												{#if item.bodyMarkdown}
													<div class="mt-1.5">
														<MarkdownContent
															markdown={item.bodyMarkdown}
															sourcePath={layer.path}
															{knownPaths}
														/>
													</div>
												{/if}
											</div>
										{/if}
									</li>
								{/each}
							</ul>
						</div>
					{/if}
				</li>
			{/each}
		</ul>
	{/if}
</div>
