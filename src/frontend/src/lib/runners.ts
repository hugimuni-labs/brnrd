// #328 spool rack: the runner catalog as the loom's thread inventory.
// Types mirror `GET /v1/dashboard/runners` (`src/brnrd_web/
// activity_dashboard.py::dashboard_runners_api`), which merges each
// connected daemon's locally-discovered catalog (`src/brr/gates/cloud.py::
// _runners_snapshot`) — what the installed shells actually offer, not a
// packaged menu that can rot.

export interface RunnerProfile {
	name: string;
	shell?: string | null;
	model?: string | null;
	provider?: string | null;
	/** economy | balanced | strong — the selector's cost class. */
	class?: string | null;
	cost_rank?: number | null;
	quota_source?: string | null;
	capability_score?: number | null;
	capability_source?: string | null;
	capability_freshness?: string | null;
	generated_core?: boolean | null;
	availability?: string | null;
	/** True on the profile the daemon resolved as its current selection. */
	selected?: boolean | null;
}

export interface RunnersResponse {
	generated_at: string;
	/** Newest daemon report time — the rack's own clock, distinct from
	 *  when this JSON was served. */
	reported_at: string | null;
	stale: boolean;
	/** The profile `resolve_runner` picks for the next default wake —
	 *  the config pin, or the cost-aware choice when unpinned. */
	default: string | null;
	profiles: RunnerProfile[];
}

export class RunnersAuthError extends Error {}

/** Fetches the merged runner catalog. Throws `RunnersAuthError` on a 401
 * so the caller can defer to the page-level login prompt. */
export async function fetchRunners(fetchImpl: typeof fetch = fetch): Promise<RunnersResponse> {
	const res = await fetchImpl('/v1/dashboard/runners', { credentials: 'include' });
	if (res.status === 401) {
		throw new RunnersAuthError('not signed in');
	}
	if (!res.ok) {
		throw new Error(`runners fetch failed: ${res.status}`);
	}
	return (await res.json()) as RunnersResponse;
}
