// proof-draft (v44hse5kpf): a half-typed draft survives a newer page. While he writes, the page holds and says so;
// leaving the box takes the reload with the draft and its armed wish back; an empty box reloads at once. In vite dev
// SvelteKit's `updated` never fires, so the spec sets `__board.newer` as `updated.current` would: this covers the
// policy, not the version poll.
import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
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

test('a newer page waits while he writes, and says so', async () => {
	await mark();
	await B.page.locator('#say').click();
	await B.page.keyboard.type(draft);
	await B.page.evaluate((w) => (window.__board.armed = w), WISH);
	await newer();
	await B.page.waitForTimeout(1500);
	const s = await state();
	assert.ok(s.same, 'the page reloaded under him');
	assert.equal(s.text, draft);
	assert.match(s.live, /newer page/);
});

test('leaving the box takes the reload; the draft and its wish come back', async () => {
	await reloaded(() => B.page.locator('header').click());
	await ready();
	const s = await state();
	assert.ok(!s.same, 'no reload on blur');
	assert.equal(s.text, draft);
	assert.equal(s.armed, WISH);
	assert.doesNotMatch(s.live, /newer page/);
});

test('an empty box reloads at once, and nothing comes back', async () => {
	await B.page.locator('#say').fill('');
	await B.page.locator('header').click();
	await mark();
	await reloaded(newer);
	await ready();
	const s = await state();
	assert.ok(!s.same);
	assert.equal(s.text, '');
	assert.deepEqual(B.errors, []);
});
