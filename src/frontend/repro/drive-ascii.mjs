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
const COOKIE_PATH = process.env.BRR_HOST_ROOT
	? `${process.env.BRR_HOST_ROOT}/.tmp/brnrd_session.cookie`
	: new URL('../../../.tmp/brnrd_session.cookie', import.meta.url).pathname;

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
	// Catches a camera render that loses its level/header row in either active
	// island mode or the dormant atlas mode.
	if (!/THE (SEA|ATLAS)/.test(text)) throw new Error('live board missing its camera header');
	// Catches the HOME fixture falling out of topology or out-of-frame bearings;
	// a quiet live account still has a real home, rather than a made-up status.
	if (!/(?:⌂ HOME|(?:←|→|↑|↓|↖|↗|↘|↙) HOME)/.test(text)) throw new Error('live board missing HOME');
	const hasActor = text.includes('@');
	// Catches an attested live resident disappearing from both the map and its
	// actor rows; dormant accounts are covered by the HOME assertion above.
	if (text.includes('CHARTS') && !hasActor) throw new Error('live board lost its resident actor');
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
	const seen = { strand: false, letter: false, inject: false, publish: false };
	for (let i = 0; i < 11; i++) {
		const text = await boardText(page);
		// Catches the dispatched child being omitted from the actor/control rows.
		if (/^a the-design-sweep\b/m.test(text)) seen.strand = true;
		// Catches pending portal counts failing to reach the HOME gate, weather,
		// pager, or actor row. Frame #84 supplies the real pending count.
		if (text.includes('◇×1')) seen.letter = true;
		// Catches frame #85's attested injected boundary losing its actor pulse.
		if (/^@ the-reference-journey.*✉>>>/m.test(text)) seen.inject = true;
		// Catches frame #87's publish boundary disappearing from the resident's
		// control row even when the forge and chart detail are outside the frame.
		if (/^@ the-reference-journey.*⌁ publish\b/m.test(text)) seen.publish = true;
		if (i === 2) await page.screenshot({ path: `${OUT}/demo-early.png`, fullPage: true });
		if (i === 5) await page.screenshot({ path: `${OUT}/demo-mid.png`, fullPage: true });
		if (i === 9) await page.screenshot({ path: `${OUT}/demo-late.png`, fullPage: true });
		await delay(3650);
	}
	const missing = Object.entries(seen)
		.filter(([, v]) => !v)
		.map(([k]) => k);
	if (missing.length) throw new Error(`demo replay never showed: ${missing.join(', ')}`);
	console.log('demo: strand ✓ letter ✓ inject ✓ publish ✓');
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
