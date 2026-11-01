// The board on the synthetic home: its own layout, the column order (board.html's proof-order and proof-stall), the
// inbox's call stack (proof-calls), a fact moving within the §D7 second, and the composer's letter.
import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { open } from './harness.mjs';
import { at, fixture, reindex } from './fixture.mjs';

let B;
before(async () => (B = await open()));
after(() => B?.close());

test('the board has its own layout: no app fetches, no grain, no page errors', async () => {
	assert.deepEqual(
		B.requests.filter((p) => p.startsWith('/v1/') && !p.startsWith('/v1/loom/')),
		[]
	);
	const grain = await B.page.evaluate(() => getComputedStyle(document.body, '::before').display);
	assert.equal(grain, 'none');
	assert.equal(
		await B.page.evaluate(() => !!window.__board?.model),
		true,
		'window.__board holds the model'
	);
	assert.deepEqual(B.errors, []);
});

test('columns stand inbox, seated, your call, mine, waiting, no body, quiet, then the settled stack', async () => {
	const cols = await B.page.$$eval('#board > .column', (cs) =>
		cs.map((c) => ({ id: c.dataset.column, b: c.dataset.b, stall: c.classList.contains('stall') }))
	);
	assert.deepEqual(
		cols.map((c) => c.id),
		['inbox', 'alpha', 'bravo', 'charlie', 'echo', 'delta', 'foxtrot', '~settled']
	);
	assert.deepEqual(
		cols.filter((c) => c.stall).map((c) => c.id),
		['delta']
	);
	const delta = B.page.locator('[data-column="delta"] .heading');
	assert.equal(await delta.locator('.badge').innerText(), 'no body');
	assert.match(
		await delta.locator('.why').innerText(),
		/^no body, \d+ open wish(es)? \(.+\): wake or settle$/
	);
	assert.equal(
		await B.page.locator('[data-column="echo"] .why').innerText(),
		'waiting: the forge to come back'
	);
	assert.equal(await B.page.locator('[data-column="bravo"] .badge').innerText(), 'verdict');
});

test('a settled thread opens beside the stack and folds back', async () => {
	await B.page.locator('[data-column="~settled"] .srow', { hasText: 'golf' }).click();
	await B.page.waitForSelector('[data-column="golf"]');
	await B.page.locator('[data-column="golf"] .unsettle').click();
	await B.page.waitForSelector('[data-column="golf"]', { state: 'detached' });
});

test('the inbox stacks the calls as one counted line that opens on a tap', async () => {
	const calls = B.page.locator('[data-column="inbox"] .calls');
	assert.equal(await calls.locator('.call').count(), 0);
	assert.equal(
		await calls.locator('.cfold').innerText(),
		'▸ 1 asks you · 1 verdict waits on you · 1 answered'
	);
	await calls.locator('.cfold').click();
	assert.equal(await calls.locator('.call').count(), 3);
	assert.equal(await calls.locator('.call.ask [data-act="done"]').count(), 1);
	assert.equal(await calls.locator('.call.shut').count(), 1);
	assert.equal(await calls.locator('.call:not(.ask):not(.shut) [data-act="agree"]').count(), 1);
	await calls.locator('.cfold').click();
});

test('the seated column shows its body at work: tape, now-line, plan', async () => {
	const alpha = B.page.locator('[data-column="alpha"]');
	assert.match(
		await alpha.locator('.nowbar').innerText(),
		/^now · step 2 · building alpha · running \S+ · Bash · npm run build$/
	);
	assert.match(await alpha.locator('.heading .state').innerText(), /plan 1\/2/);
});

test('a new letter in a part reaches its column within a second (design §D7)', async () => {
	const parts = fixture();
	const l = {
		id: 's-test-echo4/new',
		at: at(90),
		from: 's-test-echo4',
		to: 'thread:echo',
		body: 'the forge is back',
		threads: ['echo']
	};
	parts['thread/echo'].letters.push(l);
	parts['thread/echo'].count++;
	const t0 = Date.now();
	B.loom.set(reindex(parts));
	await B.page
		.locator('[data-column="echo"] .letter', { hasText: 'the forge is back' })
		.waitFor({ timeout: 1000 });
	console.log(`fact visible in ${Date.now() - t0} ms`);
});

test('the inbox opens on its wishes, done above and open below; its heading turns it to letters', async () => {
	const inbox = B.page.locator('[data-column="inbox"]');
	const states = await inbox.locator('.wishpane .wc small').allInnerTexts();
	assert.ok(states.length > 0);
	const done = states.map((s) => /^(delivered|accepted|dropped|declined) /.test(s));
	assert.deepEqual(
		done,
		[...done].sort((a, b) => +b - +a),
		'every done card stands above every open one'
	);
	assert.match(
		await inbox.locator('.heading .tog').innerText(),
		/^wishes \d+ open of \d+ · ▸ letters$/
	);
	assert.equal(await inbox.locator('.chat .letter').count(), 0);
	await inbox.locator('.wishpane .wc .wch').last().click();
	await inbox.locator('.wishpane .wc.open .insp').waitFor();
	await inbox.locator('.heading .state').click();
	await inbox.locator('.chat .letter').first().waitFor();
	assert.equal(await inbox.locator('.wishpane .wc').count(), 0);
	assert.match(await inbox.locator('.heading .tog').innerText(), /^letters · ▸ wishes \d+$/);
});

test('the composer sends a letter to the inbox and shows it pending', async () => {
	await B.page.fill('#say', 'hello from the board');
	await B.page.click('#compose button');
	await B.page.waitForFunction(() => document.querySelector('#say').value === '');
	assert.equal(B.loom.sent.at(-1).body, 'hello from the board');
	await B.page
		.locator('[data-column="inbox"] .letter', { hasText: 'hello from the board' })
		.waitFor({ timeout: 2000 });
	assert.deepEqual(B.errors, []);
});

test('signed out, `/` keeps the landing it drew while undecided and never mounts the old home', async () => {
	const ctx = await B.browser.newContext(),
		page = await ctx.newPage();
	await page.route('**/v1/loom/**', (r) => r.fulfill({ status: 401, body: '{}' }));
	await page.route('**/v1/dashboard/**', (r) => r.fulfill({ status: 401, body: '{}' }));
	const probed = page.waitForResponse((r) => r.url().includes('/v1/loom/index'));
	await page.goto(B.base);
	await probed;
	await page.waitForTimeout(300);
	assert.equal(await page.locator('[data-landing] h1').count(), 1);
	assert.equal(await page.locator('[data-home]').count(), 0);
	assert.equal(await page.locator('.brd').count(), 0);
	await ctx.close();
});

test('a localhost page with no loom behind it keeps the old home, as the repro drivers expect', async () => {
	const ctx = await B.browser.newContext(),
		page = await ctx.newPage();
	await page.route('**/v1/loom/**', (r) => r.fulfill({ status: 404, body: 'no loom' }));
	await page.route('**/v1/dashboard/**', (r) => r.fulfill({ status: 401, body: '{}' }));
	await page.goto(B.base);
	// Wait on the decided home (+page.svelte's `data-home`), not on `main`: the only `main` this path draws is
	// LoomHome's "reaching brnrd…" placeholder, gone once the 401s land.
	await page.locator('[data-home]').waitFor({ state: 'attached' });
	assert.equal(
		await page.evaluate(() => document.documentElement.classList.contains('board')),
		false
	);
	assert.equal(await page.locator('.brd').count(), 0);
	await ctx.close();
});
