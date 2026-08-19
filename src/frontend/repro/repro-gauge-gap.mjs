// w-68 defect 2 repro: "a reserved-looking gap between the gauge and what's
// below it, visible when scrolled, before the heddles dock." Scrolls in
// small steps (well past `SCROLL_STEP_THROTTLE_MS` + settle each time) and
// reads three things every step: railCondensed (gauge's own `pb-0` class),
// heddleDocked (whether the docked heddle strip is mounted), and the one
// surviving reserve spacer's live height (the div right after `.z-40`,
// `stackReserve(restHeight, liveHeight)` in stickyStack.ts). A non-zero
// spacer while `heddleDocked` is still false is exactly the reported shape:
// a gap that *reads* like it's reserved for the heddles, showing before they
// arrive.
//
// Usage: node repro/repro-gauge-gap.mjs [--out DIR] [--port N]

import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import { mkdir, writeFile } from 'node:fs/promises';
import * as fixtures from './fixtures.mjs';

const args = process.argv.slice(2);
const OUT = argValue('--out') ?? '/tmp/gauge-gap-repro';
const PORT = Number(argValue('--port') ?? 5191);

function argValue(flag) {
	const i = args.indexOf(flag);
	if (i === -1 || i + 1 >= args.length) return null;
	return args[i + 1];
}

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

async function readState(page) {
	return page.evaluate(() => {
		const stack = document.querySelector('.z-40');
		const gaugeWrapper = stack ? stack.firstElementChild : null;
		const spacer = stack ? stack.nextElementSibling : null;
		const heddleDockedBox = stack ? stack.querySelector('[aria-label="the heddles · lens"]') : null;
		return {
			scrollY: window.scrollY,
			railCondensed: gaugeWrapper ? gaugeWrapper.className.includes('pb-0') : null,
			heddleDocked: heddleDockedBox !== null,
			spacerHeight: spacer ? Math.round(spacer.getBoundingClientRect().height) : null,
			stackBottom: stack ? Math.round(stack.getBoundingClientRect().bottom) : null
		};
	});
}

async function main() {
	await mkdir(OUT, { recursive: true });
	const vite = spawn('npx', ['vite', 'dev', '--port', String(PORT), '--strictPort'], {
		stdio: ['ignore', 'pipe', 'pipe']
	});
	let viteLog = '';
	vite.stdout.on('data', (d) => (viteLog += d));
	vite.stderr.on('data', (d) => (viteLog += d));

	const trace = [];

	try {
		await waitForServer(`http://localhost:${PORT}/`);
		const browser = await chromium.launch();
		const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
		const page = await context.newPage();

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

		trace.push({ step: 'initial', ...(await readState(page)) });
		await page.screenshot({ path: `${OUT}/00-initial.png` });

		// Scroll in 150px steps to well past the stack's at-rest bottom, each
		// step waiting past SCROLL_STEP_THROTTLE_MS (100ms) + SCROLL_SETTLE_MS
		// so every step's read is a *settled* verdict, not a mid-transition one.
		let firstGapStep = null;
		let firstHeddleDockedStep = null;
		for (let i = 1; i <= 20; i++) {
			await page.evaluate((y) => window.scrollTo(0, y), i * 150);
			await delay(450);
			const state = await readState(page);
			trace.push({ step: `scroll-${i * 150}`, ...state });
			if (state.spacerHeight > 0 && firstGapStep === null) {
				firstGapStep = i;
				await page.screenshot({ path: `${OUT}/gap-first-nonzero.png` });
			}
			if (state.heddleDocked && firstHeddleDockedStep === null) {
				firstHeddleDockedStep = i;
				await page.screenshot({ path: `${OUT}/heddle-first-docked.png` });
			}
		}

		await page.screenshot({ path: `${OUT}/99-final.png` });
		await browser.close();

		await writeFile(`${OUT}/trace.json`, JSON.stringify(trace, null, 2));
		console.log(`first non-zero spacer at step: ${firstGapStep ?? 'never'}`);
		console.log(`first heddleDocked at step: ${firstHeddleDockedStep ?? 'never'}`);
		const verdict =
			firstGapStep !== null &&
			(firstHeddleDockedStep === null || firstGapStep < firstHeddleDockedStep)
				? 'REPRODUCED: gap appears before heddles dock'
				: 'not reproduced as described';
		console.log(verdict);
		console.log(`trace written: ${OUT}/trace.json`);
	} finally {
		vite.kill();
		await writeFile(`${OUT}/vite.log`, viteLog);
	}
}

main().catch((err) => {
	console.error(err);
	process.exit(1);
});
