<script lang="ts">
	import { fade, fly } from 'svelte/transition';
	import { flip } from 'svelte/animate';
	import { ageSinceCreated } from './prReviewQueue';
	import type { ConfigChangeRequestItem } from './configRequests';
	import { STATUS_WARN } from './statusPalette';

	interface Props {
		requests: ConfigChangeRequestItem[];
		now: number;
	}

	let { requests, now }: Props = $props();

	// A pending request is always "needs your action" — frost, the same
	// hue PRReviewQueue uses for a draft PR waiting on the author, not
	// amber (that's reserved for a healthy/settled state) or void
	// (reserved for an actual exhaustion/critical signal).
	const PENDING_COLOR = STATUS_WARN;
</script>

<div class="panel p-4">
	<div class="mb-3 flex items-center justify-between text-sm">
		<span class="font-mono font-medium tracking-wide text-amber-200 uppercase"
			>config-change requests</span
		>
	</div>
	{#if requests.length === 0}
		<p class="text-sm text-stone-500">No pending settings requests from any daemon.</p>
	{:else}
		<ul class="space-y-2">
			{#each requests as req (req.id)}
				<li
					class="subpanel px-2.5 py-2 text-xs"
					in:fly={{ y: -8, duration: 220 }}
					out:fade={{ duration: 150 }}
					animate:flip={{ duration: 220 }}
				>
					<div class="flex items-center justify-between gap-3">
						<span class="flex min-w-0 items-center gap-1.5 text-stone-300">
							<span
								class="inline-block h-2 w-2 shrink-0 rounded-full"
								style={`background-color: ${PENDING_COLOR}`}
								aria-hidden="true"
							></span>
							<span class="min-w-0">
								<span class="block truncate font-medium text-amber-100">
									{req.config_key}: {req.current_value || '(unset)'} → {req.requested_value}
								</span>
								<span class="block truncate text-stone-500">
									{req.repo_label || 'unknown repo'}{req.reason ? ` · ${req.reason}` : ''}
								</span>
							</span>
						</span>
						<span class="flex shrink-0 items-center gap-2 font-mono">
							<span class="uppercase tracking-wide" style={`color: ${PENDING_COLOR}`}>pending</span>
							<span class="text-stone-500">{ageSinceCreated(req.created_at, now) ?? ''}</span>
							<a
								class="text-sky-400 underline hover:text-sky-300"
								href={req.approve_url}
								target="_blank"
								rel="external noreferrer">decide</a
							>
						</span>
					</div>
				</li>
			{/each}
		</ul>
	{/if}
</div>
