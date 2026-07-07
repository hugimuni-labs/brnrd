// Slice 3 (kb/design-dashboard-live-surface.md "Reconsidered 2026-07-06"):
// account-scoped live/coexisting-runs view. Types mirror the JSON
// `GET /v1/dashboard/live-runs` returns (`src/brnrd_web/activity_dashboard.py::
// dashboard_live_runs_api`), sourced from the local presence registry
// (`src/brr/presence.py`) via the daemon's `PUT /v1/daemons/live-runs` publish.

export interface LiveRun {
	id: string;
	kind: string;
	stream: string;
	label: string;
	run_id: string;
	repo_label: string;
	started_at: string | null;
	last_seen: string | null;
}

export interface LiveRunsResponse {
	generated_at: string;
	runs: LiveRun[];
	stale: boolean;
	reported_at: string | null;
}

export class LiveRunsAuthError extends Error {}

/** Fetches the account-scoped live-runs snapshot. Throws `LiveRunsAuthError`
 * on a 401 (no session cookie), same shape as `fetchQuota`. */
export async function fetchLiveRuns(fetchImpl: typeof fetch = fetch): Promise<LiveRunsResponse> {
	const res = await fetchImpl('/v1/dashboard/live-runs', { credentials: 'include' });
	if (res.status === 401) {
		throw new LiveRunsAuthError('not signed in');
	}
	if (!res.ok) {
		throw new Error(`live-runs fetch failed: ${res.status}`);
	}
	return (await res.json()) as LiveRunsResponse;
}

/** "3m ago" / "just now" — a live run's age since it started, ticking off
 * `now` the same way `timeUntil` ticks the quota window's countdown. */
export function ageSince(startedAt: string | null, now: number): string | null {
	if (!startedAt) return null;
	const started = Date.parse(startedAt);
	if (Number.isNaN(started)) return null;
	const deltaS = Math.max(0, Math.floor((now - started) / 1000));
	if (deltaS < 60) return 'just now';
	const minutes = Math.floor(deltaS / 60);
	if (minutes < 60) return `${minutes}m ago`;
	const hours = Math.floor(minutes / 60);
	return `${hours}h ${minutes % 60}m ago`;
}
