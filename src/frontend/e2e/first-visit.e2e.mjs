import { test } from 'node:test';
import assert from 'node:assert/strict';
import { open } from './harness.mjs';
import { fixture } from './fixture.mjs';

const site = { deployment: 'cloud', tree: null, features: ['account', 'consent', 'dashboard'] };
const shots = process.env.BRNRD_E2E_SHOTS;

test('a new account sees setup and moves to its hosted board when the computer publishes', async () => {
	for (const [name, viewport] of [
		['desktop', { width: 1440, height: 900 }],
		['phone', { width: 390, height: 844 }]
	]) {
		const B = await open({
			parts: {},
			consent: { state: 'none' },
			site,
			ready: '#setup',
			viewport
		});
		try {
			let state = 'none';
			let failed = false;
			await B.page.route('**/v1/dashboard/repos', (r) =>
				r.fulfill({
					json: {
						account: { github_login: 'ada' },
						connected_repos: [],
						machines: { paired: false }
					}
				})
			);
			await B.page.route('**/v1/accounts/home-trial', (r) =>
				r.fulfill({ json: { status: 'not_started', enforcement_enabled: false } })
			);
			await B.page.route('**/v1/accounts/subscription', (r) =>
				r.fulfill({ json: { tier: 'free', status: null } })
			);
			await B.page.route('**/v1/loom/consent', (r) =>
				r.fulfill({ status: failed ? 500 : 200, json: { state } })
			);
			await B.page.addInitScript(() => {
				Object.defineProperty(navigator, 'clipboard', { value: undefined });
			});
			await B.page.reload();
			const setup = B.page.locator('#setup');
			assert.equal(new URL(B.page.url()).pathname, '/account');
			await setup.waitFor();
			await B.page.locator('.boot-glitch').waitFor({ state: 'hidden' });
			assert.match(await setup.locator('pre').innerText(), /brnrd setup channel --kind relay/);
			await B.page.getByText('@ada', { exact: true }).waitFor();
			await B.page.getByRole('link', { name: 'Switch GitHub account' }).waitFor();
			assert.equal(await B.page.getByRole('link', { name: 'Sign out' }).count(), 1);
			if (shots) await B.page.screenshot({ path: `${shots}/account-setup-${name}.png` });
			await setup.getByRole('button', { name: 'Copy commands' }).click();
			await setup.getByText('Could not copy.', { exact: false }).waitFor();
			failed = true;
			await B.page.getByRole('button', { name: 'Check again' }).click();
			await B.page.getByText('Could not check for your computer.').waitFor();
			failed = false;
			state = 'granted';
			await B.page.locator('[data-consent="waiting"]').waitFor({ timeout: 8000 });
			B.loom.set(fixture());
			await B.page.waitForSelector('.brd .column.threadcol', { timeout: 8000 });
			assert.deepEqual(B.errors, []);
		} finally {
			await B.close();
		}
	}
});

test('trial, household, and subscribed accounts see their access on the board', async () => {
	const B = await open({ site });
	try {
		for (const [state, text] of [
			[{}, 'Home trial · 9 days left'],
			[{ household: true }, 'Household access'],
			[{ subscribed: true }, 'Subscribed']
		]) {
			await B.page.route('**/v1/accounts/home-trial', (r) =>
				r.fulfill({
					json: {
						status: 'active',
						enforcement_enabled: true,
						ends_at: new Date(Date.now() + 8.5 * 86400000).toISOString(),
						...state
					}
				})
			);
			await B.page.reload();
			await B.page.getByText(text, { exact: true }).waitFor();
		}
		assert.deepEqual(B.errors, []);
	} finally {
		await B.close();
	}
});

test('a 402 on a previously loaded board shows the next step', async () => {
	const B = await open({ site, consent: { state: 'none' } });
	try {
		await B.page.route('**/v1/loom/index', (r) =>
			r.fulfill({
				status: 402,
				json: {
					detail: { reason: 'home_subscription_required' }
				}
			})
		);
		await B.page.reload();
		await B.page.getByRole('heading', { name: 'Home trial ended' }).waitFor();
		assert.equal(await B.page.locator('.column.threadcol').count(), 0);
		await B.page.getByRole('link', { name: 'Subscribe' }).click();
		await B.page.waitForURL('**/account#subscription');
		await B.page.getByRole('heading', { name: 'Home trial ended' }).waitFor();
		assert.equal(new URL(B.page.url()).pathname, '/account');
		assert.deepEqual(B.errors, []);
	} finally {
		await B.close();
	}
});

test('the public sign-in goes through the GitHub explanation first', async () => {
	const B = await open({ site });
	try {
		await B.page.route('**/v1/loom/index', (r) => r.fulfill({ status: 401, json: {} }));
		await B.page.route('**/v1/dashboard/login-context*', (r) =>
			r.fulfill({
				json: {
					authenticated: false,
					oauth_ready: true,
					signin_url: '/auth/github/start?next=/',
					next: '/'
				}
			})
		);
		await B.page.reload();
		const signIn = B.page
			.locator('[data-landing]')
			.getByRole('link', { name: 'sign in', exact: true })
			.first();
		await signIn.click();
		await B.page.waitForURL('**/login');
		const panel = B.page.getByRole('region', { name: 'GitHub sign in' });
		await panel.getByText(/hugimuni-labs on GitHub/).waitFor();
		await panel.getByText(/install the GitHub App separately/).waitFor();
		assert.equal(
			await panel.getByRole('link', { name: 'Sign in with GitHub' }).getAttribute('href'),
			'/auth/github/start?next=/'
		);
		assert.deepEqual(B.errors, []);
	} finally {
		await B.close();
	}
});
