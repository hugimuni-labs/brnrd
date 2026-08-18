// Repro harness for the origin-aware ColdStart CTA (2026-08-17): boots the
// real vite dev server, mocks /v1/dashboard/* with a cold-start fixture (zero
// connected repos, zero paired machines — the exact shape that triggers the
// `cold` block), and screenshots the dashboard at a mobile width (390, touch,
// coarse pointer) and a desktop width (1180, mouse) so the two CTA branches
// can be judged side by side against their surroundings.
//
// Usage: node repro/repro-origin-aware.mjs

import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import * as fixtures from './fixtures.mjs';

const PORT = 5187;
const OUT = process.env.REPRO_OUT ?? '/tmp/origin-aware-cta';

const coldRepos = {
	...fixtures.repos,
	connected_repos: [],
	connected_count: 0,
	installations: [],
	installed_repos: [],
	pairing_command: 'cd <repo>\nbrnrd',
	machines: { paired: false, any_enabled_repo: false }
};

const ROUTES = { ...fixtures.ROUTES, '/v1/dashboard/repos': coldRepos };

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

async function shootColdStart(browser, contextOpts, label) {
	const context = await browser.newContext(contextOpts);
	const page = await context.newPage();
	const consoleErrors = [];
	page.on('console', (msg) => {
		if (msg.type() === 'error') consoleErrors.push(msg.text());
	});
	page.on('pageerror', (err) => consoleErrors.push(String(err)));
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
	await page.goto(`http://localhost:${PORT}/`, { waitUntil: 'networkidle' });
	await page.waitForSelector('text=nothing is paired yet', { timeout: 15000 });
	await delay(500); // ignite transition + the mobile detector's $effect tick
	await page.screenshot({ path: `${OUT}/${label}.png`, fullPage: true });
	console.log(label, 'console errors:', consoleErrors);
	await context.close();
}

async function main() {
	await import('node:fs/promises').then((fs) => fs.mkdir(OUT, { recursive: true }));
	const vite = spawn('npx', ['vite', 'dev', '--port', String(PORT), '--strictPort'], {
		stdio: ['ignore', 'pipe', 'pipe']
	});
	try {
		await waitForServer(`http://localhost:${PORT}/`);
		const browser = await chromium.launch();

		await shootColdStart(
			browser,
			{
				viewport: { width: 390, height: 844 },
				deviceScaleFactor: 3,
				isMobile: true,
				hasTouch: true,
				userAgent:
					'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1'
			},
			'mobile-390'
		);

		await shootColdStart(
			browser,
			{
				viewport: { width: 1180, height: 900 },
				deviceScaleFactor: 1,
				isMobile: false,
				hasTouch: false
			},
			'desktop-1180'
		);

		await browser.close();
		console.log('screenshots written to', OUT);
	} finally {
		vite.kill();
	}
}

main().catch((err) => {
	console.error(err);
	process.exit(1);
});
