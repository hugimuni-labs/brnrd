// Drives the SpoolRack's sticky/riding row with a live run that has
// reported an *observed* model distinct from what was *requested* —
// the case no real account can produce yet (the backend piece
// — `presence.heartbeat`'s `runner_model_observed`, `cloud_publisher
// ._runner_payload`'s `model_observed` — ships in this same change, so no
// deployed daemon has published it). Fixture-driven, not the live probe:
// this is proof the rendering is wired correctly given the data, not proof
// the data exists in production yet. See the report for that distinction.
//
// Usage: node repro/drive-observed-model.mjs [--out DIR] [--port N]
import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import { mkdir } from 'node:fs/promises';
import * as fixtures from './fixtures.mjs';

const args = process.argv.slice(2);
const arg = (f, d = null) => {
	const i = args.indexOf(f);
	return i === -1 || i + 1 >= args.length ? d : args[i + 1];
};
const OUT = arg('--out', '/tmp/observed-model-drive');
const PORT = Number(arg('--port', '5198'));

const CONVERSATION_KEY = 'telegram:155783668:';

const ROUTES = { ...fixtures.buildRoutes(fixtures.DEFAULT_SCALE) };
ROUTES['/v1/dashboard/runners'] = {
	...ROUTES['/v1/dashboard/runners'],
	profiles: [
		{
			name: 'codex',
			shell: 'codex',
			model: 'default',
			class: 'balanced',
			cost_rank: 25,
			quota_source: 'codex-local',
			availability: 'available',
			available: true,
			selected: false
		},
		{
			name: 'codex-full',
			shell: 'codex',
			model: 'gpt-5.6-sol',
			class: 'strong',
			cost_rank: 45,
			quota_source: 'codex-local',
			availability: 'available',
			available: true
		}
	],
	default: 'codex',
	// #932's live claim: this thread's wakes ride `codex` (unpinned) until
	// released — the exact "unpinned codex reads only its declared class"
	// case named in the original task, now with a live run behind it.
	sticky: {
		profile: 'codex',
		persistent: true,
		claimed_at: fixtures.now,
		correspondent_key: CONVERSATION_KEY,
		conversation_key: CONVERSATION_KEY
	}
};
ROUTES['/v1/dashboard/live-runs'] = {
	generated_at: fixtures.now,
	reported_at: fixtures.now,
	stale: false,
	spawn_max_concurrent: null,
	daemon_mood: null,
	runs: [
		{
			id: 'presence-1',
			kind: 'daemon',
			stream: CONVERSATION_KEY,
			label: 'riding the telegram thread',
			name: 'the resident',
			run_id: 'run-observed-demo',
			repo_label: 'hugimuni-labs/brnrd',
			started_at: fixtures.now,
			last_seen: fixtures.now,
			parent_run_id: null,
			is_subspawn: false,
			// requested = codex/unpinned/balanced (the sticky's own pin, above);
			// observed = what this live run's own telemetry actually reports.
			// Two fields, never merged into one guess.
			runner: {
				name: 'codex',
				shell: 'codex',
				core: 'default',
				class: 'balanced',
				model_observed: 'astra'
			},
			phase: 'running',
			card_text: null,
			card_updated_at: null
		}
	]
};

async function waitForServer(url, tries = 90) {
	for (let i = 0; i < tries; i++) {
		try {
			const res = await fetch(url);
			if (res.ok || res.status === 404) return;
		} catch {
			/* not up */
		}
		await delay(500);
	}
	throw new Error(`dev server never came up at ${url}`);
}

const WIDTHS = [
	{ name: 'mobile', width: 390, height: 844 },
	{ name: 'desktop', width: 1440, height: 900 }
];

async function main() {
	await mkdir(OUT, { recursive: true });
	const vite = spawn('npx', ['vite', 'dev', '--port', String(PORT), '--strictPort'], {
		stdio: ['ignore', 'pipe', 'pipe']
	});
	vite.stdout.on('data', () => {});
	vite.stderr.on('data', () => {});
	try {
		await waitForServer(`http://localhost:${PORT}/`);
		const browser = await chromium.launch();
		for (const vp of WIDTHS) {
			const ctx = await browser.newContext({ viewport: { width: vp.width, height: vp.height } });
			const page = await ctx.newPage();
			await page.route('**/v1/dashboard/**', async (route) => {
				const body = ROUTES[new URL(route.request().url()).pathname];
				await route.fulfill({
					status: body ? 200 : 404,
					contentType: 'application/json',
					body: JSON.stringify(body ?? {})
				});
			});
			await page.goto(`http://localhost:${PORT}/`, { waitUntil: 'networkidle' });
			await page.waitForSelector('[data-measure="fuel"]', { timeout: 15000 });
			await delay(600);
			// Press the codex row (second provider row here) to open its cores.
			const rows = await page.$$('.fuel-provider-row');
			const codexRowIndex = rows.length > 1 ? 1 : 0;
			await rows[codexRowIndex].click();
			await page.waitForSelector('[data-measure="provider-bay"]', { timeout: 15000 });
			await delay(500);
			const bay = page.locator('[data-measure="provider-bay"]');
			await bay.screenshot({ path: `${OUT}/${vp.name}-observed-model.png` });
			const rowText = await page.$$eval('[data-role="rack-row-tap"]', (buttons) =>
				buttons.map((b) => b.closest('div.flex.w-full')?.innerText)
			);
			console.log(`${vp.name}:`, JSON.stringify(rowText, null, 2));
		}
		await browser.close();
	} finally {
		vite.kill();
	}
}

main().catch((err) => {
	console.error(err);
	process.exit(1);
});
