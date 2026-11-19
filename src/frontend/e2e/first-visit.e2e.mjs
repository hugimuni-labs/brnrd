import { test } from 'node:test';
import assert from 'node:assert/strict';
import { open } from './harness.mjs';
import { fixture } from './fixture.mjs';

const site = { deployment: 'cloud', tree: null, features: ['account', 'consent', 'dashboard'] };
const shots = process.env.BRNRD_E2E_SHOTS;

test('a new account sees setup and moves to its hosted board without a reload', async () => {
	for (const [name, viewport] of [
		['desktop', { width: 1440, height: 900 }],
		['phone', { width: 390, height: 844 }]
	]) {
		const B = await open({ parts: {}, site, ready: '[data-home]', viewport });
		try {
			let state = 'none';
			let failed = false;
			await B.page.route('**/v1/dashboard/repos', (r) =>
				r.fulfill({ json: { connected_repos: [], machines: { paired: false } } })
			);
			await B.page.route('**/v1/loom/consent', (r) =>
				r.fulfill({ status: failed ? 500 : 200, json: { state } })
			);
			await B.page.addInitScript(() => {
				Object.defineProperty(navigator, 'clipboard', { value: undefined });
			});
			await B.page.reload();
			const setup = B.page.getByTestId('loom-empty');
			await setup.waitFor();
			await B.page.locator('.boot-glitch').waitFor({ state: 'hidden' });
			assert.match(await setup.locator('pre').innerText(), /brnrd setup channel --kind relay/);
			assert.equal(await setup.getByRole('link', { name: 'Sign out' }).count(), 1);
			if (shots) await B.page.screenshot({ path: `${shots}/first-page-${name}.png` });
			await setup.getByRole('button', { name: 'Copy commands' }).click();
			await setup.getByText('Could not copy.', { exact: false }).waitFor();
			failed = true;
			await setup.getByRole('button', { name: 'Check again' }).click();
			await setup.getByText('Could not check for your computer.').waitFor();
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
