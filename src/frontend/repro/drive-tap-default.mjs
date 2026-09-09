// Visual receipt for tap = account default: the same browser session captures
// the rack before the tap and after the next mirror poll moves DEFAULT.
import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import * as fixtures from './fixtures.mjs';

const PORT = 5199;
const ROOT = `http://localhost:${PORT}`;
const routes = structuredClone(fixtures.ROUTES);
routes['/v1/dashboard/quota'].runner_quotas.push({
	shell: 'codex',
	status: 'known',
	windows: [{ label: 'week', used: 12, limit: 100, percent: 88, reset: 'resets Sep 15' }]
});
routes['/v1/dashboard/runners'].profiles.push({
	name: 'codex',
	shell: 'codex',
	model: 'default',
	class: 'balanced',
	cost_rank: 25,
	availability: 'available',
	available: true
});

async function waitForServer() {
	for (let i = 0; i < 90; i++) {
		try {
			if ((await fetch(ROOT)).ok) return;
		} catch {
			// Server is still starting.
		}
		await delay(500);
	}
	throw new Error('vite did not start');
}

const vite = spawn('npx', ['vite', 'dev', '--port', String(PORT), '--strictPort'], {
	stdio: ['ignore', 'pipe', 'pipe']
});
vite.stdout.on('data', () => {});
vite.stderr.on('data', () => {});

try {
	await waitForServer();
	const browser = await chromium.launch();
	const page = await browser.newPage({
		viewport: { width: 1280, height: 900 },
		deviceScaleFactor: 2
	});
	await page.route('**/v1/dashboard/**', async (route) => {
		const request = route.request();
		const path = new URL(request.url()).pathname;
		if (request.method() === 'POST' && path === '/v1/dashboard/runners/wake-request') {
			const profile = request.postDataJSON().profile;
			routes['/v1/dashboard/runners'].default = profile;
			routes['/v1/dashboard/runners'].profiles = routes['/v1/dashboard/runners'].profiles.map(
				(row) => ({ ...row, selected: row.name === profile })
			);
			routes['/v1/dashboard/config'].config[0].value = profile;
			return route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({
					wake_request: { request_id: 'wake_tap', profile, status: 'pending' }
				})
			});
		}
		const body = routes[path];
		return route.fulfill({
			status: body ? 200 : 404,
			contentType: 'application/json',
			body: JSON.stringify(body ?? {})
		});
	});
	await page.goto(ROOT, { waitUntil: 'networkidle' });
	await page.locator('.fuel-provider-row').filter({ hasText: 'codex' }).click();
	await page.waitForSelector('[data-measure="spool-rack"]');
	await page.locator('[data-measure="spool-rack"]').screenshot({
		path: 'repro/shots/tap-default-before.png'
	});
	await page.locator('[data-role="rack-row-tap"]').filter({ hasText: 'codex' }).click();
	await delay(2600);
	await page.locator('[data-measure="spool-rack"]').screenshot({
		path: 'repro/shots/tap-default-after.png'
	});
	await page.locator('[data-role="bench-handle"]').click();
	await page.locator('[data-measure="settings"]').screenshot({
		path: 'repro/shots/daemon-settings-after.png'
	});
	await browser.close();
} finally {
	vite.kill('SIGTERM');
}
