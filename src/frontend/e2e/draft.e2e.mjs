// proof-draft (v44hse5kpf): a half-typed draft survives a newer page. A visible tab holds and says so;
// a visible tab never reloads, empty box or not; the tab going away takes it, with the draft and its armed wish back. In vite dev
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
// the tab goes away, as a switch to another tab does
const hide = () =>
	B.page.evaluate(() => {
		Object.defineProperty(document, 'hidden', { value: true, configurable: true });
		document.dispatchEvent(new Event('visibilitychange'));
	});
const reloaded = (act) => Promise.all([B.page.waitForEvent('load', { timeout: 20_000 }), act()]);

const draft = `Not quite on &charlie, I think: half a thought ${Date.now() % 1000}`;

test('a newer page waits on a tab he is looking at, and says so', async () => {
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

test('leaving the box does not reload; the tab going away does, and the draft and its wish come back', async () => {
	await B.page.locator('header').click();
	await B.page.waitForTimeout(1000);
	assert.ok((await state()).same, 'the page reloaded on blur');
	await reloaded(hide);
	await ready();
	const s = await state();
	assert.ok(!s.same, 'no reload when the tab went away');
	assert.equal(s.text, draft);
	assert.equal(s.armed, WISH);
	assert.doesNotMatch(s.live, /newer page/);
});

test('an empty box on a visible tab holds too; away, it reloads and nothing comes back', async () => {
	await B.page.locator('#say').fill('');
	await B.page.locator('header').click();
	await mark();
	await newer();
	await B.page.waitForTimeout(1500);
	assert.ok((await state()).same, 'a visible tab reloaded');
	await reloaded(hide);
	await ready();
	const s = await state();
	assert.ok(!s.same);
	assert.equal(s.text, '');
	assert.deepEqual(B.errors, []);
});
