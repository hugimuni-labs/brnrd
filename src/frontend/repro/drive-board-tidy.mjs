// Real parts-board captures, using synthetic letters. Run on the base and head:
// node repro/drive-board-tidy.mjs --out DIR [--before --root /path/to/base/src/frontend]
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
const args = process.argv.slice(2);
const arg = (name) => args[args.indexOf(name) + 1];
const out = resolve(args.includes('--out') ? arg('--out') : 'repro/shots/board-tidy');
const root = resolve(args.includes('--root') ? arg('--root') : '.');
const before = args.includes('--before');
process.chdir(root);
const { open } = await import(pathToFileURL(resolve(root, 'e2e/harness.mjs')));
const { fixture, reindex } = await import(pathToFileURL(resolve(root, 'e2e/fixture.mjs')));
await mkdir(out, { recursive: true });
const parts = fixture();
// Include a person letter in the thread's chat as well as the inbox-origin rows.
parts['thread/alpha'].letters.push({
	id: 'letter:mine:chat',
	from: 'person:ada',
	person: true,
	at: '2026-10-27T09:05:00Z',
	to: 'thread:alpha',
	threads: ['alpha', 'inbox'],
	body: 'Keep this message in the chat.'
});
const B = await open({ parts: reindex(parts) });
try {
	const { page } = B;
	await page.locator('.boot-glitch').waitFor({ state: 'hidden' });
	const alpha = page.locator('[data-column="alpha"]');
	await alpha.scrollIntoViewIfNeeded();
	await page.screenshot({ path: `${out}/board.png` });
	await alpha.screenshot({ path: `${out}/thread-bars.png` });
	const rows = await alpha.locator('.from-line').count();
	assert.equal(rows > 0, before, 'only the base draws the person source rows');
	assert.equal(
		await alpha.locator('.chat .letter.person').count(),
		1,
		'thread chat keeps person letters'
	);
	await page.locator('[data-column="inbox"] .heading').click();
	assert.ok((await page.locator('[data-column="inbox"] .chat .letter.person').count()) > 0);
	if (!before) {
		assert.equal(await page.locator('#tabs a[href="#topics"]').count(), 0);
		await page.getByRole('link', { name: 'topics', exact: true }).click();
		await page.waitForURL('**/topics');
		const topic = page.locator('[data-topic="board"]');
		await topic.waitFor();
		await topic.click();
		assert.match(await topic.innerText(), /Chose four states/);
		await page.screenshot({ path: `${out}/topics.png` });
		await topic.locator('button.ref').click();
		await page.locator('#bead.on').waitFor();
		await page.getByRole('link', { name: '← board', exact: true }).click();
		await page.waitForURL(B.base);
		await page.locator('[data-column="alpha"]').waitFor();
		await page.goto(new URL('topics', B.base).href);
		await page.locator('[data-topic="board"]').waitFor();
	}
	assert.deepEqual(B.errors, []);
	await writeFile(
		`${out}/report.json`,
		JSON.stringify({ before, sourceRows: rows, errors: B.errors, ok: true }, null, 2)
	);
	console.log(`board-tidy: passed, screenshots in ${out}`);
} finally {
	await B.close();
}
