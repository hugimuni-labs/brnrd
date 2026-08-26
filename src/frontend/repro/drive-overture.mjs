// Drive the resident field's first-paint assembly (THE OVERTURE): a fresh
// page load with a resident + two limbs already in the snapshot, screenshot
// at intervals across the sequence (spine draws → docks light → cells
// glitch). Throwaway visual-verification harness, same fixture-mock posture
// as drive-field.mjs — no backend, no account.

import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';

const PORT = 5189;
const OUT = process.env.REPRO_OUT ?? '/tmp/field-overture';
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
			name: 'claude-fable',
			shell: 'claude',
			core: 'fable',
			class: 'strong'
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

const resident = liveRun({
	run_id: 'run-260825-2210-nvjn',
	name: 'the-field-takes-its-body',
	started_at: new Date(Date.now() - 22 * 60_000).toISOString(),
	mood: 'primed',
	mood_rest: 'b·_·d',
	mood_glyph: 'b·_·d',
	card_text: '## Plan\n- [x] orientation\n- [ ] the overture',
	room: { env: 'host', branch: 'brr/the-field-takes-its-body', dir: 'the shared checkout' },
	edge: {
		at: now,
		phase: 'PostToolUse',
		act: 'orient',
		tools: ['Read'],
		detail: 'design-resident-field.md'
	}
});
const strandFuel = liveRun({
	run_id: 'run-260825-2231-fu3l',
	name: 'the-fuel-that-knows-its-provider',
	parent_run_id: 'run-260825-2210-nvjn',
	started_at: new Date(Date.now() - 6 * 60_000).toISOString(),
	runner: { name: 'claude-sonnet', shell: 'claude', core: 'sonnet', class: 'balanced' },
	room: { env: 'worktree', branch: 'brr/the-fuel-that-knows-its-provider', dir: 'brr-wt-fu3l' }
});
const strandDeep = liveRun({
	run_id: 'run-260825-2240-d33p',
	name: 'the-trace-that-earns-its-glow',
	parent_run_id: 'run-260825-2210-nvjn',
	started_at: new Date(Date.now() - 2 * 60_000).toISOString(),
	runner: { name: 'claude-haiku', shell: 'claude', core: 'haiku', class: 'economy' }
});

const fixtures = await import('./fixtures.mjs');

async function waitForServer(url, tries = 60) {
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

async function main() {
	await import('node:fs/promises').then((fs) => fs.mkdir(OUT, { recursive: true }));
	const vite = spawn('npx', ['vite', 'dev', '--port', String(PORT), '--strictPort'], {
		stdio: ['ignore', 'pipe', 'pipe']
	});
	try {
		await waitForServer(`http://localhost:${PORT}/`);
		const browser = await chromium.launch();
		const context = await browser.newContext({
			viewport: { width: 390, height: 844 },
			deviceScaleFactor: 2,
			isMobile: true,
			hasTouch: true,
			reducedMotion: 'no-preference'
		});
		const page = await context.newPage();
		await page.route('**/v1/dashboard/**', async (route) => {
			const url = new URL(route.request().url());
			if (url.pathname === '/v1/dashboard/live-runs') {
				await route.fulfill({
					status: 200,
					contentType: 'application/json',
					body: JSON.stringify({
						...fixtures.liveRuns,
						runs: [resident, strandFuel, strandDeep]
					})
				});
				return;
			}
			const body = fixtures.ROUTES[url.pathname];
			if (body) {
				await route.fulfill({
					status: 200,
					contentType: 'application/json',
					body: JSON.stringify(body)
				});
			} else {
				await route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
			}
		});

		// Cold vite compiles this route on first visit — its latency would
		// swamp the ~3s overture window and make every mark measure the
		// bundler, not the animation. Warm it once, then reload (cached
		// modules) and time the marks from *that* navigation.
		await page.goto(`http://localhost:${PORT}/`, { waitUntil: 'networkidle' });
		await page.locator('[data-resident-field]').scrollIntoViewIfNeeded();

		const t0 = Date.now();
		await page.reload({ waitUntil: 'domcontentloaded' });
		const field = page.locator('[data-resident-field]');
		await field.scrollIntoViewIfNeeded();

		// The sequence budget for 2 limbs: spine 1600 + dock stagger (160*1 +
		// fade 500=660) + cell stagger (140*3 + glitch 320=740) ≈ 3000ms.
		// Sample across it densely enough to catch each phase.
		const marks = [
			1000, 1300, 1500, 1650, 1750, 1850, 1950, 2050, 2150, 2250, 2350, 2450, 2550, 2650, 2800,
			3000, 3300
		];
		for (const mark of marks) {
			const elapsed = Date.now() - t0;
			if (elapsed < mark) await delay(mark - elapsed);
			await field.screenshot({ path: `${OUT}/t${String(mark).padStart(4, '0')}.png` });
		}
		await browser.close();
		console.log(`shots in ${OUT}`);
	} finally {
		vite.kill('SIGTERM');
	}
}

main().catch((err) => {
	console.error(err);
	process.exit(1);
});
