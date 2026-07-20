// #328 spool rack: the runner catalog as the loom's thread inventory.
// Types mirror `GET /v1/dashboard/runners` (`src/brnrd/routers/
// dashboard.py::dashboard_runners_api`), which merges each
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

/** A pending spool-rack tap (#328 tap-to-request): "next wake on this
 * profile". One-shot and cancelable until a wake consumes it; the daemon
 * learns of it within one catalog-publish tick. */
export interface WakeRequest {
	request_id: string;
	profile: string;
	requested_at: string | null;
	status: string;
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
	/** The account's pending tap, if any — supersedes `default` as the
	 *  answer to "who wakes next". */
	wake_request: WakeRequest | null;
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

/** Tap a rack row: park a one-shot "next wake on this profile" request. */
export async function requestWake(
	profile: string,
	fetchImpl: typeof fetch = fetch
): Promise<WakeRequest> {
	const res = await fetchImpl('/v1/dashboard/runners/wake-request', {
		method: 'POST',
		credentials: 'include',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ profile })
	});
	if (res.status === 401) {
		throw new RunnersAuthError('not signed in');
	}
	if (!res.ok) {
		throw new Error(`wake request failed: ${res.status}`);
	}
	return ((await res.json()) as { wake_request: WakeRequest }).wake_request;
}

/** Cancel a pending tap. Returns the row's final state — `consumed`
 * means the wake already fired before the cancel landed. */
export async function cancelWake(
	requestId: string,
	fetchImpl: typeof fetch = fetch
): Promise<WakeRequest> {
	const res = await fetchImpl(`/v1/dashboard/runners/wake-request/${requestId}`, {
		method: 'DELETE',
		credentials: 'include'
	});
	if (res.status === 401) {
		throw new RunnersAuthError('not signed in');
	}
	if (!res.ok) {
		throw new Error(`wake cancel failed: ${res.status}`);
	}
	return ((await res.json()) as { wake_request: WakeRequest }).wake_request;
}
