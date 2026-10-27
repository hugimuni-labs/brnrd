import { deepEqual, equal, ok } from 'node:assert/strict';
import { test } from 'node:test';
import type { Call, Index, Letter, Thread, ThreadPart, Wish } from './types.ts';
import { chips, dur, refTokens, splitQuote } from './text.ts';
import { inSide, internal, linked, sender } from './sides.ts';
import { arrange, REORDER, SETTLED, sortThreads, wanted } from './order.ts';
import { pipOn, stages, stageWord } from './stages.ts';
import { answered, stack, verdictLetter } from './calls.ts';
import { inboxCards, ledger, threadWishes } from './ledger.ts';
import { bar, beats, liveOf, withTools } from './tape.ts';
import { ancestors, children, moved, Store, type Beads } from './parts.ts';

const L = (id: string, at: string, o: Partial<Letter> = {}): Letter => ({
	id,
	at,
	from: 's-ae66-aaaaaa/x',
	body: id,
	...o
});
const W = (wish: string, o: Partial<Wish> = {}): Wish => ({
	wish,
	line: wish,
	state: 'heard',
	stamps: [],
	...o
});
const T = (id: string, o: Partial<Thread> = {}): Thread => ({ id, ...o });

test('a Telegram quote and a reply-to both split off as the quote', () => {
	const by = new Map<string, Letter>();
	deepEqual(splitQuote(L('a', '1', { body: '> earlier\n\nnow' }), by), ['earlier', 'now']);
	by.set('letter:q', L('letter:q', '0', { body: 'asked' }));
	deepEqual(splitQuote(L('b', '1', { person: true, re: 'q', body: 'yes' }), by), ['asked', 'yes']);
	deepEqual(splitQuote(L('c', '1', { body: 'plain' }), by), [null, 'plain']);
});

test('refs read as refs.py does, outside code; bare ids chip, plain words do not', () => {
	deepEqual(refTokens('see &w-abc and (&4954) but not a&b or `&code`'), ['w-abc', '4954']);
	const runs = chips('on &wejeb and w-fsv0qi43429m, not wejeb');
	deepEqual(
		runs.filter((r) => 'bead' in r).map((r) => ('bead' in r ? r.bead : '')),
		['wejeb', 'w-fsv0qi43429m']
	);
	equal(runs.map((r) => r.text).join(''), 'on &wejeb and w-fsv0qi43429m, not wejeb');
});

test('dur reads seconds, minutes, hours', () => {
	deepEqual([dur(5), dur(65), dur(3700)], ['5s', '1m05s', '1h01m']);
});

test('sides: his letters right on the inbox; on a thread, whatever its own strands did not write', () => {
	const threads = [T('web', { strands: [{ id: 's-ae66-aaaaaa/x' }] })];
	const mine = L('1', '1'),
		his = L('2', '1', { person: true, from: 'person:arseni' }),
		inbox = L('3', '1', { from: 's-ae66-bbbbbb/y' });
	deepEqual(
		[inSide(mine, 'web', threads), inSide(his, 'web', threads), inSide(inbox, 'web', threads)],
		[false, true, true]
	);
	deepEqual([inSide(mine, 'inbox', threads), inSide(his, 'inbox', threads)], [false, true]);
	ok(internal(mine) && !internal(his) && !internal(L('4', '1', { to: 'channel:tg' })));
	equal(sender(his), 'arseni');
	equal(sender(L('5', '1', { notice: true })), 'loom');
});

test('a tapped letter links to what it answers and what answers it', () => {
	const ls = [L('letter:a', '1'), L('b', '2', { re: 'a' }), L('c', '3', { answers: ['d'] })];
	const by = new Map(ls.map((l) => [l.id, l]));
	deepEqual([...linked('letter:a', ls, by)].sort(), ['b', 'letter:a']);
	deepEqual([...linked('b', ls, by)].sort(), ['b', 'letter:a']);
});

test('order: seated by freshest letter, your-call by since, quiet by opened newest first, settled stacked last', () => {
	const ts = [
		T('inbox'),
		T('q-old', { board: { state: 'quiet' }, opened_at: '1' }),
		T('q-new', { board: { state: 'quiet' }, opened_at: '2' }),
		T('call-b', { board: { state: 'your-call', since: '5' } }),
		T('call-a', { board: { state: 'your-call', since: '4' } }),
		T('stall', { board: { state: 'mine', stall: true } }),
		T('wait', { board: { state: 'waiting' } }),
		T('seat-1', { board: { state: 'seated' } }),
		T('seat-2', { board: { state: 'seated' } }),
		T('done', { board: { state: 'settled' } })
	];
	const by = new Map([
		['seat-1', [L('x', '1')]],
		['seat-2', [L('y', '9')]]
	]);
	const sorted = sortThreads(ts, by);
	deepEqual(
		sorted.map((t) => t.id),
		['seat-2', 'seat-1', 'call-a', 'call-b', 'wait', 'stall', 'q-new', 'q-old', 'done']
	);
	deepEqual(wanted(sorted, new Set(['done'])).slice(-3), ['q-old', SETTLED, 'done']);
});

test('order moves at most once per REORDER; a new column lands at once; a hold keeps the screen', () => {
	const want = ['inbox', 'b', 'a', 'new'],
		have = ['inbox', 'a', 'b'];
	deepEqual(arrange(want, have, { first: false, held: false, now: 1000, last: 0 }), {
		ids: ['inbox', 'a', 'b', 'new'],
		last: 0,
		wait: REORDER - 1000
	});
	deepEqual(arrange(want, have, { first: false, held: false, now: REORDER, last: 0 }).ids, want);
	deepEqual(arrange(want, have, { first: false, held: true, now: 99999, last: 0 }).ids, [
		'inbox',
		'a',
		'b',
		'new'
	]);
});

test('pips: a later stage implies the earlier ones; a dropped verdict implies nothing; times never run back', () => {
	const w = W('w', {
		state: 'delivered',
		stamps: [
			{ stamp: 'delivered', at: '2026-10-02' },
			{ stamp: 'heard', at: '2026-10-05' }
		]
	});
	deepEqual([...pipOn(w)].sort(), ['delivered', 'granting', 'heard', 'read']);
	const st = stages(w, new Set());
	deepEqual(
		st.map((s) => [s.k, s.on, s.cur, s.wait]),
		[
			['heard', true, false, false],
			['read', true, false, false],
			['granting', true, false, false],
			['delivered', true, true, false],
			['agreed', false, false, true]
		]
	);
	equal(st[0].at, '2026-10-02');
	equal(stages(w, new Set(), true)[4].wait, false);
	const dropped = W('d', {
		state: 'dropped',
		stamps: [{ stamp: 'heard', at: '1' }],
		verdict: { as: 'dropped', at: '2' }
	});
	deepEqual([...pipOn(dropped)].sort(), ['agreed', 'heard']);
	equal(stageWord(dropped), 'dropped');
	equal(stages(dropped, new Set())[4].mark, 'x');
});

test('calls: an open ask stands, verdicts fold into one counted line, a pending verdict letter answers', () => {
	const calls: Call[] = [
		{ kind: 'ask', wish: 'a', line: 'q?', at: '1' },
		{ kind: 'verdict', wish: 'b', line: 'b', at: '1' },
		{ kind: 'verdict', wish: 'c', line: 'c', at: '1' },
		{
			kind: 'verdict',
			wish: 'd',
			line: 'd',
			at: '1',
			closed: { verdict: 'agree', letter: 'x', at: '2' }
		}
	];
	const pend = [L('p', '3', { ...verdictLetter('agree', 'c') })];
	equal(answered(calls[2], pend)?.pending, true);
	const s = stack(calls, pend, new Set(), false);
	deepEqual(
		s.shown.map((c) => c.wish),
		['a']
	);
	deepEqual(s.fold, { verdicts: 1, answered: 2 });
	equal(stack(calls, pend, new Set(), true).shown.length, 4);
	deepEqual(
		stack(calls, [], new Set(['b']), false).shown.map((c) => c.wish),
		['a', 'b']
	);
});

test('ledger: sections by who owes the move; an assumed wish nests under the said one it read from', () => {
	const said = W('w-said', {
		state: 'granting',
		stamps: [{ stamp: 'granting', at: '2' }],
		letters: ['l1']
	});
	const assumed = W('w-asm', {
		origin: 'derived',
		state: 'granting',
		says: [{ id: 'l1', quote: 'x', cites: 'whole' }]
	});
	const owed = W('w-owe', { state: 'delivered', stamps: [{ stamp: 'delivered', at: '3' }] });
	const old = W('w-old', { state: 'accepted', verdict: { as: 'agree', at: '4' } });
	const t = T('web', {
		opened_for: [{ wish: 'w-said' }],
		steps: [
			{ text: 'do it', wishes: ['w-said'], receipt: null },
			{ text: 'aside', wishes: [], receipt: 'abc123' }
		]
	});
	const all = [said, assumed, owed, old];
	for (const w of all) w.thread = 'web';
	const tw = threadWishes(t, [L('l1', '1'), L('l9', '2')], all);
	deepEqual(tw.extra, { 'w-said': ['l9'] });
	const g = ledger(t, tw, all, []);
	deepEqual(
		g.sections.map((s) => [s.k, s.rows.map((r) => r.w.wish), s.n]),
		[
			['waits', ['w-owe'], 1],
			['hand', ['w-said'], 2],
			['settled', ['w-old'], 1]
		]
	);
	const hand = g.sections[1].rows[0];
	deepEqual(
		[hand.hand, hand.kids.map((k) => k.w.wish), hand.steps.map((s) => s.text)],
		['working', ['w-asm'], ['do it']]
	);
	deepEqual(
		g.unplanned.map((s) => s.text),
		['aside']
	);
	equal(g.active?.text, 'do it');
	deepEqual(g.balance, { said: 3, derived: 1, delivered: 1, owe: 1 });
	equal(g.sections[2].rows[0].status, 'agree');
});

test('tape: finished calls fold per strand between letters; the live line ticks from the open call', () => {
	const t = T('web', { strands: [{ id: 's1', state: 'working' }] });
	const tools = {
		s1: [
			{ tool: 'Bash', at: '2026-10-27T10:00:00Z', end: '2026-10-27T10:00:10Z', say: 'look' },
			{ tool: 'Read', at: '2026-10-27T10:00:20Z', end: '2026-10-27T10:00:21Z', say: 'look' },
			{
				tool: 'Bash',
				at: '2026-10-27T10:00:40Z',
				end: '2026-10-27T10:00:45Z',
				say: 'push',
				cmd: 'git push'
			},
			{ tool: 'Open', at: '2026-10-27T10:01:00Z' }
		]
	};
	const live = { s1: { open: [{ tool: 'Open', at: '2026-10-27T10:01:00Z' }] } };
	const rows = withTools([L('a', '2026-10-27T10:00:30Z')], t, tools, live);
	deepEqual(
		rows.map((r) => ('fold' in r ? `fold${r.fold.length}` : 'live' in r ? 'live' : r.id)),
		['fold2', 'a', 'fold1', 'live']
	);
	const lv = liveOf({ id: 's1', state: 'working' }, live)!;
	deepEqual([lv.verb, lv.since], ['running', '2026-10-27T10:01:00Z']);
	equal(liveOf({ id: 's1', state: 'idle' }, live)!.verb, 'cut off');
	const f = tools.s1.slice(0, 3);
	deepEqual(
		beats(f).map((b) => [b.say, b.calls.length, b.thought]),
		[
			['look', 2, null],
			['push', 1, 19]
		]
	);
	deepEqual(
		bar(f).map((s) => s.kind),
		['r', 'th', 'r', 'th', 'o']
	);
});

const index = (
	parts: Record<string, string>,
	threads: Thread[] = [T('inbox'), T('web')]
): Index => ({
	v: 1,
	threads,
	wishes: {},
	unthreaded: [],
	person: 'arseni',
	parts
});
const part = (id: string, letters: Letter[], more = false): ThreadPart => ({
	id,
	count: letters.length,
	letters,
	wishes: [],
	tools: {},
	live: {},
	more
});

test('parts: only a moved hash is fetched again; a letter in two parts is held once', async () => {
	const shared = L('l1', '1', { threads: ['inbox', 'web'] });
	let idx = index({ 'thread/inbox': 'h1', 'thread/web': 'h2', beads: 'b1' });
	const served: Record<string, unknown> = {
		'/v1/loom/thread/inbox': part('inbox', [shared]),
		'/v1/loom/thread/web': part('web', [shared])
	};
	const asked: string[] = [];
	const store = new Store(async (p) => {
		asked.push(p);
		return p === '/v1/loom/index' ? idx : served[p];
	});
	ok(await store.sync());
	equal(store.model()!.letters.length, 1);
	asked.length = 0;
	equal(await store.sync(), false);
	deepEqual(asked, ['/v1/loom/index']);
	idx = index({ 'thread/inbox': 'h1', 'thread/web': 'h3', beads: 'b1' });
	served['/v1/loom/thread/web'] = part('web', [shared, L('l2', '2', { threads: ['web'] })]);
	asked.length = 0;
	ok(await store.sync());
	deepEqual(asked, ['/v1/loom/index', '/v1/loom/thread/web']);
	deepEqual(
		store.model()!.letters.map((l) => l.id),
		['l1', 'l2']
	);
	deepEqual(moved(index({ 'thread/inbox': 'h1' }), store.held).drop, ['thread/web']);
});

test('parts: older letters load by before=, or back to until=', async () => {
	const asked: string[] = [];
	const store = new Store(async (p) => {
		asked.push(p);
		if (p === '/v1/loom/index') return index({ 'thread/web': 'h' }, [T('web')]);
		if (p === '/v1/loom/thread/web') return part('web', [L('l3', '3')], true);
		return part('web', [L('l1', '1'), L('l2', '2')], false);
	});
	await store.sync();
	ok(await store.older('web'));
	equal(asked.at(-1), '/v1/loom/thread/web?before=l3');
	deepEqual(
		store.threads.get('web')!.letters.map((l) => l.id),
		['l1', 'l2', 'l3']
	);
	equal(await store.older('web'), false);
});

test('beads: the chain walks up to the source; children hang below oldest first', () => {
	const b: Beads = {
		src: [null, null, 'person:arseni', '1', 'ask'],
		w: ['src', 'said', null, '2', 'wish'],
		l2: ['w', 're', 's', '4', 'b'],
		l1: ['w', 're', 's', '3', 'a'],
		loop: ['loop', null, null, null, '']
	};
	deepEqual(ancestors(b, 'l1'), [
		{ id: 'w', edge: 're' },
		{ id: 'src', edge: 'said' }
	]);
	deepEqual(children(b, 'w'), ['l1', 'l2']);
	deepEqual(ancestors(b, 'loop'), []);
});

// The bead sheet walks the beads part in the client (no /bead on the loopback): chain up to the source, what hangs below.
import { kindOf, resolveRef, timeline } from './bead.ts';
test('a bead timeline walks the parent map up to its source and down to its children', () => {
	const beads = {
		'letter:tg:1': [null, null, 'person:ada', '2026-10-27T10:00:00Z', 'make the board'],
		'w-aaaaaaaaaaaa': ['letter:tg:1', 'says', 'person:ada', '2026-10-27T10:01:00Z', 'the board'],
		'letter:tg:2': ['w-aaaaaaaaaaaa', 're', 's-ae66-abcdef', '2026-10-27T10:05:00Z', 'on it'],
		'letter:tg:3': ['w-aaaaaaaaaaaa', 're', 's-ae66-abcdef', '2026-10-27T10:03:00Z', 'reading']
	} as Record<string, [string | null, string | null, string | null, string | null, string]>;
	const T = timeline(beads, null, 'w-aaaaaaaaaaaa');
	equal(T.root, 'person');
	deepEqual(
		T.chain.map((x) => x.id),
		['letter:tg:1']
	);
	equal(T.chain[0].edge, null);
	deepEqual(
		T.out.map((x) => x.id),
		['letter:tg:3', 'letter:tg:2']
	);
	equal(kindOf('s-ae66-abcdef', beads), 'strand');
	equal(resolveRef(beads, null, '2'), 'letter:tg:2');
	equal(resolveRef(beads, null, 'nope'), null);
});

test('inbox wish view: done above, open below, oldest first in each, newest open last', () => {
	const g = inboxCards([
		W('w-new', { state: 'granting', at: '5' }),
		W('w-done2', { state: 'accepted', at: '4' }),
		W('w-old', { state: 'heard', at: '1' }),
		W('w-done1', { state: 'delivered', at: '2' })
	]);
	deepEqual(
		g.cards.map((w) => w.wish),
		['w-done1', 'w-done2', 'w-old', 'w-new']
	);
	equal(g.open, 2);
});
