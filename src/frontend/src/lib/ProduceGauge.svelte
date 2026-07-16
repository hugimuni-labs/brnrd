<script lang="ts">
	import { gaugeDuration, gaugeTokens, gaugeUsd, rollupProduceGauge } from './produceGauge';
	import type { RunLedgerRow } from './runLedger';
	import { STATUS_GOOD, STATUS_UNKNOWN, STATUS_WARN } from './statusPalette';

	interface Props {
		rows: RunLedgerRow[];
		stale: boolean;
		now: number;
	}

	let { rows, stale, now }: Props = $props();
	let summary = $derived(rollupProduceGauge(rows, now));

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
			class="mt-3 flex flex-wrap items-stretch gap-x-3 gap-y-2 font-mono tracking-tight"
			aria-label="24-hour spend and produce"
		>
			<div
				class="flex min-w-0 flex-wrap content-center gap-x-3 gap-y-2"
				style={`color: ${STATUS_WARN}`}
			>
				<span class="self-center text-[9px] tracking-[0.18em] text-stone-500 uppercase">spend</span>
				{#each spendParts as part (part.label)}
					<span class="whitespace-nowrap leading-none">
						<span class="text-sm font-medium">{part.value}</span>
						<span class="ml-1 text-[9px] tracking-[0.12em] opacity-70 uppercase">{part.label}</span>
					</span>
				{/each}
			</div>
			<span
				class="flex min-h-7 items-center self-stretch px-1 text-lg leading-none"
				style={`color: ${STATUS_UNKNOWN}`}
				aria-hidden="true">→</span
			>
			<div
				class="flex min-w-0 flex-wrap content-center gap-x-3 gap-y-2"
				style={`color: ${STATUS_GOOD}`}
			>
				<span class="self-center text-[9px] tracking-[0.18em] text-stone-500 uppercase"
					>produce</span
				>
				{#if produceParts.length === 0}
					<span class="self-center whitespace-nowrap text-[11px] text-stone-500"
						>no recorded produce</span
					>
				{:else}
					{#each produceParts as part (part.label)}
						<span class="whitespace-nowrap leading-none">
							<span class="text-sm font-medium">{part.value}</span>
							<span class="ml-1 text-[9px] tracking-[0.12em] opacity-70 uppercase"
								>{part.label}</span
							>
						</span>
					{/each}
				{/if}
			</div>
		</div>
	{/if}
</div>
