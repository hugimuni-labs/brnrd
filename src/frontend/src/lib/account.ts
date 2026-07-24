// Account-deletion surface (Art 17): the SPA leg of
// `POST /v1/accounts/delete` (`src/brnrd/routers/accounts.py`,
// `src/brnrd/account_deletion.py`). Same session-cookie seam as billing.ts
// — `credentials: 'include'`, `require_account_or_session` server-side.
//
// The confirmation step lives entirely in the request body: the caller
// re-types the account's own GitHub login and the server matches it
// exactly (case-sensitive) before touching a single row. There is no
// separate "are you sure" token round-trip — the typed match *is* the
// confirmation, same convention GitHub itself uses for destructive
// settings.

export interface RetainedStore {
	store: string;
	reason: string;
}

export interface AccountDeletionReceipt {
	ok: boolean;
	deleted_at: string;
	stripe_subscription_canceled: boolean;
	retained: RetainedStore[];
}

export class AccountActionError extends Error {}

/** Re-types `confirmLogin` against the signed-in account's own GitHub
 * login; the server 400s on a mismatch without deleting anything. */
export async function deleteAccount(
	confirmLogin: string,
	fetchImpl: typeof fetch = fetch
): Promise<AccountDeletionReceipt> {
	const res = await fetchImpl('/v1/accounts/delete', {
		method: 'POST',
		credentials: 'include',
		headers: { 'content-type': 'application/json' },
		body: JSON.stringify({ confirm_login: confirmLogin })
	});
	if (res.status === 401) throw new AccountActionError('not signed in');
	if (!res.ok) {
		let detail = '';
		try {
			const payload = (await res.json()) as { detail?: unknown };
			if (typeof payload.detail === 'string') detail = payload.detail;
		} catch {
			// non-JSON error body — fall through to the status line
		}
		throw new AccountActionError(detail || `account deletion failed: ${res.status}`);
	}
	return (await res.json()) as AccountDeletionReceipt;
}
