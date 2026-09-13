// Drives the fuel deck with the live account's 2026-09-09 morning shape:
// every claude core `auth-error` (the Shell's credential expired at 03:36Z,
// a schedule wake re-proved it on sonnet at 05:06Z), codex healthy.
//
// Before this change the panel dropped the claude shell whole — no row, no
// windows, no tap — which is the deadlock the operator broke by deleting
// `.brr/runner-auth-health.json` by hand. The shots here are the *after*;
// the before is the operator's own photo (evt-1788949906019838000-qrwb).
//
// Usage: node repro/drive-locked-shell.mjs [--out DIR] [--port N]
import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import { mkdir } from 'node:fs/promises';
import * as fixtures from './fixtures.mjs';
import { closeBrowser, stopVite, runDriver, exerciseRecorder } from './finish.mjs';

const args = process.argv.slice(2);
const arg = (f) => {
	const i = args.indexOf(f);
	return i === -1 || i + 1 >= args.length ? null : args[i + 1];
};
const OUT = arg('--out') ?? '/tmp/locked-shell-drive';
const PORT = Number(arg('--port') ?? 5198);

const ROUTES = fixtures.buildRoutes(fixtures.DEFAULT_SCALE);
ROUTES['/v1/dashboard/quota'] = {
	generated_at: new Date(0).toISOString(),
	runner_quotas: [
		{
			shell: 'claude',
			status: 'known',
			windows: [
				{
					label: '5h window',
					used: 0,
					limit: 100,
					percent: 100,
					reset: 'resets 5:20pm',
					resets_at: 1788974400
				},
				{
					label: 'weekly',
					used: 59,
					limit: 100,
					percent: 41,
					reset: 'resets Sep 12',
					resets_at: 1789221540
				},
				{
					label: 'weekly (Fable)',
					used: 77,
					limit: 100,
					percent: 23,
					reset: 'resets Sep 12',
					resets_at: 1789221540
				}
			]
		},
		{
			shell: 'codex',
			status: 'known',
			windows: [
				{
					label: '5h window',
					used: 0,
					limit: 100,
					percent: 100,
					reset: 'resets 4:59pm',
					resets_at: 1788973140
				},
				{
					label: 'weekly',
					used: 47,
					limit: 100,
					percent: 53,
					reset: 'resets Sep 15',
					resets_at: 1789480800
				}
			]
		}
	]
};

const LOCK = {
	since: '2026-09-09T05:06:28+00:00',
	seen_on: 'claude-sonnet',
	probe: null,
	hint: 'sign in to the Shell again; the mark clears when the credential changes'
};
const claude = (name, model, cls, rank) => ({
	name,
	shell: 'claude',
	model,
	provider: 'anthropic',
	class: cls,
	cost_rank: rank,
	quota_source: 'claude-local',
	availability: 'auth-error',
	available: false,
	auth_error: LOCK
});
const codex = (name, model, cls, rank) => ({
	name,
	shell: 'codex',
	model,
	provider: 'openai',
	class: cls,
	cost_rank: rank,
	quota_source: 'codex-local',
	availability: 'available',
	available: true
});
const runners = ROUTES['/v1/dashboard/runners'];
runners.profiles = [
	claude('claude-haiku', 'haiku', 'economy', 10),
	claude('claude', null, 'balanced', 30),
	claude('claude-sonnet', 'sonnet', 'balanced', 30),
	claude('claude-opus', 'opus', 'strong', 50),
	claude('claude-fable', 'fable', 'strong', 55),
	codex('codex-gpt-6-astra', 'gpt-6-astra', null, 1),
	codex('codex-mini', 'gpt-5.6-luna', 'economy', 20),
	codex('codex', null, 'balanced', 25),
	codex('codex-full', 'gpt-5.6-sol', 'strong', 45)
];
runners.default = 'claude-sonnet';

const WIDTHS = [{ name: 'phone', width: 390, height: 844 }];

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

async function main() {
	await mkdir(OUT, { recursive: true });
	// Which source modules actually run while these shots are taken, so the
	// PR comment can say whether this check looked at the change under
	// review instead of putting a green tick on a surface nobody opened
	// (#1944).
	const modules = exerciseRecorder();
	const vite = spawn('npx', ['vite', 'dev', '--port', String(PORT), '--strictPort'], {
		stdio: ['ignore', 'pipe', 'pipe']
	});
	vite.stdout.on('data', () => {});
	vite.stderr.on('data', () => {});
	const report = {};
	try {
		await waitForServer(`http://localhost:${PORT}/`);
		const browser = await chromium.launch();
		for (const vp of WIDTHS) {
			const ctx = await browser.newContext({
				viewport: { width: vp.width, height: vp.height },
				deviceScaleFactor: 2,
				reducedMotion: 'no-preference'
			});
			const page = await modules.watch(await ctx.newPage(), 'drive-locked-shell');
			await page.route('**/v1/dashboard/**', async (route) => {
				const body = ROUTES[new URL(route.request().url()).pathname];
				await route.fulfill({
					status: body ? 200 : 404,
					contentType: 'application/json',
					body: JSON.stringify(body ?? {})
				});
			});
			await page.goto(`http://localhost:${PORT}/`, { waitUntil: 'networkidle' });
			const fuel = page.locator('[data-measure="fuel"]');
			const gauge = page.locator('[data-measure="gauge"]');
			const providerRows = page.locator('.fuel-provider-row');
			const claudeRow = providerRows.first();
			await fuel.waitFor({ state: 'visible', timeout: 15000 });
			await claudeRow.waitFor({ state: 'visible', timeout: 15000 });
			report.providerRows = await Promise.all(
				Array.from({ length: await providerRows.count() }, async (_, index) =>
					(await providerRows.nth(index).innerText()).replace(/\n/g, ' · ')
				)
			);
			await gauge.screenshot({ path: `${OUT}/${vp.name}-1-deck.png` });
			await claudeRow.click();
			await page.locator('.fuel-provider-row.is-open').filter({ hasText: 'claude' }).waitFor({
				state: 'visible',
				timeout: 15000
			});
			await page
				.locator('[data-measure="provider-bay"]')
				.waitFor({ state: 'visible', timeout: 15000 });
			await page.screenshot({ path: `${OUT}/${vp.name}-2-open-claude.png`, fullPage: false });
			const rackRows = page.locator('[data-role="rack-row-tap"]');
			report.rackRows = await Promise.all(
				Array.from({ length: await rackRows.count() }, async (_, index) => {
					const row = rackRows.nth(index);
					const [text, disabled, title] = await Promise.all([
						row.innerText(),
						row.isDisabled(),
						row.getAttribute('title')
					]);
					return { text: text.replace(/\n/g, ' '), disabled, title };
				})
			);
			await modules.harvest(page, 'drive-locked-shell');
			await ctx.close();
		}
		await modules.save(OUT, 'drive-locked-shell');
		await closeBrowser(browser, 'drive-locked-shell');
	} finally {
		stopVite(vite, 'drive-locked-shell');
	}
	console.log(JSON.stringify(report, null, 2));
}

runDriver(main);
