// A tapped letter's dotted lines (board.html's proof-links and proof-taps, live-board): the lines go to the bead
// panel's ids, and an ordinary tap draws every line the page can draw. Read from the DOM and the drawn paths only.
import { after, before, test } from 'node:test';
import assert, { deepEqual } from 'node:assert/strict';
import { open } from './harness.mjs';
import { at, fixture, reindex } from './fixture.mjs';

const LINK = 's-test-char2/link',
	WISH = 'w-alpha0000000';

// A letter in charlie that links alpha's wish: the loom hangs it under the wish (asks.Beads.parent, edge "link"),
// so its panel reaches across columns: alpha's ledger row, and the wish's card in the inbox.
const home = () => {
	const parts = fixture();
	parts['thread/charlie'].letters.push({
		id: LINK,
		at: at(17),
		from: 's-test-char2',
		to: 'thread:charlie',
		links: [WISH],
		body: 'a note on the alpha wish',
		threads: ['charlie']
	});
	parts['thread/charlie'].count++;
	parts.beads[LINK] = [WISH, 'link', 's-test-char2', at(17), 'a note on the alpha wish'];
	return reindex(parts);
};

let B;
before(async () => (B = await open({ parts: home() })));
after(() => B?.close());

const frames = () =>
	B.page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));

// What the page shows for the selection: its linked cards, the panel's ids, and which drawable targets got a path
// (a path ends within 1 px of the card's edge, at the height the card's line clamps to).
const measure = () =>
	B.page.evaluate(() => {
		const s = document.querySelector('.brd article.letter.sel');
		if (!s) return null;
		const key = (a) => (a.matches('article.letter') ? a.dataset.letter : a.dataset.wish);
		const ts = [...document.querySelectorAll('.brd .linked')].filter(
			(t) => t.offsetParent && !t.contains(s) && !s.contains(t)
		);
		const ends = [...document.querySelectorAll('#links path')].map((p) =>
			p
				.getAttribute('d')
				.match(/([-\d.]+) ([-\d.]+)$/)
				.slice(1)
				.map(Number)
		);
		const inbox = document.querySelector('.brd .column.inbox')?.getBoundingClientRect(),
			W = document.querySelector('#board').getBoundingClientRect().right;
		const drawable = ts.filter((t) => {
			const c = t.closest('.column'),
				cr = c.getBoundingClientRect(),
				r = t.getBoundingClientRect();
			return (
				c !== s.closest('.column') &&
				!(cr.right < 0 || cr.left > W) &&
				!(!t.closest('.inbox') && inbox && r.right <= inbox.right + 4)
			);
		});
		const hit = (t) => {
			const r = t.getBoundingClientRect();
			return ends.some(([x]) => Math.abs(x - r.left) < 1 || Math.abs(x - r.right) < 1);
		};
		const panel = new Set(
			[...document.querySelectorAll('#bead [data-bead]')].map((e) => e.dataset.bead)
		);
		const on = [
			...document.querySelectorAll('.brd [data-wish], .brd article.letter[data-letter]')
		].filter(
			(a) => a !== s && a.offsetParent && !a.contains(s) && !s.contains(a) && panel.has(key(a))
		);
		return {
			id: s.dataset.letter,
			targets: ts.map(key),
			drawn: drawable.filter(hit).map(key),
			drawable: drawable.map(key),
			paths: ends.length,
			panel: [...panel],
			missed: [...new Set(on.filter((a) => !ts.includes(a)).map(key))]
		};
	});

test('a tapped letter’s lines are its bead panel’s ids, across columns (proof-links)', async () => {
	await B.page.$eval(`[data-column="charlie"] article.letter[data-letter="${LINK}"]`, (a) =>
		a.click()
	);
	await B.page.waitForFunction(
		(id) =>
			document.querySelector('#bead .bid')?.textContent === id &&
			document.querySelectorAll('#bead [data-bead]').length > 1,
		LINK
	);
	await frames();
	const r = await measure();
	assert.equal(r.id, LINK);
	assert.ok(r.panel.includes(WISH), `the panel holds ${WISH}: ${r.panel}`);
	assert.ok(r.drawn.includes(WISH), `a line ends on ${WISH}'s card: ${JSON.stringify(r)}`);
	assert.deepEqual(
		r.targets.filter((t) => !r.panel.includes(t)),
		[],
		'no target outside the panel'
	);
	assert.deepEqual(r.missed, [], 'every panel id with a card on the page is a target');
	assert.equal(r.paths, r.drawable.length, 'one path per drawable target');
	assert.deepEqual(B.errors, []);
	await B.page.evaluate(() => (window.__board.select(null), window.__board.closeBead()));
});

test('ordinary taps draw every line the page can draw (proof-taps)', async () => {
	const ids = await B.page.$$eval('.brd article.letter', (as) =>
		as.filter((a) => a.offsetParent).map((a, i) => ((a.dataset.proof = i), i))
	);
	let tapped = 0;
	const drew = [];
	const miss = [];
	for (const k of ids) {
		const id = await B.page.evaluate((k) => {
			const a = document.querySelector(`article.letter[data-proof="${k}"]`);
			if (!a?.isConnected) return null;
			a.scrollIntoView({ block: 'center', inline: 'center' });
			a.click();
			return a.dataset.letter;
		}, k);
		if (!id) continue;
		await B.page.waitForFunction(() => !!window.__board.beads);
		await frames();
		const r = await measure();
		await B.page.evaluate(() => (window.__board.select(null), window.__board.closeBead()));
		if (!r) continue;
		tapped++;
		if (r.paths) drew.push(id);
		if (r.drawn.length !== r.drawable.length || r.paths !== r.drawable.length)
			miss.push(`${id} drew ${r.drawn.length}/${r.paths} of ${r.drawable.length}`);
	}
	assert.ok(tapped >= 8, `tapped ${tapped} of ${ids.length} letters`);
	assert.deepEqual(miss, [], 'drawable targets without a path');
	// board.html asked a third of its live taps to draw; here the synthetic home says exactly which letters reach a
	// card in another column: alpha's strand letter (strand → thread → wish → the inbox card) and the link letter.
	// The rest are replies to a person's ask, which the inbox draws as its wish card, not as the letter.
	deepEqual(drew.sort(), ['s-test-alph0/a1', LINK], `taps that drew a line of ${tapped}`);
	assert.deepEqual(B.errors, []);
});
