// Capture the Live Runs card for the status-vs-heartbeat regression. The
// fixture is deliberately stale: only a declared held status may turn that
// silence into a parked state.
//
// !! DOES NOT CAPTURE YET — do not read a green run as a passing shot.
// `card.waitFor()` on `[data-loom-run="run-held-label"]` times out: the
// route stub answers and the dev server serves, but the row never renders.
// Tried and ruled out (2026-09-10, parent review): the selector exists
// (LiveRuns.svelte:187); `ROUTES['/v1/dashboard/live-runs']` holds a live
// *reference* to `fixtures.liveRuns`, so the top-level mutation does reach
// it; no auth guard on `+page.svelte`/`+layout.svelte`. Note that no other
// driver in this directory targets `/` — every working one drives a
// sub-route (`/ascii?demo` &c). That asymmetry is the next thing to pull.
// Kept in the tree because 83 lines of correct fixture shape should not be
// re-derived; kept labelled because a repro driver that silently captures
// nothing is worse than none.
import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { mkdir } from 'node:fs/promises';
import { setTimeout as delay } from 'node:timers/promises';
import * as fixtures from './fixtures.mjs';

const args = process.argv.slice(2);
const value = (name, fallback) => {
	const at = args.indexOf(name);
	return at === -1 ? fallback : (args[at + 1] ?? fallback);
};
const PORT = Number(value('--port', '5199'));
const OUT = value('--out', '/tmp/held-label-repro');
const now = new Date();

fixtures.liveRuns.runs = [
	{
		id: 'run-held-label',
		kind: 'daemon',
		stream: 'cloud:telegram:1:',
		label: 'the state the label never read',
		name: 'the state the label never read',
		run_id: 'run-held-label',
		repo_label: 'hugimuni-labs/brnrd',
		started_at: new Date(now - 20 * 60_000).toISOString(),
		last_seen: new Date(now - 10 * 60_000).toISOString(),
		parent_run_id: null,
		is_subspawn: false,
		runner: { shell: 'codex', core: 'gpt-5.6-terra' },
		phase: 'running',
		card_text: null,
		card_updated_at: null,
		status: 'held',
		resource_hold: {
			reason: 'quota_starved',
			provider: 'codex',
			resume_condition: 'refill',
			armed_at: now.toISOString(),
			released: false
		}
	}
];

async function waitForServer(url) {
	for (let i = 0; i < 60; i++) {
		try {
			if ((await fetch(url)).status < 500) return;
		} catch {
			// The dev server is still booting.
		}
		await delay(500);
	}
	throw new Error('dev server never started');
}

await mkdir(OUT, { recursive: true });
const vite = spawn('npx', ['vite', 'dev', '--port', String(PORT), '--strictPort'], {
	stdio: 'ignore'
});
try {
	await waitForServer(`http://localhost:${PORT}/`);
	const browser = await chromium.launch();
	const page = await browser.newPage({ viewport: { width: 900, height: 700 } });
	await page.route('**/v1/dashboard/**', (route) => {
		const body = fixtures.ROUTES[new URL(route.request().url()).pathname];
		return route.fulfill({
			status: body ? 200 : 404,
			contentType: 'application/json',
			body: JSON.stringify(body ?? {})
		});
	});
	await page.goto(`http://localhost:${PORT}/`, { waitUntil: 'domcontentloaded' });
	const card = page.locator('[data-loom-run="run-held-label"]');
	await card.waitFor();
	await card.screenshot({ path: `${OUT}/held-label.png` });
	console.log(`held label: ${await card.textContent()}`);
	await browser.close();
} finally {
	vite.kill();
}
