<script lang="ts">
	import { fade, fly } from 'svelte/transition';
	import { flip } from 'svelte/animate';
	import { ageSinceCreated, type PRReviewItem } from './prReviewQueue';

	interface Props {
		prs: PRReviewItem[];
		stale: boolean;
		now: number;
	}

	let { prs, stale, now }: Props = $props();

	// Same fixed status palette as WindowTrack / LiveRuns.
	const READY_COLOR = '#0ca30c';
	const DRAFT_COLOR = '#fab219';
	const STALE_COLOR = '#57534e';
</script>

<div class="rounded-md border border-slate-800 bg-slate-900/60 p-3">
	<div class="mb-2 flex items-center justify-between text-sm">
		<span class="font-medium text-slate-200">PR review queue</span>
		{#if stale}
			<span class="rounded bg-amber-900/40 px-1.5 py-0.5 text-xs text-amber-300">stale report</span>
		{/if}
	</div>
	{#if prs.length === 0}
		<p class="text-sm text-slate-500">No open PRs waiting on review.</p>
	{:else}
		<ul class="space-y-2">
			{#each prs as pr (`${pr.repo_label}#${pr.number}`)}
				{@const statusColor = stale ? STALE_COLOR : pr.draft ? DRAFT_COLOR : READY_COLOR}
				{@const statusLabel = pr.draft ? 'draft' : 'review'}
				<li
					class="rounded bg-slate-800/60 px-2 py-1.5 text-xs"
					in:fly={{ y: -8, duration: 220 }}
					out:fade={{ duration: 150 }}
					animate:flip={{ duration: 220 }}
				>
					<div class="flex items-center justify-between gap-3">
						<span class="flex min-w-0 items-center gap-1.5 text-slate-300">
							<span
								class="inline-block h-2 w-2 shrink-0 rounded-full"
								style={`background-color: ${statusColor}`}
								aria-hidden="true"
							></span>
							<span class="min-w-0">
								<a
									class="block truncate font-medium text-slate-200 hover:text-slate-50"
									href={pr.url}
									target="_blank"
									rel="external noreferrer"
								>
									#{pr.number}
									{pr.title || 'Untitled PR'}
								</a>
								<span class="block truncate text-slate-500">
									{pr.repo_label || 'unknown repo'}{pr.author ? ` · ${pr.author}` : ''}
								</span>
							</span>
						</span>
						<span class="flex shrink-0 items-center gap-2">
							<span style={`color: ${statusColor}`}>{statusLabel}</span>
							<span class="text-slate-500">{ageSinceCreated(pr.created_at, now) ?? ''}</span>
						</span>
					</div>
				</li>
			{/each}
		</ul>
	{/if}
</div>
