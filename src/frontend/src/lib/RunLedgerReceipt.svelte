<script lang="ts">
	import { fade, fly } from 'svelte/transition';
	import { flip } from 'svelte/animate';
	import { SvelteSet } from 'svelte/reactivity';
	import { typeReveal } from './transitions';
	import {
		durationLabel,
		endedLabel,
		familySuffix,
		groupRelicFamilies,
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
		<p class="text-sm text-ink-quiet">No closed-run receipts yet.</p>
	{:else}
		<div class="grid grid-cols-1 gap-2">
			{#each grouped as entry (receiptKey(entry.row))}
				{@const row = entry.row}
				{@const key = receiptKey(row)}
				{@const label = row.repo_label || '—'}
				{@const runner = [row.runner_shell, row.runner_core].filter(Boolean).join(' · ') || '—'}
				{@const counts = relicCounts(entry.relics)}
				{@const summary = entry.relics.find((r) => r.kind === 'summary')}
				{@const isOpen = expanded.has(key)}
				<div
					class="subpanel p-2.5 text-xs"
					data-loom-run={key}
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
							<p class="truncate font-mono text-ink-quiet">
								{runner}
								{#if row.core_mismatch === false}
									<span
										class="text-emerald-400"
										title="observed model matches the configured core pin">✓</span
									>
								{:else if row.core_mismatch}
									<span
										class="rounded bg-red-950/70 px-1 text-red-300"
										title={row.substitution_reason ??
											'the Shell ran a different model than the configured core pin'}
										>⚠ pinned {row.core_expected ?? '?'}, ran {row.runner_core ?? '?'}</span
									>
								{/if}
							</p>
							<!-- *Why* the Core changed. Without this the badge could
							     say a pin was broken but never what broke it, which
							     is exactly the blind spot that cost three days of
							     guesswork (2026-07-13..16). Null on clean runs. -->
							{#if row.core_mismatch && row.substitution_reason}
								<p class="truncate font-mono text-red-400/80" title={row.substitution_reason}>
									{row.substitution_reason}
								</p>
							{/if}
						</div>
						<span class="shrink-0 font-mono text-ink-quiet">{endedLabel(row.ended_at)}</span>
					</div>

					<div
						class="mt-2 grid grid-cols-2 gap-2 border-t border-stone-800/70 pt-2 font-mono sm:grid-cols-4"
					>
						<div>
							<p class="text-[10px] tracking-wide text-ink-quiet uppercase">wall</p>
							<p class="font-medium text-stone-200">{durationLabel(row.wall_clock_seconds)}</p>
						</div>
						<div>
							<p class="text-[10px] tracking-wide text-ink-quiet uppercase">tokens</p>
							<p class="font-medium text-stone-200">
								{tokenLabel(row.tokens_input)} / {tokenLabel(row.tokens_output)}
							</p>
						</div>
						<div>
							<p class="text-[10px] tracking-wide text-ink-quiet uppercase">weekly / 5h</p>
							<p class="font-medium text-stone-200">
								{signedPercentLabel(row.weekly_pct_delta)} / {signedPercentLabel(
									row.five_hour_pct_delta
								)}
							</p>
						</div>
						<div>
							<p class="text-[10px] tracking-wide text-ink-quiet uppercase">subscription</p>
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
									<span class="text-ink-quiet">summary only</span>
								{/if}
							</span>
							<span class="shrink-0 text-[10px] tracking-wide text-ink-quiet uppercase"
								>{isOpen ? '▲ collapse' : '▼ produce'}</span
							>
						</button>
						{#if isOpen}
							<div
								class="mt-2 space-y-1.5 overflow-hidden border-t border-stone-800/70 pt-2"
								in:fade={{ duration: 140 }}
								out:fade={{ duration: 100 }}
							>
								{#if summary}
									<p class="text-stone-300 italic" use:typeReveal={{ text: relicLabel(summary) }}>
										{relicLabel(summary)}
									</p>
								{/if}
								<!-- #329: relic *families* — a PR absorbs its branch and
								     commits (one piece of produce, one head line, members
								     indented); attribution rides the family line once. -->
								<ul class="space-y-1">
									{#each groupRelicFamilies(entry.relics) as fam, i (i)}
										<li class="min-w-0">
											<div class="flex min-w-0 items-center gap-1.5">
												<span class="shrink-0" title={fam.head.kind}
													>{relicIcon(fam.head.kind)}</span
												>
												{#if fam.head.url}
													<a
														href={String(fam.head.url)}
														target="_blank"
														rel="external noreferrer"
														class="truncate text-sky-300 underline decoration-sky-800 hover:text-sky-200"
														><span use:typeReveal={{ text: relicLabel(fam.head) }}
															>{relicLabel(fam.head)}</span
														></a
													>
												{:else}
													<span
														class="truncate text-stone-300"
														use:typeReveal={{ text: relicLabel(fam.head) }}
														>{relicLabel(fam.head)}</span
													>
												{/if}
												{#if familySuffix(fam)}
													<span class="shrink-0 text-ink-quiet">{familySuffix(fam)}</span>
												{/if}
												{#if fam.head._from_run_id}
													<span class="shrink-0 text-[10px] text-ink-mute"
														>↳ via {fam.head._from_run_id}</span
													>
												{/if}
											</div>
											{#each fam.members.filter((m) => m.kind === 'commit') as m, j (j)}
												<p class="ml-5 truncate text-[11px] text-ink-quiet">
													<span use:typeReveal={{ text: relicLabel(m) }}>{relicLabel(m)}</span
													>{#if m._from_run_id && m._from_run_id !== fam.head._from_run_id}
														<span class="text-[10px] text-ink-mute">
															↳ via {m._from_run_id}</span
														>{/if}
												</p>
											{/each}
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
