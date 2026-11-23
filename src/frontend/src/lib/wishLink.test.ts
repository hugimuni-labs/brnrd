import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
	chainFromSays,
	chainToRequest,
	pickWish,
	showOrder,
	signInHref,
	wishesFromBoard,
	wishesFromSnapshot,
	type ShownWish
} from './wishLink.ts';

const REQUEST = 'please trace this pull request back to the letter that asked';

test('snapshot says open on the person letter, and other threads stay out', () => {
	const wishes = wishesFromSnapshot(
		[
			{
				id: 'w-sign',
				thread: 'work',
				line: 'the wish',
				state: 'heard',
				says: [
					{ id: 'l-note', from: 's-ae66-bijouk', head: 'a later note' },
					{ id: 'l-ada', from: 'person:ada', at: '2026-11-23T18:21:00Z', head: REQUEST }
				]
			},
			{ id: 'w-other', thread: 'inbox', line: 'elsewhere', says: [] }
		],
		'work'
	);
	assert.deepEqual(
		wishes.map((wish) => wish.id),
		['w-sign']
	);
	assert.equal(wishes[0].chain[0].id, 'l-ada');
	assert.equal(wishes[0].chain[0].person, true);
	assert.equal(wishes[0].chain[0].line, REQUEST);
	assert.deepEqual(
		wishes[0].chain.map((step) => step.id),
		['l-ada', 'l-note']
	);
});

test('the board chain walks up to the person letter and stops, using the letter body', () => {
	const beads = {
		'l-ada': [null, 'person', 'person:ada', '2026-11-23T18:21:00Z', 'short'],
		'w-mid': ['l-ada', 'says', 'person', '2026-11-23T18:22:00Z', 'mid'],
		'w-sign': ['w-mid', 'says', 'person', '2026-11-23T18:23:00Z', 'the wish'],
		'l-past': ['l-ada', 're', 'person:ada', '2026-11-23T18:20:00Z', 'older, not an ancestor']
	} as const;
	const chain = chainToRequest({ ...beads }, 'w-sign');
	assert.deepEqual(
		chain.map((step) => step.id),
		['l-ada', 'w-mid']
	);
	assert.equal(chain[0].person, true);
	const [shown] = wishesFromBoard({
		wishes: [{ wish: 'w-sign', line: 'the wish', state: 'heard' }],
		chain: { ...beads },
		letters: [{ id: 'l-ada', from: 'person:ada', body: REQUEST }]
	});
	assert.equal(shown.chain[0].line, REQUEST);
});

test('a cycle and a missing wish do not invent a chain', () => {
	const loop = {
		'w-a': ['w-b', 'says', null, null, 'a'],
		'w-b': ['w-a', 'says', null, null, 'b']
	} as const;
	const chain = chainToRequest({ ...loop }, 'w-a');
	assert.deepEqual(
		chain.map((step) => step.id),
		['w-b']
	);
	assert.deepEqual(chainToRequest({}, 'w-gone'), []);
});

test('the query selects the wish, and a stranger is a note', () => {
	const wishes: ShownWish[] = [
		{ id: 'w-one', line: 'one', chain: [] },
		{ id: 'w-two', line: 'two', chain: [] }
	];
	assert.equal(pickWish(wishes, 'w-two').selected?.id, 'w-two');
	assert.equal(pickWish(wishes, 'w-two').missing, null);
	assert.equal(pickWish(wishes, 'w-nope').selected, null);
	assert.equal(pickWish(wishes, 'w-nope').missing, 'w-nope');
	assert.deepEqual(pickWish(wishes, null), { selected: null, missing: null });
	assert.deepEqual(pickWish(wishes, '  '), { selected: null, missing: null });
	assert.deepEqual(
		showOrder(wishes, 'w-two').map((wish) => wish.id),
		['w-two', 'w-one']
	);
});

test('sign-in keeps the wish url and refuses an off-site next', () => {
	assert.equal(
		signInHref('/dashboard/thread/work?wish=w-sign'),
		'/login?next=' + encodeURIComponent('/dashboard/thread/work?wish=w-sign')
	);
	assert.equal(
		signInHref('https://evil.example/dashboard'),
		'/login?next=' + encodeURIComponent('/dashboard')
	);
	assert.equal(chainFromSays([]).length, 0);
});
