// Repro harness for "the sticky top row still glitches under scroll after
// #1331". Boots the real vite dev server, mocks every /v1/dashboard/*
// endpoint with repro/fixtures.mjs so THE STACK (stickyStack.ts +
// +page.svelte) renders with a long warp list under it, then drives a
// momentum-style touch scroll at 390x844 and screenshots/probes throughout.
//
// Usage: node repro/repro.mjs [chromium|webkit]

import { chromium, webkit } from 'playwright';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import * as fixtures from './fixtures.mjs';

const engine = process.argv[2] === 'webkit' ? webkit : chromium;
const PORT = 5183;
const OUT = process.env.REPRO_OUT ?? '/tmp/sticky-repro';

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
	let viteLog = '';
	vite.stdout.on('data', (d) => (viteLog += d));
	vite.stderr.on('data', (d) => (viteLog += d));

	try {
		await waitForServer(`http://localhost:${PORT}/`);

		const browser = await engine.launch();
		const context = await browser.newContext({
			viewport: { width: 390, height: 844 },
			deviceScaleFactor: 3,
			isMobile: true,
			hasTouch: true,
			userAgent:
				'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1'
		});
		const page = await context.newPage();

		const consoleErrors = [];
		page.on('console', (msg) => {
			if (msg.type() === 'error') consoleErrors.push(msg.text());
		});
		page.on('pageerror', (err) => consoleErrors.push(String(err)));

		await page.route('**/v1/dashboard/**', async (route) => {
			const url = new URL(route.request().url());
			const path = url.pathname;
			const body = fixtures.ROUTES[path];
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
		await page.waitForSelector('text=Item number 1', { timeout: 15000 });
		await delay(400); // let ignite transitions / ResizeObserver settle

		await page.screenshot({ path: `${OUT}/00-rest.png` });

		// Probe fn: dump the stack container + docked-limb rects/classes so a
		// visual anomaly can be correlated to actual state, not guessed at.
		const probe = () =>
			page.evaluate(() => {
				const stack = document.querySelector('.z-40');
				const machine = document.querySelector('.machine-dock');
				const heddles = document.querySelector('[aria-label="the heddles · lens"]');
				const rect = (el) => (el ? el.getBoundingClientRect().toJSON() : null);
				return {
					scrollY: window.scrollY,
					stackClass: stack?.className ?? null,
					stackRect: rect(stack),
					machineRect: rect(machine),
					heddlesRect: rect(heddles),
					stackPosition: stack ? getComputedStyle(stack).position : null
				};
			});

		const probes = [await probe()];

		// Momentum-style scroll: a real touch swipe sequence (touchstart, a
		// handful of touchmoves at ~16ms spacing, touchend) so the browser's own
		// compositor drives the fling/deceleration afterwards — not a scripted
		// window.scrollTo, which never exercises the "still moving after the
		// finger lifts" regime the bug report names.
		const cdp = await context.newCDPSession(page);
		const startX = 195,
			startY = 700;
		let y = startY;
		const touchPoint = () => [{ x: startX, y, id: 1 }];
		await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: touchPoint() });
		for (let i = 0; i < 10; i++) {
			y -= 55;
			await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: touchPoint() });
			await delay(16);
		}
		await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });

		// Now the fling/momentum phase: keep sampling for >1s without touching
		// anything else, screenshotting every ~80ms — "somewhere after a second
		// of scrolling" is squarely in this window.
		for (let i = 0; i < 16; i++) {
			await delay(80);
			probes.push(await probe());
			await page.screenshot({ path: `${OUT}/${String(i + 1).padStart(2, '0')}-fling.png` });
		}

		await delay(500);
		await page.screenshot({ path: `${OUT}/99-settled.png` });
		probes.push(await probe());

		await import('node:fs/promises').then((fs) =>
			fs.writeFile(`${OUT}/probes.json`, JSON.stringify(probes, null, 2))
		);
		await import('node:fs/promises').then((fs) =>
			fs.writeFile(`${OUT}/console-errors.json`, JSON.stringify(consoleErrors, null, 2))
		);

		console.log(`engine=${engine.name()} screenshots+probes written to ${OUT}`);
		console.log('console errors:', consoleErrors.length);

		await browser.close();
	} finally {
		vite.kill();
		await import('node:fs/promises').then((fs) => fs.writeFile(`${OUT}/vite.log`, viteLog));
	}
}

main().catch((err) => {
	console.error(err);
	process.exit(1);
});
