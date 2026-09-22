// Drives the home's list of asks, in place of the warp's item graph
// (design-the-ask.md §Done, reopened, linked, "the console = the warp panel,
// per ask"). `--tag before` captures the home on a tree without the four-
// bucket list (shots only); `--tag after` also asserts behaviour first,
// screenshots second — a shot that never exercised the component is a green
// tick meaning nothing looked (the 2026-08-26 room lesson). The fixture
// (`fixtures.mjs::asks`) already carries one row per bucket: w-201 (stage
// making, a live strand) → in hand; w-96 (stage delivered, stale) → yours to
// judge; w-198/w-190 (understood/shaped) → unaddressed; w-150/w-140 → done.
//
// Usage: node repro/drive-asks.mjs [--out DIR] [--port N] [--tag before|after]
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
const OUT = arg('--out') ?? '/tmp/asks-shots';
const PORT = Number(arg('--port') ?? 5207);
const TAG = arg('--tag') ?? 'after';
const VIEWPORTS = [
	{ name: '1280x800', width: 1280, height: 800 },
	{ name: '390x844', width: 390, height: 844 }
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

// One live strand wearing w-201's attempt, in both tags so before/after differ
// only by the list.
const ROUTES = {
	...fixtures.ROUTES,
	'/v1/dashboard/live-runs': {
		...fixtures.liveRuns,
		runs: [
			{
				id: 'presence-asks',
				run_id: 'run-fallback-receipt',
				kind: 'daemon',
				stream: 'telegram:loom:',
				repo_label: 'hugimuni-labs/brnrd',
				topics: ['the-loom'],
				boundaries: [
					{ at: new Date().toISOString(), phase: 'tool', act: 'read', tools: ['exec_command'] }
				]
			}
		]
	}
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
	const report = { tag: TAG };
	try {
		await waitForServer(`http://localhost:${PORT}/`);
		browser = await chromium.launch();
		for (const vp of VIEWPORTS) {
			const context = await browser.newContext({
				viewport: { width: vp.width, height: vp.height },
				deviceScaleFactor: 2,
				reducedMotion: 'reduce' // skips the boot curtain — see routes/+layout.svelte
			});
			const page = await modules.watch(await context.newPage(), 'drive-asks');
			await routePage(page);
			await page.goto(`http://localhost:${PORT}/`, { waitUntil: 'networkidle' });
			await page.waitForSelector('h1', { timeout: 20000 });

			if (TAG === 'after') {
				const list = page.locator('[aria-label="your asks"]');
				await list.waitFor({ state: 'visible', timeout: 15000 });

				// Goals stay goals, above the buckets.
				const goalsLine = list.locator('[data-goals]');
				assert.ok(
					(await goalsLine.innerText()).includes('Talk to one entity'),
					'the one goal renders above the buckets'
				);

				// The four buckets, his order, each headed and counted — replacing
				// the item-number graph that used to sit here.
				const inHand = list.locator('[data-bucket-heading="in-hand"]');
				const toJudge = list.locator('[data-bucket-heading="to-judge"]');
				const unaddressed = list.locator('[data-bucket-heading="unaddressed"]');
				const done = list.locator('[data-bucket-heading="done"]');
				// `uppercase` is a CSS transform (`text-transform`), and Playwright's
				// `innerText` reflects rendered casing (unlike `textContent`) — lower
				// both sides rather than pin a rendering detail these headings don't
				// own semantically.
				const lower = async (locator) => (await locator.innerText()).toLowerCase();
				assert.ok((await lower(inHand)).includes('in hand · 1'), 'one row in hand');
				assert.ok((await lower(toJudge)).includes('yours to judge · 1'), 'one row to judge');
				assert.ok((await lower(unaddressed)).includes('unaddressed · 2'), 'two unaddressed rows');
				assert.ok((await lower(done)).includes('done & accepted · 2'), 'two done rows');

				const rows = list.locator('[data-ask]');
				assert.equal(await rows.count(), 4, 'in hand + to judge + unaddressed, done collapsed');
				const first = (await rows.first().innerText()).replace(/\s+/g, ' ');
				assert.ok(first.includes('w-201'), `in hand leads (${first})`);
				assert.ok(first.includes('▷ run-fallback-receipt'), `wears its live strand (${first})`);
				assert.ok(await rows.first().locator('[data-drone]').count(), 'live drone mark');

				// The stale "yours to judge" row: dimmed in place, not sunk under a
				// rule — and it carries its receipt-free accept/reroute hints.
				const w96 = list.locator('[data-ask="w-96"]');
				assert.equal(await w96.getAttribute('data-ask-stale'), '', 'w-96 stays marked stale');
				const w96Li = w96.locator('xpath=..');
				assert.ok(
					(await w96Li.getAttribute('class')).includes('opacity-60'),
					'a stale row dims in its own bucket, not below a rule'
				);
				// The two words are chips now (the-lit-rows: "a small chip pair, not
				// body text") and wear the same `uppercase` transform the bucket
				// headings above already do — same reason, same fix: lower both
				// sides rather than pin a rendering detail these chips don't own
				// semantically.
				const hints = w96Li.locator('[data-to-judge-hints]');
				const hintsText = (await hints.innerText()).toLowerCase();
				assert.ok(hintsText.includes('accept w-96'), `copyable accept hint (${hintsText})`);
				assert.ok(hintsText.includes('reroute w-96'), `copyable reroute hint (${hintsText})`);

				// done is collapsed to a count with a toggle
				const toggle = list.getByRole('button', { name: /done & accepted/i });
				assert.equal(await list.locator('[data-ask-done]').count(), 0, 'done rows collapsed');
				await toggle.click();
				assert.equal(await list.locator('[data-ask-done]').count(), 2, 'done rows open');
				await toggle.click();
				// keyboard: j moves, enter expands
				await page.keyboard.press('j');
				await page.keyboard.press('Enter');
				const open = list.locator('[data-ask][aria-expanded="true"]');
				assert.equal(await open.count(), 1, 'enter expands the focused row');
				const openText = (await open.first().locator('xpath=..').innerText()).replace(/\s+/g, ' ');
				assert.ok(openText.includes('evt-'), `expanded row shows its says (${openText})`);
				await page.keyboard.press('Enter');
				assert.equal(await list.locator('[data-ask][aria-expanded="true"]').count(), 0);
				// expand the first row for the shot
				await rows.first().click();
				report[vp.name] = { first, openText };
			}
			await page.screenshot({ path: `${OUT}/${TAG}-${vp.name}.png`, fullPage: false });
			await page.screenshot({ path: `${OUT}/${TAG}-${vp.name}-full.png`, fullPage: true });
			await modules.harvest(page, 'drive-asks');
			await context.close();
		}
		await modules.save(OUT, 'drive-asks');
		console.log(JSON.stringify({ ...report, ok: true }, null, 2));
	} finally {
		if (browser) await closeBrowser(browser, 'drive-asks');
		stopVite(vite, 'drive-asks');
	}
}

runDriver(main);
