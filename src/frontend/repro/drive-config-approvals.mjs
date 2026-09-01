// Drives / at 390px against the config-approvals block that replaced the
// old mixed PR-review/config "needs you" strip (2026-09-01: the PR-review
// half retired — GitHub already lists open PRs — and read poorly on a
// phone in the first place). Two states, both load-bearing:
//
//   empty   — no config-change request pending, no fetch error: the block
//             must be entirely absent (no panel, no "nothing needs you"
//             sentence, no stray gap in "the warp · intent" section).
//   pending — one request waiting: the block renders, labeled "config
//             approvals" (not "needs you" — that name described a mixed
//             bag that no longer exists), the row's key/value/repo legible
//             at 390px.
//
// Same spawn-own-vite-dev-server + fixtures.mjs route-mock pattern as
// drive-fits.mjs; no backend/account needed.
//
// Usage: node repro/drive-config-approvals.mjs   (REPRO_OUT=/tmp/config-approvals-drive)

import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import { mkdirSync } from 'node:fs';
import * as fixtures from './fixtures.mjs';

const PORT = Number(process.env.REPRO_PORT ?? 5198);
const OUT = process.env.REPRO_OUT ?? '/tmp/config-approvals-drive';

const PENDING_REQUEST = {
	id: 'cfg-1',
	repo_label: 'hugimuni-labs/brnrd',
	config_key: 'runner.shell',
	current_value: 'claude',
	requested_value: 'codex',
	reason: 'economy',
	created_at: '2026-09-01T10:00:00Z',
	expires_at: null,
	approve_url: 'https://example.test/config/cfg-1'
};

const STATES = {
	empty: { ...fixtures.ROUTES, '/v1/dashboard/config-requests': { requests: [] } },
	pending: {
		...fixtures.ROUTES,
		'/v1/dashboard/config-requests': { requests: [PENDING_REQUEST] }
	}
};

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

async function openPage(browser, routes) {
	const context = await browser.newContext({
		viewport: { width: 390, height: 844 },
		deviceScaleFactor: 1,
		reducedMotion: 'reduce' // skips the boot curtain — see routes/+layout.svelte
	});
	const page = await context.newPage();
	await page.route('**/v1/dashboard/**', async (route) => {
		const url = new URL(route.request().url());
		const body = routes[url.pathname];
		await route.fulfill({
			status: body ? 200 : 404,
			contentType: 'application/json',
			body: JSON.stringify(body ?? {})
		});
	});
	return page;
}

async function driveState(browser, label, routes) {
	const page = await openPage(browser, routes);
	await page.goto(`http://localhost:${PORT}/`, { waitUntil: 'networkidle' });
	await page.waitForSelector('#warp-heading', { timeout: 20000 }).catch(() => {});
	await delay(1500); // let the first poll settle authState + the warp section

	const warpSection = await page.evaluate(() => {
		const heading = document.getElementById('warp-heading');
		const section = heading?.closest('section');
		return section ? section.textContent : '';
	});

	if (label === 'empty') {
		check(
			!/config approvals/i.test(warpSection ?? ''),
			'empty: no "config approvals" block rendered'
		);
		check(!/needs you/i.test(warpSection ?? ''), 'empty: no "needs you" wording remains anywhere');
	} else {
		check(/config approvals/i.test(warpSection ?? ''), 'pending: "config approvals" block renders');
		check(
			/runner\.shell/.test(warpSection ?? ''),
			'pending: the request row is legible (config_key present)'
		);
	}

	await page.screenshot({ path: `${OUT}/${label}-390.png`, fullPage: true });
	await page.context().close();
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
		console.log('\n— config approvals: empty');
		await driveState(browser, 'empty', STATES.empty);
		console.log('\n— config approvals: pending');
		await driveState(browser, 'pending', STATES.pending);
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
