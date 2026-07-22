// Daemon pairing approval (#327 Jinja-removal, /connect slice). The backend
// owns every auth consequence — session requirement, code expiry, single-use,
// account-scoped repo lookup (`approve_core`); this client only renders the
// context `GET /v1/connect/{code}` hands back and relays the approve click.

export interface ConnectRepo {
	id: string;
	repo_full_name: string;
}

export type PairCodeStatus = 'pending' | 'approved' | 'consumed' | 'expired' | 'unknown';

export interface ConnectContext {
	code: string;
	status: PairCodeStatus;
	repos: ConnectRepo[];
}

export interface TelegramPair {
	pair_code: string;
	instructions: string;
	deep_link: string | null;
}

export interface ApproveResult {
	ok: boolean;
	notice: string;
	telegram: TelegramPair | null;
}

export class ConnectAuthError extends Error {}

export async function fetchConnectContext(
	code: string,
	fetchImpl: typeof fetch = fetch
): Promise<ConnectContext> {
	const res = await fetchImpl(`/v1/connect/${encodeURIComponent(code)}`, {
		credentials: 'include'
	});
	if (res.status === 401) throw new ConnectAuthError('not signed in');
	if (!res.ok) throw new Error(`connect context fetch failed: ${res.status}`);
	return (await res.json()) as ConnectContext;
}

export async function approveConnect(
	code: string,
	repoId: string,
	fetchImpl: typeof fetch = fetch
): Promise<ApproveResult> {
	const res = await fetchImpl(`/v1/connect/${encodeURIComponent(code)}`, {
		method: 'POST',
		credentials: 'include',
		headers: { 'content-type': 'application/json' },
		body: JSON.stringify({ repo_id: repoId })
	});
	if (res.status === 401) throw new ConnectAuthError('not signed in');
	const body = (await res.json().catch(() => ({}))) as Partial<ApproveResult>;
	return {
		ok: body.ok === true && res.ok,
		notice:
			typeof body.notice === 'string' && body.notice
				? body.notice
				: `approve failed: ${res.status}`,
		telegram: body.telegram ?? null
	};
}

// The one state the page can act in: a live code and at least one repo to
// bind it to. Everything else renders a terminal notice.
export function canApprove(context: ConnectContext): boolean {
	return (
		(context.status === 'pending' || context.status === 'approved') && context.repos.length > 0
	);
}

// Terminal-state copy — mirrors the notices the backend's approve path
// would return, so a dead code reads the same before and after the click.
export function statusNotice(context: ConnectContext): string | null {
	switch (context.status) {
		case 'unknown':
			return 'This pair code is unknown. Re-run `brnrd account connect` for a fresh link.';
		case 'expired':
			return 'This pair code expired. Re-run `brnrd account connect` for a fresh link.';
		case 'consumed':
			return 'This pair code was already used. Your daemon should be connected.';
		default:
			return context.repos.length === 0
				? 'No repos connected yet. Connect a repo first, then reload this approval page.'
				: null;
	}
}
