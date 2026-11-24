import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ACCOUNT_PATH, githubReturn, loginFor, readAccount } from './accountPage.ts';
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
	assert.deepEqual(read, {
		kind: 'authed',
		githubLogin: 'octo',
		messengerDoors: [],
		github: { notice: null, repos: [], installUrl: '' }
	});
});

test('the GitHub App return is read here: its notice and the repos the App reaches', async () => {
	let asked = '';
	const fetchImpl = (async (url: string) => {
		asked = String(url);
		return {
			ok: true,
			status: 200,
			json: async () => ({
				account: { github_login: 'octo' },
				notice: 'GitHub installations synced.',
				installed_repos: [{ repo_full_name: 'octo/app' }],
				install_url: 'https://github.com/apps/brnrd-dev/installations/new'
			})
		};
	}) as unknown as typeof fetch;
	const params = githubReturn('?installation_id=42&notice=github-synced');
	const read = await readAccount(fetchImpl, params);
	assert.equal(asked, '/v1/dashboard/repos?notice=github-synced&installation_id=42&view=account');
	assert.ok(read.kind === 'authed');
	assert.equal(read.github.notice, 'GitHub installations synced.');
	assert.deepEqual(read.github.repos, ['octo/app']);
	// A plain visit shows no stale notice.
	const plain = await readAccount(fetchImpl, githubReturn(''));
	assert.ok(plain.kind === 'authed' && plain.github.notice === null);
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
