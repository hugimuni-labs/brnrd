// Drive the resident field with a living fixture: resident + strands on
// real traces, then mutate the snapshot between polls to catch the packet
// ceremonies mid-flight (spawn outward, boundary flash, portal inject).
// Same fixture-mock posture as drive.mjs — no backend, no account.

import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import * as fixtures from './fixtures.mjs';

const PORT = 5187;
const OUT = process.env.REPRO_OUT ?? '/tmp/field-drive';
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

const resident = (edge, portals) =>
	liveRun({
		portals,
		run_id: 'run-260825-2210-nvjn',
		name: 'the-field-takes-its-body',
		started_at: new Date(Date.now() - 22 * 60_000).toISOString(),
		mood: 'primed',
		mood_rest: 'b·_·d',
		mood_glyph: 'b·_·d',
		mood_frames: [['b·_·d', 'bo_od', 'b·_·d']],
		mood_pitch: 0.55,
		card_text:
			'## Plan\n- [x] orientation\n- [x] strand out\n- [ ] the field itself\n- [ ] ceremony\n- [ ] PR',
		room: { env: 'host', branch: 'brr/the-field-takes-its-body', dir: 'the shared checkout' },
		edge
	});

const strandFuel = liveRun({
	run_id: 'run-260825-2231-fu3l',
	name: 'the-fuel-that-knows-its-provider',
	parent_run_id: 'run-260825-2210-nvjn',
	started_at: new Date(Date.now() - 6 * 60_000).toISOString(),
	runner: { name: 'claude-sonnet', shell: 'claude', core: 'sonnet', class: 'balanced' },
	room: { env: 'worktree', branch: 'brr/the-fuel-that-knows-its-provider', dir: 'brr-wt-fu3l' },
	edge: {
		at: '2026-08-25T22:31:11Z',
		phase: 'PostToolUse',
		act: 'orient',
		tools: ['Read'],
		detail: 'Read quota.ts',
		dir: 'src/frontend/src/lib',
		out_bytes: 4210,
		injected: false
	}
});

const strandDeep = liveRun({
	run_id: 'run-260825-2240-d33p',
	name: 'the-trace-that-earns-its-glow',
	parent_run_id: 'run-260825-2210-nvjn',
	started_at: new Date(Date.now() - 2 * 60_000).toISOString(),
	runner: { name: 'claude-haiku', shell: 'claude', core: 'haiku', class: 'economy' },
	lifecycle: 'awaiting',
	await_until: new Date(Date.now() + 20 * 60_000).toISOString(),
	room: { env: 'worktree', branch: 'brr/the-trace-that-earns-its-glow', dir: 'brr-wt-d33p' },
	edge: {
		at: '2026-08-25T22:40:02Z',
		phase: 'PostToolUse',
		act: 'wait',
		tools: ['Bash'],
		detail: 'brnrd await --timeout 30m',
		out_bytes: 88,
		injected: false
	}
});

// The snapshot evolves per poll-phase:
// phase 0 — resident + fuel strand, quiet.
// phase 1 — resident crosses a mutate boundary (local flash).
// phase 2 — a second strand spawns (packet outward + birth).
// phase 3 — a message arrives at the door (◈ drop, resting breath).
// phase 4 — the world folds in (inject: the read is attested, marker ends).
let phase = 0;
function snapshot() {
	const edges = [
		{
			at: '2026-08-25T22:30:00Z',
			phase: 'PostToolUse',
			act: 'probe',
			tools: ['Bash'],
			detail: 'git status --short',
			out_bytes: 19,
			injected: false
		},
		{
			at: '2026-08-25T22:30:40Z',
			phase: 'PostToolUse',
			act: 'mutate',
			tools: ['Edit'],
			detail: 'Edit ResidentField.svelte',
			out_bytes: 812,
			injected: false
		},
		{
			at: '2026-08-25T22:30:40Z',
			phase: 'PostToolUse',
			act: 'mutate',
			tools: ['Edit'],
			detail: 'Edit ResidentField.svelte',
			out_bytes: 812,
			injected: false
		},
		{
			at: '2026-08-25T22:30:40Z',
			phase: 'PostToolUse',
			act: 'mutate',
			tools: ['Edit'],
			detail: 'Edit ResidentField.svelte',
			out_bytes: 812,
			injected: false
		},
		{
			at: '2026-08-25T22:31:30Z',
			phase: 'PostToolUse',
			act: 'orient',
			tools: ['Bash'],
			detail: 'brnrd do',
			out_bytes: 402,
			injected: true
		}
	];
	const portals = [
		null,
		{ pending: 0, oldest_at: null },
		{ pending: 0, oldest_at: null },
		{ pending: 1, oldest_at: '2026-08-25T22:31:00Z' },
		{ pending: 0, oldest_at: null }
	];
	const runs = [resident(edges[Math.min(phase, 4)], portals[Math.min(phase, 4)]), strandFuel];
	if (phase >= 2) runs.push(strandDeep);
	return { ...fixtures.liveRuns, runs };
}

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
					body: JSON.stringify(snapshot())
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

		await page.goto(`http://localhost:${PORT}/`, { waitUntil: 'networkidle' });
		await delay(2500);
		const field = page.locator('[data-resident-field]');
		await field.scrollIntoViewIfNeeded();
		await delay(1200);
		await field.screenshot({ path: `${OUT}/0-quiet.png` });

		phase = 1; // boundary → local flash + slow edge re-reveal
		await delay(2600);
		await field.screenshot({ path: `${OUT}/1-boundary-flash.png` });

		phase = 2; // spawn → packet outward + limb birth
		await delay(2300);
		await field.screenshot({ path: `${OUT}/2-spawn-packet.png` });
		await delay(1500);
		await field.screenshot({ path: `${OUT}/2b-spawn-settled.png` });

		phase = 3; // message arrives → ◈ drop, then the resting breath
		await delay(2600);
		await field.screenshot({ path: `${OUT}/3-message-drop.png` });
		await delay(2600);
		await field.screenshot({ path: `${OUT}/3b-message-resting.png` });

		phase = 4; // inject → the read attested; the marker ends
		await delay(2600);
		await field.screenshot({ path: `${OUT}/3c-inject-read.png` });

		// Press the resident cell — the overlay must answer.
		await page.locator('[data-field-cell="run-260825-2210-nvjn"]').click();
		await delay(900);
		await page.screenshot({ path: `${OUT}/4-overlay.png` });

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
