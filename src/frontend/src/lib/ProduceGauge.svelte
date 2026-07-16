<script lang="ts">
	import {
		gaugeDuration,
		gaugeTokens,
		gaugeUsd,
		produceGaugeLinks,
		rollupProduceGauge
	} from './produceGauge';
	import { relicIcon, type RunLedgerRow } from './runLedger';
	import { STATUS_GOOD, STATUS_UNKNOWN, STATUS_WARN } from './statusPalette';

	interface Props {
		rows: RunLedgerRow[];
		stale: boolean;
		now: number;
	}

	let { rows, stale, now }: Props = $props();
	let summary = $derived(rollupProduceGauge(rows, now));
	let linkedProduce = $derived(produceGaugeLinks(rows, now));

	interface GaugePart {
		value: string;
		label: string;
	}

	function labelFor(count: number, singular: string, pluralWord = `${singular}s`): string {
		return count === 1 ? singular : pluralWord;
	}

	let spendParts = $derived.by(() => {
		const parts: GaugePart[] = [
			{ value: String(summary.runCount), label: labelFor(summary.runCount, 'run') }
		];
		if (summary.wallClockSeconds !== null) {
			parts.push({ value: gaugeDuration(summary.wallClockSeconds), label: 'wall' });
		}
		if (summary.tokensInput !== null)
			parts.push({ value: gaugeTokens(summary.tokensInput), label: 'tok in' });
		if (summary.tokensOutput !== null)
			parts.push({ value: gaugeTokens(summary.tokensOutput), label: 'tok out' });
		for (const quota of summary.weeklyQuota) {
			const consumption = `${quota.percent > 0 ? '−' : ''}${quota.percent.toFixed(1)}%`;
			parts.push({ value: consumption, label: `wk ${quota.shell}` });
		}
		if (summary.usdSubscriptionAttributed !== null) {
			parts.push({ value: gaugeUsd(summary.usdSubscriptionAttributed), label: 'attributed' });
		}
		return parts;
	});

	let produceParts = $derived.by(() => {
		const parts: GaugePart[] = [];
		if (summary.prs) parts.push({ value: String(summary.prs), label: labelFor(summary.prs, 'PR') });
		if (summary.commits)
			parts.push({ value: String(summary.commits), label: labelFor(summary.commits, 'commit') });
		if (summary.kbPages) parts.push({ value: String(summary.kbPages), label: 'kb' });
		if (summary.replies)
			parts.push({
				value: String(summary.replies),
				label: labelFor(summary.replies, 'reply', 'replies')
			});
		return parts;
	});
</script>

<div class="panel p-4">
	<div class="flex items-center justify-between gap-3">
		<span class="eyebrow">last 24h · spend → produce</span>
		{#if stale}
			<span
				class="shrink-0 border border-sky-900/60 bg-sky-950/40 px-1.5 py-0.5 font-mono text-[10px] tracking-wide text-sky-300 uppercase"
				>stale report</span
			>
		{/if}
	</div>
	{#if summary.runCount === 0}
		<p class="mt-2 text-sm text-stone-500">no closed runs in the last 24h</p>
	{:else}
		<div
			class="mt-3 flex flex-wrap items-stretch gap-3 font-mono tracking-tight"
			aria-label="24-hour spend and produce"
		>
			<section class="subpanel flex-none px-3 py-2.5" aria-label="spend">
				<p class="mb-2 text-[10px] leading-none tracking-[0.18em] text-stone-500 uppercase">
					spend
				</p>
				<div class="flex flex-nowrap items-start gap-4">
					{#each spendParts as part (part.label)}
						<div class="flex-none whitespace-nowrap">
							<span class="block text-sm leading-none font-medium" style={`color: ${STATUS_WARN}`}
								>{part.value}</span
							>
							<span
								class="mt-1 block text-[9px] leading-none tracking-[0.12em] uppercase opacity-60"
								style={`color: ${STATUS_WARN}`}>{part.label}</span
							>
						</div>
					{/each}
				</div>
			</section>

			<!-- The arrow belongs to the produce cluster so flex-wrap can never
			     leave it orphaned at the end of the spend row. -->
			<div class="flex flex-none items-stretch gap-3">
				<div
					class="subpanel flex w-9 flex-none items-center justify-center text-xl leading-none"
					style={`color: ${STATUS_UNKNOWN}`}
					aria-hidden="true"
				>
					→
				</div>
				<section class="subpanel flex-none px-3 py-2.5" aria-label="produce">
					<p class="mb-2 text-[10px] leading-none tracking-[0.18em] text-stone-500 uppercase">
						produce
					</p>
					{#if produceParts.length === 0}
						<p class="whitespace-nowrap text-[11px] text-stone-500">no recorded produce</p>
					{:else}
						<div class="flex flex-nowrap items-start gap-4">
							{#each produceParts as part (part.label)}
								<div class="flex-none whitespace-nowrap">
									<span
										class="block text-sm leading-none font-medium"
										style={`color: ${STATUS_GOOD}`}>{part.value}</span
									>
									<span
										class="mt-1 block text-[9px] leading-none tracking-[0.12em] uppercase opacity-60"
										style={`color: ${STATUS_GOOD}`}>{part.label}</span
									>
								</div>
							{/each}
						</div>
					{/if}
				</section>
			</div>
		</div>
		{#if linkedProduce.length > 0}
			<details class="group mt-3 border-t border-stone-800/70 pt-2 font-mono">
				<summary
					class="flex cursor-pointer list-none items-center justify-between gap-3 text-[10px] tracking-[0.12em] text-stone-500 uppercase"
				>
					<span>linked produce · {linkedProduce.length}</span>
					<span class="group-open:hidden">▼ expand</span>
					<span class="hidden group-open:inline">▲ collapse</span>
				</summary>
				<ul class="mt-2 grid max-h-64 grid-cols-1 gap-1.5 overflow-y-auto sm:grid-cols-2">
					{#each linkedProduce as relic (relic.url)}
						<li class="flex min-w-0 items-center gap-1.5 text-[11px]">
							<span class="shrink-0" title={relic.kind}>{relicIcon(relic.kind)}</span>
							<a
								href={relic.url}
								target="_blank"
								rel="external noreferrer"
								class="truncate text-sky-300 underline decoration-sky-800 hover:text-sky-200"
								>{relic.label}</a
							>
						</li>
					{/each}
				</ul>
			</details>
		{/if}
	{/if}
</div>
