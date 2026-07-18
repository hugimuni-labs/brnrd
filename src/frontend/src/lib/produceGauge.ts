import type { RelicRecord, RunLedgerRow } from './runLedger';

export const PRODUCE_GAUGE_WINDOW_MS = 24 * 60 * 60 * 1000;
// Shared with the loom: enough published rows to cover its seven-day shelf
// at the observed run rate while the gauge still rolls up only trailing 24h.
export const PRODUCE_GAUGE_LEDGER_LIMIT = 256;

export interface ShellQuotaSpend {
	shell: string;
	percent: number;
}

export interface ProduceGaugeSummary {
	runCount: number;
	wallClockSeconds: number | null;
	tokensInput: number | null;
	tokensOutput: number | null;
	weeklyQuota: ShellQuotaSpend[];
	usdSubscriptionAttributed: number | null;
	prs: number;
	commits: number;
	kbPages: number;
	replies: number;
}

export interface ProduceGaugeLink {
	kind: 'pr' | 'commit' | 'kb' | 'reply';
	label: string;
	url: string;
}

function finite(value: unknown): number | null {
	return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function sumPresent(rows: RunLedgerRow[], field: keyof RunLedgerRow): number | null {
	const values = rows
		.map((row) => finite(row[field]))
		.filter((value): value is number => value !== null);
	return values.length > 0 ? values.reduce((total, value) => total + value, 0) : null;
}

function recentGaugeRows(rows: RunLedgerRow[], nowMs: number): RunLedgerRow[] {
	const cutoff = nowMs - PRODUCE_GAUGE_WINDOW_MS;
	return rows.filter((row) => {
		if (!row.ended_at) return false;
		const ended = Date.parse(row.ended_at);
		return Number.isFinite(ended) && ended >= cutoff && ended <= nowMs;
	});
}

function produceLinkLabel(relic: RelicRecord): string {
	switch (relic.kind) {
		case 'pr':
			return `PR #${relic.number ?? '?'}`;
		case 'commit':
			return `${String(relic.sha ?? '').slice(0, 7)} ${relic.subject ?? ''}`.trim() || 'commit';
		case 'kb':
			return String(relic.path ?? 'kb page');
		case 'reply':
			return String(relic.excerpt ?? 'reply');
		default:
			return relic.kind;
	}
}

/** Roll up the spend and produce proved by rows closed during the trailing
 * 24 hours. Invalid timestamps and absent metrics disappear instead of
 * becoming fabricated zeroes. */
export function rollupProduceGauge(
	rows: RunLedgerRow[],
	nowMs: number = Date.now()
): ProduceGaugeSummary {
	const recent = recentGaugeRows(rows, nowMs);

	const quota = new Map<string, number>();
	for (const row of recent) {
		const delta = finite(row.weekly_pct_delta);
		if (delta === null) continue;
		const shell = row.runner_shell?.trim().toLowerCase() || 'unknown';
		// A quota reset can make a per-run delta negative. It is rollover
		// noise, not quota returned by the run, so only consumption adds up.
		quota.set(shell, (quota.get(shell) ?? 0) + Math.max(0, delta));
	}

	const prNumbers = new Set<string>();
	let commits = 0;
	let kbPages = 0;
	let replies = 0;
	for (const row of recent) {
		const repo = row.repo_label?.trim().toLowerCase() || 'unknown';
		const rowRelics = Array.isArray(row.external_refs) ? row.external_refs : [];
		for (const relic of rowRelics) {
			if (relic === null || typeof relic !== 'object') continue;
			const kind = String(relic.kind ?? '').toLowerCase();
			if (kind === 'pr' && relic.number !== null && relic.number !== undefined) {
				const number = String(relic.number).trim();
				if (number) prNumbers.add(`${repo}#${number}`);
			} else if (kind === 'commit') {
				commits += 1;
			} else if (kind === 'kb' || kind === 'kb_page') {
				kbPages += 1;
			} else if (kind === 'reply') {
				replies += 1;
			}
		}
	}

	return {
		runCount: recent.length,
		wallClockSeconds: sumPresent(recent, 'wall_clock_seconds'),
		tokensInput: sumPresent(recent, 'tokens_input'),
		tokensOutput: sumPresent(recent, 'tokens_output'),
		weeklyQuota: [...quota.entries()]
			.map(([shell, percent]) => ({ shell, percent }))
			.sort((a, b) => a.shell.localeCompare(b.shell)),
		usdSubscriptionAttributed: sumPresent(recent, 'usd_subscription_attributed'),
		prs: prNumbers.size,
		commits,
		kbPages,
		replies
	};
}

/** URL-bearing produce behind the aggregate gauge. Relics without a URL
 * still contribute to the honest counts above; they simply cannot become
 * navigable links. Summary prose and non-counted relic kinds stay out. */
export function produceGaugeLinks(
	rows: RunLedgerRow[],
	nowMs: number = Date.now()
): ProduceGaugeLink[] {
	const seen = new Set<string>();
	const links: ProduceGaugeLink[] = [];
	for (const row of recentGaugeRows(rows, nowMs)) {
		for (const relic of Array.isArray(row.external_refs) ? row.external_refs : []) {
			if (relic === null || typeof relic !== 'object') continue;
			const rawKind = String(relic.kind ?? '').toLowerCase();
			const kind = rawKind === 'kb_page' ? 'kb' : rawKind;
			if (!['pr', 'commit', 'kb', 'reply'].includes(kind)) continue;
			const url = String(relic.url ?? '').trim();
			if (!url || seen.has(url)) continue;
			try {
				const parsed = new URL(url);
				if (parsed.protocol !== 'https:' && parsed.protocol !== 'http:') continue;
			} catch {
				continue;
			}
			seen.add(url);
			const normalized: RelicRecord = kind === rawKind ? relic : { ...relic, kind };
			links.push({
				kind: kind as ProduceGaugeLink['kind'],
				label: produceLinkLabel(normalized),
				url
			});
		}
	}
	return links;
}

export function gaugeDuration(seconds: number): string {
	const wholeMinutes = Math.floor(seconds / 60);
	if (wholeMinutes < 1) return `${Math.round(seconds)}s`;
	const hours = Math.floor(wholeMinutes / 60);
	const minutes = wholeMinutes % 60;
	return hours > 0 ? `${hours}h${String(minutes).padStart(2, '0')}m` : `${wholeMinutes}m`;
}

export function gaugeTokens(value: number): string {
	return new Intl.NumberFormat('en', {
		notation: 'compact',
		maximumFractionDigits: 1
	}).format(value);
}

export function gaugeUsd(value: number): string {
	return new Intl.NumberFormat('en', {
		style: 'currency',
		currency: 'USD',
		minimumFractionDigits: 2,
		maximumFractionDigits: 2
	}).format(value);
}
