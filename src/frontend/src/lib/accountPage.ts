// The account page (`/account`): the one place a signed-in person reaches
// their subscription and the account's deletion. Before it, the only pay
// button sat at the foot of the daemon-era dashboard, which a new account
// never sees (brnrd-self threads/inbox/payments.md, 2026-11-17).
//
// It learns the session the way the dashboard does: `GET /v1/dashboard/repos`
// answers 401 to an anonymous visitor and otherwise carries the GitHub login
// that AccountDeletion's confirmation needs. One read, both answers.
import { ReposAuthError, fetchRepos } from './repos.ts';

export const ACCOUNT_PATH = '/account';

export type AccountRead =
	| { kind: 'authed'; githubLogin: string | null }
	| { kind: 'anon' }
	| { kind: 'error'; message: string };

export async function readAccount(fetchImpl: typeof fetch = fetch): Promise<AccountRead> {
	try {
		const repos = await fetchRepos(fetchImpl);
		return { kind: 'authed', githubLogin: repos.account?.github_login ?? null };
	} catch (e) {
		if (e instanceof ReposAuthError) return { kind: 'anon' };
		return { kind: 'error', message: e instanceof Error ? e.message : 'account read failed' };
	}
}

/** The sign-in URL that comes back to `path` afterwards. `/login` hands
 * `next` to the backend, whose `_safe_next` keeps it on this site; this side
 * only ever builds it from a path of our own. */
export function loginFor(path: string): string {
	return `/login?next=${encodeURIComponent(path)}`;
}
