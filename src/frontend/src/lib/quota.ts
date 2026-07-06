// Slice 2: window-track live-quota view (kb/design-dashboard-live-surface.md
// "A shape for the live-flow surface"). Types mirror the JSON
// `GET /v1/dashboard/quota` returns (`src/brnrd_web/activity_dashboard.py::
// dashboard_quota_api`), which is a thin wrapper around `_quota_views` — the
// same data the (soon to be replaced) Jinja dashboard renders.

export interface QuotaWindow {
	label: string;
	used: number | null;
	limit: number | null;
	percent: number | null;
	reset: string | null;
	/** Unix epoch seconds — machine-parseable twin of `reset`'s display text.
	 *  Absent (not just null) on daemon builds older than 2026-07-06. */
	resets_at?: number | null;
}

export interface QuotaShell {
	shell: string;
	status: 'known' | 'stale' | 'unknown' | string;
	windows: QuotaWindow[];
}

export interface QuotaResponse {
	generated_at: string;
	runner_quotas: QuotaShell[];
}

export class QuotaAuthError extends Error {}

/** Fetches the live per-shell quota snapshot. Throws `QuotaAuthError` on a
 * 401 (no session cookie) so the caller can point the user at `/login`
 * instead of rendering an empty track. */
export async function fetchQuota(fetchImpl: typeof fetch = fetch): Promise<QuotaResponse> {
	const res = await fetchImpl('/v1/dashboard/quota', { credentials: 'include' });
	if (res.status === 401) {
		throw new QuotaAuthError('not signed in');
	}
	if (!res.ok) {
		throw new Error(`quota fetch failed: ${res.status}`);
	}
	return (await res.json()) as QuotaResponse;
}

export type QuotaLevel = 'ample' | 'low' | 'critical' | 'unknown';

/** Draining-bar color threshold — matches the maintainer's own correction
 * (ledger 2026-07-05): "the track runs out, it doesn't fill up, and changes
 * color by remaining level." Percent here is *remaining*, not used. */
export function quotaLevel(percent: number | null | undefined): QuotaLevel {
	if (percent === null || percent === undefined) return 'unknown';
	if (percent <= 15) return 'critical';
	if (percent <= 40) return 'low';
	return 'ample';
}

/** Renders a countdown ("2h 14m", "38m", "<1m") from an epoch, ticking off
 * `now` rather than re-fetching — the track should visibly drain between
 * polls, not just jump on refresh. */
export function timeUntil(resetsAt: number | null | undefined, now: number): string | null {
	if (resetsAt === null || resetsAt === undefined) return null;
	const seconds = Math.max(0, resetsAt * 1000 - now) / 1000;
	if (seconds <= 0) return 'now';
	const hours = Math.floor(seconds / 3600);
	const minutes = Math.floor((seconds % 3600) / 60);
	if (hours > 0) return `${hours}h ${minutes}m`;
	if (minutes > 0) return `${minutes}m`;
	return '<1m';
}
