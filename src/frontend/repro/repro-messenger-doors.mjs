// brr/every-door-on-the-page — offline render recipe for the
// `MessengerDoors.svelte` panel on `/repos`. Same pattern
// `measure-rail.mjs` uses (real vite dev server, `/v1/dashboard/*` mocked
// from `repro/fixtures.mjs`), extended with a `POST /v1/dashboard/pair`
// mock (fixed 180s TTL, matching the real backend default) and Playwright's
// virtual clock (`page.clock`) to fast-forward past the ample/low
// boundary and the TTL itself — so all three countdown tiers are captured
// deterministically, never by waiting 3 real minutes per screenshot.
//
// (First cut of this script re-minted with an artificially short
// `expires_at` per tap instead — that's wrong: a fresh mint always reads
// the fraction of *its own* TTL as ~1.0 in the instant right after
// minting, so every "short TTL" tap still rendered ample. The tiers only
// exist across elapsed time on one still-live code, which is exactly what
// `page.clock.fastForward` gives for free and a re-mint does not.)
//
// Usage: node repro/repro-messenger-doors.mjs [--out DIR] [--port N]

import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import { mkdir, writeFile } from 'node:fs/promises';
import * as fixtures from './fixtures.mjs';

const args = process.argv.slice(2);
const OUT = argValue('--out') ?? '/tmp/messenger-doors-repro';
const PORT = Number(argValue('--port') ?? 5187);

function argValue(flag) {
	const i = args.indexOf(flag);
	if (i === -1 || i + 1 >= args.length) return null;
	return args[i + 1];
}

const WIDTHS = [
	{ name: 'phone', width: 390, height: 844 },
	{ name: 'desktop', width: 1200, height: 950 }
];

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

// The real backend default (`Settings.messenger_pair_ttl_s`) — every mint
// gets the full window; tiers are reached by elapsed time on the virtual
// clock, below, never by shrinking this.
const TTL_S = 180;
let mintCount = 0;

async function mountRoutes(page) {
	await page.route('**/v1/dashboard/**', async (route) => {
		const req = route.request();
		if (req.method() === 'POST' && req.url().includes('/v1/dashboard/pair')) {
			const body = JSON.parse(req.postData() || '{}');
			const platform = body.platform || 'telegram';
			mintCount += 1;
			const expires_at = new Date(Date.now() + TTL_S * 1000).toISOString();
			const deep_link =
				platform === 'telegram'
					? `https://t.me/brnrd_bot?start=PK-repro${mintCount}`
					: `https://wa.me/15551234567?text=PK-repro${mintCount}`;
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({
					pair_code: `PK-repro${mintCount}`,
					instructions: `Open ${deep_link}`,
					deep_link,
					platform,
					expires_at
				})
			});
			return;
		}
		const url = new URL(req.url());
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
}

async function shot(page, path) {
	await page.screenshot({ path, fullPage: true });
}

async function main() {
	await mkdir(OUT, { recursive: true });
	const vite = spawn('npx', ['vite', 'dev', '--port', String(PORT), '--strictPort'], {
		stdio: ['ignore', 'pipe', 'pipe']
	});
	let viteLog = '';
	vite.stdout.on('data', (d) => (viteLog += d));
	vite.stderr.on('data', (d) => (viteLog += d));

	try {
		await waitForServer(`http://localhost:${PORT}/`);
		const browser = await chromium.launch();

		for (const viewport of WIDTHS) {
			mintCount = 0;
			const context = await browser.newContext({
				viewport: { width: viewport.width, height: viewport.height }
			});
			const page = await context.newPage();
			const consoleErrors = [];
			page.on('console', (msg) => {
				if (msg.type() === 'error') consoleErrors.push(msg.text());
			});
			page.on('pageerror', (err) => consoleErrors.push(String(err)));

			// Installed before `goto` — Playwright's virtual clock takes over
			// `Date`/`setTimeout`/`setInterval` for everything the page creates
			// from here on, which is exactly the interval the component's own
			// countdown ticks on.
			await page.clock.install({ time: Date.now() });

			await mountRoutes(page);
			await page.goto(`http://localhost:${PORT}/repos`, { waitUntil: 'networkidle' });
			await page.waitForSelector('[data-testid="door-telegram"]', { timeout: 15000 });
			await delay(200);
			await shot(page, `${OUT}/${viewport.name}-01-initial.png`);

			// Ample: mint the lit door (telegram), full 180s TTL.
			await page.locator('[data-testid="connect-telegram"]').click();
			await page.waitForSelector('[data-testid="countdown-telegram"]', { timeout: 15000 });
			await delay(200);
			await shot(page, `${OUT}/${viewport.name}-02-ample.png`);

			// Low: fast-forward past the ample/low boundary (⅓ of 180s = 60s
			// remaining) on the *same* still-live code — no new mint.
			await page.clock.fastForward(125_000);
			await delay(200);
			await shot(page, `${OUT}/${viewport.name}-03-low.png`);

			// Critical: fast-forward past the TTL itself.
			await page.clock.fastForward(60_000);
			await delay(200);
			await shot(page, `${OUT}/${viewport.name}-04-critical.png`);

			await writeFile(
				`${OUT}/${viewport.name}-console.json`,
				JSON.stringify(consoleErrors, null, 2)
			);
			await context.close();
		}

		await browser.close();
	} finally {
		vite.kill();
		await writeFile(`${OUT}/vite.log`, viteLog);
	}

	console.log(`written: ${OUT}`);
}

main().catch((err) => {
	console.error(err);
	process.exit(1);
});
