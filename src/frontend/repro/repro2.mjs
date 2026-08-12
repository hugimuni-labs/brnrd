// Second-pass repro: instead of polling screenshots (150ms granularity vs a
// 100ms throttle + 50ms settle — too coarse to catch a mid-transition frame),
// hook requestAnimationFrame in-page and log the stack's own rect/position
// every frame for ~2s of scripted momentum-style scrolling (fast start,
// exponential decay — the shape a real touch fling leaves behind). A
// correctly "stuck" `position: sticky` container must read rect.top === 0
// (viewport-pinned) every single frame once scrolled past its home; any
// frame where that's false while position is still 'sticky' is the ghost
// this bug report describes, caught structurally instead of by eyeballing
// screenshots.

import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import * as fixtures from './fixtures.mjs';

const PORT = 5184;
const OUT = process.env.REPRO_OUT ?? '/tmp/sticky-repro2';

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
		const consoleErrors = [];
		page.on('console', (msg) => {
			if (msg.type() === 'error') consoleErrors.push(msg.text());
		});
		page.on('pageerror', (err) => consoleErrors.push(String(err)));

		await page.route('**/v1/dashboard/**', async (route) => {
			const url = new URL(route.request().url());
			const body = fixtures.ROUTES[url.pathname];
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
		await delay(400);

		// Install the frame recorder, then drive an eased scroll from 0 to
		// ~4000px over 1.6s (fast-start/slow-end — the deceleration shape a
		// real momentum fling leaves the compositor to finish), sampling
		// every animation frame throughout AND for 800ms after motion ends
		// (momentum "settling" is exactly where the report says the glitch
		// shows up).
		const result = await page.evaluate(async () => {
			function rect(el) {
				if (!el) return null;
				const r = el.getBoundingClientRect();
				return { top: r.top, bottom: r.bottom, left: r.left, right: r.right, height: r.height };
			}
			const frames = [];
			let raf = true;
			function sample(tag) {
				const stack = document.querySelector('.z-40');
				const machine = document.querySelector('.machine-dock');
				const heddles = document.querySelector('[aria-label="the heddles · lens"]');
				const rail = stack?.firstElementChild ?? null;
				const reserve = stack?.nextElementSibling ?? null;
				frames.push({
					t: performance.now(),
					tag,
					scrollY: window.scrollY,
					scrollHeight: document.documentElement.scrollHeight,
					stackClass: stack?.className ?? null,
					stackPosition: stack ? getComputedStyle(stack).position : null,
					stackRect: rect(stack),
					railRect: rect(rail),
					machineRect: rect(machine),
					heddlesRect: rect(heddles),
					machineDockedClass: machine ? machine.className.includes('mt-6') : null,
					reserveHeight: reserve ? reserve.getBoundingClientRect().height : null
				});
			}
			function loop() {
				sample('raf');
				if (raf) requestAnimationFrame(loop);
			}
			requestAnimationFrame(loop);

			// Eased scroll: exponential ease-out to ~4200px over 1600ms.
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
			// Keep sampling through the settle window.
			await new Promise((resolve) => setTimeout(resolve, 900));
			raf = false;
			await new Promise((resolve) => requestAnimationFrame(resolve));

			// Flag any frame where the stack is marked `position: sticky` in
			// its class but its own rect.top isn't pinned to 0 (viewport-top) —
			// a stuck sticky container detached from the viewport is exactly
			// the "docked at a stale document coordinate" symptom.
			const anomalies = frames.filter(
				(f) =>
					f.stackClass &&
					f.stackClass.includes('sticky') &&
					f.stackRect &&
					Math.abs(f.stackRect.top) > 0.5
			);
			return { frameCount: frames.length, anomalies, frames };
		});

		await import('node:fs/promises').then((fs) =>
			fs.writeFile(`${OUT}/frames.json`, JSON.stringify(result, null, 2))
		);
		await page.screenshot({ path: `${OUT}/final.png` });

		console.log('frames sampled:', result.frameCount);
		console.log('anomalies (sticky-but-not-pinned):', result.anomalies.length);
		if (result.anomalies.length) {
			console.log(JSON.stringify(result.anomalies.slice(0, 5), null, 2));
		}
		console.log('console errors:', consoleErrors.length, consoleErrors.slice(0, 5));

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
