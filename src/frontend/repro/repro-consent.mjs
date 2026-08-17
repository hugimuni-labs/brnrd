// Visual repro for the in-place consent popover (WithheldNotice's new
// affordance): the "the cloth · past" run-ledger section withheld because
// the one connected repo never recorded a publish scope, the reader opening
// the popover from the panel itself, and the panel after enabling the lane
// in place. Reuses the same fixture-mock dance as drive.mjs/repro.mjs, with
// local overrides so the shared fixtures.mjs stays untouched for the other
// repro scripts — plus a route for `POST /v1/repos/*/publish-layers`, which
// no existing repro script needed until this one.

import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import * as fixtures from './fixtures.mjs';

const PORT = 5187;
const OUT = process.env.REPRO_OUT ?? '/tmp/consent-popover-repro';

const REPO_ID = fixtures.repos.connected_repos[0].id;

const repos = {
	...fixtures.repos,
	connected_repos: [{ ...fixtures.repos.connected_repos[0], publish_layers: null }]
};

const runLedger = {
	...fixtures.runLedger,
	withheld: {
		lane: 'run_ledger',
		unrecorded: [fixtures.repos.connected_repos[0].repo_full_name],
		unrecorded_ids: [REPO_ID]
	}
};

const ROUTES = {
	...fixtures.ROUTES,
	'/v1/dashboard/repos': repos,
	'/v1/dashboard/run-ledger': runLedger
};

async function waitForServer(url, tries = 60) {
	for (let i = 0; i < tries; i++) {
		try {
			const res = await fetch(url);
			if (res.ok || res.status === 404) return;
		} catch {
			/* not up yet */
		}
		await delay(500);
	}
	throw new Error(`dev server never came up at ${url}`);
}

async function main() {
	await import('node:fs/promises').then((fs) => fs.mkdir(OUT, { recursive: true }));
	const vite = spawn('npx', ['vite', 'dev', '--port', String(PORT), '--strictPort'], {
		stdio: ['ignore', 'pipe', 'pipe']
	});
	try {
		await waitForServer(`http://localhost:${PORT}/`);
		const browser = await chromium.launch();
		const context = await browser.newContext({ viewport: { width: 1180, height: 900 } });
		const page = await context.newPage();

		await page.route('**/v1/dashboard/**', async (route) => {
			const url = new URL(route.request().url());
			const body = ROUTES[url.pathname];
			if (body) {
				await route.fulfill({
					status: 200,
					contentType: 'application/json',
					body: JSON.stringify(body)
				});
			} else {
				await route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
			}
		});
		await page.route('**/v1/repos/*/publish-layers', async (route) => {
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({ ok: true, notice: 'Publish scope updated.' })
			});
		});

		await page.goto(`http://localhost:${PORT}/`, { waitUntil: 'networkidle' });
		await page.waitForSelector('text=what has become', { timeout: 15000 });
		// The boot-glitch mascot animates for a couple seconds before the shell
		// settles (layout.css's `.boot-glitch` sequence) — without this wait the
		// screenshot below catches the wordmark flicker instead of the panel.
		await delay(3000);
		await page.evaluate(() => {
			document.querySelector('#cloth-heading')?.scrollIntoView({ block: 'center' });
		});
		await delay(400);
		await page.screenshot({ path: `${OUT}/1-withheld-panel.png` });

		await page.getByRole('button', { name: 'Or fix it here.' }).click();
		await delay(250);
		await page.screenshot({ path: `${OUT}/2-dialog-open.png` });

		await page.getByRole('button', { name: 'enable here' }).click();
		await delay(250);
		await page.screenshot({ path: `${OUT}/3-dialog-after-enable.png` });

		console.log('consent popover repro screenshots written to', OUT);
		await browser.close();
	} finally {
		vite.kill();
	}
}

main().catch((err) => {
	console.error(err);
	process.exit(1);
});
