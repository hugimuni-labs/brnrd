// Drive /ascii — the reference camera — both lanes:
//   live: the real brnrd.dev wire (session cookie read from .tmp/, requests
//         re-fetched node-side and fulfilled into the page, so no cookie
//         domain gymnastics), proving the camera renders the actual account
//         state, sparse or busy, without fixtures;
//   demo: the shared /new replay, sampled across frames, proving the stage
//         choreography (strand rise, letter at the rack, injection pulse)
//         reaches the board.
// Behavioural asserts first, screenshots second — a board can look busy and
// say nothing (the 2026-08-26 room lesson).

import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import { readFileSync, mkdirSync } from 'node:fs';

const PORT = 5193;
const OUT = process.env.REPRO_OUT ?? '/tmp/ascii-drive';
const COOKIE_PATH = new URL('../../../.tmp/brnrd_session.cookie', import.meta.url).pathname;

function readCookie() {
	try {
		return readFileSync(COOKIE_PATH, 'utf8').trim();
	} catch {
		return null;
	}
}

async function waitForServer(url, tries = 60) {
	for (let i = 0; i < tries; i++) {
		try {
			const res = await fetch(url);
			if (res.ok) return;
		} catch {
			/* not up yet */
		}
		await delay(500);
	}
	throw new Error(`server never came up at ${url}`);
}

async function boardText(page) {
	return page.locator('pre.board').innerText();
}

async function driveLive(browser, cookie) {
	const page = await (
		await browser.newContext({ viewport: { width: 900, height: 1100 } })
	).newPage();
	await page.route('**/v1/dashboard/**', async (route) => {
		const url = new URL(route.request().url());
		const upstream = `https://brnrd.dev${url.pathname}${url.search}`;
		const res = await fetch(upstream, { headers: { Cookie: `brnrd_session=${cookie}` } });
		route.fulfill({ status: res.status, contentType: 'application/json', body: await res.text() });
	});
	await page.goto(`http://localhost:${PORT}/ascii`, { waitUntil: 'networkidle' });
	await delay(2500);
	const text = await boardText(page);
	if (!text.includes('THE SEA')) throw new Error('live board missing the sea header');
	if (!/╔═ .+ ═+╗/.test(text)) throw new Error('live board raised no island');
	const hasActor = text.includes('@');
	const quiet = text.includes('G ·');
	if (!hasActor && !quiet) throw new Error('live board neither shows an actor nor admits quiet');
	console.log(`live: island up · ${hasActor ? 'actor present' : 'dormant (honest)'}`);
	console.log('--- live board ---\n' + text + '\n------------------');
	await page.screenshot({ path: `${OUT}/live-desktop.png`, fullPage: true });
	const phone = await (
		await browser.newContext({ viewport: { width: 390, height: 844 } })
	).newPage();
	await phone.route('**/v1/dashboard/**', async (route) => {
		const url = new URL(route.request().url());
		const res = await fetch(`https://brnrd.dev${url.pathname}${url.search}`, {
			headers: { Cookie: `brnrd_session=${cookie}` }
		});
		route.fulfill({ status: res.status, contentType: 'application/json', body: await res.text() });
	});
	await phone.goto(`http://localhost:${PORT}/ascii`, { waitUntil: 'networkidle' });
	await delay(2500);
	await phone.screenshot({ path: `${OUT}/live-phone.png`, fullPage: true });
}

async function driveDemo(browser) {
	const page = await (
		await browser.newContext({ viewport: { width: 900, height: 1100 } })
	).newPage();
	await page.goto(`http://localhost:${PORT}/ascii?demo`, { waitUntil: 'networkidle' });
	// Sample across the replay; the stages must surface the ceremonies.
	const seen = { strand: false, letter: false, inject: false, watch: false };
	for (let i = 0; i < 11; i++) {
		const text = await boardText(page);
		if (/^ {3}[a-z] /m.test(text) || / [a-z] {2}(RIG|WATCH|FORGE|CHART|DESK|BAY)/.test(text))
			seen.strand = true;
		if (text.includes('◇×')) seen.letter = true;
		if (text.includes('✉>>>')) seen.inject = true;
		if (text.includes('WATCH')) seen.watch = true;
		if (i === 2) await page.screenshot({ path: `${OUT}/demo-early.png`, fullPage: true });
		if (i === 5) await page.screenshot({ path: `${OUT}/demo-mid.png`, fullPage: true });
		if (i === 9) await page.screenshot({ path: `${OUT}/demo-late.png`, fullPage: true });
		await delay(3650);
	}
	const missing = Object.entries(seen)
		.filter(([, v]) => !v)
		.map(([k]) => k);
	if (missing.length) throw new Error(`demo replay never showed: ${missing.join(', ')}`);
	console.log('demo: strand ✓ letter ✓ inject ✓ watch ✓');
}

mkdirSync(OUT, { recursive: true });
const vite = spawn('npx', ['vite', 'dev', '--port', String(PORT), '--strictPort'], {
	cwd: new URL('..', import.meta.url).pathname,
	stdio: 'ignore'
});
try {
	await waitForServer(`http://localhost:${PORT}/`);
	const browser = await chromium.launch();
	try {
		await driveDemo(browser);
		const cookie = readCookie();
		if (cookie) {
			await driveLive(browser, cookie);
		} else {
			console.log('live: no session cookie found — lane skipped (named, not silent)');
		}
	} finally {
		await browser.close();
	}
	console.log(`shots in ${OUT}`);
} finally {
	vite.kill();
}
