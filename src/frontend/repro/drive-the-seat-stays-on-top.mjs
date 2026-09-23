// Before/after for "the seat stays on top" (his screenshot 2026-09-22 20:58Z:
// with five strands live, the MACHINE block shows four strand rows in the
// amber frame, "+1 more picking", and the seat's own run is gone). Same
// fixture-mock posture as drive-overture.mjs — no backend, no account — with
// one seat and six strands live at once (more than PICKING_ROW_CAP=4), so the
// old cap's failure mode and the new pinned-seat fix are both visible in one
// frame.
//
// Usage: node repro/drive-the-seat-stays-on-top.mjs
//   REPRO_OUT=/tmp/seat-drive (default) REPRO_TAG=before|after — run once
//   against each tree state (`git checkout HEAD~1 -- <the changed files>`
//   for "before", restore for "after").

import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import { mkdirSync } from 'node:fs';
import * as fixtures from './fixtures.mjs';

const PORT = Number(process.env.REPRO_PORT ?? 5199);
const OUT = process.env.REPRO_OUT ?? '/tmp/seat-drive';
const TAG = process.env.REPRO_TAG ?? 'after';
const now = new Date().toISOString();

function liveRun(over) {
	return {
		id: over.run_id,
		kind: 'daemon',
		stream: `cloud:telegram:1:`,
		label: null,
		name: over.name ?? null,
		run_id: over.run_id,
		repo_label: 'hugimuni-labs/brnrd',
		started_at: over.started_at ?? now,
		last_seen: now,
		parent_run_id: over.parent_run_id ?? null,
		is_subspawn: !!over.parent_run_id,
		runner: over.runner ?? {
			name: 'claude-sonnet',
			shell: 'claude',
			core: 'sonnet',
			class: 'balanced'
		},
		phase: over.phase ?? 'working',
		card_text: over.card_text ?? null,
		card_updated_at: now,
		relics_counts: over.relics_counts ?? null,
		mood: over.mood ?? null,
		mood_glyph: over.mood_glyph ?? null,
		mood_frames: over.mood_frames ?? null,
		mood_rest: over.mood_rest ?? null,
		mood_pitch: over.mood_pitch ?? null,
		topics: [],
		stop_requested: false,
		lifecycle: over.lifecycle ?? null,
		await_until: over.await_until ?? null,
		room: over.room ?? null,
		edge: over.edge ?? null,
		portals: over.portals ?? null,
		daemon_stale: false
	};
}

const seat = liveRun({
	run_id: 'run-260922-2103-ascm',
	name: 'the-seat-stays-on-top',
	started_at: new Date(Date.now() - 41 * 60_000).toISOString(),
	runner: { name: 'claude-fable', shell: 'claude', core: 'fable', class: 'strong' },
	card_text: '## Now\nwatching six strands, staying itself'
});

const STRAND_NAMES = [
	'the-row-you-can-judge',
	'the-panel-third-pass',
	'the-ask-panel-two-doors',
	'the-fox-round',
	'the-account-bases',
	'the-heartbeat-that-starves'
];
const strands = STRAND_NAMES.map((name, index) =>
	liveRun({
		run_id: `run-260922-21${10 + index}-s${index}`,
		name,
		parent_run_id: seat.run_id,
		started_at: new Date(Date.now() - (30 - index * 4) * 60_000).toISOString(),
		runner: { name: 'claude-sonnet', shell: 'claude', core: 'sonnet', class: 'balanced' }
	})
);

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

const VIEWPORTS = [
	{ name: '1280x800', viewport: { width: 1280, height: 800 } },
	{ name: '390x844', viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true }
];

async function shoot(browser, spec) {
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
		if (url.pathname === '/v1/dashboard/live-runs') {
			// Seat last, deliberately: his live report had the seat's row
			// missing, and the old code drew `picking` in whatever order the
			// wire sent `runs` — no re-sort. Putting the seat first in the
			// fixture would hide that dependency; putting it last reproduces
			// it, and proves the fix does not depend on wire order either.
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({ ...fixtures.liveRuns, runs: [...strands, seat] })
			});
			return;
		}
		const body = fixtures.ROUTES[url.pathname];
		await route.fulfill({
			status: body ? 200 : 404,
			contentType: 'application/json',
			body: JSON.stringify(body ?? {})
		});
	});

	await page.goto(`http://localhost:${PORT}/`, { waitUntil: 'networkidle' });
	await page.waitForSelector('[aria-label="the machine"]', { timeout: 20000 });
	await delay(1200); // ignite transitions + glitch reveals settle
	await page.evaluate(() => window.scrollTo({ top: 0 }));
	await delay(200);

	await page.screenshot({ path: `${OUT}/${TAG}-machine-${spec.name}.png` });
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
			console.log(`— ${TAG} ${spec.name}`);
			await shoot(browser, spec);
		}
		await browser.close();
	} finally {
		vite.kill('SIGTERM');
	}
	console.log(`shots → ${OUT}`);
}

main().catch((e) => {
	console.error(e);
	process.exitCode = 1;
});
