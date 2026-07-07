// Loom slice 3 (#271): closed-run receipt feed. Types mirror the JSON
// `GET /v1/dashboard/run-ledger` returns, sourced from the daemon's local
// `.brr/run-ledger.jsonl` via `PUT /v1/daemons/run-ledger`.

export interface RunLedgerRow {
	run_id: string | null;
	event_id: string | null;
	started_at: string | null;
	ended_at: string | null;
	wall_clock_seconds: number | null;
	runner_shell: string | null;
	runner_core: string | null;
	repo_label: string | null;
	source_system: string | null;
	external_refs: unknown[] | null;
	task_classification: string | null;
	parent_run_id: string | null;
	is_subspawn: boolean | null;
	tokens_input: number | null;
	tokens_output: number | null;
	tokens_cache_read: number | null;
	tokens_cache_creation: number | null;
	context_window_used: number | null;
	weekly_pct_delta: number | null;
	five_hour_pct_delta: number | null;
	usd_subscription_attributed: number | null;
	usd_credits_equivalent: number | null;
	estimate_vs_actual: string | null;
}

export interface RunLedgerResponse {
	generated_at: string;
	rows: RunLedgerRow[];
	stale: boolean;
	reported_at: string | null;
}

export class RunLedgerAuthError extends Error {}

/** Fetches the closed-run receipt feed. Throws `RunLedgerAuthError` on a
 * 401 (no session cookie), same shape as the other dashboard fetchers. */
export async function fetchRunLedger(
	fetchImpl: typeof fetch = fetch,
	limit = 10
): Promise<RunLedgerResponse> {
	const res = await fetchImpl(`/v1/dashboard/run-ledger?limit=${limit}`, {
		credentials: 'include'
	});
	if (res.status === 401) {
		throw new RunLedgerAuthError('not signed in');
	}
	if (!res.ok) {
		throw new Error(`run-ledger fetch failed: ${res.status}`);
	}
	return (await res.json()) as RunLedgerResponse;
}

export function durationLabel(seconds: number | null): string {
	if (seconds === null || seconds === undefined) return '—';
	if (seconds < 90) return `${Math.round(seconds)}s`;
	const minutes = Math.floor(seconds / 60);
	if (minutes < 90)
		return `${minutes}m ${Math.round(seconds % 60)
			.toString()
			.padStart(2, '0')}s`;
	const hours = Math.floor(minutes / 60);
	return `${hours}h ${minutes % 60}m`;
}

export function tokenLabel(value: number | null): string {
	if (value === null || value === undefined) return '—';
	return new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 }).format(
		value
	);
}

export function signedPercentLabel(value: number | null): string {
	if (value === null || value === undefined) return '—';
	const sign = value > 0 ? '+' : '';
	return `${sign}${value.toFixed(2)}%`;
}

export function usdLabel(value: number | null): string {
	if (value === null || value === undefined) return '—';
	return new Intl.NumberFormat('en', {
		style: 'currency',
		currency: 'USD',
		maximumFractionDigits: 4
	}).format(value);
}

export function endedLabel(endedAt: string | null): string {
	if (!endedAt) return '—';
	const ended = Date.parse(endedAt);
	if (Number.isNaN(ended)) return '—';
	return new Date(ended).toLocaleTimeString();
}
