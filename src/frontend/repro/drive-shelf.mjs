// Before/after for "the shelf gets a place on /daily" (maintainer,
// 2026-09-06: "we have no place for the shelf items on the web UI, no?
// maybe add one to /daily"). Same drive.mjs/fixtures.mjs pattern
// `drive-daily.mjs` uses — no backend, no account, a mocked browser wire.
//
// The fixture's `/v1/dashboard/surface` carries no `surface/shelf/*.md`
// pages (fixtures.mjs's own list is topics + warp items only), so this
// overrides that one route locally with three shelf pages exercising both
// frontmatter shapes on disk and both expiry states — same "override
// locally" pattern `drive-daily.mjs` already uses for `live-runs`.
//
// Usage: node repro/drive-shelf.mjs   (REPRO_OUT=/tmp/shelf-drive default,
// REPRO_TAG=before|after to label files — run once against each tree state)

import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import { mkdirSync } from 'node:fs';
import * as fixtures from './fixtures.mjs';

const SHELF_FILES = [
	{
		path: 'surface/shelf/index.md',
		markdown: '# The shelf\n\nkeeps: n/a — the contract page, not a commissioned artifact\n'
	},
	{
		path: 'surface/shelf/stars-plan-2026-09-06.md',
		markdown:
			'# GitHub stars — the plan, measured from zero\n\n' +
			'keeps: until the repo has a description and topics, and the stargazer count is re-measured\n' +
			'made: 2026-09-06 02:20Z, run-260906-0037-b09r, on his ask\n\n' +
			'## The measurement\n\nStargazers: 0.\n'
	},
	{
		path: 'surface/shelf/gauge-bench-heights-2026-08-19.md',
		markdown:
			'made-for: arseni\n' +
			'asked: evt-1787155584839918000-q8ro\n' +
			'keeps: until the provider-row gauge deck changes shape again\n' +
			'topics: the-workshop the-clockwork\n\n' +
			"# The provider-row gauge's fixed height — the acceptance number, read\n\n" +
			'The sticky gauge wrapper is 160px at every tested scale.\n'
	},
	{
		path: 'surface/shelf/rail-heights-2026-08-19.md',
		markdown:
			'made-for: arseni\n' +
			'asked: evt-1787140446318045000-tout\n' +
			'keeps: expired 2026-08-19 — the gauge/bench split landed\n\n' +
			"# ~~The rail's own height — the instrument, and its first reading~~\n\n" +
			'Superseded the same day it was written.\n'
	}
];

const ROUTE_BODIES = {
	...fixtures.ROUTES,
	'/v1/dashboard/surface': {
		...fixtures.surface,
		files: [...fixtures.surfaceFiles, ...SHELF_FILES]
	}
};

const PORT = Number(process.env.REPRO_PORT ?? 5197);
const OUT = process.env.REPRO_OUT ?? '/tmp/shelf-drive';
const TAG = process.env.REPRO_TAG ?? 'after';

const VIEWPORTS = [
	{ name: '390x844', viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true },
	{ name: '1440x900', viewport: { width: 1440, height: 900 } }
];

const failures = [];
function check(ok, what) {
	if (ok) console.log(`  ✓ ${what}`);
	else {
		console.log(`  ✗ ${what}`);
		failures.push(what);
	}
}

async function waitForServer(url, tries = 90) {
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

async function driveShelf(browser, spec) {
	const context = await browser.newContext({
		viewport: spec.viewport,
		deviceScaleFactor: 1,
		isMobile: spec.isMobile ?? false,
		hasTouch: spec.hasTouch ?? false,
		reducedMotion: 'reduce'
	});
	const page = await context.newPage();
	await page.route('**/v1/dashboard/**', async (route) => {
		const url = new URL(route.request().url());
		const body = ROUTE_BODIES[url.pathname];
		await route.fulfill({
			status: body ? 200 : 404,
			contentType: 'application/json',
			body: JSON.stringify(body ?? {})
		});
	});

	await page.goto(`http://localhost:${PORT}/daily`, { waitUntil: 'networkidle' });
	await page.waitForSelector('#warp-heading', { timeout: 20000 });
	await delay(1000);

	const heading = page.locator('#shelf-heading');
	const present = (await heading.count()) > 0;
	console.log(`  (shelf section present: ${present})`);
	if (present) {
		await heading.scrollIntoViewIfNeeded();
		await delay(300);
		check(await heading.isVisible(), `/daily ${spec.name}: the shelf section renders`);
		check(
			(await page.getByText('the shelf').count()) > 0,
			`/daily ${spec.name}: "the shelf" heading text present`
		);
		// One open page, one expired page, both visible in the same list.
		check(
			(await page.getByText('GitHub stars').count()) > 0,
			`/daily ${spec.name}: an open shelf page's title renders`
		);
		check(
			(await page.getByText("The rail's own height").count()) > 0,
			`/daily ${spec.name}: an expired shelf page still renders, not hidden`
		);
		// Opening a row lands on the same corpus renderer the library uses.
		await page.getByText('GitHub stars').first().click();
		await page.waitForSelector('#corpus-heading', { timeout: 10000 });
		await delay(600);
		check(
			(await page.getByText('The measurement').count()) > 0,
			`/daily ${spec.name}: opening a row renders the page body in the library pane`
		);
		await page.evaluate(() => window.scrollTo({ top: 0 }));
		await delay(300);
	} else {
		check(true, `/daily ${spec.name}: no shelf section yet (pre-change tree)`);
	}

	await page.screenshot({
		path: `${OUT}/${TAG}-daily-${spec.name}.png`,
		fullPage: true
	});
	await context.close();
}

async function main() {
	mkdirSync(OUT, { recursive: true });
	const vite = spawn('npx', ['vite', 'dev', '--port', String(PORT), '--strictPort'], {
		stdio: ['ignore', 'pipe', 'pipe']
	});
	try {
		await waitForServer(`http://localhost:${PORT}/`);
		const browser = await chromium.launch();
		for (const spec of VIEWPORTS) {
			console.log(`\n— ${spec.name}`);
			await driveShelf(browser, spec);
		}
		await browser.close();
	} finally {
		vite.kill('SIGTERM');
	}
	console.log(`\nshots → ${OUT}`);
	if (failures.length > 0) {
		console.log(`\n${failures.length} FAILED:`);
		for (const f of failures) console.log(`  ✗ ${f}`);
		process.exitCode = 1;
	} else {
		console.log('\nall checks held');
	}
}

main().catch((e) => {
	console.error(e);
	process.exitCode = 1;
});
