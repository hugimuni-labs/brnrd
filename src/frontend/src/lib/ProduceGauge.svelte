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

	function plural(count: number, singular: string, pluralWord = `${singular}s`): string {
		return `${count} ${count === 1 ? singular : pluralWord}`;
	}

	let spendParts = $derived.by(() => {
		const parts = [plural(summary.runCount, 'run')];
		if (summary.wallClockSeconds !== null) {
			parts.push(`${gaugeDuration(summary.wallClockSeconds)} wall`);
		}
		if (summary.tokensInput !== null) parts.push(`${gaugeTokens(summary.tokensInput)} tok in`);
		if (summary.tokensOutput !== null) parts.push(`${gaugeTokens(summary.tokensOutput)} out`);
		for (const quota of summary.weeklyQuota) {
			const consumption = `${quota.percent > 0 ? '−' : ''}${quota.percent.toFixed(1)}%`;
			parts.push(`${consumption} wk (${quota.shell})`);
		}
		if (summary.usdSubscriptionAttributed !== null) {
			parts.push(`${gaugeUsd(summary.usdSubscriptionAttributed)} attributed`);
		}
		return parts;
	});

	let produceParts = $derived.by(() => {
		const parts: string[] = [];
		if (summary.prs) parts.push(plural(summary.prs, 'PR'));
		if (summary.commits) parts.push(plural(summary.commits, 'commit'));
		if (summary.kbPages) parts.push(`${summary.kbPages} kb`);
		if (summary.replies) parts.push(plural(summary.replies, 'reply', 'replies'));
		return parts.length > 0 ? parts : ['no recorded produce'];
	});
</script>

<div class="panel p-4">
	<div class="flex items-center justify-between gap-3">
		<span class="eyebrow">past half · spend → produce</span>
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
		<p
			class="mt-2 font-mono text-[11px] leading-relaxed tracking-tight"
			aria-label="24-hour spend and produce"
		>
			<span style={`color: ${STATUS_WARN}`}>last 24h: {spendParts.join(' · ')}</span>
			<span class="mx-1.5" style={`color: ${STATUS_UNKNOWN}`}>→</span>
			<span style={`color: ${STATUS_GOOD}`}>{produceParts.join(' · ')}</span>
		</p>
	{/if}
</div>
