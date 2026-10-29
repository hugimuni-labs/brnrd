// The board prompt on `/` (w-g98svlo7bhrb): signed in, no board, and /v1/loom/consent says why. A never-asked loom
// gets the prompt and a no gets a plain page, never the old app; a yes waits, then the first push turns it into the board.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { open } from './harness.mjs';
import { fixture } from './fixture.mjs';

const OLD_APP = ['/v1/dashboard', '/v1/me', '/v1/accounts'];
const oldApp = (B) => B.requests.filter((p) => OLD_APP.some((o) => p.startsWith(o)));

test('a loom never asked: the prompt names what is sent, and a yes waits for the push, then shows the board', async () => {
	const B = await open({
		parts: {},
		consent: { state: 'ask', repo: 'sasha/self', last_seen_at: new Date().toISOString() },
		ready: '[data-consent="ask"]'
	});
	try {
		const text = await B.page.locator('[data-consent]').innerText();
		assert.match(text, /Show your board here\?/);
		assert.match(text, /letter text/);
		assert.match(text, /head of a command up to 120 characters, secrets masked/);
		assert.equal(await B.page.locator('[data-home]').count(), 0);
		await B.page.locator('button.yes').click();
		await B.page.waitForSelector('[data-consent="waiting"]');
		assert.deepEqual(B.loom.answers, [true]);
		B.loom.set(fixture());
		await B.page.waitForSelector('.brd .column.threadcol', { timeout: 15_000 });
		assert.deepEqual(oldApp(B), []);
		assert.deepEqual(B.errors, []);
	} finally {
		await B.close();
	}
});

test('not now: a plain page with a way back, and nothing of the old app', async () => {
	const B = await open({ parts: {}, consent: { state: 'ask' }, ready: '[data-consent="ask"]' });
	try {
		await B.page.locator('button.no').click();
		await B.page.waitForSelector('[data-consent="declined"]');
		assert.deepEqual(B.loom.answers, [false]);
		assert.match(await B.page.locator('[data-consent]').innerText(), /nothing is sent now/);
		await B.page.reload();
		await B.page.waitForSelector('[data-consent="declined"]');
		assert.equal(await B.page.locator('button.yes').innerText(), 'Send my board after all');
		assert.equal(await B.page.locator('[data-home]').count(), 0);
		assert.deepEqual(oldApp(B), []);
		assert.deepEqual(B.errors, []);
	} finally {
		await B.close();
	}
});

test('granted with no board yet waits for the push instead of falling back', async () => {
	const B = await open({
		parts: {},
		consent: { state: 'granted' },
		ready: '[data-consent="waiting"]'
	});
	try {
		assert.equal(await B.page.locator('[data-home]').count(), 0);
		assert.deepEqual(oldApp(B), []);
	} finally {
		await B.close();
	}
});

test('an answer brnrd.dev did not take says so and stays on the prompt', async () => {
	const B = await open({ parts: {}, consent: { state: 'ask' }, ready: '[data-consent="ask"]' });
	try {
		await B.page.route('**/v1/loom/consent', (r) =>
			r.request().method() === 'POST' ? r.fulfill({ status: 500, body: '{}' }) : r.continue()
		);
		await B.page.locator('button.yes').click();
		await B.page.locator('[role="alert"]').waitFor();
		assert.equal(await B.page.locator('[data-consent="ask"]').count(), 1);
	} finally {
		await B.close();
	}
});
