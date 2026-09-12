// Captures /ascii before and after the newest boundary moves to a file leaf.
// Usage: node repro/drive-ascii-file-touch.mjs [--out DIR] [--port N]
import { spawn } from 'node:child_process';
import { mkdir } from 'node:fs/promises';
import { chromium } from 'playwright';
import { setTimeout as delay } from 'node:timers/promises';
import { closeBrowser, exerciseRecorder, runDriver, stopVite } from './finish.mjs';

const args = process.argv.slice(2);
const arg = (flag) => {
	const index = args.indexOf(flag);
	return index === -1 || index + 1 >= args.length ? null : args[index + 1];
};
const OUT = arg('--out') ?? '/tmp/ascii-file-touch';
const PORT = Number(arg('--port') ?? 5204);
let phase = 0;

async function waitForServer(url, tries = 90) {
	for (let attempt = 0; attempt < tries; attempt += 1) {
		try {
			const response = await fetch(url);
			if (response.ok || response.status === 404) return;
		} catch {
			// Vite has not bound its port yet.
		}
		await delay(500);
	}
	throw new Error(`dev server never came up at ${url}`);
}

function liveRuns() {
	const boundary =
		phase === 0
			? {
					at: '2026-09-12T19:00:00Z',
					phase: 'tool',
					act: 'orient',
					tools: ['exec_command'],
					detail: 'Read package.json',
					out_bytes: 42,
					injected: false,
					dir: '.'
				}
			: {
					at: '2026-09-12T19:00:02Z',
					phase: 'tool',
					act: 'orient',
					tools: ['exec_command'],
					detail: 'Read AsciiField.svelte',
					out_bytes: 42,
					injected: false,
					dir: 'src/frontend/src/lib'
				};
	return {
		generated_at: boundary.at,
		stale: false,
		reported_at: boundary.at,
		spawn_max_concurrent: 3,
		runs: [
			{
				id: 'presence-file-touch',
				run_id: 'run-file-touch',
				kind: 'daemon',
				stream: 'telegram:file-touch:',
				label: '',
				name: 'the shuttle reads',
				repo_label: 'hugimuni-labs/brnrd',
				started_at: '2026-09-12T18:55:00Z',
				last_seen: boundary.at,
				parent_run_id: null,
				is_subspawn: false,
				runner: {},
				phase: 'running',
				card_text: null,
				card_updated_at: null,
				relics_counts: {},
				portals: { pending: 0, oldest_at: null },
				room: { env: 'worktree', branch: 'brr/file-touch', dir: null },
				edge: boundary,
				boundaries: [boundary],
				crossings: []
			}
		]
	};
}

function routes() {
	return {
		'/v1/dashboard/live-runs': liveRuns(),
		'/v1/dashboard/run-ledger': {
			generated_at: '2026-09-12T19:00:00Z',
			rows: [],
			stale: false,
			reported_at: null,
			span_seconds_served: 0
		},
		'/v1/dashboard/scheduled-wakes': { generated_at: '2026-09-12T19:00:00Z', rows: [], total: 0 },
		'/v1/dashboard/quota': { generated_at: '2026-09-12T19:00:00Z', runner_quotas: [] }
	};
}

async function main() {
	await mkdir(OUT, { recursive: true });
	const vite = spawn('npx', ['vite', 'dev', '--port', String(PORT), '--strictPort'], {
		stdio: ['ignore', 'pipe', 'pipe']
	});
	const modules = exerciseRecorder();
	let browser;
	try {
		await waitForServer(`http://localhost:${PORT}/`);
		browser = await chromium.launch();
		const page = await modules.watch(
			await browser.newPage({ viewport: { width: 1280, height: 900 }, deviceScaleFactor: 2 }),
			'drive-ascii-file-touch'
		);
		await page.route('**/v1/dashboard/**', async (route) => {
			const body = routes()[new URL(route.request().url()).pathname];
			await route.fulfill({
				status: body ? 200 : 404,
				contentType: 'application/json',
				body: JSON.stringify(body ?? {})
			});
		});
		await page.goto(`http://localhost:${PORT}/ascii`, { waitUntil: 'networkidle' });
		const board = page.locator('pre.board');
		await board.waitFor({ state: 'visible', timeout: 15_000 });
		await delay(200);
		const before = await board.innerText();
		if (!before.includes('∙ package.json'))
			throw new Error('root boundary did not light its file leaf');
		await board.screenshot({ path: `${OUT}/before-root-file-touch.png` });
		phase = 1;
		await delay(2_200);
		const after = await board.innerText();
		if (!after.includes('∙ AsciiField.svelte') || after.includes('∙ package.json'))
			throw new Error('newest boundary did not move the light');
		await board.screenshot({ path: `${OUT}/after-file-touch.png` });
		await modules.harvest(page, 'drive-ascii-file-touch');
		await modules.save(OUT, 'drive-ascii-file-touch');
		console.log(JSON.stringify({ before: 'package.json', after: 'AsciiField.svelte' }, null, 2));
	} finally {
		if (browser) await closeBrowser(browser, 'drive-ascii-file-touch');
		stopVite(vite, 'drive-ascii-file-touch');
	}
}

runDriver(main);
