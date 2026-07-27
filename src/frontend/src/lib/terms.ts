// Acceptance state for the legal documents a user can accept (#735).
//
// `GET /v1/dashboard/terms-status` used to describe exactly one document with
// flat `needs_accept` / `terms_version` keys. There are two now — the general
// Terms of Service and the hosted-execution addendum — and a privacy notice
// plus a mentions légales are already named as owed, so the shape is a map
// keyed by document. The client never decides which version is current or
// whether acceptance is outstanding; the server does, and this module only
// carries the answer.

export const DOC_TOS = 'tos';
export const DOC_HOSTED = 'hosted-execution';

export interface DocumentStatus {
	/** The operator's label for the current text — what re-consent triggers on. */
	version: string;
	/** sha256 of the pinned plain text of the current version. */
	sha256: string;
	/** Where this document is read and accepted. */
	accept_url: string;
	/** Null when no account is present, so acceptance does not apply. */
	needs_accept: boolean | null;
	accepted_at: string | null;
	/** The hash this account's record actually stored; null when never accepted. */
	accepted_sha256: string | null;
}

export interface TermsStatus {
	authenticated: boolean;
	documents: Record<string, DocumentStatus>;
}

export type AcceptOutcome = { ok: true } | { ok: false; notice: string; unauthenticated: boolean };

export async function fetchTermsStatus(fetchImpl: typeof fetch = fetch): Promise<TermsStatus> {
	const res = await fetchImpl('/v1/dashboard/terms-status', { credentials: 'include' });
	if (!res.ok) {
		throw new Error(`terms-status fetch failed: ${res.status}`);
	}
	return (await res.json()) as TermsStatus;
}

export async function acceptDocument(
	document: string,
	fetchImpl: typeof fetch = fetch
): Promise<AcceptOutcome> {
	const res = await fetchImpl('/v1/terms/accept', {
		method: 'POST',
		credentials: 'include',
		headers: { 'content-type': 'application/json' },
		body: JSON.stringify({ document, accept_terms: 'yes' })
	});
	const body = (await res.json().catch(() => ({}))) as { ok?: boolean; notice?: string };
	if (res.status === 401) {
		return { ok: false, notice: 'Sign in before accepting.', unauthenticated: true };
	}
	if (!res.ok || body.ok !== true) {
		return {
			ok: false,
			notice:
				typeof body.notice === 'string' ? body.notice : `terms acceptance failed: ${res.status}`,
			unauthenticated: false
		};
	}
	return { ok: true };
}

/** `next=` is backend-owned and user-supplied; the same guard `_safe_next` applies.
 *
 * Both the `//host` and `/\host` forms are rejected: a browser normalises the
 * backslash to a slash in the authority position, so `/\evil.example` reaches
 * `https://evil.example/` through `window.location.assign` even though it
 * starts with `/`. This is the sink that guard exists for — keep it in step
 * with `_safe_next` in `_session.py`.
 */
export function safeNext(value: string | null): string {
	if (!value || !value.startsWith('/')) return '/';
	if (value[1] === '/' || value[1] === '\\') return '/';
	return value;
}
