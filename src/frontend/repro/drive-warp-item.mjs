// Drives `/warp/[id]` — the warp item's own page (#2122, #2127): the
// metadata panel (says · runs · refs · dispatch prompt), the stage chip, the
// authored `return:` line and the `touched` footer, then the prose body.
//
// Written because #2127's ui-shots check reported this route as "changed and
// never fetched": no driver opened it, so the page the PR reshaped most was
// the one nothing looked at. Two items: one carrying every row the page
// renders, and one carrying *only* a `prompt:` — the case where the panel's
// own guard once omitted `item.prompt` and the dispatch prompt vanished.
//
// Behavioural asserts first, screenshots second (drive-bench-view.mjs's
// pattern, `finish.mjs`'s bounded teardown and coverage recorder).
//
// Usage: node repro/drive-warp-item.mjs [--out DIR] [--port N]
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
const OUT = arg('--out') ?? '/tmp/warp-item';
const PORT = Number(arg('--port') ?? 5206);

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

const FULL = {
	path: 'surface/warp/w-900.md',
	markdown: [
		'# The item that shows its work',
		'',
		'type: action',
		'topics: mint',
		'stage: making',
		'says: evt-1790524160631887000-8kn1 evt-1790525805808986000-yitn',
		'attempts: run-260926-2316-5efh run-260926-2351-n10b',
		'taken: run-260926-2351-n10b',
		'refs: #2127',
		'prompt: Render says, runs and stage as fields, not prose',
		'return: a warp page a reader can act from',
		'touched: 2026-09-26T23:28Z',
		'',
		'## Asked',
		'',
		'Show the work the item has already seen.',
		''
	].join('\n')
};

const PROMPT_ONLY = {
	path: 'surface/warp/w-901.md',
	markdown: [
		'# The item with only a prompt',
		'',
		'type: action',
		'prompt: Dispatch me with this line',
		'',
		'Body text.',
		''
	].join('\n')
};

const SURFACE = {
	...fixtures.surface,
	files: [...fixtures.surface.files, FULL, PROMPT_ONLY]
};
const ROUTES = { ...fixtures.ROUTES, '/v1/dashboard/surface': SURFACE };

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

const text = async (locator) => (await locator.innerText()).replace(/\s+/g, ' ').trim();

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
			reducedMotion: 'reduce'
		});
		const page = await modules.watch(await context.newPage(), 'drive-warp-item');
		await routePage(page);

		await page.goto(`http://localhost:${PORT}/warp/w-900`, { waitUntil: 'networkidle' });
		await page.waitForSelector('h1', { timeout: 20000 });
		const header = await text(page.locator('header').last());
		assert.ok(header.includes('The item that shows its work'), `headline (${header})`);
		assert.ok(/making/i.test(header), `stage chip (${header})`);
		assert.ok(header.includes('a warp page a reader can act from'), `return line (${header})`);

		const meta = page.locator('[aria-label="item metadata"]');
		await meta.waitFor({ state: 'visible', timeout: 10000 });
		const metaText = await text(meta);
		assert.ok(/comments · 2/i.test(metaText), `comments count (${metaText})`);
		assert.ok(metaText.includes('8kn1') && metaText.includes('yitn'), `says tails (${metaText})`);
		// attempts ∪ taken, de-duplicated: two distinct run ids, not three.
		assert.ok(/runs · 2/i.test(metaText), `runs de-duplicated (${metaText})`);
		assert.ok(metaText.includes('#2127'), `refs (${metaText})`);
		assert.ok(metaText.includes('Render says, runs and stage'), `dispatch prompt (${metaText})`);
		const footer = await text(page.locator('footer').last());
		assert.ok(footer.includes('touched 2026-09-26T23:28Z'), `touched footer (${footer})`);
		const body = await text(page.locator('[aria-label="item body"]'));
		assert.ok(body.includes('Show the work'), `prose body (${body})`);
		await page.screenshot({ path: `${OUT}/1-full-item.png`, fullPage: true });

		await page.goto(`http://localhost:${PORT}/warp/w-901`, { waitUntil: 'networkidle' });
		await page.waitForSelector('h1', { timeout: 20000 });
		const lone = page.locator('[aria-label="item metadata"]');
		await lone.waitFor({ state: 'visible', timeout: 10000 });
		const loneText = await text(lone);
		assert.ok(loneText.includes('Dispatch me with this line'), `prompt-only item (${loneText})`);
		await page.screenshot({ path: `${OUT}/2-prompt-only.png`, fullPage: true });

		await modules.harvest(page, 'drive-warp-item');
		await context.close();
		await modules.save(OUT, 'drive-warp-item');
		console.log(JSON.stringify({ header, metaText, loneText, ok: true }, null, 2));
	} finally {
		if (browser) await closeBrowser(browser, 'drive-warp-item');
		stopVite(vite, 'drive-warp-item');
	}
}

runDriver(main);
