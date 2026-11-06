// A restart lands where he was (w-kkghfg2vo6do, his 10361): the phone drops the board's tab on every switch to
// Telegram. The last page's parts sit in the browser's cache (lib/board/cache.ts), so the next page paints from them
// and fetches only the parts whose hash moved; the sheet, the grown column and the scrolls come back from
// localStorage (lib/board/place.ts). A 401 forgets the cache. Timing is not measured here (harness.mjs).
import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { open } from './harness.mjs';
import { at, fixture, reindex } from './fixture.mjs';

const BRAVO = 'w-bravo0000000';
const MANY = 60;

// charlie long enough to scroll in a short viewport
const home = () => {
	const parts = fixture();
	const p = parts['thread/charlie'];
	const old = Array.from({ length: MANY }, (_, i) => ({
		id: `s-test-old${String(i).padStart(3, '0')}/x`,
		at: at(-600 + i),
		from: 's-test-char',
		to: 'thread:charlie',
		body: `old letter ${i}`,
		threads: ['charlie']
	}));
	p.letters = [...old, ...p.letters];
	p.count = p.letters.length;
	return reindex(parts);
};

let B, parts;
before(
	async () => (B = await open({ parts: (parts = home()), viewport: { width: 1440, height: 520 } }))
);
after(() => B?.close());

const ready = () => B.page.waitForSelector('.brd .column.threadcol', { timeout: 60_000 });
const col = () => B.page.locator('[data-column="charlie"] .messages');
const since = (n) => B.requests.slice(n).filter((p) => p.startsWith('/v1/loom/'));
const place = () => B.page.evaluate(() => JSON.parse(localStorage.getItem('board.place')));

test('the board remembers its parts, and where he stands a second after he moves', async () => {
	await B.page.locator(`.brd [data-pip="${BRAVO}"]`).first().click();
	await B.page.waitForSelector(`#bead.on .bd.me[data-bead="${BRAVO}"]`);
	await col().evaluate((p) => (p.scrollTop = 100));
	await B.page.$eval('#board', (m) => (m.scrollLeft = 400));
	await B.page.locator('[data-column="alpha"] .heading .state').click();
	await B.page.waitForSelector('[data-column="alpha"] .band.ex');
	await B.page.waitForTimeout(1300);
	const p = await place();
	assert.equal(p.bead, BRAVO);
	assert.equal(p.y.charlie, 100);
	assert.equal(p.y.alpha, 'end');
	assert.equal(p.x, 400);
	assert.ok(p.grown.includes('alpha'));
	assert.equal(await B.page.evaluate(() => localStorage.getItem('board.cached')), '1');
	assert.ok(await B.page.evaluate(() => caches.has('brnrd-board')));
});

test('a restart paints from the cache, fetches no thread part again, and lands where he was', async () => {
	const n = B.requests.length;
	await B.page.reload();
	await ready();
	await B.page.waitForSelector(`#bead.on .bd.me[data-bead="${BRAVO}"]`);
	await B.page.waitForTimeout(300);
	const asked = since(n);
	assert.ok(asked.includes('/v1/loom/index'), 'the index is read from the network');
	assert.deepEqual(
		asked.filter((p) => p.startsWith('/v1/loom/thread/')),
		[],
		'no thread part was fetched again'
	);
	assert.equal(await col().evaluate((p) => p.scrollTop), 100);
	assert.equal(await B.page.$eval('#board', (m) => m.scrollLeft), 400);
	assert.ok(await B.page.locator('[data-column="alpha"] .band.ex').count(), 'alpha is grown');
	assert.deepEqual(B.errors, []);
});

test('a part that moved while the tab was away is the one part fetched, and its letter shows', async () => {
	await B.page.goto('about:blank');
	parts['thread/alpha'].letters.push({
		id: 's-test-alph/away',
		at: at(70),
		from: 's-test-alph',
		to: 'thread:alpha',
		body: 'landed while the tab was away',
		threads: ['alpha']
	});
	parts = reindex(parts);
	B.loom.set(parts);
	const n = B.requests.length;
	await B.page.goto(B.base);
	await ready();
	await B.page.waitForSelector('[data-letter="s-test-alph/away"]');
	assert.deepEqual(
		since(n).filter((p) => p.startsWith('/v1/loom/thread/')),
		['/v1/loom/thread/alpha']
	);
	assert.deepEqual(B.errors, []);
});

test('a 401 forgets the cache: the landing shows, and nothing of his is held', async () => {
	await B.page.route('**/v1/loom/index', (r) =>
		r.fulfill({ status: 401, contentType: 'application/json', body: '{}' })
	);
	await B.page.reload();
	await B.page.waitForSelector('[data-landing]');
	await B.page.waitForFunction(() => localStorage.getItem('board.cached') === null);
	assert.equal(await B.page.evaluate(() => caches.has('brnrd-board')), false);
	assert.equal(await B.page.locator('.brd').count(), 0);
	await B.page.unroute('**/v1/loom/index');
});
