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
		B.requests.filter(
			(p) => p.startsWith('/v1/') && !p.startsWith('/v1/loom/') && p !== '/v1/site'
		),
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

test('the default shows inbox and active columns in order', async () => {
	const cols = await B.page.$$eval('#board > .column', (cs) =>
		cs.map((c) => ({ id: c.dataset.column, b: c.dataset.b, stall: c.classList.contains('stall') }))
	);
	assert.deepEqual(
		cols.map((c) => c.id),
		['inbox', 'alpha', 'bravo', 'charlie', 'echo', 'delta']
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

test('the counted filters select a union, persist on reload and return to the default', async () => {
	const parts = fixture();
	const quietPart = parts['thread/foxtrot'];
	const quietWish = quietPart.wishes[0];
	quietWish.state = 'delivered';
	quietWish.verdict = null;
	quietWish.stamps = quietWish.stamps.filter((s) => s.stamp !== 'accepted');
	quietPart.board.calls = [
		{
			kind: 'verdict',
			wish: quietWish.wish,
			line: quietWish.line,
			at: quietWish.results[0].at,
			stale: true
		}
	];
	B.loom.set(reindex(parts));
	const page = B.page;
	const filters = page.getByRole('group', { name: 'Show threads' });
	assert.deepEqual(
		await filters
			.getByRole('button')
			.evaluateAll((buttons) => buttons.map((button) => button.getAttribute('aria-label'))),
		['Working 1', 'Needs you 1', 'Waiting 1', 'Stuck 2', 'Quiet 1', 'Settled 2']
	);
	const pressed = () =>
		filters
			.getByRole('button', { pressed: true })
			.evaluateAll((buttons) => buttons.map((button) => button.getAttribute('aria-label')));
	const defaults = ['Working 1', 'Needs you 1', 'Waiting 1', 'Stuck 2'];
	assert.deepEqual(await pressed(), defaults);
	const quiet = filters.getByRole('button', { name: 'Quiet 1' });
	const working = filters.getByRole('button', {
		name: 'Working 1',
		description: 'Work in progress'
	});
	const columns = () =>
		page.locator('#board > .column').evaluateAll((cs) => cs.map((c) => c.dataset.column));
	const active = ['inbox', 'alpha', 'bravo', 'charlie', 'echo', 'delta'];
	const withoutWorking = active.filter((id) => id !== 'alpha');
	await working.click();
	assert.deepEqual(await pressed(), defaults.slice(1));
	assert.deepEqual(await columns(), withoutWorking);
	await quiet.click();
	assert.deepEqual(await pressed(), [...defaults.slice(1), 'Quiet 1']);
	assert.deepEqual(await columns(), [...withoutWorking, 'foxtrot']);
	const quietCard = page.locator('[data-column="foxtrot"] .lg-row');
	await quietCard.getByText('not marked', { exact: true }).waitFor();
	assert.equal(await quietCard.locator('.pip.wait').count(), 0);
	await page.reload();
	await quietCard.waitFor();
	assert.deepEqual(await pressed(), [...defaults.slice(1), 'Quiet 1']);
	assert.deepEqual(await columns(), [...withoutWorking, 'foxtrot']);
	for (const name of defaults.slice(1)) await filters.getByRole('button', { name }).click();
	assert.deepEqual(await pressed(), ['Quiet 1']);
	assert.deepEqual(await columns(), ['inbox', 'foxtrot']);
	await quiet.click();
	assert.deepEqual(await pressed(), defaults);
	assert.deepEqual(await columns(), active);
	B.loom.set(fixture());
});

test('topics has its own path; a quote opens and back returns to the board', async () => {
	const page = B.page;
	assert.equal(await page.locator('#board [data-column="topics"]').count(), 0);
	await page.getByRole('link', { name: 'topics', exact: true }).click();
	await page.waitForURL('**/topics');
	const row = page.locator('[data-topic="board"]');
	await row.waitFor();
	assert.equal(await page.locator('#board').count(), 0);
	await row.click();
	assert.match(await row.locator('.from-body').innerText(), /Chose four states/);
	await page.getByRole('link', { name: '← board', exact: true }).click();
	await page.waitForURL(B.base);
	await page.locator('[data-column="alpha"]').waitFor();
	await page.goto(new URL('topics', B.base).href);
	await page.locator('[data-topic="board"]').waitFor();
	assert.deepEqual(B.errors, []);
	await page.getByRole('link', { name: '← board', exact: true }).click();
	await page.waitForURL(B.base);
	await page.locator('[data-column="alpha"]').waitFor();
});

test('person source bars are hidden while their letters remain in the inbox and sheet', async () => {
	const B = await open();
	try {
		const page = B.page;
		assert.equal(await page.locator('.threadcol .from-line').count(), 0);
		const modelHasSource = await page.evaluate(() =>
			window.__board.model.byId.has('letter:mine:100')
		);
		assert.equal(modelHasSource, true);
		await page.locator('[data-column="inbox"] .heading').click();
		const source = page.locator('[data-column="inbox"] [data-letter="letter:mine:100"]');
		assert.match(await source.innerText(), /please make alpha/);
		await page.evaluate(() => window.__board.openBead('letter:mine:100'));
		await page.locator('#bead.on').waitFor();
		assert.match(await page.locator('#bead').innerText(), /please make alpha/);
		await page.keyboard.press('Escape');
		await page.locator('[data-column="inbox"] .heading').click();
	} finally {
		await B.close();
	}
});

test('a new letter patches beads; the part is not fetched whole again', async () => {
	const hits = [];
	const on = (r) => {
		const u = new URL(r.url());
		if (u.pathname.endsWith('/beads')) hits.push(u.pathname + u.search);
	};
	B.page.on('request', on);
	try {
		await B.page.$eval('.brd article.letter', (a) => a.click());
		await B.page.waitForFunction(
			() => !!window.__board.beads && Object.keys(window.__board.beads).length > 1
		);
		assert.equal(hits.filter((h) => h === '/v1/loom/beads').length, 1, hits.join());
		const parts = fixture();
		const prev = parts.index.parts.beads;
		parts.beads = { ...parts.beads, 'letter:new': [null, 'person', 'person:ada', at(99), 'later'] };
		const hashed = reindex(parts);
		hashed.index.beads_delta = {
			since: prev,
			hash: hashed.index.parts.beads,
			set: { 'letter:new': parts.beads['letter:new'] },
			drop: []
		};
		const ready = reindex(hashed);
		B.loom.set(ready);
		await B.page.waitForFunction(
			(h) => window.__board.store.held.get('beads') === h,
			ready.index.parts.beads
		);
		assert.equal(hits.filter((h) => h === '/v1/loom/beads').length, 1, hits.join());
		assert.equal(await B.page.evaluate(() => window.__board.beads['letter:new']?.[4]), 'later');
	} finally {
		B.page.off('request', on);
		await B.page.evaluate(() => (window.__board.select(null), window.__board.closeBead()));
	}
});

test('a settled thread opens beside the stack and folds back', async () => {
	const settled = B.page.getByRole('button', { name: 'Settled 2' });
	await settled.click();
	await B.page.locator('[data-column="~settled"] .srow', { hasText: 'golf' }).click();
	await B.page.waitForSelector('[data-column="golf"]');
	await B.page.locator('[data-column="golf"] .unsettle').click();
	await B.page.waitForSelector('[data-column="golf"]', { state: 'detached' });
	await settled.click();
	await B.page.waitForSelector('[data-column="~settled"]', { state: 'detached' });
});

test('the inbox stacks the calls as one counted line that opens on a tap', async () => {
	const calls = B.page.locator('[data-column="inbox"] .calls');
	assert.equal(await calls.locator('.call').count(), 0);
	assert.equal(
		await calls.locator('.cfold').innerText(),
		'▸ 1 asks you · 1 verdict to give · 1 answered'
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

test('a letter is a two-line head until a press opens it whole', async () => {
	const parts = fixture();
	parts['thread/echo'].letters.push({
		id: 's-test-echo4/long',
		at: at(91),
		from: 's-test-echo4',
		to: 'thread:echo',
		body: Array.from({ length: 12 }, (_, i) => `line ${i} of a long report`).join('\n'),
		threads: ['echo']
	});
	parts['thread/echo'].count++;
	B.loom.set(reindex(parts));
	const card = B.page.locator('[data-letter="s-test-echo4/long"]');
	await card.waitFor();
	const lines = () =>
		card
			.locator('.body')
			.evaluate((e) => Math.round(e.clientHeight / parseFloat(getComputedStyle(e).lineHeight)));
	assert.equal(await lines(), 2);
	await card.locator('.body').click();
	assert.equal(await lines(), 12);
	await B.page.keyboard.press('Escape');
	await card.locator('.body').click();
	assert.equal(await lines(), 2);
	await B.page.keyboard.press('Escape');
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

test('a signed-in cloud page without a board opens Account', async () => {
	const ctx = await B.browser.newContext(),
		page = await ctx.newPage();
	await page.route('**/v1/loom/index', (r) => r.fulfill({ status: 404, body: '{}' }));
	await page.route('**/v1/loom/consent', (r) => r.fulfill({ json: { state: 'none' } }));
	await page.route(
		(url) => url.pathname === '/v1/dashboard/repos',
		(r) =>
			r.fulfill({
				json: {
					account: { github_login: 'ada' },
					connected_repos: []
				}
			})
	);
	await page.goto(B.base);
	await page.getByRole('heading', { name: 'Set up brnrd' }).waitFor();
	assert.equal(new URL(page.url()).pathname, '/account');
	await ctx.close();
});
