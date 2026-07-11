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
