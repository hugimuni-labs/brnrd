// Owner-facing config-change approval (#327's final Jinja-removal slice).
// Authentication, account scope, expiry, and inbox enqueueing stay on the
// backend in `web_auth` + `config_approval.decide_core`; this module only
// moves the JSON payloads between that policy boundary and the SPA.

export type ConfigChangeStatus = 'pending' | 'approved' | 'rejected' | 'expired';

export interface ConfigApproveRequest {
	id: string;
	repo_label: string;
	config_key: string;
	current_value: string;
	requested_value: string;
	reason: string;
	status: ConfigChangeStatus;
	expires_at: string | null;
}

export interface ConfigApproveResult {
	ok: boolean;
	notice: string;
	request: ConfigApproveRequest | null;
}

export class ConfigApproveAuthError extends Error {}

export async function fetchConfigApproveRequest(
	requestId: string,
	fetchImpl: typeof fetch = fetch
): Promise<ConfigApproveRequest> {
	const res = await fetchImpl(`/v1/config-approve/${encodeURIComponent(requestId)}`, {
		credentials: 'include'
	});
	if (res.status === 401) throw new ConfigApproveAuthError('not signed in');
	if (!res.ok) throw new Error(`config approval fetch failed: ${res.status}`);
	return (await res.json()) as ConfigApproveRequest;
}

export async function decideConfigApproveRequest(
	requestId: string,
	decision: 'approve' | 'reject',
	fetchImpl: typeof fetch = fetch
): Promise<ConfigApproveResult> {
	const res = await fetchImpl(`/v1/config-approve/${encodeURIComponent(requestId)}`, {
		method: 'POST',
		credentials: 'include',
		headers: { 'content-type': 'application/json' },
		body: JSON.stringify({ decision })
	});
	if (res.status === 401) throw new ConfigApproveAuthError('not signed in');
	const body = (await res.json().catch(() => ({}))) as Partial<ConfigApproveResult>;
	return {
		ok: body.ok === true && res.ok,
		notice:
			typeof body.notice === 'string' && body.notice
				? body.notice
				: `config approval failed: ${res.status}`,
		request: body.request ?? null
	};
}

export function canDecide(request: ConfigApproveRequest): boolean {
	return (
		request.status === 'pending' &&
		(!request.expires_at || Date.parse(request.expires_at) >= Date.now())
	);
}

export function statusNotice(request: ConfigApproveRequest): string | null {
	if (request.status === 'approved' || request.status === 'rejected') {
		return `Already ${request.status}. No further action is needed.`;
	}
	if (
		request.status === 'expired' ||
		(request.expires_at && Date.parse(request.expires_at) < Date.now())
	) {
		return 'This config-change request expired before a decision was made. No change was applied.';
	}
	return null;
}
