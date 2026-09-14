// Drives the bench section on `/daily` and the file it opens onto
// (design-the-loom.md §6/§18): the fixture's one fold renders as a row —
// place, truncated commit, question — and a tap opens
// `/bench/<repo>/<place>/<commit>`, where the same file's body renders
// through the existing `MarkdownContent` renderer (§18: "reuse that
// renderer" — no new markdown pipeline for this route).
//
// Behavioural asserts first, screenshots second (the 2026-08-26 room lesson,
// named again by #1953/#1944: a screenshot that never exercised the changed
// component is a green tick meaning nothing looked) — on the #1955 pattern
// (`drive-heddle-live-topics.mjs`): `finish.mjs`'s bounded teardown and JS
// coverage recorder, so a hung browser handle can never turn "did the work"
// into "never exited" again (#1944's sixteen-for-sixteen).
//
// Usage: node repro/drive-bench-view.mjs [--out DIR] [--port N]
import { spawn } from 'node:child_process';
import { strict as assert } from 'node:assert';
import { mkdir } from 'node:fs/promises';
import { chromium } from 'playwright';
import { setTimeout as delay } from 'node:timers/promises';
import * as fixtures from './fixtures.mjs';
import { closeBrowser, exerciseRecorder, runDriver, stopVite } from './finish.mjs';

const args = process.argv.slice(2);
const arg = (flag) => {
	const index = args.indexOf(flag);
	return index === -1 || index + 1 >= args.length ? null : args[index + 1];
};
const OUT = arg('--out') ?? '/tmp/bench-view';
const PORT = Number(arg('--port') ?? 5204);

async function waitForServer(url, tries = 90) {
	for (let attempt = 0; attempt < tries; attempt += 1) {
		try {
			const response = await fetch(url);
			if (response.ok || response.status === 404) return;
		} catch {
			// Vite has not bound its port yet.
		}
		await delay(500);
	}
	throw new Error(`dev server never came up at ${url}`);
}

const FOLD = fixtures.bench.files[0];
const ROUTES = {
	...fixtures.ROUTES,
	[`/v1/dashboard/bench/${FOLD.path}`]: fixtures.benchFile
};

async function routePage(page) {
	await page.route('**/v1/dashboard/**', async (route) => {
		const body = ROUTES[new URL(route.request().url()).pathname];
		await route.fulfill({
			status: body ? 200 : 404,
			contentType: 'application/json',
			body: JSON.stringify(body ?? {})
		});
	});
}

async function main() {
	await mkdir(OUT, { recursive: true });
	const vite = spawn('npx', ['vite', 'dev', '--port', String(PORT), '--strictPort'], {
		stdio: ['ignore', 'pipe', 'pipe']
	});
	const modules = exerciseRecorder();
	let browser;
	try {
		await waitForServer(`http://localhost:${PORT}/`);
		browser = await chromium.launch();
		const context = await browser.newContext({
			viewport: { width: 390, height: 844 },
			deviceScaleFactor: 2,
			reducedMotion: 'reduce' // skips the boot curtain — see routes/+layout.svelte
		});
		const page = await modules.watch(await context.newPage(), 'drive-bench-view');
		await routePage(page);
		await page.goto(`http://localhost:${PORT}/daily`, { waitUntil: 'networkidle' });
		await page.waitForSelector('#warp-heading', { timeout: 20000 });

		const bench = page.locator('[aria-label="the bench"]');
		await bench.waitFor({ state: 'visible', timeout: 15000 });
		const listText = (await bench.innerText()).replace(/\s+/g, ' ').trim();
		assert.ok(listText.includes(FOLD.place), `the row names its place (${listText})`);
		assert.ok(listText.includes(FOLD.commit), `the row names its commit (${listText})`);
		assert.ok(listText.includes(FOLD.question), `the row names its question (${listText})`);
		await bench.screenshot({ path: `${OUT}/1-list.png` });

		const scrollY = await page.evaluate(() => window.scrollY);
		await bench.getByRole('link').first().click();
		await page.waitForURL(`**/bench/${FOLD.path}`, { timeout: 10000 });
		await page.waitForSelector('h2', { timeout: 10000 });
		const bodyText = (await page.locator('.panel').first().innerText()).replace(/\s+/g, ' ').trim();
		assert.ok(bodyText.includes(FOLD.place), `the page names its place (${bodyText})`);
		assert.ok(bodyText.includes(FOLD.question), `the page carries its question (${bodyText})`);
		assert.ok(
			bodyText.includes('One markdown file per fold'),
			`the fixture's markdown body rendered (${bodyText})`
		);
		await page.screenshot({ path: `${OUT}/2-file.png`, fullPage: true });

		// The 404 shape: a place with no bench file names itself, not a
		// generic "not found" — design-the-loom.md §18's own line.
		await page.goto(`http://localhost:${PORT}/bench/hugimuni-labs__brnrd/nowhere.md/dead`, {
			waitUntil: 'networkidle'
		});
		await page.waitForSelector('.panel', { timeout: 10000 });
		const missingText = (await page.locator('.panel').first().innerText())
			.replace(/\s+/g, ' ')
			.trim();
		assert.ok(
			missingText.includes('hugimuni-labs__brnrd/nowhere.md/dead'),
			`a missing fold names its own address (${missingText})`
		);
		await page.screenshot({ path: `${OUT}/3-missing.png` });

		await modules.harvest(page, 'drive-bench-view');
		await context.close();
		await modules.save(OUT, 'drive-bench-view');
		console.log(JSON.stringify({ listText, bodyText, missingText, scrollY, ok: true }, null, 2));
	} finally {
		if (browser) await closeBrowser(browser, 'drive-bench-view');
		stopVite(vite, 'drive-bench-view');
	}
}

runDriver(main);
