// Drives the cores rack with the live account's shape: claude cores with
// the vendor id the Shell attested on the ledger, codex with two dead
// (`shell-not-found`) 5.4 rows. Before: the operator's photo (the dead take
// full rows, `fable` without a version). After: the shots here.
//
// Usage: node repro/drive-observed-cores.mjs [--out DIR] [--port N]
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
const OUT = arg('--out') ?? '/tmp/observed-drive';
const PORT = Number(arg('--port') ?? 5199);

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

const OBSERVED = {
	haiku: 'claude-haiku-4-5-20251001',
	sonnet: 'claude-sonnet-5',
	opus: 'claude-opus-5',
	fable: 'claude-fable-5-1'
};
const claude = (name, model, cls, rank) => ({
	name,
	shell: 'claude',
	model,
	provider: 'anthropic',
	class: cls,
	cost_rank: rank,
	quota_source: 'claude-local',
	availability: 'available',
	available: true,
	observed_model: model ? OBSERVED[model] : null,
	observed_at: model ? '2026-09-09T03:36:33Z' : null
});
const DISPLAY = {
	'gpt-6-astra': 'GPT-6-Astra',
	'gpt-5.6-luna': 'GPT-5.6-Luna',
	'gpt-5.6-terra': 'GPT-5.6-Terra',
	'gpt-5.6-sol': 'GPT-5.6-Sol',
	'gpt-5.4': 'GPT-5.4',
	'gpt-5.4-mini': 'GPT-5.4-Mini'
};
const codex = (name, model, cls, rank) => ({
	name,
	shell: 'codex',
	model,
	display_name: model ? DISPLAY[model] : null,
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
	{ ...codex('codex-gpt-6-astra', 'gpt-6-astra', 'strong', 54), vendor_priority: 1 },
	codex('codex-mini', 'gpt-5.6-luna', 'economy', 20),
	codex('codex', null, 'balanced', 25),
	codex('codex-full', 'gpt-5.6-sol', 'strong', 45),
	{
		...codex('codex-gpt-5.4', 'gpt-5.4', 'balanced', 30),
		availability: 'shell-not-found',
		available: false
	},
	{
		...codex('codex-gpt-5.4-mini', 'gpt-5.4-mini', 'balanced', 28),
		availability: 'shell-not-found',
		available: false
	}
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
			const page = await modules.watch(await ctx.newPage(), 'drive-observed-cores');
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
			const codexRow = providerRows.nth(1);
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
			const providerBay = page.locator('[data-measure="provider-bay"]');
			await providerBay.waitFor({ state: 'visible', timeout: 15000 });
			await providerBay.screenshot({ path: `${OUT}/${vp.name}-2-claude-cores.png` });
			await codexRow.click();
			await page.locator('.fuel-provider-row.is-open').filter({ hasText: 'codex' }).waitFor({
				state: 'visible',
				timeout: 15000
			});
			await providerBay.screenshot({ path: `${OUT}/${vp.name}-3-codex-cores.png` });
			const offCount = page.locator('[data-role="rack-off-count"]');
			report.offCount = (await offCount.count()) ? await offCount.innerText() : null;
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
			await modules.harvest(page, 'drive-observed-cores');
			await ctx.close();
		}
		await modules.save(OUT, 'drive-observed-cores');
		await closeBrowser(browser, 'drive-observed-cores');
	} finally {
		stopVite(vite, 'drive-observed-cores');
	}
	console.log(JSON.stringify(report, null, 2));
}

runDriver(main);
