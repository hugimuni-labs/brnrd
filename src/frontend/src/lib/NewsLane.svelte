<script lang="ts">
	import { fade, fly } from 'svelte/transition';
	import { flip } from 'svelte/animate';
	import type { NewsItem } from './news';
	import { renderNewsItem } from './news';
	import { STATUS_WARN } from './statusPalette';

	interface Props {
		items: NewsItem[];
		error?: string | null;
	}

	let { items, error = null }: Props = $props();

	// An item with `expires_at` is a deadline, not news (see
	// `brr.news_lane`'s own "Cadence" doc) — the one case this panel marks
	// with the same frost dot ConfigRequests reserves for "needs your
	// action". A plain release/version line is informational: no colored
	// dot, just the fact.
	function isDeadline(item: NewsItem): boolean {
		return Boolean(item.expires_at);
	}
</script>

<!-- The web dashboard's half of "the user hears it first" — until this
     panel, a newer brnrd release reached the CLI, the daemon boot log, and
     the resident's own wake prompt, and never this surface. Mounted only
     when there is something to show (Dashboard.svelte's own guard) or a
     fetch failed, same contract as ConfigRequests.svelte beside it: no
     "you're all caught up" sentence for the common case of nothing new. -->
<div class="panel mt-2 p-4">
	<div class="mb-3 flex items-center justify-between text-sm">
		<span class="font-mono font-medium tracking-wide text-sky-200 uppercase">news</span>
	</div>
	{#if error}
		<p class="text-sm text-red-400">{error}</p>
	{:else}
		<ul class="space-y-1.5">
			{#each items as item (`${item.kind}:${item.subject}`)}
				<li
					class="subpanel px-2.5 py-2 text-xs"
					in:fly={{ y: -8, duration: 220 }}
					out:fade={{ duration: 150 }}
					animate:flip={{ duration: 220 }}
				>
					<div class="flex items-center justify-between gap-3">
						<span class="flex min-w-0 items-center gap-1.5 text-stone-300">
							{#if isDeadline(item)}
								<span
									class="inline-block h-2 w-2 shrink-0 rounded-full"
									style={`background-color: ${STATUS_WARN}`}
									aria-hidden="true"
								></span>
							{/if}
							<span class="min-w-0">
								<span class="block truncate font-medium text-sky-100">
									{renderNewsItem(item)}
								</span>
							</span>
						</span>
						{#if item.daemon_stale}
							<span class="shrink-0 font-mono text-ink-quiet uppercase tracking-wide">stale</span>
						{/if}
					</div>
				</li>
			{/each}
		</ul>
	{/if}
</div>
