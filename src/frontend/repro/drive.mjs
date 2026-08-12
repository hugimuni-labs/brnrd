// Requirement 4's "drive the rendered result": 390x844, scroll hard past the
// dock threshold and back, screenshot before/after. Reuses the same fixture
// mock as repro.mjs/repro2.mjs so it needs no backend/account.

import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import * as fixtures from './fixtures.mjs';

const PORT = 5185;
const OUT = process.env.REPRO_OUT ?? '/tmp/sticky-drive';

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
		const context = await browser.newContext({
			viewport: { width: 390, height: 844 },
			deviceScaleFactor: 3,
			isMobile: true,
			hasTouch: true,
			userAgent:
				'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1'
		});
		const page = await context.newPage();
		await page.route('**/v1/dashboard/**', async (route) => {
			const url = new URL(route.request().url());
			const body = fixtures.ROUTES[url.pathname];
			if (body) {
				await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
			} else {
				await route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
			}
		});

		await page.goto(`http://localhost:${PORT}/`, { waitUntil: 'networkidle' });
		await page.waitForSelector('text=Item number 1', { timeout: 15000 });
		await delay(400);
		await page.screenshot({ path: `${OUT}/1-before-rest.png` });

		// Scroll hard past the dock threshold with the same eased-fling shape
		// the regression repro uses, then screenshot mid-flight and settled.
		await page.evaluate(async () => {
			const duration = 1600;
			const target = 4200;
			const start = performance.now();
			await new Promise((resolve) => {
				function step(now) {
					const elapsed = now - start;
					const t = Math.min(1, elapsed / duration);
					const eased = 1 - Math.pow(1 - t, 3);
					window.scrollTo(0, Math.round(eased * target));
					if (t < 1) requestAnimationFrame(step);
					else resolve();
				}
				requestAnimationFrame(step);
			});
		});
		await page.screenshot({ path: `${OUT}/2-during-scroll-past-dock.png` });
		await delay(900);
		await page.screenshot({ path: `${OUT}/3-after-settled-docked.png` });

		// And back to the top.
		await page.evaluate(async () => {
			const duration = 1200;
			const start = performance.now();
			const from = window.scrollY;
			await new Promise((resolve) => {
				function step(now) {
					const elapsed = now - start;
					const t = Math.min(1, elapsed / duration);
					const eased = 1 - Math.pow(1 - t, 3);
					window.scrollTo(0, Math.round(from * (1 - eased)));
					if (t < 1) requestAnimationFrame(step);
					else resolve();
				}
				requestAnimationFrame(step);
			});
		});
		await delay(400);
		await page.screenshot({ path: `${OUT}/4-after-scrolled-back-to-top.png` });

		console.log('drive screenshots written to', OUT);
		await browser.close();
	} finally {
		vite.kill();
	}
}

main().catch((err) => {
	console.error(err);
	process.exit(1);
});
