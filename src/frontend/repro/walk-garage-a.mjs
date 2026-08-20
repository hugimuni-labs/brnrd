// Mobile acceptance walk for garage sketch A. Boots the real route, mocks
// the dashboard stores, adds one dispatcher + one hand, and captures the
// expanded and scrolled-past forms at the design's 390×844 phone viewport.
import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { mkdir } from 'node:fs/promises';
import { setTimeout as delay } from 'node:timers/promises';
import * as fixtures from './fixtures.mjs';

const OUT = process.argv[2] ?? '/tmp/garage-a';
const PORT = 5187;
const routes = fixtures.buildRoutes(fixtures.DEFAULT_SCALE);
const started = new Date(Date.now() - 6_000).toISOString();
routes['/v1/dashboard/live-runs'] = {
	...routes['/v1/dashboard/live-runs'],
	runs: [
		{
			id: 'garage-parent',
			kind: 'daemon',
			stream: 'garage',
			label: '',
			name: 'the-garage-and-the-bench',
			run_id: 'run-garage',
			repo_label: 'project-1',
			started_at: started,
			last_seen: new Date().toISOString(),
			parent_run_id: null,
			is_subspawn: false,
			runner: { name: 'claude-opus', shell: 'claude', core: 'opus' },
			phase: 'running',
			card_text: null,
			card_updated_at: null
		},
		{
			id: 'garage-hand',
			kind: 'daemon',
			stream: 'spawn:default',
			label: '',
			name: 'garage-lighting-pass',
			run_id: 'run-hand',
			repo_label: 'project-1',
			started_at: started,
			last_seen: new Date().toISOString(),
			parent_run_id: 'run-garage',
			is_subspawn: true,
			runner: { name: 'claude-opus', shell: 'claude', core: 'opus' },
			phase: 'running',
			card_text: null,
			card_updated_at: null
		}
	]
};

async function waitForServer(url) {
	for (let i = 0; i < 60; i += 1) {
		try {
			if ((await fetch(url)).status < 500) return;
		} catch {
			/* booting */
		}
		await delay(500);
	}
	throw new Error(`vite did not start at ${url}`);
}

await mkdir(OUT, { recursive: true });
const vite = spawn('npx', ['vite', 'dev', '--port', String(PORT), '--strictPort'], {
	stdio: 'inherit'
});
try {
	await waitForServer(`http://localhost:${PORT}/garage/a`);
	const browser = await chromium.launch();
	const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
	await page.route('**/v1/dashboard/**', async (route) => {
		const request = route.request();
		const path = new URL(request.url()).pathname;
		if (request.method() === 'POST' && path === '/v1/dashboard/runners/wake-request') {
			const body = request.postDataJSON();
			return route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({
					wake_request: {
						request_id: 'garage-pick',
						profile: body.profile,
						repo_label: body.repo_label,
						environment: body.environment,
						requested_at: new Date().toISOString(),
						status: 'pending'
					}
				})
			});
		}
		const body = routes[path];
		return body
			? route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
			: route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
	});
	await page.goto(`http://localhost:${PORT}/garage/a`, { waitUntil: 'networkidle' });
	await page.waitForSelector('[data-garage="a"] .shell-row');
	await delay(2_500); // the shared boot curtain completes before the receipt is meaningful
	await page.screenshot({ path: `${OUT}/garage-a-expanded-390x844.png` });
	await page.evaluate(() => window.scrollTo(0, 400));
	await page.waitForSelector('[data-garage="a"] .compact');
	await delay(250);
	await page.screenshot({ path: `${OUT}/garage-a-collapsed-390x844.png` });
	console.log(`${OUT}/garage-a-expanded-390x844.png`);
	console.log(`${OUT}/garage-a-collapsed-390x844.png`);
	await browser.close();
} finally {
	vite.kill('SIGTERM');
}
