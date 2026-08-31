// Drive `/daily` and `/` at both widths, on the same fixture mock
// drive.mjs/repro*.mjs use — no backend, no account.
//
// Behavioural asserts first, screenshots second (the 2026-08-26 room lesson):
// a page can look right and be the wrong page. What this pins:
//
//   1. `/daily` renders the main dashboard's own sections — it is the house,
//      not a second composition wearing its colours.
//   2. Nothing from the ditched composition survives on it (buoy strip,
//      islands, kb reef, the old bespoke header).
//   3. The live-runs slot on `/daily` is the map + the compacted bars, and on
//      `/` it is still `ResidentField` — the one seam, in both directions.
//   4. A press on a bar, and a press on `⤢ expand`, both open the full-screen
//      stage; `↙ collapse` (and Escape) put the reader back exactly where
//      they were, with the compact view intact.
//   5. Both of the above at 390x844 and 1440x900, because this pair of routes
//      has a history of width-specific regressions.
//
// Usage: node repro/drive-daily.mjs   (REPRO_OUT=/tmp/daily-drive by default,
// REPRO_ROUTES="/daily" to drive one route, REPRO_TAG=before to label files)

import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import { mkdirSync } from 'node:fs';
import * as fixtures from './fixtures.mjs';

// The shared fixture serves an empty room (`runs: []`), which is the right
// default for the sticky-stack drivers and useless here — the bars are half
// of what this route is. Overridden locally rather than in fixtures.mjs so
// drive.mjs/repro*.mjs keep their behaviour byte-for-byte, as that file's own
// note asks. Two runs, one nested under the other, so the bar list exercises
// its `--nest` indent too.
const RUN_STARTED = '2026-08-12T22:40:00Z';
const RUN_SEEN = '2026-08-12T22:49:55Z';
const liveRun = (id, parent) => ({
	id,
	run_id: id,
	parent_run_id: parent,
	is_subspawn: parent !== null,
	name: id,
	label: '',
	stream: 'telegram',
	kind: 'daemon',
	repo_label: 'hugimuni-labs/brnrd',
	started_at: RUN_STARTED,
	last_seen: RUN_SEEN,
	runner: { shell: 'claude', core: 'sonnet' },
	phase: 'running',
	card_text: null,
	card_updated_at: null,
	course: { done: 1, total: 3, current: 'build' },
	portals: { pending: 2, oldest_at: null },
	room: { env: 'worktree', branch: `brr/${id}`, dir: id },
	edge: { at: RUN_SEEN, kind: 'tool', detail: 'reading src/lib/Dashboard.svelte' }
});
const ROUTE_BODIES = {
	...fixtures.ROUTES,
	'/v1/dashboard/live-runs': {
		...fixtures.liveRuns,
		runs: [
			liveRun('run-260831-1040-zkt1', null),
			liveRun('run-260831-1102-str1', 'run-260831-1040-zkt1')
		]
	}
};

const PORT = Number(process.env.REPRO_PORT ?? 5196);
const OUT = process.env.REPRO_OUT ?? '/tmp/daily-drive';
const TAG = process.env.REPRO_TAG ?? 'after';
const ROUTES = (process.env.REPRO_ROUTES ?? '/,/daily').split(',');

// The compact view's whole promise: the map is a glance, and the page keeps
// going under it. Half the viewport is the ceiling — past that a phone reader
// scrolls a screen before reaching the warp.
const GLANCE_SHARE = 0.68;

const VIEWPORTS = [
	{ name: 'phone', viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true },
	{ name: 'desktop', viewport: { width: 1440, height: 900 } }
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

async function openPage(browser, spec) {
	const context = await browser.newContext({
		viewport: spec.viewport,
		deviceScaleFactor: 2,
		isMobile: spec.isMobile ?? false,
		hasTouch: spec.hasTouch ?? false,
		reducedMotion: 'reduce' // skips the boot curtain — see routes/+layout.svelte
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
	return page;
}

/** The sections that make this the main dashboard and not something else. */
async function assertWearsTheHouse(page, where) {
	check(await page.getByText('resident dashboard').first().isVisible(), `${where}: the masthead`);
	check(await page.locator('#warp-heading').isVisible(), `${where}: the warp section`);
	check(await page.locator('#cloth-heading').isVisible(), `${where}: the cloth section`);
	check(await page.locator('#corpus-heading').isVisible(), `${where}: the library section`);
	check(await page.locator('#billing-heading').isVisible(), `${where}: the account section`);
}

/** Nothing from the first `/daily` may survive anywhere. */
async function assertOldCompositionGone(page, where) {
	const html = await page.content();
	// NB: not "the water line" — the ascii camera draws that phrase itself as
	// the cloth boundary on the board (`asciiCamera.ts`). The dead strings are
	// the ones the ditched *composition* owned.
	for (const dead of ['ready warp items', 'kb reef', 'above water', 'dashboard ↗']) {
		check(!html.includes(dead), `${where}: "${dead}" is gone`);
	}
	check(
		(await page.locator('.buoy').count()) === 0 && (await page.locator('.island').count()) === 0,
		`${where}: no buoys, no islands`
	);
}

async function driveDaily(page, spec) {
	const shot = (name) => page.screenshot({ path: `${OUT}/${TAG}-daily-${spec.name}-${name}.png` });
	await page.goto(`http://localhost:${PORT}/daily`, { waitUntil: 'networkidle' });
	await page.waitForSelector('#warp-heading', { timeout: 20000 });
	await delay(1200); // let the field's first poll compile a board

	await assertWearsTheHouse(page, `/daily ${spec.name}`);
	await assertOldCompositionGone(page, `/daily ${spec.name}`);

	const scene = page.locator('section[aria-label="the room, live"]');
	check(await scene.isVisible(), `/daily ${spec.name}: the live slot is the room`);
	check(
		(await page.locator('[aria-label="the resident field"]').count()) === 0 ||
			(await page.locator('section[aria-label="the room, live"] pre.board').count()) > 0,
		`/daily ${spec.name}: the ascii board renders inline`
	);
	const bars = scene.locator('button.live-bar');
	check((await bars.count()) > 0, `/daily ${spec.name}: compacted run bars present`);
	// The inline scene must leave room for the page under it — the whole
	// reason `mapRows` reads the viewport instead of standing at 22 rows.
	// Measured on the frame, because what costs the reader their page is the
	// whole box: `renderWorld` appends control rows (actor bearings, CHARTS,
	// the cloth selvage) *below* the `rows`-tall board, so the painted height
	// is never just `rows × line-height`.
	const geom = await page.evaluate(() => {
		const sec = document.querySelector('section[aria-label="the room, live"]');
		const board = sec?.querySelector('pre.board');
		const frame = sec?.querySelector('.field-frame');
		if (!board || !frame) return null;
		const lh = parseFloat(getComputedStyle(board).lineHeight);
		return {
			lines: board.textContent.split('\n').length,
			lineHeight: lh,
			boardH: Math.round(board.getBoundingClientRect().height),
			frameH: Math.round(frame.getBoundingClientRect().height)
		};
	});
	console.log(`    geom: ${JSON.stringify(geom)}`);
	const sceneBox = { height: geom?.frameH ?? -1 };
	check(
		geom !== null && geom.frameH < spec.viewport.height * GLANCE_SHARE,
		`/daily ${spec.name}: the inline map is a glance (${sceneBox.height}px of ${spec.viewport.height}, budget ${Math.round(spec.viewport.height * GLANCE_SHARE)})`
	);
	await shot('1-collapsed');

	// Press a bar → the stage.
	const scrollY = await page.evaluate(() => window.scrollY);
	await bars.first().click();
	await page.waitForSelector('div[role="dialog"][aria-label="the room, in characters"]', {
		timeout: 10000
	});
	await delay(1200);
	const stage = page.locator('div[role="dialog"][aria-label="the room, in characters"]');
	check(await stage.isVisible(), `/daily ${spec.name}: a bar press opens the stage`);
	const stageBox = await stage.locator('pre.board').boundingBox();
	check(
		stageBox !== null && stageBox.height > (geom?.boardH ?? 0),
		`/daily ${spec.name}: the stage is taller than the glance (${Math.round(stageBox?.height ?? -1)}px)`
	);
	check(
		(await page.locator('section[aria-label="the room, live"]').count()) === 0,
		`/daily ${spec.name}: the inline field stands down while the stage is up`
	);
	const collapse = stage.getByRole('button', { name: '↙ collapse' });
	check(await collapse.isVisible(), `/daily ${spec.name}: the collapse control says "collapse"`);
	await shot('2-expanded');

	// Collapse → back exactly where we were.
	await collapse.click();
	await delay(600);
	check(
		(await page.locator('div[role="dialog"]').count()) === 0,
		`/daily ${spec.name}: collapse closes the stage`
	);
	check(
		await page.locator('section[aria-label="the room, live"]').isVisible(),
		`/daily ${spec.name}: the compact view is back`
	);
	check(
		Math.abs((await page.evaluate(() => window.scrollY)) - scrollY) < 4,
		`/daily ${spec.name}: the reader keeps their scroll position`
	);

	// The explicit expand control, and Escape as the other way back.
	await page.getByRole('button', { name: 'expand the map full screen' }).click();
	await page.waitForSelector('div[role="dialog"][aria-label="the room, in characters"]', {
		timeout: 10000
	});
	check(true, `/daily ${spec.name}: "⤢ expand" opens the stage too`);
	await page.keyboard.press('Escape');
	await delay(400);
	check(
		(await page.locator('div[role="dialog"]').count()) === 0,
		`/daily ${spec.name}: Escape collapses it`
	);
	await shot('3-recollapsed');
}

async function driveHome(page, spec) {
	const shot = (name) => page.screenshot({ path: `${OUT}/${TAG}-home-${spec.name}-${name}.png` });
	await page.goto(`http://localhost:${PORT}/`, { waitUntil: 'networkidle' });
	await page.waitForSelector('#warp-heading', { timeout: 20000 });
	await delay(1200);
	await assertWearsTheHouse(page, `/ ${spec.name}`);
	check(
		(await page.locator('section[aria-label="the room, live"]').count()) === 0,
		`/ ${spec.name}: no map in the live slot — the seam only moved on /daily`
	);
	check(
		(await page.locator('pre.board').count()) === 0,
		`/ ${spec.name}: no ascii board anywhere on the home page`
	);
	await shot('1-top');
	await page.evaluate(() => window.scrollTo({ top: 1400 }));
	await delay(700);
	await shot('2-scrolled');
	await page.evaluate(() => window.scrollTo({ top: 0 }));
	await delay(500);
	// Full-page capture is the side-by-side that matters for the regression.
	await page.screenshot({ path: `${OUT}/${TAG}-home-${spec.name}-3-full.png`, fullPage: true });
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
			console.log(`\n— ${spec.name} ${spec.viewport.width}x${spec.viewport.height}`);
			if (ROUTES.includes('/')) {
				const page = await openPage(browser, spec);
				await driveHome(page, spec);
				await page.context().close();
			}
			if (ROUTES.includes('/daily')) {
				const page = await openPage(browser, spec);
				await driveDaily(page, spec);
				await page.context().close();
			}
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
