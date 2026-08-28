// Drives THE BENCH — the collapsed project · environment block that sits
// above the fuel rail (maintainer, 2026-08-28: "we need a bench/settings
// whatever block, collapsed, on the very top of the page, above the fuel,
// stating the settings, and expandable on press").
//
// Three claims, each read as geometry rather than looked at:
//
//   1. `benchAboveGauge` — the bench's bottom edge is above the gauge's top
//      edge. This is the whole ask: the handle used to sit on the gauge's
//      own footline while the body mounted below the provider bay.
//   2. `handleStates` — the folded line names the project and the
//      environment. A handle that only said "settings" made a reader open it
//      to learn the one thing they usually wanted.
//   3. `deckAfterBenchOpen` — `.fuel-deck` is still 85px with the bench
//      unfolded. The bench grows *above* the sticky stack now, so it must
//      not be able to reach into the gauge's fixed height either.
//
// Plus the seam that made this a two-part change: a core tap in the provider
// bay used to write `runnersNote` to a strip that only rendered inside the
// settings panel, so a tap made with settings shut produced no visible
// receipt at all. `noteVisibleWithBenchShut` is that fix, driven.
//
// Usage: node repro/drive-bench.mjs [--out DIR] [--port N]
import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import { mkdir, writeFile } from 'node:fs/promises';
import * as fixtures from './fixtures.mjs';

const args = process.argv.slice(2);
const arg = (f) => {
	const i = args.indexOf(f);
	return i === -1 || i + 1 >= args.length ? null : args[i + 1];
};
const OUT = arg('--out') ?? '/tmp/bench-drive';
const PORT = Number(arg('--port') ?? 5198);

const ROUTES = fixtures.buildRoutes(fixtures.DEFAULT_SCALE);

const WIDTHS = [
	{ name: 'phone', width: 390, height: 844 },
	{ name: 'desktop', width: 1280, height: 900 }
];

async function waitForServer(url, tries = 90) {
	for (let i = 0; i < tries; i++) {
		try {
			const res = await fetch(url);
			if (res.ok || res.status === 404) return;
		} catch {
			/* not up */
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
	vite.stdout.on('data', () => {});
	vite.stderr.on('data', () => {});
	const results = {};
	try {
		await waitForServer(`http://localhost:${PORT}/`);
		const browser = await chromium.launch();
		for (const vp of WIDTHS) {
			const ctx = await browser.newContext({
				viewport: { width: vp.width, height: vp.height },
				deviceScaleFactor: 2,
				reducedMotion: 'no-preference'
			});
			const page = await ctx.newPage();
			await page.route('**/v1/dashboard/**', async (route) => {
				const body = ROUTES[new URL(route.request().url()).pathname];
				await route.fulfill({
					status: body ? 200 : 404,
					contentType: 'application/json',
					body: JSON.stringify(body ?? {})
				});
			});
			await page.goto(`http://localhost:${PORT}/`, { waitUntil: 'networkidle' });
			await page.waitForSelector('[data-measure="settings"]', { timeout: 15000 });
			await delay(700);

			const geom = async () =>
				page.evaluate(() => {
					const box = (sel) => {
						const el = document.querySelector(sel);
						if (!el) return null;
						const r = el.getBoundingClientRect();
						return {
							top: Math.round(r.top + window.scrollY),
							bottom: Math.round(r.bottom + window.scrollY),
							height: Math.round(r.height)
						};
					};
					return { bench: box('[data-measure="settings"]'), gauge: box('[data-measure="gauge"]') };
				});

			const shut = await geom();
			const handleText = (await page.locator('[data-role="bench-handle"]').innerText()).replace(
				/\n/g,
				' '
			);
			await page.screenshot({ path: `${OUT}/${vp.name}-1-shut.png`, fullPage: false });

			// The receipt seam: tap a core row with the bench SHUT and the
			// note must still reach the page.
			await page.locator('.fuel-provider-row').first().click();
			await page.waitForSelector('[data-measure="provider-bay"]', { timeout: 15000 });
			await delay(400);
			const rackRow = page.locator('[data-role="rack-row-tap"]').first();
			let noteVisibleWithBenchShut = null;
			if ((await rackRow.count()) > 0) {
				await rackRow.click();
				await delay(500);
				noteVisibleWithBenchShut = await page.evaluate(() => {
					const strip = document.querySelector('[data-measure="error-note"]');
					return strip ? strip.innerText.trim() || null : null;
				});
			}
			await page.screenshot({ path: `${OUT}/${vp.name}-2-tap-receipt.png`, fullPage: false });
			await page.locator('.fuel-provider-row').first().click();
			await delay(300);

			// Unfold the bench from its own handle.
			await page.locator('[data-role="bench-handle"]').click();
			await page.waitForSelector('[data-role="bench-pick"]', { timeout: 15000 });
			await delay(600);
			const open = await geom();
			const deckAfterBenchOpen = await page.evaluate(() =>
				Math.round(document.querySelector('[data-measure="fuel"]').getBoundingClientRect().height)
			);
			await page.screenshot({ path: `${OUT}/${vp.name}-3-open.png`, fullPage: false });

			// The selection must survive a fold — it used to unmount with the
			// component and silently reset to the repo default.
			const picks = page.locator('[data-measure="environment"] [data-role="bench-pick"]');
			let selectionSurvivesFold = null;
			if ((await picks.count()) > 1) {
				await picks.nth(1).click();
				await delay(300);
				const chosen = (await picks.nth(1).innerText()).split('\n')[0].trim();
				await page.locator('[data-role="bench-handle"]').click();
				await delay(400);
				const folded = (await page.locator('[data-role="bench-handle"]').innerText()).replace(
					/\n/g,
					' '
				);
				selectionSurvivesFold = { chosen, folded, held: folded.includes(chosen) };
			}
			await page.screenshot({ path: `${OUT}/${vp.name}-4-refolded.png`, fullPage: false });

			results[vp.name] = {
				handleText,
				benchAboveGauge: shut.bench.bottom <= shut.gauge.top,
				benchHeightShut: shut.bench.height,
				benchHeightOpen: open.bench.height,
				deckAfterBenchOpen,
				noteVisibleWithBenchShut,
				selectionSurvivesFold
			};
			await ctx.close();
		}
		await browser.close();
	} finally {
		vite.kill('SIGTERM');
	}
	await writeFile(`${OUT}/results.json`, JSON.stringify(results, null, 2));
	console.log(JSON.stringify(results, null, 2));
}

main().catch((err) => {
	console.error(err);
	process.exit(1);
});
