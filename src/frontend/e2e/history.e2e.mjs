// History (design §D4): a thread part ships its newest 200 letters; scrolled to the top, a column loads the 200 before
// the oldest it holds, the reader stays on the letter they were reading, and a new letter does not drop what came in.
import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { open } from './harness.mjs';
import { at, fixture, reindex } from './fixture.mjs';

const OLD = 250;
const id = (i) => `s-test-old${String(i).padStart(3, '0')}/x`;

const home = () => {
	const parts = fixture();
	const p = parts['thread/charlie'];
	const old = Array.from({ length: OLD }, (_, i) => ({
		id: id(i),
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
const asked = [];
before(async () => {
	B = await open({ parts: (parts = home()) });
	B.page.on('request', (r) => asked.push(r.url()));
});
after(() => B?.close());

const col = () => B.page.locator('[data-column="charlie"] .messages');
const held = () =>
	B.page.$$eval('[data-column="charlie"] [data-letter^="s-test-old"]', (ls) =>
		ls.map((l) => l.dataset.letter)
	);

test('scrolled to the top, a column loads the letters before its oldest and keeps the reader’s place', async () => {
	await col().locator('.letter').first().waitFor();
	const first = await held();
	assert.ok(
		first.length > 0 && first.length < OLD,
		`a window, not the whole thread: ${first.length}`
	);
	assert.ok(!first.includes(id(0)));

	const anchor = first[0];
	const top = () =>
		B.page.$eval(
			`[data-column="charlie"] [data-letter="${anchor}"]`,
			(l) => l.getBoundingClientRect().top
		);
	// Read the anchor in the same task that scrolls, before the load can land.
	const was = await col().evaluate((p, a) => {
		p.scrollTop = 0;
		return p.querySelector(`[data-letter="${a}"]`).getBoundingClientRect().top;
	}, anchor);
	await col()
		.locator(`[data-letter="${id(0)}"]`)
		.waitFor({ state: 'attached' });
	await B.page.waitForTimeout(100);
	assert.ok(Math.abs((await top()) - was) < 40, 'the letter under the reader did not jump');
	assert.equal((await held()).length, OLD);
	assert.ok(asked.some((u) => u.includes(`/thread/charlie?before=${encodeURIComponent(anchor)}`)));
	assert.equal(await col().locator('.older').count(), 0, 'nothing older is left to load');
});

test('a new letter on the thread keeps the history a reader scrolled in', async () => {
	parts['thread/charlie'].letters.push({
		id: 's-test-char/new',
		at: at(50),
		from: 's-test-char',
		to: 'thread:charlie',
		body: 'a new one',
		threads: ['charlie']
	});
	parts = reindex(parts);
	B.loom.set(parts);
	await col().locator('[data-letter="s-test-char/new"]').waitFor({ state: 'attached' });
	assert.equal((await held()).length, OLD);
});
