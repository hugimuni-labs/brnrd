import { test } from 'node:test';
import { deepEqual, equal } from 'node:assert/strict';
import { timeline, trace } from './bead.ts';
import { assemble, type Beads } from './parts.ts';
import type { Wish } from './types.ts';

const ASK = 'letter:telegram:10823',
	REPLY = 's-ae66-3t7887/u0wof',
	WISH = 'w-z1t072x4a7r5';
const at = '2026-11-11T13:31:12Z';

// send --re 10823 --wish ... --says ... --answered: the say is on HIS letter;
// the reply has no wish property and is itself the delivered result, kind letter.
function fixture() {
	const wish: Wish = {
		wish: WISH,
		line: 'Ideas for the virtual booth',
		state: 'delivered',
		origin: 'said',
		stamps: [
			{ stamp: 'heard', at },
			{ stamp: 'delivered', at, ref: REPLY }
		],
		says: [{ id: ASK, quote: 'What about this draft?', cites: 'whole' }],
		results: [{ ref: REPLY, kind: 'letter', delivered: true, at }]
	};
	const letters = [
		{ id: ASK, from: 'person:ada', body: 'What about this draft?', at },
		{ id: REPLY, from: 's-ae66-3t7887', body: 'Send it.', re: ASK, at }
	];
	const model = assemble(
		{ v: 1, threads: [{ id: 'inbox' }], wishes: {}, person: 'ada', parts: {} },
		new Map([['inbox', { id: 'inbox', count: 2, letters, wishes: [wish], tools: {}, live: {} }]])
	);
	const beads: Beads = {
		[ASK]: [null, 'person', 'person:ada', at, letters[0].body],
		[REPLY]: [ASK, 're', 's-ae66-3t7887', at, letters[1].body],
		[WISH]: [ASK, 'says', 'person:ada', at, wish.line]
	};
	return { beads, model, wish };
}

test('an answered reply finds the wish said by its source and traces the answer to that wish', () => {
	const { beads, model } = fixture();
	const T = timeline(beads, model, REPLY);
	deepEqual(
		T.chain.map((b) => b.id),
		[ASK]
	);
	deepEqual(
		T.wishes.map((w) => w.wish),
		[WISH]
	);
	deepEqual(trace(undefined, T), [
		{ t: 'answered', at, ref: WISH, line: 'Ideas for the virtual booth' }
	]);
	deepEqual(
		timeline(beads, model, ASK).wishes.map((w) => w.wish),
		[WISH]
	);
});

test('says follow replies upward even when the source letters have paged out of the model', () => {
	const { beads, model } = fixture();
	model.byId.clear();
	beads.followup = [REPLY, 're', 's-ae66-3t7887', at, 'More detail'];
	const T = timeline(beads, model, 'followup');
	deepEqual(
		T.wishes.map((w) => w.wish),
		[WISH]
	);
	equal(trace(undefined, T).length, 0, 'a followup is associated, but is not itself a result');
});

test('a result ref finds a wish even without a say or reply, and never appears twice', () => {
	const { beads, model, wish } = fixture();
	beads[REPLY][0] = null;
	model.byId.get(REPLY)!.re = undefined;
	wish.says = [];
	deepEqual(
		timeline(beads, model, REPLY).wishes.map((w) => w.wish),
		[WISH]
	);
	equal(trace(undefined, timeline(beads, model, REPLY)).length, 1);
});

test('several wishes in an exchange all appear; links do not make an unrelated letter part of it', () => {
	const { beads, model, wish } = fixture();
	model.wishes.push({ ...wish, wish: 'w-second000000', results: [] });
	deepEqual(
		timeline(beads, model, REPLY).wishes.map((w) => w.wish),
		[WISH, 'w-second000000']
	);
	beads.unrelated = [ASK, 'link', 's-ae66-3t7887', at, 'a passing reference'];
	deepEqual(timeline(beads, model, 'unrelated').wishes, []);
	deepEqual(trace(undefined, timeline(beads, model, 'unrelated')), []);
});

test('reply cycles terminate, and a wish view keeps its existing trace', () => {
	const { beads, model, wish } = fixture();
	beads[ASK][0] = REPLY;
	beads[ASK][1] = 're';
	deepEqual(
		timeline(beads, model, REPLY).wishes.map((w) => w.wish),
		[WISH]
	);
	const T = timeline(beads, model, WISH);
	deepEqual(T.wishes, []);
	deepEqual(
		trace(wish, T).map((r) => r.t),
		['stamp']
	);
});
