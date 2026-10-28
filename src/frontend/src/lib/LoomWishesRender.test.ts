import { ok, equal } from 'node:assert/strict';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { compile } from 'svelte/compiler';
import { render } from 'svelte/server';

import type { LoomWish } from './loomSnapshot.ts';

// The wishes section as a reader sees it, server-rendered (the compile dance
// HeddleStripRender.test.ts uses). loomSnapshot.test.ts covers the logic;
// this pins which buttons a row offers and the lines that must not read as
// done when they are not.
const here = dirname(fileURLToPath(import.meta.url));
const componentPath = join(here, 'LoomWishes.svelte');
const generated = join(here, '.loomWishes.generated.mjs');
const NOW = Date.parse('2026-10-19T12:00:00Z');
const at = (minutes: number) => new Date(NOW - minutes * 60_000).toISOString();

async function renderWishes(wishes: LoomWish[]): Promise<string> {
	const compiled = compile(readFileSync(componentPath, 'utf8'), {
		generate: 'server',
		runes: true,
		name: 'LoomWishes'
	});
	writeFileSync(generated, compiled.js.code);
	try {
		const module = await import(`${generated}?t=${process.pid}-${Math.random()}`);
		return render(module.default, { props: { wishes, now: NOW } }).body;
	} finally {
		rmSync(generated, { force: true });
	}
}

/** The markup of one row, from its opening tag to the next row's. */
function rowOf(html: string, id: string): string {
	const start = html.indexOf(`data-wish="${id}"`);
	ok(start !== -1, `row ${id} renders`);
	const next = html.indexOf('data-testid="wish"', start + 1);
	return html.slice(start, next === -1 ? undefined : next);
}

after(() => rmSync(generated, { force: true }));

const said = (head: string) => [{ id: 'l-1', from: 'person:arseni', at: at(30), head }];

test('an owed verdict offers agree and correct, a try-it link, and is counted', async () => {
	const html = await renderWishes([
		{
			id: 'w-owed',
			thread: 'loom-on-the-web',
			from: 'person',
			state: 'delivered',
			origin: 'said',
			at: at(30),
			says: said('Body choice belongs on the fuel strip.'),
			reading: 'Tap a shell, pick a core.',
			results: [
				{ kind: 'pr', ref: 'https://github.com/x/y/pull/1', delivered: true, at: at(20) },
				{ kind: 'ui', ref: 'https://brnrd.dev/', line: 'on the strip', delivered: true, at: at(5) }
			]
		}
	]);
	const row = rowOf(html, 'w-owed');
	ok(row.includes('data-owed="true"'), 'the row is marked owed');
	ok(row.includes('data-testid="agree"'), 'agree is offered');
	ok(row.includes('data-testid="correct"'), 'correct is offered');
	ok(row.includes('href="https://brnrd.dev/"'), 'the newest result is the one to try');
	ok(row.includes('+1 earlier'), 'the older result is counted, not shown');
	ok(row.includes('“Body choice belongs on the fuel strip.”'), 'his words, quoted');
	ok(html.includes('1 waits on you'), 'the header counts it');
});

test('an undelivered wish offers only correct; its PR is a link, not a try-it', async () => {
	const row = rowOf(
		await renderWishes([
			{
				id: 'w-granting',
				state: 'granting',
				origin: 'said',
				says: said('Answer asks from the board.'),
				results: [{ kind: 'pr', ref: 'https://github.com/x/y/pull/2', delivered: false }]
			}
		]),
		'w-granting'
	);
	ok(!row.includes('data-testid="agree"'), 'nothing to agree with yet');
	ok(row.includes('data-testid="correct"'), 'correct is always there');
	ok(row.includes('data-testid="result-ref"'), 'the PR links out');
	ok(!row.includes('data-testid="try-it"'), 'a PR is not something to try');
	ok(row.includes('not read yet'), 'no reading says so');
});

test('an answer replaces the buttons: agree is sent, not recorded; correct is sent', async () => {
	const html = await renderWishes([
		{
			id: 'w-agreed',
			state: 'delivered',
			says: said('Keep my line.'),
			answer: { verdict: 'agree', at: at(4), head: 'agree' }
		},
		{
			id: 'w-corrected',
			state: 'delivered',
			says: said('Keep my line.'),
			answer: { verdict: 'correct', at: at(9), head: 'Shorter.' }
		}
	]);
	const agreed = rowOf(html, 'w-agreed');
	ok(agreed.includes('you said agree · 4m ago · sent, not recorded yet'), 'not done yet');
	ok(!agreed.includes('data-testid="agree"'), 'no second tap offered');
	ok(agreed.includes('data-owed="false"'), 'an answered wish no longer waits on him');
	const corrected = rowOf(html, 'w-corrected');
	ok(corrected.includes('you asked for a correction · 9m ago · sent to brnrd'), 'correct is sent');
	ok(corrected.includes('“Shorter.”'), 'his correction, quoted');
	ok(html.includes('nothing waits on you'), 'the header has nothing to count');
});

test('a derived wish is labelled as brnrd’s reading; a resident ask is brnrd asking', async () => {
	const html = await renderWishes([
		{ id: 'w-derived', state: 'read', origin: 'derived', line: 'Never stop when one shell is dry' },
		{ id: 'w-ask', from: 'resident', state: 'scoped', line: 'Pick a price ladder' },
		{ id: 'w-rec', from: 'resident', state: 'heard', line: 'Merge #12?', rec: 'merge it' },
		{ id: 'w-done', state: 'accepted', line: 'Park and wake', verdict: { as: 'accepted' } }
	]);
	const derived = rowOf(html, 'w-derived');
	ok(derived.includes('brnrd’s reading, not your words'), 'derived is not his words');
	ok(!derived.includes('“'), 'and is not quoted');
	const ask = rowOf(html, 'w-ask');
	ok(ask.includes('brnrd asks you'), 'an ask reads as brnrd asking');
	ok(ask.includes('data-owed="true"'), 'an unanswered ask waits on him');
	const rec = rowOf(html, 'w-rec');
	ok(rec.includes('proposed: merge it'), 'the asker’s rec reads as a proposal, not his answer');
	ok(/show\s+1 done/.test(html), 'closed folds behind done');
	ok(!html.includes('data-wish="w-done"'), 'closed rows stay folded');
});

test('an answered ask reads as his answer, not a correction', async () => {
	const at = new Date(NOW - 9 * 60_000).toISOString();
	const html = await renderWishes([
		{
			id: 'w-ask',
			from: 'resident',
			state: 'scoped',
			line: 'Pick a price ladder',
			at: new Date(NOW - 60 * 60_000).toISOString(),
			answer: { verdict: 'correct', at, head: 'The middle one.' }
		}
	]);
	const ask = rowOf(html, 'w-ask');
	ok(ask.includes('you answered · 9m ago · sent to brnrd'), 'an answer is an answer');
	ok(!ask.includes('correction'), 'never a correction');
});

test('no wishes is one quiet line, not a blank', async () => {
	const html = await renderWishes([]);
	ok(html.includes('data-testid="wishes-empty"'), 'the empty line renders');
	ok(html.includes('no wishes yet'), 'and says why');
	equal(html.includes('waits on you'), false, 'no count over nothing');
});
