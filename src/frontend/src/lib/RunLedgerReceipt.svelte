<script lang="ts">
	import { fade, fly } from 'svelte/transition';
	import { flip } from 'svelte/animate';
	import { SvelteSet } from 'svelte/reactivity';
	import { glitchReveal } from './transitions';
	import {
		durationLabel,
		endedLabel,
		groupWithChildren,
		relicCounts,
		relicIcon,
		relicLabel,
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

	// #200/#317: "the run should be clickable... collapsed it would just
	// show plain data (3 commits, 1 pr, 1 issue modified, maybe as
	// kaomojis/icons)". Expansion state is local UI state, keyed by run_id
	// (falling back to event_id for the rare row missing one) — not part of
	// the fetched data, so a re-poll doesn't collapse an open receipt.
	let expanded = new SvelteSet<string>();

	function receiptKey(row: RunLedgerRow): string {
		return row.run_id ?? row.event_id ?? row.ended_at ?? '';
	}

	function toggle(key: string) {
		if (expanded.has(key)) expanded.delete(key);
		else expanded.add(key);
	}

	let grouped = $derived(groupWithChildren(rows));
</script>

<div class="panel p-4">
	<div class="mb-3 flex items-center justify-between text-sm">
		<span class="font-mono font-medium tracking-wide text-amber-200 uppercase">run receipts</span>
		{#if stale}
			<span
				class="border border-sky-900/60 bg-sky-950/40 px-1.5 py-0.5 font-mono text-[10px] tracking-wide text-sky-300 uppercase"
				>stale report</span
			>
		{/if}
	</div>
	{#if rows.length === 0}
		<p class="text-sm text-stone-500">No closed-run receipts yet.</p>
	{:else}
		<div class="grid grid-cols-1 gap-2">
			{#each grouped as entry (receiptKey(entry.row))}
				{@const row = entry.row}
				{@const key = receiptKey(row)}
				{@const label = row.task_classification || row.repo_label || '—'}
				{@const runner = [row.runner_shell, row.runner_core].filter(Boolean).join(' · ') || '—'}
				{@const counts = relicCounts(entry.relics)}
				{@const summary = entry.relics.find((r) => r.kind === 'summary')}
				{@const isOpen = expanded.has(key)}
				<div
					class="subpanel p-2.5 text-xs"
					in:fly={{ y: -8, duration: 220 }}
					out:fade={{ duration: 150 }}
					animate:flip={{ duration: 220 }}
				>
					<div class="flex items-start justify-between gap-3">
						<div class="min-w-0">
							<p class="truncate font-medium text-amber-100">{label}</p>
							<!-- Core attestation: runner_core is the *observed* model
							     (from the Shell's own result JSON), not the config
							     claim. ✓ = observed matches the dispatch pin;
							     red badge = the pin was not respected (the
							     shell=/core= shadowing failure mode, caught
							     2026-07-09, must never be silent again). -->
							<p class="truncate font-mono text-stone-500">
								{runner}
								{#if row.core_mismatch === false}
									<span class="text-emerald-400" title="observed model matches the configured core pin"
										>✓</span
									>
								{:else if row.core_mismatch}
									<span
										class="rounded bg-red-950/70 px-1 text-red-300"
										title="the Shell ran a different model than the configured core pin"
										>⚠ pinned {row.core_expected ?? '?'}, ran {row.runner_core ?? '?'}</span
									>
								{/if}
							</p>
						</div>
						<span class="shrink-0 font-mono text-stone-500">{endedLabel(row.ended_at)}</span>
					</div>

					<div
						class="mt-2 grid grid-cols-2 gap-2 border-t border-stone-800/70 pt-2 font-mono sm:grid-cols-4"
					>
						<div>
							<p class="text-[10px] tracking-wide text-stone-500 uppercase">wall</p>
							<p class="font-medium text-stone-200">{durationLabel(row.wall_clock_seconds)}</p>
						</div>
						<div>
							<p class="text-[10px] tracking-wide text-stone-500 uppercase">tokens</p>
							<p class="font-medium text-stone-200">
								{tokenLabel(row.tokens_input)} / {tokenLabel(row.tokens_output)}
							</p>
						</div>
						<div>
							<p class="text-[10px] tracking-wide text-stone-500 uppercase">weekly / 5h</p>
							<p class="font-medium text-stone-200">
								{signedPercentLabel(row.weekly_pct_delta)} / {signedPercentLabel(
									row.five_hour_pct_delta
								)}
							</p>
						</div>
						<div>
							<p class="text-[10px] tracking-wide text-stone-500 uppercase">subscription</p>
							<p class="font-medium text-stone-200">{usdLabel(row.usd_subscription_attributed)}</p>
						</div>
					</div>

					<!-- Run relics (#200/#317): collapsed receipt = clickable
					     icon/count summary line ("3 commits, 1 pr, 1 issue" per
					     the maintainer's own framing); click expands to the full
					     linked list, sub-spawn relics folded in and tagged. -->
					{#if entry.relics.length > 0}
						<button
							type="button"
							class="mt-2 flex w-full items-center justify-between gap-2 border-t border-stone-800/70 pt-2 text-left font-mono"
							onclick={() => toggle(key)}
							aria-expanded={isOpen}
						>
							<span class="flex flex-wrap items-center gap-x-2 gap-y-1 text-stone-300">
								{#if Object.keys(counts).length > 0}
									{#each Object.entries(counts) as [kind, count] (kind)}
										<span title={kind}>{relicIcon(kind)} {count}</span>
									{/each}
								{:else}
									<span class="text-stone-500">summary only</span>
								{/if}
							</span>
							<span class="shrink-0 text-[10px] tracking-wide text-stone-500 uppercase"
								>{isOpen ? '▲ collapse' : '▼ produce'}</span
							>
						</button>
						{#if isOpen}
							<div
								class="mt-2 space-y-1.5 overflow-hidden border-t border-stone-800/70 pt-2"
								in:glitchReveal={{ duration: 180, steps: 6 }}
								out:fade={{ duration: 100 }}
							>
								{#if summary}
									<p class="text-stone-300 italic">{relicLabel(summary)}</p>
								{/if}
								<ul class="space-y-1">
									{#each entry.relics.filter((r) => r.kind !== 'summary') as r, i (i)}
										<li class="flex min-w-0 items-center gap-1.5">
											<span class="shrink-0" title={r.kind}>{relicIcon(r.kind)}</span>
											{#if r.url}
												<a
													href={String(r.url)}
													target="_blank"
													rel="external noreferrer"
													class="truncate text-sky-300 underline decoration-sky-800 hover:text-sky-200"
													>{relicLabel(r)}</a
												>
											{:else}
												<span class="truncate text-stone-300">{relicLabel(r)}</span>
											{/if}
											{#if r._from_run_id}
												<span class="shrink-0 text-[10px] text-stone-600"
													>↳ via {r._from_run_id}</span
												>
											{/if}
										</li>
									{/each}
								</ul>
							</div>
						{/if}
					{/if}
				</div>
			{/each}
		</div>
	{/if}
</div>
