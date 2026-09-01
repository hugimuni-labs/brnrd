// Drive /ascii and /daily at four widths and assert the room fits the
// reader: `document.documentElement.scrollWidth === clientWidth`, and no
// `.line` (the board's own rows) or `.legend` block pokes past its own
// container. Behavioural asserts first, screenshots second — a page can
// look busy and be the wrong page (the 2026-08-26 room lesson every repro
// script in this dir already carries).
//
// No backend, no account: the same `fixtures.mjs` route-mock every other
// driver in this dir reads, with one live run added so the board renders
// island-level content (actor row, corridors, a camp) rather than the
// empty-account atlas — the overflow this pins is about line *width*, and
// a dormant board's lines are too short to exercise it.
//
// Usage: node repro/drive-fits.mjs   (REPRO_OUT=/tmp/fits-drive by default,
// REPRO_TAG=before|after labels the screenshot files)

import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import { mkdirSync } from 'node:fs';
import * as fixtures from './fixtures.mjs';

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
	edge: {
		at: RUN_SEEN,
		kind: 'tool',
		detail: 'reading src/lib/Dashboard.svelte, a long path chosen on purpose'
	}
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

const PORT = Number(process.env.REPRO_PORT ?? 5197);
const OUT = process.env.REPRO_OUT ?? '/tmp/fits-drive';
const TAG = process.env.REPRO_TAG ?? 'after';

// The four widths the spec pins ("whatever you do must hold at 390, 768,
// 1024, 1440"). Heights are realistic device heights, not load-bearing —
// this repro tests horizontal fit.
const WIDTHS = [
	{ name: '390', width: 390, height: 844 },
	{ name: '768', width: 768, height: 1024 },
	{ name: '1024', width: 1024, height: 768 },
	{ name: '1440', width: 1440, height: 900 }
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

async function openPage(browser, viewport) {
	const context = await browser.newContext({
		viewport: { width: viewport.width, height: viewport.height },
		deviceScaleFactor: 1,
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

/** document-level fit + per-line/legend containment, evaluated in-page. */
async function measureFit(page) {
	return page.evaluate(() => {
		const de = document.documentElement;
		const lines = [...document.querySelectorAll('pre.board .line')];
		const board = document.querySelector('pre.board');
		const legend = document.querySelector('pre.legend');
		const boardRight = board ? board.getBoundingClientRect().right : null;
		const overLines = lines
			.map((el) => el.getBoundingClientRect())
			.filter((r) => boardRight !== null && r.right > boardRight + 1).length;
		const legendRect = legend ? legend.getBoundingClientRect() : null;
		const deckRight = legend?.closest('.deck')?.getBoundingClientRect().right ?? null;
		return {
			scrollW: de.scrollWidth,
			clientW: de.clientWidth,
			overLines,
			lineCount: lines.length,
			legendOverflows:
				legendRect !== null && deckRight !== null && legendRect.right > deckRight + 1,
			legendRight: legendRect ? Math.round(legendRect.right) : null,
			deckRight: deckRight !== null ? Math.round(deckRight) : null
		};
	});
}

async function driveRoute(browser, route, label) {
	const results = [];
	for (const vp of WIDTHS) {
		const page = await openPage(browser, vp);
		const url = `http://localhost:${PORT}${route}`;
		await page.goto(url, { waitUntil: 'networkidle' });
		await page.waitForSelector('pre.board', { timeout: 20000 }).catch(() => {});
		await delay(1500); // let the first poll compile a board
		const fit = await measureFit(page);
		results.push({ width: vp.name, ...fit });
		check(
			fit.scrollW <= fit.clientW,
			`${label} ${vp.name}: document fits (scrollWidth ${fit.scrollW} <= clientWidth ${fit.clientW})`
		);
		check(
			fit.overLines === 0,
			`${label} ${vp.name}: no .line exceeds its board (${fit.overLines}/${fit.lineCount} over)`
		);
		check(
			!fit.legendOverflows,
			`${label} ${vp.name}: legend stays inside its box (right ${fit.legendRight} vs deck ${fit.deckRight})`
		);
		if (vp.name === '390' || vp.name === '1440') {
			await page.screenshot({ path: `${OUT}/${TAG}-${label}-${vp.name}.png`, fullPage: true });
		}
		await page.context().close();
	}
	return results;
}

async function main() {
	mkdirSync(OUT, { recursive: true });
	const vite = spawn('npx', ['vite', 'dev', '--port', String(PORT), '--strictPort'], {
		cwd: new URL('..', import.meta.url).pathname,
		stdio: ['ignore', 'pipe', 'pipe']
	});
	try {
		await waitForServer(`http://localhost:${PORT}/`);
		const browser = await chromium.launch();
		console.log(`\n— /ascii (${TAG})`);
		const asciiResults = await driveRoute(browser, '/ascii', 'ascii');
		console.log(`\n— /daily (${TAG})`);
		const dailyResults = await driveRoute(browser, '/daily', 'daily');
		await browser.close();
		console.log('\n--- table ---');
		console.log(JSON.stringify({ ascii: asciiResults, daily: dailyResults }, null, 2));
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
