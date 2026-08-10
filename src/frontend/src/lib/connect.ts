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
	// The repo the connecting checkout itself reported (`owner/name`, parsed
	// from its git remote) — `""` when this pair code predates the
	// capability, ran with none detected, or is already dead. When present,
	// the page leads with "connect *this* repo" instead of asking the
	// reader to pick one from `repos` — the retired "enable a repository"
	// website click no longer has to have already happened.
	suggested_repo_full_name: string;
	// "github" or "local" for the suggestion above — "" alongside an empty
	// suggestion, or on a pair code old enough to predate this field. A
	// local checkout has no forge behind `owner/name`; the page says so
	// rather than letting a synthesized `local/foo-a1b2c3` read as a real
	// GitHub org.
	suggested_forge: string;
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

// The approval proof the pairing daemon minted, read off the URL fragment
// (`/connect/BR-XXXX#<secret>`). It rides the fragment and not the query
// string deliberately: a fragment is never sent to a server, so it stays out
// of access logs and out of `Referer`. Only the terminal that ran
// `brnrd account connect` printed a link carrying it, which is the whole
// point — a signed-in session says who you are, not that you are the one who
// asked to pair.
export function approvalProofFromHash(hash: string): string {
	return hash.startsWith('#') ? hash.slice(1) : hash;
}

// The sign-in detour has to carry the proof with it. `next=` is consumed
// server-side (cookie -> 303 Location), and a fragment dropped at the login
// hop lands the reader back on the approval page with nothing to present.
// Encoded as one whole value so the `#` survives the query string it rides
// in; the backend's `_safe_next` passes it through unchanged.
export function connectNextUrl(code: string, hash: string): string {
	return `/connect/${encodeURIComponent(code)}${approvalProofFromHash(hash) ? hash : ''}`;
}

export function loginUrlForConnect(code: string, hash: string): string {
	return `/login?next=${encodeURIComponent(connectNextUrl(code, hash))}`;
}

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

// `repoId` omitted (or empty) tells the backend to bind — creating it on
// first use — the repo the pairing daemon itself reported, instead of one
// the reader picked from a list. The primary "connect <repo>" button calls
// this with no id; the fallback dropdown always passes one explicitly.
//
// `approveProof` is the initiator proof from the URL fragment. The backend
// refuses an approve without it (403) — a session alone was never meant to
// authorize binding someone else's pairing.
export async function approveConnect(
	code: string,
	repoId: string = '',
	approveProof: string = '',
	fetchImpl: typeof fetch = fetch
): Promise<ApproveResult> {
	const sent: Record<string, string> = {};
	if (repoId) sent.repo_id = repoId;
	if (approveProof) sent.approve_secret = approveProof;
	const res = await fetchImpl(`/v1/connect/${encodeURIComponent(code)}`, {
		method: 'POST',
		credentials: 'include',
		headers: { 'content-type': 'application/json' },
		body: JSON.stringify(sent)
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

// The states the page can act in: a live code, and either a repo the
// checkout itself already named or at least one existing repo to fall back
// to picking from. Everything else renders a terminal notice.
export function canApprove(context: ConnectContext): boolean {
	return (
		(context.status === 'pending' || context.status === 'approved') &&
		(context.repos.length > 0 || context.suggested_repo_full_name !== '')
	);
}

// A live code the reader still can't approve, because the link they opened
// lost its fragment — hand-copied, or forwarded without the tail. The
// backend answers 403 either way; saying it before the click is the
// difference between a fixable instruction and a wall.
export function missingApprovalProof(context: ConnectContext, hash: string): boolean {
	return canApprove(context) && approvalProofFromHash(hash) === '';
}

// The true dead end (2026-08-03, narrowed 2026-08-06 once pairing could
// name its own repo): a live code, an account with nothing already
// connected, *and* a pairing that reported no repo of its own — an older
// CLI, or `brnrd account connect` run outside a git checkout. Every other
// shape of "nothing enabled yet" now self-resolves on approve.
export function needsRepoEnable(context: ConnectContext): boolean {
	return (
		(context.status === 'pending' || context.status === 'approved') &&
		context.repos.length === 0 &&
		context.suggested_repo_full_name === ''
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
			return needsRepoEnable(context)
				? "This pairing didn't report a repo (older CLI, or run outside a git checkout), " +
						'and this account has nothing connected to fall back to. Connect a repo, then ' +
						'reload this approval page.'
				: null;
	}
}
