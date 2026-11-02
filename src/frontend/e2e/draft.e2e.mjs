// proof-draft (v44hse5kpf, 4a46d017): a newer page never reloads by itself, hidden tab or not, empty box or not; a
// tiny update mark takes it on a click, with the draft and its armed wish back. In vite dev
// SvelteKit's `updated` never fires, so the spec sets `__board.newer` as `updated.current` would: this covers the
// policy, not the version poll.
import { after, before, test } from 'node:test';
import assert, { equal, ok } from 'node:assert/strict';
import { open } from './harness.mjs';

const WISH = 'w-test-armed01';
let B;
before(async () => (B = await open()));
after(() => B?.close());

const ready = () => B.page.waitForSelector('.brd .column.threadcol', { timeout: 60_000 });
const newer = () => B.page.evaluate(() => (window.__board.newer = true));
const mark = () => B.page.evaluate(() => (window.__same = 1));
const state = () =>
	B.page.evaluate(() => ({
		same: window.__same === 1,
		text: document.querySelector('#say').value,
		armed: window.__board.armed,
		live: document.querySelector('#live').textContent
	}));
const reloaded = (act) => Promise.all([B.page.waitForEvent('load', { timeout: 20_000 }), act()]);

const draft = `Not quite on &charlie, I think: half a thought ${Date.now() % 1000}`;

test('a newer page never reloads by itself: not while he writes, not on blur, not hidden', async () => {
	await mark();
	await B.page.locator('#say').click();
	await B.page.keyboard.type(draft);
	await B.page.evaluate((w) => (window.__board.armed = w), WISH);
	await newer();
	// blur on plain text: a bare header click lands on its centre, where #update sits once the header is short (#2585)
	await B.page.locator('#at').click();
	await B.page.evaluate(() => {
		Object.defineProperty(document, 'hidden', { value: true, configurable: true });
		document.dispatchEvent(new Event('visibilitychange'));
	});
	await B.page.waitForTimeout(1500);
	const s = await state();
	assert.ok(s.same, 'the page reloaded by itself');
	assert.equal(s.text, draft);
	assert.equal(await B.page.locator('#update').textContent(), 'update');
});

test('the update mark takes the page; the draft and its wish come back', async () => {
	await reloaded(() => B.page.locator('#update').click());
	await ready();
	const s = await state();
	assert.ok(!s.same, 'no reload on the click');
	assert.equal(s.text, draft);
	assert.equal(s.armed, WISH);
	equal(await B.page.locator('#update').count(), 0);
	assert.deepEqual(B.errors, []);
});

test('a failed fetch with a newer page out makes the mark loud, and still no reload', async () => {
	await mark();
	await B.page.evaluate(() => {
		window.__board.newer = true;
		window.__board.error = 'Error: HTTP 500';
	});
	await B.page.waitForTimeout(500);
	ok((await state()).same);
	ok(await B.page.locator('#update.loud').count());
	await B.page.evaluate(() => (window.__board.error = ''));
});
