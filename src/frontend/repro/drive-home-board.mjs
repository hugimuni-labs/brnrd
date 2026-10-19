// Drives `/` as the loom home board and `/thread/[id]`: a busy board, a
// quiet one, one where fuel is the news, a thread page, and the empty state
// a signed-in account with no snapshot and no paired daemon sees.
//
// Behavioural asserts first, screenshots second (drive-warp-item.mjs's
// pattern, `finish.mjs`'s bounded teardown and coverage recorder). The
// snapshots come from `loomSnapshots()` in fixtures.mjs, stamped relative to
// the moment this driver starts, and `brnrd.loom.seen` is seeded ten minutes
// back so the unread dots have something to mark.
//
// Usage: node repro/drive-home-board.mjs [--out DIR] [--port N]
import { spawn } from 'node:child_process';
import { strict as assert } from 'node:assert';
import { mkdir } from 'node:fs/promises';
import { chromium } from 'playwright';
import { setTimeout as delay } from 'node:timers/promises';
import * as fixtures from './fixtures.mjs';
import { closeBrowser, exerciseRecorder, runDriver, stopVite } from './finish.mjs';

const args = process.argv.slice(2);
const arg = (flag) => {
	const index = args.indexOf(flag);
	return index === -1 || index + 1 >= args.length ? null : args[index + 1];
};
const OUT = arg('--out') ?? '/tmp/home-board';
const PORT = Number(arg('--port') ?? 5207);

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

const NOW = Date.now();
const SNAPSHOTS = fixtures.loomSnapshots(NOW);
const SEEN = new Date(NOW - 10 * 60_000).toISOString();
// A signed-in account no daemon has ever paired with.
const NEVER_PAIRED = { ...fixtures.repos, connected_repos: [], machines: { paired: false } };

const VIEWPORTS = {
	phone: { width: 390, height: 844 },
	desktop: { width: 1280, height: 900 }
};

/** `loom` is a snapshot, or null for the 404 a fresh account gets. */
async function routePage(page, { loom, repos = fixtures.repos }, letters) {
	await page.route('**/v1/dashboard/**', async (route) => {
		const request = route.request();
		const path = new URL(request.url()).pathname;
		if (path === '/v1/dashboard/loom/letters' && request.method() === 'POST') {
			letters.push(JSON.parse(request.postData() ?? '{}'));
			await route.fulfill({
				status: 202,
				contentType: 'application/json',
				body: JSON.stringify({ ok: true, event_id: `evt-${letters.length}` })
			});
			return;
		}
		const body =
			path === '/v1/dashboard/loom'
				? loom
				: path === '/v1/dashboard/repos'
					? repos
					: fixtures.ROUTES[path];
		await route.fulfill({
			status: body ? 200 : 404,
			contentType: 'application/json',
			body: JSON.stringify(body ?? {})
		});
	});
}

const text = async (locator) => (await locator.innerText()).replace(/\s+/g, ' ').trim();

/** `kept` seeds `brnrd.loom.body`, the pick an earlier visit left behind. */
async function scene(browser, modules, viewport, scenario, route = '/', kept = null) {
	const context = await browser.newContext({
		viewport: VIEWPORTS[viewport],
		deviceScaleFactor: 2,
		reducedMotion: 'reduce'
	});
	await context.addInitScript(
		([seen, body]) => {
			localStorage.setItem('brnrd.loom.seen', seen);
			if (body) localStorage.setItem('brnrd.loom.body', body);
			else localStorage.removeItem('brnrd.loom.body');
		},
		[SEEN, kept]
	);
	const page = await modules.watch(await context.newPage(), 'drive-home-board');
	const letters = [];
	await routePage(page, scenario, letters);
	await page.goto(`http://localhost:${PORT}${route}`, { waitUntil: 'networkidle' });
	return { context, page, letters };
}

const kept = (page) => page.evaluate(() => localStorage.getItem('brnrd.loom.body'));

async function send(page, words) {
	await page.getByRole('textbox', { name: 'letter' }).fill(words);
	await page.getByRole('button', { name: 'send' }).click();
	await page.getByRole('status').waitFor({ timeout: 5000 });
}

// The glass (`body::before`) is position:fixed, so a full-page capture paints
// it over the first viewport only and leaves a seam; growing the viewport to
// the page first matches what a reader sees at every scroll position (#2350's
// drive-landing.mjs does the same).
async function shot(page, name) {
	const viewport = page.viewportSize();
	const height = await page.evaluate(() => document.documentElement.scrollHeight);
	await page.setViewportSize({ width: viewport.width, height });
	await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });
	await page.setViewportSize(viewport);
}

async function main() {
	await mkdir(OUT, { recursive: true });
	const vite = spawn('npx', ['vite', 'dev', '--port', String(PORT), '--strictPort'], {
		stdio: ['ignore', 'pipe', 'pipe']
	});
	const modules = exerciseRecorder();
	const report = {};
	let browser;
	try {
		await waitForServer(`http://localhost:${PORT}/`);
		browser = await chromium.launch();

		for (const viewport of Object.keys(VIEWPORTS)) {
			const phone = viewport === 'phone';

			// 1. Busy: yours first, every turn mark, unread dots, body choice.
			{
				const { context, page, letters } = await scene(browser, modules, viewport, {
					loom: SNAPSHOTS.busy
				});
				await page.waitForSelector('[data-testid="loom-board"]', { timeout: 20000 });
				const cards = page.locator('[data-testid="thread-card"]');
				const turns = await cards.evaluateAll((els) => els.map((el) => el.dataset.turn));
				assert.equal(turns[0], 'yours', `yours card first (${turns})`);
				assert.deepEqual(
					[...new Set(turns)],
					['yours', 'mine', 'blocked', 'none'],
					`turn order (${turns})`
				);
				const blocked = await text(page.locator('[data-turn="blocked"]').first());
				assert.ok(
					blocked.includes('waits on an xAI API key'),
					`blocked names its wait (${blocked})`
				);
				const strip = page.locator('[data-testid="fuel-strip"]');
				assert.equal(await strip.getAttribute('data-promoted'), 'false', 'healthy strip is quiet');
				const presence = await text(page.locator('[data-testid="presence"]'));
				assert.ok(
					/working/.test(presence) && /owes 2 letters/.test(presence),
					`presence (${presence})`
				);
				const unread = await page.locator('[data-testid="loom-log"] [data-unread="true"]').count();
				assert.ok(unread >= 3, `unread dots on letters newer than seen (${unread})`);
				const receipts = await page
					.locator('[data-testid="loom-log"] [data-kind="receipt"]')
					.count();
				assert.ok(receipts >= 2, `receipts in the log (${receipts})`);
				// The wishes come first: what waits on him is amber and on top, the
				// closed ones fold behind 'N done'.
				const wishes = page.locator('[data-testid="wish"]');
				const owedRows = await wishes.evaluateAll((els) => els.map((el) => el.dataset.owed));
				assert.deepEqual(
					owedRows,
					['true', 'true', 'false', 'false', 'false'],
					`wishes (${owedRows})`
				);
				const owedLine = await text(page.locator('[data-testid="wishes-owed"]'));
				assert.equal(owedLine, '2 wait on you', `owed count (${owedLine})`);
				assert.equal(
					await wishes.first().locator('[data-testid="try-it"]').getAttribute('href'),
					'https://brnrd.dev/',
					'the owed verdict carries its try-it link'
				);
				const recorded = await text(
					page.locator('[data-wish="w-h3r0ln"] [data-testid="answered"]')
				);
				assert.ok(/sent, not recorded yet/.test(recorded), `agree is not a verdict (${recorded})`);
				await shot(page, `${viewport}-1-busy`);

				if (phone) {
					// Nothing picked: the line names the default, the letter carries no
					// body and nothing is kept, so the loom still chooses.
					const before = await text(page.locator('[data-testid="composer-body"]'));
					assert.equal(before, 'as claude/opus · default', `default body (${before})`);
					await send(page, 'Unpicked.');
					assert.deepEqual(letters.at(-1), { thread: 'inbox', text: 'Unpicked.' });
					assert.equal(await kept(page), null, 'an unpicked body is not stored');
					await page.locator('[data-shell="codex"]').click();
					const cores = page.locator('[data-testid="cores"]');
					await cores.waitFor({ state: 'visible', timeout: 5000 });
					assert.ok((await text(cores)).includes('gpt-6.1-sol'), 'codex cores shown');
					await cores.getByRole('button', { name: 'gpt-6.1-sol', exact: true }).click();
					const after = await text(page.locator('[data-testid="composer-body"]'));
					assert.equal(
						after,
						'asks for codex/gpt-6.1-sol',
						`picked body reaches composer (${after})`
					);
					await send(page, 'Picked.');
					assert.deepEqual(letters.at(-1), {
						thread: 'inbox',
						text: 'Picked.',
						body: 'codex/gpt-6.1-sol'
					});
					assert.equal(await kept(page), 'codex/gpt-6.1-sol', 'a pick is stored');
					// A thread with a live strand reads the letter in that strand's
					// body: the pick is not offered and not sent.
					await page.locator('[data-testid="composer-thread"]').selectOption('answer-asks-build');
					const live = await text(page.locator('[data-testid="composer-body"]'));
					assert.equal(live, 'as claude/opus', `live strand's body (${live})`);
					await send(page, 'Live.');
					assert.deepEqual(letters.at(-1), { thread: 'answer-asks-build', text: 'Live.' });
					await page.locator('[data-testid="composer-thread"]').selectOption('inbox');
					const back = await text(page.locator('[data-testid="composer-body"]'));
					assert.equal(back, 'asks for codex/gpt-6.1-sol', `pick offered again (${back})`);
					await page.locator('[data-filter="yours"]').click();
					const yours = await cards.evaluateAll((els) => els.map((el) => el.dataset.turn));
					assert.deepEqual(yours, ['yours', 'yours'], `yours filter (${yours})`);
					report.body = { before, after, live, back, letters: [...letters] };

					// Last, since each leaves a status line: Agree is one tap, Answer
					// opens a box; both are letters on the wish's thread.
					const owedWish = page.locator('[data-wish="w-b0dy7k"]');
					await owedWish.locator('[data-testid="agree"]').click();
					await owedWish.locator('[data-testid="answered"]').waitFor({ timeout: 5000 });
					assert.deepEqual(letters.at(-1), {
						thread: 'loom-on-the-web',
						text: 'agree',
						wish: 'w-b0dy7k',
						verdict: 'agree'
					});
					const agreed = await text(owedWish.locator('[data-testid="answered"]'));
					assert.ok(/queued for the relay/.test(agreed), `agree queues (${agreed})`);
					const ask = page.locator('[data-wish="w-pr1c3s"]');
					assert.equal(await ask.locator('[data-testid="agree"]').count(), 0, 'no agree on an ask');
					await ask.locator('[data-testid="correct"]').click();
					await ask.getByRole('textbox', { name: 'your answer' }).fill('The middle one.');
					await ask.getByRole('button', { name: 'send answer' }).click();
					await ask.locator('[data-testid="answered"]').waitFor({ timeout: 5000 });
					assert.deepEqual(letters.at(-1), {
						thread: 'pricing-page',
						text: 'The middle one.',
						wish: 'w-pr1c3s',
						verdict: 'correct'
					});
					await page.locator('[data-testid="wishes-done"]').click();
					assert.equal(await wishes.count(), 7, 'done wishes unfold');
					await shot(page, `${viewport}-1b-busy-answered`);
					report.wishes = { owedRows, owedLine, recorded, agreed, letters: letters.slice(-2) };
				}
				report[`${viewport}-busy`] = { turns, unread, receipts };
				await modules.harvest(page, 'drive-home-board');
				await context.close();
			}

			// 2. Quiet: nothing is yours, the strip stays quiet.
			{
				const { context, page } = await scene(browser, modules, viewport, {
					loom: SNAPSHOTS.quiet
				});
				await page.waitForSelector('[data-testid="loom-board"]', { timeout: 20000 });
				const turns = await page
					.locator('[data-testid="thread-card"]')
					.evaluateAll((els) => els.map((el) => el.dataset.turn));
				assert.deepEqual(turns, ['none', 'none'], `quiet threads (${turns})`);
				const empty = await text(page.locator('[data-testid="wishes-empty"]'));
				assert.ok(/no wishes yet/.test(empty), `wishes empty state (${empty})`);
				assert.equal(
					await page.locator('[data-testid="fuel-strip"]').getAttribute('data-promoted'),
					'false'
				);
				await shot(page, `${viewport}-2-quiet`);
				await context.close();
			}

			// 3. Low fuel: a shell under 15% and one dry promote the strip.
			{
				const { context, page } = await scene(browser, modules, viewport, {
					loom: SNAPSHOTS.lowFuel
				});
				await page.waitForSelector('[data-testid="loom-board"]', { timeout: 20000 });
				const strip = page.locator('[data-testid="fuel-strip"]');
				assert.equal(await strip.getAttribute('data-promoted'), 'true', 'low strip is promoted');
				const low = await strip
					.locator('[data-shell]')
					.evaluateAll((els) => els.map((el) => `${el.dataset.shell}:${el.dataset.low}`));
				assert.deepEqual(low, ['claude:true', 'codex:true'], `both shells low (${low})`);
				const stripText = await text(strip);
				assert.ok(/dry/i.test(stripText) && /resets in/.test(stripText), `strip (${stripText})`);
				const age = await text(page.locator('[data-testid="snapshot-age"]'));
				assert.equal(age, 'snapshot 5m old', `stale snapshot says its age (${age})`);
				await shot(page, `${viewport}-3-low-fuel`);
				await context.close();
			}

			// 4. Thread page: Plan, Done, its letters, its strands; a letter queues.
			{
				const { context, page, letters } = await scene(
					browser,
					modules,
					viewport,
					{ loom: SNAPSHOTS.busy },
					'/thread/answer-asks-build',
					// A pick from an earlier visit the loom no longer offers.
					'grok/grok-4.6'
				);
				await page.waitForSelector('[data-testid="loom-thread"] h1', { timeout: 20000 });
				const main = await text(page.locator('[data-testid="loom-thread"]'));
				// The headings are eyebrows: uppercase on screen, so match without case.
				for (const word of ['plan', 'done', 'letters', 'strands'])
					assert.ok(main.toLowerCase().includes(word), `thread page shows ${word}`);
				const done = await text(page.locator('[data-testid="done"]'));
				assert.ok(done.includes('asks, graph, receipts on the board'), `done (${done})`);
				const entries = await page.locator('[data-testid="loom-log"] li').count();
				assert.equal(entries, 2, `only this thread's letters (${entries})`);
				await shot(page, `${viewport}-4-thread`);

				if (phone) {
					assert.equal(await kept(page), null, 'a stale pick is dropped');
					await send(page, 'Merging after lunch.');
					const status = await text(page.getByRole('status'));
					assert.equal(status, 'queued for answer-asks-build · evt-1', `queued (${status})`);
					// A pick made here still does not reach the holding strand.
					await page.locator('[data-shell="codex"]').click();
					const cores = page.locator('[data-testid="cores"]');
					await cores.waitFor({ state: 'visible', timeout: 5000 });
					await cores.getByRole('button', { name: 'gpt-6.1-sol', exact: true }).click();
					const live = await text(page.locator('[data-testid="composer-body"]'));
					assert.equal(live, 'as claude/opus', `holding strand's body (${live})`);
					await send(page, 'Still you.');
					assert.deepEqual(letters, [
						{ thread: 'answer-asks-build', text: 'Merging after lunch.' },
						{ thread: 'answer-asks-build', text: 'Still you.' }
					]);
					report.letter = { status, live, letters };
				}
				await context.close();
			}

			// 5. Empty: signed in, no snapshot, never paired: the three nouns.
			{
				const { context, page } = await scene(browser, modules, viewport, {
					loom: null,
					repos: NEVER_PAIRED
				});
				await page.waitForSelector('[data-testid="loom-empty"]', { timeout: 20000 });
				const empty = await text(page.locator('[data-testid="loom-empty"]'));
				for (const word of [
					'thread',
					'strand',
					'letter',
					'npm install -g brnrd',
					'brnrd account connect'
				])
					assert.ok(empty.includes(word), `empty state shows ${word}`);
				await shot(page, `${viewport}-5-empty`);
				await context.close();
			}
		}

		await modules.save(OUT, 'drive-home-board');
		console.log(JSON.stringify({ ...report, ok: true }, null, 2));
	} finally {
		if (browser) await closeBrowser(browser, 'drive-home-board');
		stopVite(vite, 'drive-home-board');
	}
}

runDriver(main);
