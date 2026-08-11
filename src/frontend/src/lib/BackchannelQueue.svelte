<script lang="ts">
	import { fade, fly } from 'svelte/transition';
	import { flip } from 'svelte/animate';
	import { buildDerivedAsks, draftPrCount } from './backchannel';
	import type { ConfigChangeRequestItem } from './configRequests';
	import type { PRReviewItem } from './prReviewQueue';
	import { ageSince } from './runLedger';
	import { STATUS_GOOD, STATUS_UNKNOWN, STATUS_WARN } from './statusPalette';
	import WithheldNotice from './WithheldNotice.svelte';
	import type { WithheldLane } from './withheld';

	interface Props {
		prs: PRReviewItem[];
		requests: ConfigChangeRequestItem[];
		stale: boolean;
		now: number;
		withheld?: WithheldLane | null;
	}

	let { prs, requests, stale, now, withheld = null }: Props = $props();

	const REVIEW_COLOR = STATUS_GOOD;
	const ACTION_COLOR = STATUS_WARN;
	const STALE_COLOR = STATUS_UNKNOWN;

	let derivedItems = $derived(buildDerivedAsks(prs, requests));
	// Drafts never enter `derivedItems` (a draft PR means "the resident isn't
	// done with it", not "needs you" — buildDerivedAsks filters at the
	// source). The count still matters for an honest reader, so it renders
	// as a quiet footnote below the rows — informational, never a row of
	// its own.
	let draftCount = $derived(draftPrCount(prs));
</script>

<div class="panel p-4">
	{#if stale}
		<div class="mb-3 flex items-center justify-end text-sm">
			<span
				class="border border-sky-900/60 bg-sky-950/40 px-1.5 py-0.5 font-mono text-[10px] tracking-wide text-sky-300 uppercase"
				>stale report</span
			>
		</div>
	{/if}

	{#if derivedItems.length === 0}
		{#if withheld}
			<WithheldNotice {withheld} />
		{:else}
			<p class="text-sm text-ink-quiet">Nothing needs you right now.</p>
		{/if}
	{:else}
		<ul class="space-y-1.5">
			{#each derivedItems as item (item.key)}
				{@const statusColor =
					stale && item.kind === 'pr'
						? STALE_COLOR
						: item.kind === 'pr'
							? REVIEW_COLOR
							: ACTION_COLOR}
				<li
					class="subpanel px-2.5 py-1.5 text-xs"
					in:fly={{ y: -8, duration: 220 }}
					out:fade={{ duration: 150 }}
					animate:flip={{ duration: 220 }}
				>
					<div class="flex items-center justify-between gap-3">
						<span class="flex min-w-0 items-center gap-1.5 text-stone-300">
							<span
								class="inline-block h-2 w-2 shrink-0 rounded-full"
								style={`background-color: ${statusColor}`}
								aria-hidden="true"
							></span>
							<span class="min-w-0">
								<a
									class="block truncate font-medium text-amber-100 hover:text-amber-50"
									href={item.href}
									target="_blank"
									rel="external noreferrer"
								>
									{item.headline}
								</a>
								<span class="block truncate text-ink-quiet">
									{item.kind === 'pr' ? 'pull request' : 'settings'} · {item.context}
								</span>
							</span>
						</span>
						<span class="flex shrink-0 items-center gap-2 font-mono">
							<span class="uppercase tracking-wide" style={`color: ${statusColor}`}
								>{item.statusLabel}</span
							>
							<span class="text-ink-quiet">{ageSince(item.createdAt, now) ?? ''}</span>
							<a
								class="text-sky-400 underline hover:text-sky-300"
								href={item.href}
								target="_blank"
								rel="external noreferrer">{item.linkLabel}</a
							>
						</span>
					</div>
				</li>
			{/each}
		</ul>
		{#if draftCount > 0}
			<p class="mt-1.5 font-mono text-[10px] text-ink-quiet">
				· {draftCount} draft, still being worked
			</p>
		{/if}
	{/if}
</div>
