// A letter's stamps (board.html stampsHtml): the strand that picked it up first, `picked up · <strand> <time>`, then
// its wishes' stamps; and the header's loom clock, `loom HH:MM:SSZ`, which moves when the index comes back.
import { after, before, test } from 'node:test';
import { equal, match, notEqual, ok } from 'node:assert/strict';
import { open } from './harness.mjs';
import { fixture, reindex } from './fixture.mjs';

let B;
before(async () => (B = await open({ parts: fixture() })));
after(() => B?.close());

test('a letter a strand picked up says so before its wish stamps', async () => {
	// alpha's reply to the ask, which the inbox's strand picked up
	const reply = 's-test-alph0/a0';
	const st = await B.page.$$eval(
		`[data-column="alpha"] article.letter[data-letter="${reply}"] .stamp`,
		(ss) => ss.map((s) => [s.className, s.textContent])
	);
	ok(st.length > 1, JSON.stringify(st));
	equal(st[0][0], 'stamp read');
	match(st[0][1], /^picked up · inbox0 \d\d-\d\d \d\d:\d\d$/);
	ok(
		st.slice(1).every(([, t]) => / · 000000 /.test(t)),
		JSON.stringify(st)
	);
});

test('the header carries the loom clock and it moves with the index', async () => {
	const clock = () => B.page.$eval('#at', (e) => e.textContent.trim());
	const was = await clock();
	match(was, /^loom \d\d:\d\d:\d\dZ · \d+ letters · as /);
	await B.page.waitForTimeout(1100);
	const parts = fixture();
	parts.index.threads[1].strands.slice(-1)[0].state = 'parked';
	B.loom.set(reindex(parts));
	await B.page.waitForFunction((w) => document.querySelector('#at').textContent.trim() !== w, was);
	notEqual(await clock(), was);
});
