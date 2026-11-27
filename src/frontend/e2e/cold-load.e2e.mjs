// A cold load paints columns before every thread part has arrived. Two decisions treated that first paint as the
// board: open() returned on the first thread column, and a wish backlink resolved against the partial model and
// never looked again. One part is held back, so a fast machine still fails without the fix.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { open } from './harness.mjs';
import { at, fixture, reindex } from './fixture.mjs';

const LINK = 's-test-char2/link';

test('open returns only once a late thread part has painted its letter', async () => {
	const parts = fixture();
	parts['thread/charlie'].letters.push({
		id: LINK,
		at: at(17),
		from: 's-test-char2',
		to: 'thread:charlie',
		links: ['w-alpha0000000'],
		body: 'a note on the alpha wish',
		threads: ['charlie']
	});
	parts['thread/charlie'].count++;
	const B = await open({ parts: reindex(parts), lag: { 'thread/charlie': 800 } });
	try {
		const id = await B.page.$eval(
			`[data-column="charlie"] article.letter[data-letter="${LINK}"]`,
			(a) => a.dataset.letter
		);
		assert.equal(id, LINK);
	} finally {
		await B.close();
	}
});

test('a wish backlink waits for the cold load before it chooses the review', async () => {
	const parts = fixture();
	parts['thread/inbox'].board.calls.find((call) => call.wish === 'w-bravo0000000').delivery_id =
		'wish.result:bravo-v1';
	const B = await open({
		parts: reindex(parts),
		lag: { 'thread/inbox': 800 },
		path: '/?wish=w-bravo0000000',
		// The review takes the place of the columns, so readiness is the settled board, not a visible column.
		ready: '.brd:not([data-partial])'
	});
	try {
		await B.page.getByRole('heading', { name: 'bravo happens' }).waitFor();
		assert.equal(new URL(B.page.url()).searchParams.get('delivery'), 'wish.result:bravo-v1');
		assert.deepEqual(B.errors, []);
	} finally {
		await B.close();
	}
});
