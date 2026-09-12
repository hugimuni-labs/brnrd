// Drives the production run-node route with a closed dispatch-fallback row.
// The row has `core_mismatch: false` because dispatch rewrites the expected
// core to the substitute; the receipt must still show the fallback marker and
// reason instead of the old "observed model matches" green title.
//
// Usage: node repro/drive-run-ledger.mjs [--out DIR] [--port N]
import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import { mkdir } from 'node:fs/promises';
import * as fixtures from './fixtures.mjs';
import { closeBrowser, stopVite, runDriver } from './finish.mjs';

const args = process.argv.slice(2);
const arg = (flag) => {
	const index = args.indexOf(flag);
	return index === -1 || index + 1 >= args.length ? null : args[index + 1];
};
const OUT = arg('--out') ?? '/tmp/run-ledger-drive';
const PORT = Number(arg('--port') ?? 5196);
const RUN_ID = 'run-fallback-receipt';
const RUN_PATH = `/runs/hugimuni-labs__brnrd/${RUN_ID}`;
const ROUTES = fixtures.buildRoutes(fixtures.DEFAULT_SCALE);
ROUTES['/v1/dashboard/run-ledger'] = fixtures.runLedgerFallback;

async function waitForServer(url, tries = 90) {
	for (let i = 0; i < tries; i++) {
		try {
			const response = await fetch(url);
			if (response.ok || response.status === 404) return;
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
	const report = {};
	try {
		await waitForServer(`http://localhost:${PORT}/`);
		const browser = await chromium.launch();
		const context = await browser.newContext({
			viewport: { width: 390, height: 844 },
			deviceScaleFactor: 2,
			reducedMotion: 'no-preference'
		});
		const page = await context.newPage();
		await page.route('**/v1/dashboard/**', async (route) => {
			const body = ROUTES[new URL(route.request().url()).pathname];
			await route.fulfill({
				status: body ? 200 : 404,
				contentType: 'application/json',
				body: JSON.stringify(body ?? {})
			});
		});

		await page.goto(`http://localhost:${PORT}${RUN_PATH}`, { waitUntil: 'networkidle' });
		const receipt = page.locator('[data-loom-run="run-fallback-receipt"]');
		await receipt.waitFor({ state: 'visible', timeout: 15000 });
		await page.getByText('⚙ fell back from claude-opus', { exact: true }).waitFor();
		// The shared layout curtain runs for ~1.2s and covers the route; waiting
		// for the receipt alone can still photograph the boot wordmark.
		await delay(1600);

		report.viewport = { width: 390, height: 844, deviceScaleFactor: 2 };
		report.receiptText = (await receipt.innerText()).replace(/\n/g, ' · ');
		report.fallbackMarker = await page
			.getByText('⚙ fell back from claude-opus', { exact: true })
			.count();
		report.fallbackReason = await page
			.getByText('dispatch fallback: claude-opus -> codex-gpt-5.6-luna after auth_error', {
				exact: true
			})
			.count();
		report.falseGreenTitle = await page
			.locator('[title="observed model matches the configured core pin"]')
			.count();
		if (
			report.fallbackMarker !== 1 ||
			report.fallbackReason !== 1 ||
			report.falseGreenTitle !== 0
		) {
			throw new Error(`fallback receipt assertion failed: ${JSON.stringify(report)}`);
		}
		await page.screenshot({ path: `${OUT}/phone-1-run-ledger-receipt.png`, fullPage: false });
		await context.close();
		await closeBrowser(browser, 'drive-run-ledger');
	} finally {
		stopVite(vite, 'drive-run-ledger');
	}
	console.log(
		JSON.stringify({ ...report, shot: `${OUT}/phone-1-run-ledger-receipt.png` }, null, 2)
	);
}

runDriver(main);
