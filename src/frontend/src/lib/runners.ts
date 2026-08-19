import type { WithheldLane } from './withheld';

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
	/** Tri-state, and the type says so: `true` (verified live), `false`
	 *  (verified dead), and — the state a row missing this field used to
	 *  render as `true` (2026-08-19, the rack of dead spools) — `null` /
	 *  `undefined` for "this daemon's report didn't say". Absence of a fact
	 *  is not the fact; see `spoolRack.ts::availabilityOf`, the one place
	 *  that turns this into a render decision. */
	available?: boolean | null;
	/** True on the profile the daemon resolved as its current selection. */
	selected?: boolean | null;
	/** When this row's own source report was last received — distinct from
	 *  `RunnersResponse.reported_at` (the account's newest report across
	 *  every daemon). A dict merged by profile name across daemons can hold
	 *  a row from a daemon that retired days ago while a *different* daemon
	 *  keeps the account-wide timestamp looking fresh (dashboard.py's
	 *  `_runners_views`) — this is the fact that lets a reader catch that. */
	daemon_reported_at?: string | null;
	/** This row's own source report is older than the freshness window —
	 *  server-computed (`dashboard.py`), same threshold as the account-wide
	 *  `stale` flag but scored per row instead of per account. */
	daemon_stale?: boolean | null;
}

/** A pending spool-rack tap (#328 tap-to-request): "next wake on this
 * profile". One-shot and cancelable until a wake consumes it; the daemon
 * learns of it within one catalog-publish tick. */
export interface WakeRequest {
	request_id: string;
	profile: string;
	repo_label: string | null;
	environment: string | null;
	requested_at: string | null;
	status: string;
}

/** #932's conversation-sticky, mirrored up the runners lane: a claimed tap
 * binds its profile to the claiming conversation for a TTL, and until it
 * expires it — not the pin, not a parked tap — answers that thread's wakes.
 * Rendering it (timer included) is what stops the rack lying about who
 * wakes next (2026-08-08). */
export interface RunnerSticky {
	profile: string;
	claimed_at?: string | null;
	expires_at?: string | null;
	correspondent_key?: string | null;
	conversation_key?: string | null;
	request_id?: string | null;
}

/** The sticky, if it decides anything right now — expiry checked against
 * the caller's clock so a stale mirror can't render a dead promise. */
export function liveSticky(
	sticky: RunnerSticky | null | undefined,
	nowMs: number = Date.now()
): RunnerSticky | null {
	if (!sticky || !sticky.profile) return null;
	if (sticky.expires_at) {
		const expires = Date.parse(sticky.expires_at);
		if (!Number.isNaN(expires) && nowMs >= expires) return null;
	}
	return sticky;
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
	/** #932: the conversation-sticky in force, if any — the answer to "who
	 *  wakes next *in the bound thread*", outranking `default` there. */
	sticky?: RunnerSticky | null;
	withheld?: WithheldLane;
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
	dispatch: { repo_label?: string | null; environment?: string | null } = {},
	fetchImpl: typeof fetch = fetch
): Promise<WakeRequest> {
	const res = await fetchImpl('/v1/dashboard/runners/wake-request', {
		method: 'POST',
		credentials: 'include',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({
			profile,
			repo_label: dispatch.repo_label ?? null,
			environment: dispatch.environment ?? null
		})
	});
	if (res.status === 401) {
		throw new RunnersAuthError('not signed in');
	}
	if (!res.ok) {
		throw new Error(`wake request failed: ${res.status}`);
	}
	return ((await res.json()) as { wake_request: WakeRequest }).wake_request;
}

/** #932's exit tap: ask the daemon to drop its conversation-sticky now.
 * The server parks a timestamped ask; the daemon honours it on its next
 * publish tick (a sticky claimed *after* the ask survives it), so expect
 * the chip to clear within one tick, not instantly. */
export async function releaseSticky(fetchImpl: typeof fetch = fetch): Promise<void> {
	const res = await fetchImpl('/v1/dashboard/runners/sticky-release', {
		method: 'POST',
		credentials: 'include'
	});
	if (res.status === 401) {
		throw new RunnersAuthError('not signed in');
	}
	if (!res.ok) {
		throw new Error(`sticky release failed: ${res.status}`);
	}
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
