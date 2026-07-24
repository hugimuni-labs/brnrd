import assert from 'node:assert/strict';
import test from 'node:test';

import { AccountActionError, deleteAccount } from './account.ts';

function fakeFetch(status: number, body: unknown): typeof fetch {
	const calls: { url: string; init?: RequestInit }[] = [];
	const impl = (async (url: string, init?: RequestInit) => {
		calls.push({ url, init });
		return {
			ok: status >= 200 && status < 300,
			status,
			json: async () => body
		} as Response;
	}) as unknown as typeof fetch;
	(impl as unknown as { calls: typeof calls }).calls = calls;
	return impl;
}

test('deleteAccount posts the confirmation phrase with the session cookie', async () => {
	const impl = fakeFetch(200, {
		ok: true,
		deleted_at: '2026-07-25T00:00:00Z',
		stripe_subscription_canceled: true,
		retained: [{ store: 'billing_ledger', reason: 'kept for accounting' }]
	});
	const receipt = await deleteAccount('octocat', impl);
	assert.equal(receipt.stripe_subscription_canceled, true);
	assert.deepEqual(receipt.retained, [{ store: 'billing_ledger', reason: 'kept for accounting' }]);
	const calls = (impl as unknown as { calls: { url: string; init?: RequestInit }[] }).calls;
	assert.equal(calls[0].url, '/v1/accounts/delete');
	assert.equal(calls[0].init?.method, 'POST');
	assert.equal(calls[0].init?.credentials, 'include');
	assert.deepEqual(JSON.parse(String(calls[0].init?.body)), { confirm_login: 'octocat' });
});

test('deleteAccount raises the auth error on 401', async () => {
	await assert.rejects(
		() => deleteAccount('octocat', fakeFetch(401, { detail: 'unauthenticated' })),
		AccountActionError
	);
});

test('deleteAccount surfaces the server detail on a confirmation mismatch', async () => {
	await assert.rejects(
		() =>
			deleteAccount(
				'wrong',
				fakeFetch(400, { detail: "confirmation phrase did not match this account's GitHub login" })
			),
		/did not match/
	);
});

test('deleteAccount falls back to the status line when the error body is not JSON', async () => {
	const impl = (async () =>
		({
			ok: false,
			status: 502,
			json: async () => {
				throw new Error('not json');
			}
		}) as unknown as Response) as unknown as typeof fetch;
	await assert.rejects(() => deleteAccount('octocat', impl), /502/);
});
