// Mobile proof for garage sketch C. Runs the live route at 390x844 with a
// dispatcher + strand, captures the one-line sticky gauge and its flow drawer.
// Usage: node repro/walk-garage-c.mjs [--out DIR] [--port N]

import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import { setTimeout as delay } from 'node:timers/promises';
import * as fixtures from './fixtures.mjs';

const args = process.argv.slice(2);
const value = (flag) => {
	const index = args.indexOf(flag);
	return index < 0 ? null : args[index + 1];
};
const OUT = value('--out') ?? '/tmp/garage-c-mobile';
const PORT = Number(value('--port') ?? 5193);
const started = new Date(Date.now() - 6_000).toISOString();

const routes = structuredClone(fixtures.ROUTES);
routes['/v1/dashboard/runners'].profiles.push(
	{ name: 'claude-opus', shell: 'claude', model: 'opus', class: 'strong', available: true },
	{ name: 'claude-haiku', shell: 'claude', model: 'haiku', class: 'economy', available: true },
	{ name: 'codex', shell: 'codex', model: 'default', class: 'balanced', available: true },
	{ name: 'codex-full', shell: 'codex', model: 'gpt-5.6-sol', class: 'strong', available: true }
);
routes['/v1/dashboard/runners'].default = 'claude-opus';
routes['/v1/dashboard/quota'].runner_quotas.push({
	shell: 'codex',
	status: 'known',
	windows: [
		{ label: 'session', used: 13, limit: 100, percent: 87, reset: 'resets 21:00' },
		{ label: 'week', used: 42, limit: 100, percent: 58, reset: 'resets Friday' }
	]
});
routes['/v1/dashboard/live-runs'].runs = [
	{
		id: 'run-parent',
		kind: 'daemon',
		stream: 'garage',
		label: '',
		name: 'the-garage-and-the-bench',
		run_id: 'run-parent',
		repo_label: 'hugimuni-labs/brnrd',
		started_at: started,
		last_seen: new Date().toISOString(),
		parent_run_id: null,
		is_subspawn: false,
		runner: { name: 'claude-opus', shell: 'claude', core: 'opus' },
		phase: 'running',
		card_text: null,
		card_updated_at: null,
		daemon_stale: false
	},
	{
		id: 'run-hand',
		kind: 'daemon',
		stream: 'garage',
		label: '',
		name: 'header detail pass',
		run_id: 'run-hand',
		repo_label: 'hugimuni-labs/brnrd',
		started_at: started,
		last_seen: new Date().toISOString(),
		parent_run_id: 'run-parent',
		is_subspawn: true,
		runner: { name: 'claude-haiku', shell: 'claude', core: 'haiku' },
		phase: 'running',
		card_text: null,
		card_updated_at: null,
		daemon_stale: false
	}
];

async function waitForServer(url) {
	for (let i = 0; i < 60; i++) {
		try {
			if ((await fetch(url)).status < 500) return;
		} catch {
			/* starting */
		}
		await delay(500);
	}
	throw new Error(`dev server never came up at ${url}`);
}

async function main() {
	await mkdir(OUT, { recursive: true });
	const vite = spawn('npx', ['vite', 'dev', '--port', String(PORT), '--strictPort'], {
		stdio: ['ignore', 'pipe', 'pipe']
	});
	let log = '';
	vite.stdout.on('data', (data) => (log += data));
	vite.stderr.on('data', (data) => (log += data));
	try {
		await waitForServer(`http://localhost:${PORT}/garage/c`);
		const browser = await chromium.launch();
		const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
		await page.route('**/v1/dashboard/**', async (route) => {
			const body = routes[new URL(route.request().url()).pathname];
			await route.fulfill({
				status: body ? 200 : 404,
				contentType: 'application/json',
				body: JSON.stringify(body ?? {})
			});
		});
		await page.goto(`http://localhost:${PORT}/garage/c`, { waitUntil: 'networkidle' });
		await page.waitForSelector('text=the-garage-and-the-bench');
		await delay(1_500);
		await page.evaluate(() => window.scrollTo(0, 650));
		await delay(300);
		await page.screenshot({ path: `${OUT}/01-sticky-closed.png` });
		await page.getByRole('button', { name: 'toggle garage drawer' }).click();
		await delay(300);
		await page.screenshot({ path: `${OUT}/02-drawer-open.png`, fullPage: true });
		const evidence = await page.evaluate(() => ({
			viewport: [innerWidth, innerHeight],
			gaugePosition: getComputedStyle(document.querySelector('.gauge')).position,
			nextText: document.querySelector('.next')?.textContent?.replace(/\s+/g, ' ').trim(),
			handText: document.querySelector('.hand')?.textContent?.trim()
		}));
		await writeFile(`${OUT}/evidence.json`, JSON.stringify(evidence, null, 2));
		console.log(JSON.stringify(evidence));
		await browser.close();
	} finally {
		vite.kill();
		await writeFile(`${OUT}/vite.log`, log);
	}
}

main().catch((error) => {
	console.error(error);
	process.exit(1);
});
