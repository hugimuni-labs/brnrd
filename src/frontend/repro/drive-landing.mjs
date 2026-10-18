// Drives `/` logged out — the landing (`Landing.svelte`), phone and desktop,
// full page. Every /v1 call answers 401 except the public support matrix, so
// the dashboard gate settles on 'anon' exactly as it does for a visitor.
//
// Asserts before shots: the hero headline, the loom nouns (letter · thread ·
// strand · receipt) and no daemon-era copy, and that every link on the page
// leads somewhere: an in-page anchor names an element that exists, and a
// same-origin route answers 200 from the dev server. External links (docs,
// GitHub) are listed in the payload, not fetched: CI has no business
// depending on them.
//
// Usage: node repro/drive-landing.mjs [--out DIR] [--port N]
import { spawn } from 'node:child_process';
import { strict as assert } from 'node:assert';
import { mkdir } from 'node:fs/promises';
import { chromium } from 'playwright';
import { setTimeout as delay } from 'node:timers/promises';
import { closeBrowser, exerciseRecorder, runDriver, stopVite } from './finish.mjs';

const args = process.argv.slice(2);
const arg = (flag) => {
	const index = args.indexOf(flag);
	return index === -1 || index + 1 >= args.length ? null : args[index + 1];
};
const OUT = arg('--out') ?? '/tmp/landing';
const PORT = Number(arg('--port') ?? 5207);
const ORIGIN = `http://localhost:${PORT}`;

const VIEWPORTS = [
	{ name: 'phone', width: 390, height: 844 },
	{ name: 'desktop', width: 1440, height: 900 }
];

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

async function routePage(page) {
	await page.route('**/v1/**', async (route) => {
		const path = new URL(route.request().url()).pathname;
		if (path === '/v1/stats/support')
			return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
		await route.fulfill({ status: 401, contentType: 'application/json', body: '{}' });
	});
}

// The scanline glass (`body::before`, layout.css) is `position: fixed`, so
// Chromium's `fullPage` capture paints it over the first viewport only and
// the shot shows a hard seam at y = viewport height that no reader ever sees:
// scrolled, the glass covers every screen. Growing the viewport to the page
// first makes the picture match what a reader sees at every scroll position.
async function fullPageShot(page, viewport, path) {
	const height = await page.evaluate(() => document.documentElement.scrollHeight);
	await page.setViewportSize({ width: viewport.width, height });
	await page.screenshot({ path, fullPage: true });
	await page.setViewportSize({ width: viewport.width, height: viewport.height });
}

const text = async (locator) => (await locator.innerText()).replace(/\s+/g, ' ').trim();

async function checkLinks(page) {
	const links = await page.$$eval('a[href]', (anchors) =>
		anchors.map((a) => ({ label: a.textContent.replace(/\s+/g, ' ').trim(), href: a.href }))
	);
	const external = [];
	for (const link of links) {
		const url = new URL(link.href);
		if (url.origin !== ORIGIN) {
			external.push(link.href);
			continue;
		}
		if (url.hash && url.pathname === '/') {
			const id = url.hash.slice(1);
			assert.ok(await page.$(`[id="${id}"]`), `${link.label} → ${url.hash} names no element`);
			continue;
		}
		const response = await fetch(link.href);
		assert.equal(response.status, 200, `${link.label} → ${link.href} answered ${response.status}`);
	}
	return { count: links.length, external: [...new Set(external)].sort() };
}

async function main() {
	await mkdir(OUT, { recursive: true });
	const vite = spawn('npx', ['vite', 'dev', '--port', String(PORT), '--strictPort'], {
		stdio: ['ignore', 'pipe', 'pipe']
	});
	const modules = exerciseRecorder();
	let browser;
	const seen = {};
	try {
		await waitForServer(`${ORIGIN}/`);
		browser = await chromium.launch();
		for (const viewport of VIEWPORTS) {
			const context = await browser.newContext({
				viewport: { width: viewport.width, height: viewport.height },
				deviceScaleFactor: viewport.name === 'phone' ? 2 : 1,
				reducedMotion: 'reduce'
			});
			const page = await modules.watch(await context.newPage(), 'drive-landing');
			await routePage(page);
			await page.goto(`${ORIGIN}/`, { waitUntil: 'networkidle' });
			const hero = page.locator('h1');
			await hero.waitFor({ state: 'visible', timeout: 20000 });
			// Let the boot curtain finish. The shot comes before the copy asserts, so
			// a tree that fails them (the base, on the PR that changes them) still
			// leaves its picture.
			await delay(1500);
			await fullPageShot(page, viewport, `${OUT}/${viewport.name}-landing.png`);
			assert.equal(await text(hero), 'assign the work. get back a receipt.');

			const body = await text(page.locator('body'));
			for (const noun of ['letter', 'thread', 'strand', 'receipt'])
				assert.ok(body.toLowerCase().includes(noun), `"${noun}" is on the page`);
			for (const stale of ['daemon', 'routes bring work', 'bounded workers'])
				assert.ok(!body.toLowerCase().includes(stale), `daemon-era copy "${stale}" is gone`);

			seen[viewport.name] = await checkLinks(page);
			await modules.harvest(page, 'drive-landing');
			await context.close();
		}
		await modules.save(OUT, 'drive-landing');
		console.log(JSON.stringify({ seen, ok: true }, null, 2));
	} finally {
		if (browser) await closeBrowser(browser, 'drive-landing');
		stopVite(vite, 'drive-landing');
	}
}

runDriver(main);
