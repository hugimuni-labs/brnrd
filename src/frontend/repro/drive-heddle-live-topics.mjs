// Captures the warp's heddle rail on either side of a live `.topics` claim.
// Usage: node repro/drive-heddle-live-topics.mjs [--out DIR] [--port N]
import { spawn } from 'node:child_process';
import { mkdir } from 'node:fs/promises';
import { chromium } from 'playwright';
import { setTimeout as delay } from 'node:timers/promises';
import * as fixtures from './fixtures.mjs';
import { closeBrowser, exerciseRecorder, runDriver, stopVite } from './finish.mjs';

const args = process.argv.slice(2);
const arg = (flag) => {
	const index = args.indexOf(flag);
	return index === -1 || index + 1 >= args.length ? null : args[index + 1];
};
const OUT = arg('--out') ?? '/tmp/heddle-live-topics';
const PORT = Number(arg('--port') ?? 5203);

async function waitForServer(url, tries = 90) {
	for (let attempt = 0; attempt < tries; attempt += 1) {
		try {
			const response = await fetch(url);
			if (response.ok || response.status === 404) return;
		} catch {
			// Vite has not bound its port yet.
		}
		await delay(500);
	}
	throw new Error(`dev server never came up at ${url}`);
}

function routesFor(topics) {
	return {
		...fixtures.ROUTES,
		'/v1/dashboard/live-runs': {
			...fixtures.liveRuns,
			runs: topics.length
				? [
						{
							id: 'presence-loom',
							run_id: 'run-loom',
							kind: 'daemon',
							stream: 'telegram:loom:',
							repo_label: 'hugimuni-labs/brnrd',
							topics,
							boundaries: [
								{
									at: new Date().toISOString(),
									phase: 'tool',
									act: 'read',
									tools: ['exec_command']
								}
							]
						}
					]
				: []
		}
	};
}

async function capture(page, routes, name) {
	await page.route('**/v1/dashboard/**', async (route) => {
		const body = routes[new URL(route.request().url()).pathname];
		await route.fulfill({
			status: body ? 200 : 404,
			contentType: 'application/json',
			body: JSON.stringify(body ?? {})
		});
	});
	await page.goto(`http://localhost:${PORT}/warp`, { waitUntil: 'networkidle' });
	const rail = page.locator('.subpanel', { hasText: 'heddles' });
	await rail.waitFor({ state: 'visible', timeout: 15_000 });
	await rail.locator('button').first().click();
	await delay(150);
	await rail.screenshot({ path: `${OUT}/${name}.png` });
	return {
		bolts: await page.locator('[aria-label="weaving now"]').count(),
		text: (await rail.innerText()).replace(/\s+/g, ' ').trim()
	};
}

async function main() {
	await mkdir(OUT, { recursive: true });
	const vite = spawn('npx', ['vite', 'dev', '--port', String(PORT), '--strictPort'], {
		stdio: ['ignore', 'pipe', 'pipe']
	});
	const modules = exerciseRecorder();
	let browser;
	try {
		await waitForServer(`http://localhost:${PORT}/`);
		browser = await chromium.launch();
		const context = await browser.newContext({
			viewport: { width: 390, height: 844 },
			deviceScaleFactor: 2
		});
		const beforePage = await modules.watch(await context.newPage(), 'drive-heddle-live-topics');
		const before = await capture(beforePage, routesFor([]), 'before-unclaimed');
		await beforePage.close();
		const afterPage = await modules.watch(await context.newPage(), 'drive-heddle-live-topics');
		const after = await capture(afterPage, routesFor(['mint']), 'after-mint-lit');
		await modules.harvest(afterPage, 'drive-heddle-live-topics');
		await context.close();
		await modules.save(OUT, 'drive-heddle-live-topics');
		console.log(JSON.stringify({ before, after }, null, 2));
	} finally {
		if (browser) await closeBrowser(browser, 'drive-heddle-live-topics');
		stopVite(vite, 'drive-heddle-live-topics');
	}
}

runDriver(main);
