// Live-account screenshot pass for the resident dashboard's core-selection
// and fuel controls (w-cockpit-names-its-controls). Drives the real backend
// through the probe proxy (vite.probe.config.ts) rather than fixtures —
// the report this feeds needs live shapes: the actual runner catalog, the
// actual quota report, the actual spawn_max_concurrent (or lack of it).
//
// Usage: node repro/drive-cockpit-controls.mjs --out DIR --port N --tag before|after
import { chromium } from 'playwright';
import { mkdir } from 'node:fs/promises';

const args = process.argv.slice(2);
const arg = (f, d = null) => {
	const i = args.indexOf(f);
	return i === -1 || i + 1 >= args.length ? d : args[i + 1];
};
const OUT = arg('--out', '/tmp/cockpit-shots');
const PORT = Number(arg('--port', '5199'));
const TAG = arg('--tag', 'shot');
const BASE = `http://localhost:${PORT}`;

const SIZES = [
	{ name: 'desktop', width: 1440, height: 900 },
	{ name: 'mobile', width: 390, height: 844 }
];

const ROUTES = [
	{ path: '/', name: 'dashboard' },
	{ path: '/daily', name: 'daily' },
	{ path: '/ascii', name: 'ascii' }
];

await mkdir(OUT, { recursive: true });

const browser = await chromium.launch();
try {
	for (const size of SIZES) {
		const context = await browser.newContext({
			viewport: { width: size.width, height: size.height }
		});
		const page = await context.newPage();
		page.on('console', (msg) => {
			if (msg.type() === 'error') console.log(`[console:${size.name}] ${msg.text()}`);
		});
		for (const route of ROUTES) {
			const url = `${BASE}${route.path}`;
			console.log(`-> ${size.name} ${url}`);
			try {
				await page.goto(url, { waitUntil: 'networkidle', timeout: 20000 });
			} catch (e) {
				console.log(`   goto failed: ${e.message}`);
				continue;
			}
			await page.waitForTimeout(1500);
			const shotPath = `${OUT}/${TAG}-${route.name}-${size.name}.png`;
			await page.screenshot({ path: shotPath, fullPage: true });
			console.log(`   saved ${shotPath}`);

			// On the dashboard, open every provider row so the CODEX/CLAUDE
			// windows+cores expansion (ProviderBay/SpoolRack) is in the shot,
			// not just the collapsed gauge.
			if (route.name === 'dashboard') {
				const rows = await page.$$('.fuel-provider-row');
				for (let i = 0; i < rows.length; i++) {
					await rows[i].click();
					await page.waitForTimeout(400);
					const expandedPath = `${OUT}/${TAG}-dashboard-${size.name}-provider${i}.png`;
					await page.screenshot({ path: expandedPath, fullPage: true });
					console.log(`   saved ${expandedPath}`);
					await rows[i].click(); // fold back before opening the next
					await page.waitForTimeout(200);
				}
			}
		}
		await context.close();
	}
} finally {
	await browser.close();
}
