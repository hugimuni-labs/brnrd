<script lang="ts">
	import { fade, fly } from 'svelte/transition';
	import { flip } from 'svelte/animate';
	import type { ConfigChangeRequestItem } from './configRequests';
	import { ageSince } from './runLedger';
	import { STATUS_WARN } from './statusPalette';

	interface Props {
		requests: ConfigChangeRequestItem[];
		now: number;
		error?: string | null;
	}

	let { requests, now, error = null }: Props = $props();

	// A pending request is always "needs your action" — frost, the same
	// hue PRReviewQueue used for a draft PR waiting on the author, not
	// amber (that's reserved for a healthy/settled state) or void
	// (reserved for an actual exhaustion/critical signal).
	const PENDING_COLOR = STATUS_WARN;
</script>

<!-- The dashboard's one surviving "needs you" surface (2026-09-01): the PR
     review half retired (GitHub already lists open PRs; duplicating that
     read poorly on a phone) and the authored half moved into the warp
     earlier — what's left is config-change requests, the one population
     with no other list anywhere in the app (the per-request
     /config-approve/[id] page answers "decide this one", never "what's
     waiting"). The caller (Dashboard.svelte) only mounts this component
     when there is something to show — a pending request, or a fetch
     error — so "nothing pending" never needs a sentence of its own: an
     empty, resolved, error-free queue is absent, not a panel announcing
     absence. -->
<div class="panel mt-2 p-4">
	<div class="mb-3 flex items-center justify-between text-sm">
		<span class="font-mono font-medium tracking-wide text-amber-200 uppercase"
			>config approvals</span
		>
	</div>
	{#if error}
		<p class="text-sm text-red-400">{error}</p>
	{:else}
		<ul class="space-y-1.5">
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
								<span class="block truncate text-ink-quiet">
									{req.repo_label || 'unknown repo'}{req.reason ? ` · ${req.reason}` : ''}
								</span>
							</span>
						</span>
						<span class="flex shrink-0 items-center gap-2 font-mono">
							<span class="uppercase tracking-wide" style={`color: ${PENDING_COLOR}`}>pending</span>
							<span class="text-ink-quiet">{ageSince(req.created_at, now) ?? ''}</span>
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
