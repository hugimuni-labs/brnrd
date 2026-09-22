// Drives the home's list of asks, in place of the warp's item graph
// (design-the-ask.md §Done, reopened, linked, "the console = the warp panel,
// per ask"; the-panel-third-pass for the reorder + collapse + sign;
// the-row-you-can-judge for the collapsed-bar previews, the judge row
// rework, and the seat join). `--tag before` captures the home on a tree
// without the four-bucket list (shots only); `--tag after` also asserts
// behaviour first, screenshots second — a shot that never exercised the
// component is a green tick meaning nothing looked (the 2026-08-26 room
// lesson). The fixture (`fixtures.mjs::asks`) carries: w-201 (stage making,
// a live strand wearing the attempt) → in hand, drone; w-205 (stage making,
// no attempt) → in hand, the bare-seat case, joined against the seat's own
// live run below; w-96 (stage delivered, stale, carrying a `sign` and two
// receipts, one titled) → yours to judge; w-198/w-190 (understood/shaped,
// non-stale) plus four stale rows (w-80/w-72/w-68/w-61) → unaddressed, so
// its nested stale sub-block has something to hide too; five done rows
// (w-150 newest .. w-110 oldest) → done, so its own "newest three, rest
// behind the bar" actually has something to hide.
//
// Usage: node repro/drive-asks.mjs [--out DIR] [--port N] [--tag before|after]
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
const OUT = arg('--out') ?? '/tmp/asks-shots';
const PORT = Number(arg('--port') ?? 5207);
const TAG = arg('--tag') ?? 'after';
const VIEWPORTS = [
	{ name: '1280x800', width: 1280, height: 800 },
	{ name: '390x844', width: 390, height: 844 }
];

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

// Two live runs, in both tags so before/after differ only by the list: the
// strand wearing w-201's attempt (`is_subspawn: true`, the drone case), and
// the seat's own run (`is_subspawn: false`) — the-row-you-can-judge §4's
// join target for w-205's bare "seat" row. `card_text` on the seat mirrors
// what `Dashboard.svelte` actually feeds `AskList` (the `## Now` projection
// already, per `liveRuns.ts`'s own note on the field) — multi-line, so the
// shot also proves only the first line renders.
const ROUTES = {
	...fixtures.ROUTES,
	'/v1/dashboard/live-runs': {
		...fixtures.liveRuns,
		runs: [
			{
				id: 'presence-asks',
				run_id: 'run-fallback-receipt',
				kind: 'daemon',
				stream: 'telegram:loom:',
				repo_label: 'hugimuni-labs/brnrd',
				topics: ['the-loom'],
				is_subspawn: true,
				boundaries: [
					{ at: new Date().toISOString(), phase: 'tool', act: 'read', tools: ['exec_command'] }
				]
			},
			{
				id: 'presence-seat',
				run_id: 'run-260922-1948-dsgu',
				kind: 'daemon',
				stream: 'spawn:default',
				repo_label: 'hugimuni-labs/brnrd',
				topics: ['the-loom'],
				is_subspawn: false,
				card_text:
					'the row you can judge — collapsed bars, the seat join, receipts\n\nfull card body below the first line',
				boundaries: []
			}
		]
	}
};

async function routePage(page) {
	await page.route('**/v1/dashboard/**', async (route) => {
		const body = ROUTES[new URL(route.request().url()).pathname];
		await route.fulfill({
			status: body ? 200 : 404,
			contentType: 'application/json',
			body: JSON.stringify(body ?? {})
		});
	});
}

async function main() {
	await mkdir(OUT, { recursive: true });
	const vite = spawn('npx', ['vite', 'dev', '--port', String(PORT), '--strictPort'], {
		stdio: ['ignore', 'pipe', 'pipe']
	});
	const modules = exerciseRecorder();
	let browser;
	const report = { tag: TAG };
	try {
		await waitForServer(`http://localhost:${PORT}/`);
		browser = await chromium.launch();
		for (const vp of VIEWPORTS) {
			const context = await browser.newContext({
				viewport: { width: vp.width, height: vp.height },
				deviceScaleFactor: 2,
				reducedMotion: 'reduce' // skips the boot curtain — see routes/+layout.svelte
			});
			const page = await modules.watch(await context.newPage(), 'drive-asks');
			await routePage(page);
			await page.goto(`http://localhost:${PORT}/`, { waitUntil: 'networkidle' });
			await page.waitForSelector('h1', { timeout: 20000 });

			if (TAG === 'after') {
				const list = page.locator('[aria-label="your asks"]');
				await list.waitFor({ state: 'visible', timeout: 15000 });

				// Goals stay goals, above the buckets — §5, one per line now
				// (his 19:46Z: "one goal per line, title only, no metric text,
				// no tooltip"), still just the one goal in this fixture.
				const goalsLine = list.locator('[data-goals]');
				assert.ok(
					(await goalsLine.innerText()).includes('Talk to one entity'),
					'the one goal renders above the buckets'
				);
				assert.equal(
					await goalsLine.locator('p').count(),
					1,
					'one goal renders as its own line, not joined into the label row'
				);

				// The four buckets, his third-pass order — done leads, then the
				// two live buckets, "not started" sinks to the quiet bottom —
				// each headed and counted.
				const inHand = list.locator('[data-bucket-heading="in-hand"]');
				const toJudge = list.locator('[data-bucket-heading="to-judge"]');
				const done = list.locator('[data-bucket-preview="done"]');
				// `uppercase` is a CSS transform (`text-transform`), and Playwright's
				// `innerText` reflects rendered casing (unlike `textContent`) — lower
				// both sides rather than pin a rendering detail these headings don't
				// own semantically.
				const lower = async (locator) => (await locator.innerText()).toLowerCase();
				assert.ok((await lower(done)).includes('done & accepted · 5'), 'five done rows total');
				assert.ok((await lower(inHand)).includes('in hand · 2'), 'two rows in hand');
				assert.ok((await lower(toJudge)).includes('yours to judge · 1'), 'one row to judge');
				// §6, his 19:46Z: "the unaddressed bar's count must not read as
				// neglect: label it `not started · 68`" — the collapsed bar
				// carries the renamed label and its own preview, same shape as
				// done's.
				const notStarted = list.locator('[data-bucket-preview="unaddressed"]');
				const notStartedText = (await lower(notStarted)).trim();
				assert.ok(
					notStartedText.startsWith('▸ not started · 6 —'),
					`renamed label, collapsed bar (${notStartedText})`
				);
				assert.ok(
					!notStartedText.includes('unaddressed'),
					`"unaddressed" itself no longer reads on the bar (${notStartedText})`
				);

				// Order on the page: done first (his "on top"), then the two live
				// buckets, "not started" last.
				const bucketOrder = await list
					.locator('[data-bucket]')
					.evaluateAll((els) => els.map((el) => el.getAttribute('data-bucket')));
				assert.deepEqual(
					bucketOrder,
					['done', 'in-hand', 'to-judge', 'unaddressed'],
					`buckets render done-first (${bucketOrder})`
				);

				// §1, his roast: "when collapsed it should show three last items
				// but occupy one bar … you gotta expand to interact with either
				// of them, the block is only a preview." Collapsed, done and
				// not-started carry zero individually-interactive rows — only
				// their bar. `in hand` + `to judge` are unaffected, still
				// unconditional.
				const rows = list.locator('[data-ask]');
				assert.equal(
					await list.locator('[data-bucket="done"] [data-ask]').count(),
					0,
					'no done row is a click target while the bucket is collapsed'
				);
				assert.equal(
					await list.locator('[data-bucket="unaddressed"] [data-ask]').count(),
					0,
					'same for not-started'
				);
				assert.equal(await rows.count(), 3, 'in-hand×2 + to-judge×1, the two previews collapsed');

				// keyboard: j moves, enter expands — checked here, before any row
				// has been clicked, so `focus` is still null and `j` is
				// guaranteed to land on the very first visible row (in-hand's
				// w-201, done and not-started being collapsed bars with nothing
				// to walk into) rather than wherever a prior row click last left
				// the pointer.
				await page.keyboard.press('j');
				await page.keyboard.press('Enter');
				const open = list.locator('[data-ask][aria-expanded="true"]');
				assert.equal(await open.count(), 1, 'enter expands the focused row');
				const openText = (await open.first().locator('xpath=..').innerText()).replace(/\s+/g, ' ');
				assert.ok(openText.includes('evt-'), `expanded row shows its says (${openText})`);
				await page.keyboard.press('Enter');
				assert.equal(await list.locator('[data-ask][aria-expanded="true"]').count(), 0);

				// The done bar names its newest three titles — expanding it
				// replaces the bar with every row, no window left hidden behind
				// a second toggle (§1 retires the old "N more" shape).
				assert.ok(
					(await lower(done)).includes('a design page for the ask'),
					`the newest done title rides the preview (${await lower(done)})`
				);
				await done.click();
				assert.equal(
					await list.locator('[data-bucket="done"] [data-ask]').count(),
					5,
					'expanding the bar shows every done row at once'
				);
				const doneOpenHeading = list.locator('[data-bucket-heading="done"]');
				assert.equal(
					(await lower(doneOpenHeading)).trim(),
					'▾ done & accepted · 5',
					'the expanded heading names the full count, not a remainder'
				);
				await doneOpenHeading.click();
				assert.equal(
					await list.locator('[data-bucket="done"] [data-ask]').count(),
					0,
					'closes back to the bar'
				);

				// §4, his 19:46Z: "an in-hand seat row shows what the seat is
				// doing." w-201 wears a live strand (the drone mark wins); w-205
				// has no attempt of its own, so it joins the seat's own live run
				// instead of the bare "seat" word.
				const inHandRow = list.locator('[data-ask="w-201"]');
				const inHandText = (await inHandRow.innerText()).replace(/\s+/g, ' ');
				assert.ok(
					inHandText.includes('▷ run-fallback-receipt'),
					`wears its live strand (${inHandText})`
				);
				assert.ok(await inHandRow.locator('[data-drone]').count(), 'live drone mark');

				const seatRow = list.locator('[data-ask="w-205"]');
				const seatCell = seatRow.locator('[data-in-hand]');
				const seatText = (await seatCell.innerText()).replace(/\s+/g, ' ').trim();
				assert.ok(
					seatText.includes('seat') &&
						seatText.includes('the row you can judge — collapsed bars, the seat join, receipts'),
					`the bare-seat row names what the seat is doing (${seatText})`
				);
				assert.ok(
					!seatText.includes('full card body below the first line'),
					`only the card's first line rides the row (${seatText})`
				);
				const seatLink = seatCell.locator('a');
				assert.equal(await seatLink.count(), 1, 'the seat word links to its own run page');
				assert.ok(
					(await seatLink.getAttribute('href')).includes('run-260922-1948-dsgu'),
					'linked to the seat’s actual run id'
				);

				// The 17:51Z steer ("the evt-say lines say nothing"): expand w-201
				// and check its says + attempts detail speaks now — a say with a
				// resolved excerpt + route renders time · excerpt · a real ↗ link;
				// one with neither falls back to its bare event id (`sayText`'s
				// floor, never a blank); the attempt run id links to its run page
				// with the ask's own topic glyph beside.
				await inHandRow.click();
				const inHandDetail = inHandRow.locator('xpath=..');
				const sayWithExcerpt = inHandDetail.getByText('slick ui to inspect the done things');
				assert.ok(await sayWithExcerpt.count(), 'the resolved excerpt renders');
				const sayLink = inHandDetail.locator('a[aria-label="open this message"]');
				assert.equal(await sayLink.count(), 1, 'the say with a url gets a real ↗ link');
				assert.equal(await sayLink.getAttribute('href'), 'https://t.me/c/loom/42');
				assert.ok(
					await inHandDetail.getByText('evt-1790033000000000000-aa01').count(),
					'the excerpt-less say falls back to its event id, not a blank'
				);
				const attemptLink = inHandDetail.locator('a[href*="run-fallback-receipt"]');
				assert.equal(await attemptLink.count(), 1, 'the attempt links to its run page');
				assert.ok(
					(await attemptLink.getAttribute('href')).includes('hugimuni-labs__brnrd'),
					'the one connected repo resolves the run link'
				);
				const attemptLine = (await attemptLink.locator('xpath=..').innerText()).replace(
					/\s+/g,
					' '
				);
				assert.notEqual(
					attemptLine.trim(),
					'run-fallback-receipt',
					`a topic glyph rides beside the run id (${attemptLine})`
				);
				await inHandRow.click(); // collapse it back

				// §2, the judge row you can judge: collapsed, w-96 carries only
				// its id · title · derived delivery line · touched time — the
				// receipt data no longer repeats three times (his roast). The
				// derived line reads the newest receipt's title, never `return:`
				// once a titled receipt exists.
				const w96 = list.locator('[data-ask="w-96"]');
				assert.equal(await w96.getAttribute('data-ask-stale'), '', 'w-96 stays marked stale');
				const w96Text = (await w96.innerText()).replace(/\s+/g, ' ');
				assert.ok(w96Text.includes('w-96 · anltcs'), `id cell speaks the sign (${w96Text})`);
				assert.ok(
					w96Text.includes('ledger step 6a — a hold never stamps ambiguous'),
					`the newest receipt's title is the delivery line (${w96Text})`
				);
				assert.ok(
					!w96Text.includes('in time'),
					`return: text yields to the titled receipt (${w96Text})`
				);
				assert.ok(
					!w96Text.includes('preparation') && !w96Text.includes('1 say'),
					`the old type/say-count line is gone while collapsed (${w96Text})`
				);
				assert.ok(
					!w96Text.toLowerCase().includes('accept'),
					`accept/reroute don't render until the row opens (${w96Text})`
				);
				const w96Li = w96.locator('xpath=..');
				assert.ok(
					(await w96Li.getAttribute('class')).includes('opacity-60'),
					'a stale row dims in its own bucket, not below a rule'
				);

				// Expanded: receipts once as a chip row (linked when a receipt
				// carries a url, plain text when it doesn't yet), then the
				// accept/reroute chips, then says/attempts fold behind their own
				// `▸ history` toggle — closed by default.
				await w96.click();
				const receiptsRow = w96Li.locator('[data-receipts]');
				const receiptsText = (await receiptsRow.innerText()).replace(/\s+/g, ' ');
				assert.ok(
					receiptsText.includes('#2082') && receiptsText.includes('ledger step 6a'),
					`the titled receipt renders (${receiptsText})`
				);
				assert.ok(
					receiptsText.includes('#2081'),
					`the untitled receipt still renders (${receiptsText})`
				);
				const receiptLinks = receiptsRow.locator('a');
				assert.equal(await receiptLinks.count(), 1, 'only the receipt carrying a url is a link');
				assert.ok(
					(await receiptLinks.first().getAttribute('href')).includes('/pull/2082'),
					'linked to the PR the receipt names'
				);

				// The two words are chips (the-lit-rows: "a small chip pair, not
				// body text") and wear the same `uppercase` transform the bucket
				// headings above already do — lower both sides. The sign, not the
				// id, is what they address.
				const hints = w96Li.locator('[data-to-judge-hints]');
				const hintsText = (await hints.innerText()).toLowerCase();
				assert.ok(
					hintsText.includes('accept anltcs'),
					`copyable accept hint uses the sign (${hintsText})`
				);
				assert.ok(
					hintsText.includes('reroute anltcs'),
					`copyable reroute hint uses the sign (${hintsText})`
				);
				assert.ok(
					!hintsText.includes('w-96'),
					`the sign replaces the id in the chips (${hintsText})`
				);

				const historyToggle = w96Li.locator('[data-history-toggle]');
				assert.equal(
					(await w96Li.getByText('none recorded yet').count()) +
						(await w96Li.getByText(/evt-1780000000000000000-old1/).count()),
					0,
					'says/attempts stay folded until history opens'
				);
				await historyToggle.click();
				assert.ok(
					await w96Li.getByText('evt-1780000000000000000-old1').count(),
					'opening history reveals the excerpt-less say, falling back to its event id'
				);
				assert.ok(
					await w96Li.getByText('none yet').count(),
					'and the empty attempts list, both folded a level deeper now'
				);
				await historyToggle.click();
				await w96.click(); // collapse the row back

				// not-started: the quiet block, collapsed to a preview bar until
				// asked (§1 + §6).
				const unaddressedRows = list.locator('[data-bucket="unaddressed"] [data-ask]');
				await notStarted.click();
				assert.equal(await unaddressedRows.count(), 2, 'the bar opens the two non-stale rows');
				const notStartedOpenHeading = list.locator('[data-bucket-heading="unaddressed"]');
				assert.equal(
					(await lower(notStartedOpenHeading)).trim(),
					'▾ not started · 6',
					'the expanded heading keeps the renamed label'
				);

				// His 18:27Z follow-up: the stale rows inside not-started get
				// their own nested collapse — a bare count, then the newest
				// three, then the rest — the non-stale two above untouched.
				const staleHeading = list.locator('[data-bucket-heading="unaddressed-stale"]');
				assert.equal(
					(await staleHeading.innerText()).trim().toLowerCase(),
					'▸ stale · 4',
					'the stale sub-block starts as a bare count'
				);
				await staleHeading.click();
				assert.equal(await unaddressedRows.count(), 5, '2 non-stale + the stale window of 3');
				const staleToggle = list.locator('[data-bucket-toggle="unaddressed-stale"]');
				assert.equal(
					(await staleToggle.innerText()).trim().toLowerCase(),
					'▸ 1 stale',
					'names the one stale row still hidden'
				);
				await staleToggle.click();
				assert.equal(await unaddressedRows.count(), 6, '2 non-stale + all 4 stale');
				await staleToggle.click();
				assert.equal(await unaddressedRows.count(), 5, 'the rest toggle closes back to the window');
				await staleHeading.click();
				assert.equal(
					await unaddressedRows.count(),
					2,
					'the stale sub-block closes back to its count'
				);

				await notStartedOpenHeading.click();
				assert.equal(
					await unaddressedRows.count(),
					0,
					'and the whole bucket closes back to its bar'
				);

				// For the shot: leave the judge row (w-96) open with its receipts
				// showing — the point of this whole pass — and w-201 open beside
				// it for the say/attempt-link work above; done and not-started
				// stay collapsed, their bars the thing to actually judge.
				const first = (await rows.first().innerText()).replace(/\s+/g, ' ');
				await w96.click();
				await inHandRow.click();
				report[vp.name] = { first, openText };
			}
			await page.screenshot({ path: `${OUT}/${TAG}-${vp.name}.png`, fullPage: false });
			await page.screenshot({ path: `${OUT}/${TAG}-${vp.name}-full.png`, fullPage: true });
			await modules.harvest(page, 'drive-asks');
			await context.close();
		}
		await modules.save(OUT, 'drive-asks');
		console.log(JSON.stringify({ ...report, ok: true }, null, 2));
	} finally {
		if (browser) await closeBrowser(browser, 'drive-asks');
		stopVite(vite, 'drive-asks');
	}
}

runDriver(main);
