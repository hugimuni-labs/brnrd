// Sign-in page context (#327 Jinja-removal, /login slice). The backend owns
// `next` validation and the OAuth start URL; this client only renders what
// `GET /v1/dashboard/login-context` hands back.

export interface LoginContext {
	authenticated: boolean;
	oauth_ready: boolean;
	signin_url: string;
	next: string;
}

export async function fetchLoginContext(
	next: string | null,
	fetchImpl: typeof fetch = fetch
): Promise<LoginContext> {
	const query = next ? `?next=${encodeURIComponent(next)}` : '';
	const res = await fetchImpl(`/v1/dashboard/login-context${query}`, {
		credentials: 'include'
	});
	if (!res.ok) {
		throw new Error(`login-context fetch failed: ${res.status}`);
	}
	return (await res.json()) as LoginContext;
}

// The landing's own "sign in" affordances (#327 follow-up, 2026-08-06): a
// visitor who taps "sign in" from the landing has just read what brnrd is,
// so sending them through /login's own near-identical pitch a second time
// before the real CTA is a redundant hop, not a second page. Once GitHub
// OAuth is confirmed live, jump straight to the handshake; otherwise (not
// yet loaded, or a self-hosted deployment with no OAuth client configured)
// fall back to /login unchanged — it still exists, and still earns its
// keep for cold deep links that carry their own `next` (a pairing URL) or
// need the disabled-OAuth explanation `/login` itself renders.
export function resolveSigninHref(context: LoginContext | null, fallback: string): string {
	return context?.oauth_ready ? context.signin_url : fallback;
}
