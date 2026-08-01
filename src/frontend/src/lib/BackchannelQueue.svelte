<script lang="ts">
	import { fade, fly } from 'svelte/transition';
	import { flip } from 'svelte/animate';
	import { buildBackchannelItems, toggleFold } from './backchannel';
	import type { AuthoredBackchannelItem, BackchannelItemKind } from './backchannelPage';
	import type { ConfigChangeRequestItem } from './configRequests';
	import MarkdownContent from './MarkdownContent.svelte';
	import { ageSinceCreated, type PRReviewItem } from './prReviewQueue';
	import { STATUS_GOOD, STATUS_SPENT, STATUS_UNKNOWN, STATUS_WARN } from './statusPalette';
	import WithheldNotice from './WithheldNotice.svelte';
	import type { WithheldLane } from './withheld';

	interface Props {
		/** #875 v2: the resident-authored half — parsed `surface/backchannel.md`
		 *  sections, document order preserved (order *is* the priority). */
		authoredItems?: AuthoredBackchannelItem[];
		/** Briefing fold (design-dashboard-briefing §3): key of the one row
		 *  whose body starts unfolded. Tests set it; the page leaves it null. */
		initialOpenKey?: string | null;
		/** Corpus paths, for resolving internal links inside an item's body. */
		knownPaths?: Set<string>;
		prs: PRReviewItem[];
		requests: ConfigChangeRequestItem[];
		stale: boolean;
		now: number;
		withheld?: WithheldLane | null;
	}

	let {
		authoredItems = [],
		initialOpenKey = null,
		knownPaths = new Set<string>(),
		prs,
		requests,
		stale,
		now,
		withheld = null
	}: Props = $props();

	const REVIEW_COLOR = STATUS_GOOD;
	const ACTION_COLOR = STATUS_WARN;
	const STALE_COLOR = STATUS_UNKNOWN;

	// Four kinds, four already-existing status colors — no new hue enters the
	// palette for this. `decide` reads as the same "needs a call" blue the
	// derived config-request row already used; `review` mirrors the derived
	// PR row's amber; `read` and `act` take the two remaining tiers.
	const KIND_COLOR: Record<BackchannelItemKind, string> = {
		decide: STATUS_WARN,
		review: STATUS_GOOD,
		read: STATUS_UNKNOWN,
		act: STATUS_SPENT
	};
	const KIND_LABEL: Record<BackchannelItemKind, string> = {
		decide: 'decide',
		review: 'review',
		read: 'read',
		act: 'act'
	};

	const SOURCE_PATH = 'surface/backchannel.md';

	let derivedItems = $derived(buildBackchannelItems(prs, requests));

	// The briefing fold (design-dashboard-briefing §3): the queue was measured
	// at 62% of the page because every item rendered its full markdown body —
	// the secretary reading every memo aloud. An item is now one row; the body
	// folds behind it, one open at a time. The whole accordion state is this
	// single key. Capturing only the prop's initial value is the point — the
	// prop seeds the fold, the reader's taps own it from there.
	// svelte-ignore state_referenced_locally
	let openKey = $state<string | null>(initialOpenKey);

	// The prompt button's honest job: the dashboard has no free-text dispatch
	// field yet (#875 — `wake_requests` only carries profile/repo/environment,
	// no message). Until that channel exists, "pre-fill and send" collapses to
	// "copy, then paste it wherever you message the resident" — real, useful,
	// and not a claim the button can't back up.
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
		<span class="font-mono font-medium tracking-wide text-amber-200 uppercase"
			>resident backchannel</span
		>
		{#if stale}
			<span
				class="border border-sky-900/60 bg-sky-950/40 px-1.5 py-0.5 font-mono text-[10px] tracking-wide text-sky-300 uppercase"
				>stale report</span
			>
		{/if}
	</div>

	{#if authoredItems.length === 0 && derivedItems.length === 0}
		{#if withheld}
			<WithheldNotice {withheld} />
		{:else}
			<p class="text-sm text-ink-quiet">Nothing is waiting in the backchannel.</p>
		{/if}
	{:else}
		{#if authoredItems.length > 0}
			<ul class="space-y-1.5">
				{#each authoredItems as item (item.key)}
					{@const kindColor = item.kind ? KIND_COLOR[item.kind] : STATUS_UNKNOWN}
					{@const open = openKey === item.key}
					{@const bodyId = `backchannel-fold-${item.key}`}
					<li
						class="subpanel px-3 py-2 text-xs"
						in:fly={{ y: -8, duration: 220 }}
						out:fade={{ duration: 150 }}
						animate:flip={{ duration: 220 }}
					>
						<!-- One row: headline · kind chip · refs · the prompt as the
						     tap-action. The full prose is the open state of exactly
						     one row (§3). The grammar carries no date, so authored
						     rows have no age — only the derived rows below do. -->
						{#if item.bodyMarkdown}
							<button
								type="button"
								class="flex w-full cursor-pointer flex-wrap items-baseline gap-x-2 gap-y-0.5 text-left"
								aria-expanded={open}
								aria-controls={bodyId}
								onclick={() => (openKey = toggleFold(openKey, item.key))}
							>
								<span class="font-mono text-[10px] text-ink-quiet" aria-hidden="true"
									>{open ? '▾' : '▸'}</span
								>
								<span class="min-w-0 flex-1 font-medium text-amber-100">{item.headline}</span>
								{#if item.kind}
									<span
										class="shrink-0 font-mono text-[10px] tracking-wide uppercase"
										style={`color: ${kindColor}`}>{KIND_LABEL[item.kind]}</span
									>
								{/if}
							</button>
						{:else}
							<div class="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
								<span class="min-w-0 flex-1 font-medium text-amber-100">{item.headline}</span>
								{#if item.kind}
									<span
										class="shrink-0 font-mono text-[10px] tracking-wide uppercase"
										style={`color: ${kindColor}`}>{KIND_LABEL[item.kind]}</span
									>
								{/if}
							</div>
						{/if}
						{#if item.refs.length > 0}
							<div class="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 font-mono text-[10px]">
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
						{#if item.prompt}
							<!-- The prompt line *is* the tap-action: tapping it copies
							     the dispatch mandate (the honest collapse of "pre-fill
							     and send" — see copyPrompt above). Surfaced on the
							     closed row, not behind the fold. -->
							<button
								type="button"
								class="mt-1 flex w-full max-w-full cursor-pointer items-baseline gap-1.5 border border-amber-800/60 bg-amber-950/30 px-2 py-1 text-left hover:border-amber-600/70 hover:bg-amber-950/50"
								title="Copy this item's dispatch mandate — paste it wherever you message the resident to send it. No auto-dispatch."
								onclick={() => copyPrompt(item.key, item.prompt!)}
							>
								<span class="shrink-0 font-mono text-[10px] tracking-wide text-amber-200 uppercase"
									>{copiedKey === item.key ? 'copied ✓' : 'copy'}</span
								>
								<span class="min-w-0 truncate text-ink-quiet italic">{item.prompt}</span>
							</button>
						{/if}
						{#if open && item.bodyMarkdown}
							<div class="mt-1.5" id={bodyId} transition:fade={{ duration: 150 }}>
								<MarkdownContent
									markdown={item.bodyMarkdown}
									sourcePath={SOURCE_PATH}
									{knownPaths}
								/>
							</div>
						{/if}
					</li>
				{/each}
			</ul>
		{/if}

		{#if derivedItems.length > 0}
			<!-- Demoted, not dropped: PRs and config requests are one *source* of
			     backchannel attention, not the surface's definition (#875 v2). -->
			<div class="mt-3 flex items-center justify-between text-[10px]">
				<span class="font-mono tracking-wide text-ink-quiet uppercase"
					>derived — forge & config</span
				>
			</div>
			<ul class="mt-1.5 space-y-1.5 opacity-80">
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
								<span class="text-ink-quiet">{ageSinceCreated(item.createdAt, now) ?? ''}</span>
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
		{/if}
	{/if}
</div>
