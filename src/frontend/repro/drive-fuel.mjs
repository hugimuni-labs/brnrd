// Drives the fuel deck against fixtures shaped like the live account:
// claude with three windows (a burned 5h session, a weekly ceiling, fable's
// core allowance) and codex with two.
//
// The load-bearing measurement is `deckAfterOpen`. A provider row is a
// control now — pressing it opens that provider's windows and cores — and
// the whole design rests on the expansion mounting *below* the sticky gauge
// rather than inside it. If `.fuel-deck` is anything but 85px after a press,
// the fixed-height rule has been broken by the very feature that was
// supposed to respect it.
//
// It also presses the second row and reads the bay's heading back: with the
// tab strip gone there is no second control that could disagree, and
// `openRows === 1` is the check that pressing one folds the other.
//
// Usage: node repro/drive-fuel.mjs [--out DIR] [--port N]
import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import { mkdir } from 'node:fs/promises';
import * as fixtures from './fixtures.mjs';

const args = process.argv.slice(2);
const arg = (f) => {
	const i = args.indexOf(f);
	return i === -1 || i + 1 >= args.length ? null : args[i + 1];
};
const OUT = arg('--out') ?? '/tmp/fuel-drive';
const PORT = Number(arg('--port') ?? 5197);

const ROUTES = fixtures.buildRoutes(fixtures.DEFAULT_SCALE);
// The live account's own shape, not the generic fixture: a session window
// that has burned past the weekly one is the case the old "primary = week"
// rule got wrong, so it is the case the shot has to show.
ROUTES['/v1/dashboard/quota'] = {
	generated_at: new Date(0).toISOString(),
	runner_quotas: [
		{
			shell: 'claude',
			status: 'known',
			windows: [
				{
					label: '5h window',
					used: 88,
					limit: 100,
					percent: 12,
					reset: 'resets 8:10pm',
					resets_at: 1787950000
				},
				{
					label: 'weekly',
					used: 67,
					limit: 100,
					percent: 33,
					reset: 'resets Aug 29',
					resets_at: 1788005000
				},
				{
					label: 'weekly (Fable)',
					used: 96,
					limit: 100,
					percent: 4,
					reset: 'resets Aug 29',
					resets_at: 1788005000
				}
			]
		},
		{
			shell: 'codex',
			status: 'known',
			windows: [
				{
					label: '5h window',
					used: 5,
					limit: 100,
					percent: 95,
					reset: 'resets 6pm',
					resets_at: 1787945000
				},
				{
					label: 'weekly',
					used: 19,
					limit: 100,
					percent: 81,
					reset: 'resets Sep 3',
					resets_at: 1788400000
				}
			]
		}
	]
};

// A `claude-fable` profile so the core-allowance chip has a row to land on:
// the allowance is matched on the model the profile pins, not its name.
const runners = ROUTES['/v1/dashboard/runners'];
runners.profiles = [
	{
		name: 'claude-fable',
		shell: 'claude',
		model: 'fable',
		provider: 'anthropic',
		class: 'strong',
		cost_rank: 55,
		quota_source: 'claude-local',
		availability: 'available',
		available: true
	},
	...runners.profiles
];

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
			await page.waitForSelector('[data-measure="fuel"]', { timeout: 15000 });
			await delay(700);

			const deck = page.locator('[data-measure="gauge"]');
			await deck.screenshot({ path: `${OUT}/${vp.name}-1-deck.png` });

			// press the CLAUDE fuel row, as a reader would
			await page.locator('.fuel-provider-row').first().click();
			await page.waitForSelector('[data-measure="provider-bay"]', { timeout: 15000 });
			await delay(600);
			const deckAfterOpen = await page.evaluate(() =>
				Math.round(document.querySelector('[data-measure="fuel"]').getBoundingClientRect().height)
			);
			await page.screenshot({ path: `${OUT}/${vp.name}-2-open-claude.png`, fullPage: false });
			const before = await page.locator('[data-measure="resources"] .workshop-label').textContent();

			// press CODEX: the open row folds, this one opens, and the bay follows.
			// There is no second control that could disagree with it.
			await page.locator('.fuel-provider-row').nth(1).click();
			await delay(500);
			const after = await page.locator('[data-measure="resources"] .workshop-label').textContent();
			const openRows = await page.evaluate(
				() => document.querySelectorAll('.fuel-provider-row[aria-expanded="true"]').length
			);
			await page.screenshot({ path: `${OUT}/${vp.name}-3-open-codex.png`, fullPage: false });

			// settings is its own small block now, and holds no core picker
			await page.getByRole('button', { name: /open settings/ }).click();
			await page.waitForSelector('[data-measure="settings"]', { timeout: 15000 });
			await delay(400);
			await page.screenshot({ path: `${OUT}/${vp.name}-5-settings.png`, fullPage: false });
			const settingsHasRack = await page.evaluate(
				() =>
					document.querySelector('[data-measure="settings"] [data-measure="spool-rack"]') !== null
			);

			// back to claude, and read the allowance chip off the fable row
			await page.locator('.fuel-provider-row').first().click();
			await page.waitForSelector('[data-measure="provider-bay"]', { timeout: 15000 });
			await delay(400);
			const allowanceChip = await page.evaluate(() => {
				const row = [...document.querySelectorAll('[data-role="rack-row-tap"]')].find((b) =>
					b.textContent.includes('claude-fable')
				);
				return row ? row.innerText.replace(/\n/g, ' ') : null;
			});
			await page.screenshot({ path: `${OUT}/${vp.name}-4-allowance.png`, fullPage: false });

			const rows = await page.evaluate(() =>
				[...document.querySelectorAll('.fuel-provider-row')].map((row) => ({
					text: row.innerText.replace(/\n/g, ' | '),
					fills: row.querySelectorAll('.fuel-fill').length,
					ghosts: row.querySelectorAll('.fuel-ghost').length,
					height: Math.round(row.getBoundingClientRect().height)
				}))
			);
			const gaugeH = await page.evaluate(() =>
				Math.round(document.querySelector('[data-measure="gauge"]').getBoundingClientRect().height)
			);
			console.log(
				JSON.stringify(
					{
						width: vp.name,
						gaugeHeight: gaugeH,
						rows,
						deckAfterOpen,
						resourcesBefore: before?.trim(),
						resourcesAfter: after?.trim(),
						openRows,
						settingsHasRack,
						cursorHolds: after?.trim().startsWith('codex') && openRows === 1,
						allowanceChip
					},
					null,
					1
				)
			);
			await ctx.close();
		}
		await browser.close();
	} finally {
		vite.kill('SIGTERM');
	}
	console.log(`shots → ${OUT}`);
}
main().catch((e) => {
	console.error(e);
	process.exit(1);
});
