<script lang="ts">
	import { fade, fly } from 'svelte/transition';
	import { flip } from 'svelte/animate';
	import {
		durationLabel,
		endedLabel,
		signedPercentLabel,
		tokenLabel,
		type RunLedgerRow,
		usdLabel
	} from './runLedger';

	interface Props {
		rows: RunLedgerRow[];
		stale: boolean;
	}

	let { rows, stale }: Props = $props();
</script>

<div class="rounded-md border border-stone-800 bg-stone-900/60 p-3">
	<div class="mb-2 flex items-center justify-between text-sm">
		<span class="font-medium text-amber-200">run receipts</span>
		{#if stale}
			<span class="rounded bg-sky-900/40 px-1.5 py-0.5 text-xs text-sky-300">stale report</span>
		{/if}
	</div>
	{#if rows.length === 0}
		<p class="text-sm text-stone-500">No closed-run receipts yet.</p>
	{:else}
		<div class="grid grid-cols-1 gap-2">
			{#each rows as row (row.run_id ?? row.event_id ?? row.ended_at)}
				{@const label = row.task_classification || row.repo_label || '—'}
				{@const runner = [row.runner_shell, row.runner_core].filter(Boolean).join(' · ') || '—'}
				<div
					class="rounded-lg border border-stone-800 bg-stone-800/60 p-2.5 text-xs"
					in:fly={{ y: -8, duration: 220 }}
					out:fade={{ duration: 150 }}
					animate:flip={{ duration: 220 }}
				>
					<div class="flex items-start justify-between gap-3">
						<div class="min-w-0">
							<p class="truncate font-medium text-amber-100">{label}</p>
							<p class="truncate text-stone-500">{runner}</p>
						</div>
						<span class="shrink-0 text-stone-500">{endedLabel(row.ended_at)}</span>
					</div>

					<div class="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
						<div>
							<p class="text-[10px] uppercase tracking-wide text-stone-500">wall</p>
							<p class="font-medium text-stone-200">{durationLabel(row.wall_clock_seconds)}</p>
						</div>
						<div>
							<p class="text-[10px] uppercase tracking-wide text-stone-500">tokens</p>
							<p class="font-medium text-stone-200">
								{tokenLabel(row.tokens_input)} / {tokenLabel(row.tokens_output)}
							</p>
						</div>
						<div>
							<p class="text-[10px] uppercase tracking-wide text-stone-500">weekly / 5h</p>
							<p class="font-medium text-stone-200">
								{signedPercentLabel(row.weekly_pct_delta)} / {signedPercentLabel(
									row.five_hour_pct_delta
								)}
							</p>
						</div>
						<div>
							<p class="text-[10px] uppercase tracking-wide text-stone-500">subscription</p>
							<p class="font-medium text-stone-200">{usdLabel(row.usd_subscription_attributed)}</p>
						</div>
					</div>
				</div>
			{/each}
		</div>
	{/if}
</div>
