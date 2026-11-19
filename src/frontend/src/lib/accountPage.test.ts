import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ACCOUNT_PATH, loginFor, readAccount } from './accountPage.ts';
import { cloudOnly } from './site.ts';

function reposFetch(status: number, body: unknown): typeof fetch {
	return (async (url: string) => {
		assert.ok(String(url).startsWith('/v1/dashboard/repos'), `fetched ${url}`);
		return {
			ok: status >= 200 && status < 300,
			status,
			json: async () => body
		};
	}) as unknown as typeof fetch;
}

test('a signed-in read carries the GitHub login the deletion confirm needs', async () => {
	const read = await readAccount(
		reposFetch(200, { account: { id: 1, github_login: 'octo' }, connected_repos: [] })
	);
	assert.deepEqual(read, { kind: 'authed', githubLogin: 'octo' });
});

test('a 401 reads as anonymous, so the page sends the visitor to sign in', async () => {
	assert.deepEqual(await readAccount(reposFetch(401, {})), { kind: 'anon' });
});

test('any other failure is an error the page shows, not a sign-in loop', async () => {
	const read = await readAccount(reposFetch(500, {}));
	assert.equal(read.kind, 'error');
});

test('the sign-in comes back to the account page', () => {
	const url = new URL(loginFor(ACCOUNT_PATH), 'https://brnrd.dev');
	assert.equal(url.pathname, '/login');
	assert.equal(url.searchParams.get('next'), '/account');
});

test('a local loom does not offer the account page', () => {
	assert.ok(cloudOnly(ACCOUNT_PATH));
});
