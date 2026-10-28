// board.html's behavioural proofs (rooms' proof-*.mjs) as specs over the synthetic home, for the parts the SPA draws
// today: chips (proof-chips), sides (proof-sides), steps under their wish (proof-progress, proof-left) and the reorder
// hold (proof-order, board-states/proof-hold). Each reads the DOM or geometry, never the page's internals.
import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { open } from './harness.mjs';
import { at, fixture, reindex } from './fixture.mjs';

const GRAMMAR =
	'see w-qa0bi21dc0hj, letter:mine:155783668:4954. s-ae66-jyyh16/wejeb (board-9mswfh0474) thread:beads; ' +
	's-ae66-86difl &4954 but not xw-qa0bi21dc0hj, w-short, wejeb, a/w-qa0bi21dc0hjx';

const home = () => {
	const parts = fixture();
	parts['thread/charlie'].letters.push({
		id: 's-test-char2/chips',
		at: at(16),
		from: 's-test-char2',
		to: 'thread:charlie',
		body: GRAMMAR + ' and &w-charlie0000000',
		threads: ['charlie']
	});
	parts['thread/charlie'].count++;
	return reindex(parts);
};

let B;
before(async () => (B = await open({ parts: home() })));
after(() => B?.close());

const order = () => B.page.$$eval('#board > .column', (cs) => cs.map((c) => c.dataset.column));

test('a bare id or an &ref is a chip; a word that only looks like one stays text (proof-chips)', async () => {
	const l = B.page.locator('[data-letter="s-test-char2/chips"]');
	await l.waitFor();
	assert.deepEqual(await l.locator('.body .ref').allInnerTexts(), [
		'w-qa0bi21dc0hj',
		'letter:mine:155783668:4954',
		's-ae66-jyyh16/wejeb',
		'board-9mswfh0474',
		'thread:beads',
		's-ae66-86difl',
		'&4954',
		'&w-charlie0000000'
	]);
	await l.locator('.body .ref', { hasText: '&w-charlie0000000' }).click();
	const sheet = B.page.locator('#bead');
	await sheet.waitFor();
	// the sheet opens before the beads part lands; on a slow runner the first read sees only its header
	await sheet
		.getByText(/charlie happens/)
		.first()
		.waitFor();
	assert.match(await sheet.innerText(), /charlie happens/);
	await B.page.keyboard.press('Escape');
	await sheet.waitFor({ state: 'hidden' });
});

test('a person’s letters sit on the right, a strand’s on the left, by geometry (proof-sides)', async () => {
	const inbox = B.page.locator('[data-column="inbox"]');
	await inbox.locator('.heading .state').click();
	await inbox.locator('.chat .letter').first().waitFor();
	const got = await B.page.$$eval('#board .letter', (ls) =>
		ls
			.filter((a) => a.offsetParent)
			.map((a) => {
				const R = a.getBoundingClientRect(),
					M = a.parentElement.getBoundingClientRect();
				return {
					id: a.dataset.letter,
					cls: a.className,
					col: a.closest('.column')?.dataset.column,
					person: a.classList.contains('person'),
					right: R.left - M.left > M.right - R.right
				};
			})
	);
	await inbox.locator('.heading .state').click();
	assert.ok(got.some((g) => g.person) && got.some((g) => !g.person), 'both sides drawn');
	assert.deepEqual(
		got.filter((g) => g.person !== g.right),
		[]
	);
});

test('a thread’s steps sit under the wish they cite, none loose, no unplanned fold (proof-progress, proof-left)', async () => {
	const alpha = B.page.locator('[data-column="alpha"]');
	const row = alpha.locator('.lg-row', { hasText: 'alpha happens' });
	assert.deepEqual(
		(await row.locator('.stp .st').allInnerTexts()).map((t) => t.replace(/\s+/g, ' ')),
		['✓ write the parserabc1234', '→ wire the parser']
	);
	assert.equal(await alpha.locator('.stp-h').count(), 0, 'no unplanned fold');
	assert.equal(await alpha.locator('.lg > .stp').count(), 0, 'no step outside a wish row');
});

test('the order holds for 10 s, then moves once; the same data moves nothing (proof-order, proof-hold)', async () => {
	const first = await order();
	B.loom.set(reindex(fixture()));
	await B.page.waitForTimeout(600);
	assert.deepEqual(await order(), first, 'a render with the same data moves no column');
	const parts = fixture();
	const fox = parts.index.threads.find((t) => t.id === 'foxtrot');
	fox.board = { state: 'seated', strand: 'working' };
	fox.strands[0].state = 'working';
	B.loom.set(reindex(parts));
	await B.page.waitForTimeout(600);
	const held = await order();
	assert.equal(held.indexOf('foxtrot'), first.indexOf('foxtrot'), 'inside the hold it stays put');
	await B.page.waitForFunction(
		() =>
			[...document.querySelectorAll('#board > .column')]
				.slice(1, 3)
				.some((c) => c.dataset.column === 'foxtrot'),
		null,
		{ timeout: 12_000 }
	);
	const moved = await order();
	assert.equal(moved[0], 'inbox', 'the inbox does not move');
	assert.deepEqual(moved.slice(1, 3).sort(), ['alpha', 'foxtrot'], 'it joins the seated block');
	assert.deepEqual(B.errors, []);
});
