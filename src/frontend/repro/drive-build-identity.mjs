// #1734 convergence drive: the build-identity line under the dashboard h1,
// at 390 and 1440, with /v1/stats/version mocked three ways —
// both fields, commit only, and absent (the honest-nothing case).
import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import * as fixtures from './fixtures.mjs';

const PORT = 5191;
const OUT = process.env.REPRO_OUT ?? '/tmp/build-identity-drive';

async function waitForServer(url, tries = 90) {
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

const CASES = [
	{
		name: 'both',
		body: {
			commit: 'da86b3c2f19a4b7c8e3d5061a2b9c4d7e8f01234',
			built_at: '2026-09-01T20:12:00+00:00',
			started_at: '2026-09-01T20:14:00+00:00'
		}
	},
	{
		name: 'commit-only',
		body: { commit: 'da86b3c2f19a4b7c8e3d5061a2b9c4d7e8f01234', built_at: null, started_at: null }
	},
	{ name: 'absent', body: { commit: null, built_at: null, started_at: null } }
];

async function main() {
	const fs = await import('node:fs/promises');
	await fs.mkdir(OUT, { recursive: true });
	const vite = spawn('npx', ['vite', 'dev', '--port', String(PORT), '--strictPort'], {
		stdio: ['ignore', 'pipe', 'pipe'],
		cwd: new URL('..', import.meta.url).pathname
	});
	vite.stderr.on('data', (d) => process.stderr.write(String(d)));
	try {
		await waitForServer(`http://localhost:${PORT}/`);
		const browser = await chromium.launch();
		for (const vp of [
			{ w: 390, h: 844, tag: '390' },
			{ w: 1440, h: 900, tag: '1440' }
		]) {
			for (const c of CASES) {
				const context = await browser.newContext({ viewport: { width: vp.w, height: vp.h } });
				const page = await context.newPage();
				await page.route('**/v1/dashboard/**', async (route) => {
					const url = new URL(route.request().url());
					const body = fixtures.ROUTES[url.pathname];
					await route.fulfill({
						status: body ? 200 : 404,
						contentType: 'application/json',
						body: JSON.stringify(body ?? {})
					});
				});
				await page.route('**/v1/stats/version', async (route) => {
					await route.fulfill({
						status: 200,
						contentType: 'application/json',
						body: JSON.stringify(c.body)
					});
				});
				await page.goto(`http://localhost:${PORT}/`, { waitUntil: 'networkidle' });
				await delay(2500);
				const header = await page.locator('header').first();
				const txt = await header.innerText().catch(() => '(no header)');
				const box = await header.boundingBox().catch(() => null);
				const overflow = await page.evaluate(
					() => document.documentElement.scrollWidth > window.innerWidth
				);
				console.log(
					`[${vp.tag} · ${c.name}] header height=${box ? Math.round(box.height) : '?'} hOverflow=${overflow}`
				);
				console.log(`   header text: ${JSON.stringify(txt).slice(0, 300)}`);
				await page.screenshot({
					path: `${OUT}/${vp.tag}-${c.name}.png`,
					clip: box
						? {
								x: 0,
								y: Math.max(0, box.y - 8),
								width: vp.w,
								height: Math.min(vp.h, box.height + 40)
							}
						: undefined
				});
				await context.close();
			}
		}
		await browser.close();
		console.log(`shots in ${OUT}`);
	} finally {
		vite.kill('SIGTERM');
	}
}
main().catch((e) => {
	console.error(e);
	process.exit(1);
});
