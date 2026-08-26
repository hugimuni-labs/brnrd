// Drive the axonometric room (/new) with a living fixture: assemble, then
// mutate the snapshot between polls to catch every ceremony mid-flight —
// spawn rise, boundary flash, message drop at the gate, inject transit,
// return sink. Shots at phone size and social-preview size, because the
// round's acceptance is judged at both (the 2026-08-26 brief).

import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';

const PORT = 5191;
const OUT = process.env.REPRO_OUT ?? '/tmp/room-drive';
const now = new Date().toISOString();

function liveRun(over) {
	return {
		id: over.run_id,
		kind: 'daemon',
		stream: 'cloud:telegram:1:',
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
		phase: over.phase ?? 'running',
		card_text: over.card_text ?? null,
		card_updated_at: now,
		relics_counts: null,
		mood: over.mood ?? null,
		mood_glyph: over.mood ? 'b·_·d' : null,
		mood_rest: over.mood ? 'b·_·d' : null,
		mood_frames: over.mood ? [['b·_·d', 'bo_od', 'b·_·d']] : null,
		mood_pitch: over.mood ? 0.55 : null,
		topics: [],
		stop_requested: false,
		lifecycle: over.lifecycle ?? null,
		await_until: over.await_until ?? null,
		room: over.room ?? null,
		edge: over.edge ?? null,
		portals: over.portals ?? { pending: 0, oldest_at: null },
		daemon_stale: false
	};
}

const resident = (edge, portals) =>
	liveRun({
		run_id: 'run-260826-1049-f67f',
		name: 'the-axonometric-room',
		started_at: new Date(Date.now() - 40 * 60_000).toISOString(),
		card_text: '## Plan\n- [x] orient\n- [x] geometry\n- [ ] the room\n- [ ] PR',
		mood: 'primed',
		room: { env: 'host', branch: 'brr/the-operational-diorama', dir: null },
		edge,
		portals
	});

const strandA = (edge) =>
	liveRun({
		run_id: 'run-260826-1102-s0na',
		name: 'the-lane-that-earns-its-cable',
		parent_run_id: 'run-260826-1049-f67f',
		started_at: new Date(Date.now() - 9 * 60_000).toISOString(),
		runner: { name: 'claude-sonnet', shell: 'claude', core: 'sonnet', class: 'balanced' },
		room: { env: 'worktree', branch: 'brr/the-lane', dir: 'brr-wt-s0na' },
		edge: edge ?? {
			at: '2026-08-26T11:02:30Z',
			phase: 'PostToolUse',
			act: 'probe',
			tools: ['Bash'],
			detail: 'git status --short',
			out_bytes: 19,
			injected: false
		}
	});

const strandB = liveRun({
	run_id: 'run-260826-1107-h4ik',
	name: 'the-quiet-vigil',
	parent_run_id: 'run-260826-1049-f67f',
	started_at: new Date(Date.now() - 3 * 60_000).toISOString(),
	runner: { name: 'claude-haiku', shell: 'claude', core: 'haiku', class: 'economy' },
	lifecycle: 'awaiting',
	await_until: new Date(Date.now() + 25 * 60_000).toISOString(),
	room: { env: 'worktree', branch: 'brr/the-vigil', dir: 'brr-wt-h4ik' },
	edge: {
		at: '2026-08-26T11:07:40Z',
		phase: 'PostToolUse',
		act: 'wait',
		tools: ['Bash'],
		detail: 'brnrd await --timeout 30m',
		out_bytes: 88,
		injected: false
	}
});

// phase 0 — resident + one strand, quiet.
// phase 1 — resident crosses a mutate boundary (top-face flash + beacon).
// phase 2 — a second strand spawns (packet outward, block rises).
// phase 3 — a message rests at the gate (◈ drop).
// phase 4 — inject (gate-feed transit) and the message is read.
// phase 5 — the first strand returns (sink + packet home).
let phase = 0;
function snapshot() {
	const edges = [
		{ at: 'T0', act: 'orient', detail: 'Read design-resident-field.md' },
		{ at: 'T1', act: 'mutate', detail: 'Edit +page.svelte' },
		{ at: 'T1', act: 'mutate', detail: 'Edit +page.svelte' },
		{ at: 'T1', act: 'mutate', detail: 'Edit +page.svelte' },
		{ at: 'T2', act: 'orient', detail: 'brnrd do', injected: true },
		{ at: 'T2', act: 'orient', detail: 'brnrd do' }
	][Math.min(phase, 5)];
	const edge = {
		at: edges.at,
		phase: 'PostToolUse',
		act: edges.act,
		tools: ['Bash'],
		detail: edges.detail,
		out_bytes: 402,
		injected: !!edges.injected,
		dir: '.'
	};
	const portals = phase === 3 ? { pending: 1, oldest_at: now } : { pending: 0, oldest_at: null };
	const runs = [resident(edge, portals), strandA()];
	if (phase >= 2 && phase < 5) runs.push(strandB);
	return {
		generated_at: now,
		runs,
		stale: false,
		reported_at: now,
		spawn_max_concurrent: 5,
		daemon_mood: null
	};
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

async function drive(context, dir, viewportLabel, body = 'automaton') {
	const tag = `${body}-${viewportLabel}`;
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
		await route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
	});

	phase = 0;
	await page.goto(`http://localhost:${PORT}/new?body=${body}`, { waitUntil: 'networkidle' });
	await delay(3600); // overture completes
	await page.screenshot({ path: `${dir}/0-quiet-${tag}.png` });

	phase = 1; // boundary flash — shot early enough to catch the 1.6s pulse
	await delay(2450);
	await page.screenshot({ path: `${dir}/1-boundary-${tag}.png` });
	await delay(400);

	phase = 2; // spawn — packet outward, block rises
	await delay(2300);
	// Behavioural probe, not a screenshot: the packet must actually MOVE.
	// (The SMIL cut of this rendered every packet frozen at its endpoint —
	// begin="0s" resolves against the document timeline — and the stills
	// looked fine because the drive fires seconds after load. Sample the
	// computed offset-distance twice; a still packet fails loudly here.)
	const probe = async () =>
		page.evaluate(() => {
			const el = document.querySelector('.pkt');
			return el ? getComputedStyle(el).offsetDistance : null;
		});
	const d1 = await probe();
	await delay(400);
	const d2 = await probe();
	if (d1 !== null && d1 === d2) {
		throw new Error(`packet did not move: offset-distance stuck at ${d1}`);
	}
	console.log(`packet motion probe (${tag}): ${d1} -> ${d2}`);
	await page.screenshot({ path: `${dir}/2-spawn-${tag}.png` });
	await delay(1600);
	await page.screenshot({ path: `${dir}/2b-spawn-settled-${tag}.png` });

	phase = 3; // ◈ rests at the gate
	await delay(2800);
	await page.screenshot({ path: `${dir}/3-message-${tag}.png` });

	phase = 4; // inject — gate-feed transit
	await delay(2400);
	await page.screenshot({ path: `${dir}/4-inject-${tag}.png` });

	phase = 5; // return — sink + packet home
	await delay(2400);
	await page.screenshot({ path: `${dir}/5-return-${tag}.png` });
	await delay(1600);
	await page.screenshot({ path: `${dir}/5b-settled-${tag}.png` });

	await page.close();
}

async function main() {
	await import('node:fs/promises').then((fs) => fs.mkdir(OUT, { recursive: true }));
	const vite = spawn('npx', ['vite', 'dev', '--port', String(PORT), '--strictPort'], {
		stdio: ['ignore', 'pipe', 'pipe']
	});
	try {
		await waitForServer(`http://localhost:${PORT}/`);
		const browser = await chromium.launch();

		// Phone, the primary judge.
		const phone = await browser.newContext({
			viewport: { width: 390, height: 844 },
			deviceScaleFactor: 2,
			isMobile: true,
			hasTouch: true,
			reducedMotion: 'no-preference'
		});
		await drive(phone, OUT, 'phone', 'stele');
		await phone.close();

		// Social preview, the other acceptance frame.
		const social = await browser.newContext({
			viewport: { width: 1200, height: 675 },
			deviceScaleFactor: 2,
			reducedMotion: 'no-preference'
		});
		await drive(social, OUT, 'social', 'stele');
		await social.close();

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
