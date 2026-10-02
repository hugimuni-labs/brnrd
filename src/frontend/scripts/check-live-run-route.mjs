// Representative API fixtures, no account cookie or private corpus.
// npm run dev -- --host 127.0.0.1 --port 5186, then:
// node scripts/check-live-run-route.mjs http://127.0.0.1:5186 /tmp/route-proof
// --before captures the same fixture against main's original route.
import { chromium } from 'playwright';
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
const [base = 'http://127.0.0.1:5186', output = '/tmp/route-proof', mode] = process.argv.slice(2);
await mkdir(output, { recursive: true });
const browser = await chromium.launch(
	process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}
);
try {
	const page = await browser.newPage({
		viewport: { width: 1100, height: 900 },
		reducedMotion: 'reduce'
	});
	const errors = [];
	page.on('pageerror', (error) => errors.push(error.message));
	const runId = 'run-fixture';
	const makeRun = (repo, name, card) => ({
		id: repo,
		kind: 'agent',
		stream: 'fixture',
		label: repo,
		name,
		run_id: runId,
		repo_label: repo,
		started_at: '2026-10-02T00:00:00Z',
		last_seen: '2026-10-02T00:01:00Z',
		parent_run_id: null,
		is_subspawn: false,
		runner: { shell: 'codex', core: 'default' },
		phase: 'running',
		lifecycle: 'weaving',
		card_text: card,
		card_updated_at: '2026-10-02T00:01:00Z'
	});
	let liveStatus = 200;
	let live = {
		runs: [
			makeRun(
				'example/project',
				'Route proof',
				'## Now\nThe live route is readable before the mirror.'
			),
			makeRun('other/project', 'Other route', 'Other repo card')
		],
		stale: false
	};
	let surface = {
		generated_at: 'fixture',
		reported_at: null,
		files: [],
		withheld: { lane: 'runs', opted_out: ['example/project'] }
	};
	const counts = {};
	await page.route('**/v1/**', async (route) => {
		const path = new URL(route.request().url()).pathname;
		counts[path] = (counts[path] ?? 0) + 1;
		let data = {};
		let status = 200;
		if (path.endsWith('/live-runs')) {
			data = live;
			status = liveStatus;
		} else if (path.endsWith('/surface')) data = surface;
		else if (path.endsWith('/run-ledger')) data = { rows: [], stale: false };
		else if (path.endsWith('/repos')) data = { connected_repos: [] };
		await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
	});
	const url = `${base}/runs/example__project/${runId}`;
	await page.goto(url);
	await page.getByText('paused —').first().waitFor();
	if (mode === '--before') {
		assert.equal(await page.getByText('The live route is readable before the mirror.').count(), 0);
		await page.screenshot({ path: `${output}/before.png`, fullPage: true });
		console.log('before: main with permitted live / withheld corpus shows no card');
	} else {
		await page.getByText('The live route is readable before the mirror.').waitFor();
		assert.equal(await page.getByText('Other repo card', { exact: true }).count(), 0);
		await page.screenshot({ path: `${output}/after.png`, fullPage: true });
		await page.clock.install();
		const refresh = async () => {
			await page.clock.fastForward(15_100);
		};
		live.runs[0].card_text = '## Now\nCard updated through the live feed.';
		await refresh();
		await page.getByText('Card updated through the live feed.').waitFor();
		liveStatus = 503;
		await refresh();
		await page.getByText('Live feed unavailable', { exact: false }).waitFor();
		await page.getByText('live status: stale', { exact: false }).waitFor();
		assert.equal(await page.getByText('Card updated through the live feed.').count(), 1);
		liveStatus = 401;
		await refresh();
		await page.getByText('Session expired.', { exact: false }).waitFor();
		assert.equal(await page.getByText('Card updated through the live feed.').count(), 0);
		liveStatus = 200;
		live = { runs: [], stale: false, withheld: { lane: 'live', opted_out: ['example/project'] } };
		await refresh();
		assert.equal(await page.getByText('live identity', { exact: true }).count(), 0);
		assert.equal(await page.getByText('node not mirrored', { exact: true }).count(), 0);
		live = { runs: [], stale: false };
		surface = {
			generated_at: 'fixture',
			reported_at: '2026-10-02T00:02:00Z',
			files: [
				{
					path: `runs/example__project/${runId}/state.md`,
					layer: 'runs',
					markdown:
						'---\nrepo_label: example/project\nstatus: finished\n---\n\n## Request\nRoute proof fixture.'
				},
				{
					path: `runs/example__project/${runId}/body.md`,
					layer: 'runs',
					markdown: '## Now\nDurable fallback arrived after closeout.'
				}
			]
		};
		await refresh();
		await page.getByText('Durable fallback arrived after closeout.').waitFor();
		assert.equal(await page.getByText('Session expired.', { exact: false }).count(), 0);
		await page.screenshot({ path: `${output}/closed.png`, fullPage: true });
		assert.equal(await page.getByText('live identity', { exact: true }).count(), 0);
		// Real client-side navigation reuses route params without inheriting the
		// previous node's corpus or live selection.
		live = {
			runs: [
				makeRun('example/project', 'Route proof', 'First repo card'),
				makeRun('other/project', 'Other route', 'Other repo card')
			],
			stale: false
		};
		await refresh();
		await page.evaluate(() => {
			const a = document.createElement('a');
			a.id = 'route-test-link';
			a.href = '/runs/other__project/run-fixture';
			a.textContent = 'fixture navigation';
			document.body.append(a);
		});
		await page.locator('#route-test-link').click();
		await page.getByText('Other repo card', { exact: true }).waitFor();
		assert.equal(await page.getByText('First repo card', { exact: true }).count(), 0);
		assert.equal(await page.getByText('Durable fallback arrived after closeout.').count(), 0);
		// Navigating away stops all recurring requests.
		await page.goto(`${base}/login`);
		const last = counts['/v1/dashboard/live-runs'];
		await page.clock.fastForward(30_000);
		assert.equal(counts['/v1/dashboard/live-runs'], last);
		assert.deepEqual(errors, []);
		console.log(
			'PASS: withheld corpus / live match / card update / 503 stale / 401 clears / withheld live / durable closeout / SPA repo switch / teardown'
		);
	}
} finally {
	await browser.close();
}
