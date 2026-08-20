import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { mkdir } from 'node:fs/promises';
import { setTimeout as delay } from 'node:timers/promises';
import * as fixtures from './fixtures.mjs';

const PORT = 5186;
const OUT = process.env.REPRO_OUT ?? '/tmp/garage-b-mobile';

async function waitForServer(url) {
	for (let i = 0; i < 60; i++) {
		try {
			if ((await fetch(url)).status < 500) return;
		} catch {
			/* booting */
		}
		await delay(500);
	}
	throw new Error('vite did not start');
}

const routes = structuredClone(fixtures.ROUTES);
routes['/v1/dashboard/runners'] = {
	...fixtures.runners,
	default: 'claude-opus',
	profiles: [
		{ name: 'claude-opus', shell: 'claude', model: 'opus', selected: true, available: true },
		{ name: 'claude-sonnet', shell: 'claude', model: 'sonnet', available: true },
		{ name: 'codex-sol', shell: 'codex', model: 'gpt-5.6-sol', available: true },
		{ name: 'codex-default', shell: 'codex', model: 'default', available: true }
	]
};
routes['/v1/dashboard/quota'] = {
	generated_at: new Date().toISOString(),
	runner_quotas: [
		{
			shell: 'claude',
			status: 'known',
			windows: [
				{ label: 'session', percent: 72 },
				{ label: 'week', percent: 29 }
			]
		},
		{
			shell: 'codex',
			status: 'known',
			windows: [
				{ label: 'session', percent: 44 },
				{ label: 'week', percent: 81 }
			]
		}
	]
};
routes['/v1/dashboard/live-runs'] = {
	...fixtures.liveRuns,
	runs: [
		{
			id: 'root',
			kind: 'run',
			stream: '',
			label: '',
			name: 'the-garage-and-the-bench',
			run_id: 'run-root',
			repo_label: 'hugimuni-labs/brnrd',
			started_at: new Date(Date.now() - 65_000).toISOString(),
			last_seen: new Date().toISOString(),
			parent_run_id: null,
			is_subspawn: false,
			runner: { name: 'claude-opus', shell: 'claude', core: 'opus' },
			phase: 'running',
			card_text: null,
			card_updated_at: null
		},
		{
			id: 'hand',
			kind: 'run',
			stream: '',
			label: '',
			name: 'garage sketch b',
			run_id: 'run-hand',
			repo_label: 'hugimuni-labs/brnrd',
			started_at: new Date(Date.now() - 30_000).toISOString(),
			last_seen: new Date().toISOString(),
			parent_run_id: 'run-root',
			is_subspawn: true,
			runner: { name: 'codex-sol', shell: 'codex', core: 'gpt-5.6-sol' },
			phase: 'running',
			card_text: null,
			card_updated_at: null
		}
	]
};

await mkdir(OUT, { recursive: true });
const vite = spawn('npx', ['vite', 'dev', '--port', String(PORT), '--strictPort'], {
	stdio: 'ignore'
});
try {
	await waitForServer(`http://localhost:${PORT}/garage/b`);
	const browser = await chromium.launch();
	const page = await browser.newPage({
		viewport: { width: 390, height: 844 },
		deviceScaleFactor: 3,
		isMobile: true,
		hasTouch: true
	});
	await page.route('**/v1/dashboard/**', async (route) => {
		const url = new URL(route.request().url());
		if (route.request().method() === 'POST')
			return route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({
					wake_request: {
						request_id: 'picked',
						profile: 'codex-sol',
						repo_label: null,
						environment: null,
						requested_at: new Date().toISOString(),
						status: 'pending'
					}
				})
			});
		const body = routes[url.pathname];
		return body
			? route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
			: route.fulfill({ status: 404, body: '{}' });
	});
	await page.goto(`http://localhost:${PORT}/garage/b`, { waitUntil: 'networkidle' });
	await page.waitForSelector('[aria-label="runner garage"]');
	await delay(2_000);
	await page.screenshot({ path: `${OUT}/garage-b-expanded.png`, fullPage: false });
	await page.evaluate(() => window.scrollTo(0, 900));
	await delay(350);
	await page.screenshot({ path: `${OUT}/garage-b-collapsed.png`, fullPage: false });
	console.log(`${OUT}/garage-b-expanded.png\n${OUT}/garage-b-collapsed.png`);
	await browser.close();
} finally {
	vite.kill();
}
