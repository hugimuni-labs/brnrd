// Loom envelope Phase 2 dashboard surface (kb/design-multi-workstream-
// concurrency.md "Named forks - round 2"). Types mirror the JSON
// `GET /v1/dashboard/config-requests` returns
// (`src/brnrd/routers/dashboard.py::dashboard_config_requests_api`),
// a direct read of the `config_change_requests` table the daemon writes via
// `POST /v1/daemons/config-requests` — unlike live-runs/PR-queue/run-ledger,
// there's no publish/mirror step and no staleness concept here.

export interface ConfigChangeRequestItem {
	id: string;
	repo_label: string;
	config_key: string;
	current_value: string;
	requested_value: string;
	reason: string;
	created_at: string | null;
	expires_at: string | null;
	approve_url: string;
}

export interface ConfigRequestsResponse {
	generated_at: string | null;
	requests: ConfigChangeRequestItem[];
}

export class ConfigRequestsAuthError extends Error {}

/** Fetches the account-scoped pending config-change requests. Throws
 * `ConfigRequestsAuthError` on a 401 (no session cookie), same shape as the
 * other dashboard fetchers. */
export async function fetchConfigRequests(
	fetchImpl: typeof fetch = fetch
): Promise<ConfigRequestsResponse> {
	const res = await fetchImpl('/v1/dashboard/config-requests', { credentials: 'include' });
	if (res.status === 401) {
		throw new ConfigRequestsAuthError('not signed in');
	}
	if (!res.ok) {
		throw new Error(`config-requests fetch failed: ${res.status}`);
	}
	return (await res.json()) as ConfigRequestsResponse;
}
