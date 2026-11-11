import { deepEqual, equal, ok } from 'node:assert/strict';
import { test } from 'node:test';
import type { Call, Index, Letter, Thread, ThreadPart, Wish } from './types.ts';
import { chips, dur, refTokens, splitQuote } from './text.ts';
import { inSide, internal, letterMark, linked, sender } from './sides.ts';
import { arrange, REORDER, SETTLED, TOPICS, sortThreads, wanted } from './order.ts';
import { pipOn, stages, stageWord } from './stages.ts';
import { answered, stack, verdictLetter } from './calls.ts';
import { inboxCards, ledger, threadWishes, unechoed } from './ledger.ts';
import { bar, beats, liveOf, withTools } from './tape.ts';
import {
	ancestors,
	children,
	headerFuel,
	leadName,
	machinesTitle,
	moved,
	patchBeads,
	Store,
	type Beads
} from './parts.ts';
import { face, moves, seen, waitOf } from './rail.ts';
import { stamps } from './stamps.ts';

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

test('the header names every provider, and a collector error is the word it was given', () => {
	equal(
		headerFuel([
			{
				shell: 'claude',
				gauges: [
					{ window: 'session', left_pct: 15 },
					{ window: 'week', left_pct: 48 }
				]
			},
			{
				shell: 'codex',
				gauges: [
					{ window: 'session', left_pct: 95 },
					{ window: 'week', left_pct: 99 }
				]
			},
			{ shell: 'grok', gauges: [{ window: 'week', left_pct: 58 }] },
			{ shell: 'vibe', gauges: [], error: 'fails' }
		]),
		'claude session 15% week 48%   codex session 95% week 99%   grok week 58%   vibe fails'
	);
	equal(headerFuel(undefined), '');
});

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
	deepEqual(wanted(sorted, new Set(['done'])).slice(0, 2), ['inbox', TOPICS]);
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
		[]
	);
	deepEqual(s.fold, { asks: 1, verdicts: 1, answered: 2 });
	equal(stack(calls, pend, new Set(), true).shown.length, 4);
	deepEqual(
		stack(calls, [], new Set(['b']), false).shown.map((c) => c.wish),
		['b']
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
	// a top-level assumed wish names the first said wish elsewhere that holds a letter it cites, never one shown here
	const far = W('w-far', { thread: 'other', letters: ['l7'] }),
		near = W('w-near', { thread: 'other', says: [{ id: 'l8', quote: 'y', cites: 'whole' }] }),
		lone = W('w-lone', {
			thread: 'web',
			origin: 'derived',
			state: 'delivered',
			says: [
				{ id: 'l8', quote: 'y', cites: 'whole' },
				{ id: 'l7', quote: 'z', cites: 'whole' },
				{ id: 'l1', quote: 'x', cites: 'whole' }
			]
		});
	const wide = [said, far, near, lone];
	const row = ledger(t, threadWishes(t, [L('l1', '1')], wide), wide, [])
		.sections.flatMap((s) => s.rows)
		.find((r) => r.w.wish === 'w-lone');
	equal(row?.from, 'w-far');
	equal(
		ledger(t, threadWishes(t, [L('l1', '1')], [said, lone]), [said, lone], [])
			.sections.flatMap((s) => s.rows)
			.find((r) => r.w.wish === 'w-lone')?.from,
		undefined
	);
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

test('parts: a short index row joins its part; unthreaded wishes load from theirs', async () => {
	const loose = W('w-loose', {
		line: 'loose',
		thread: null,
		says: [{ id: 'l', quote: 'a long quote', cites: 'whole' }]
	});
	const idx = index({ 'thread/web': 'h', unthreaded: 'u' }, [
		T('web', {
			title: 'Web',
			status: 'open',
			last_at: 't',
			board: { state: 'quiet' },
			counts: { heard: 1 }
		})
	]);
	const asked: string[] = [];
	const store = new Store(async (p) => {
		asked.push(p);
		if (p === '/v1/loom/index') return idx;
		if (p === '/v1/loom/unthreaded') return [loose];
		return {
			...part('web', []),
			steps: [{ text: 'fat step', wishes: [], receipt: null }],
			strands: [{ id: 's-ae66-webbod', state: 'idle' }],
			board: {
				state: 'quiet',
				why: 'parked on ci',
				calls: [{ kind: 'ask', wish: 'w', line: 'x', at: 't' }]
			},
			opened_at: 'opened'
		};
	});
	ok(await store.sync());
	deepEqual(
		asked.filter((p) => p.includes('unthreaded')),
		['/v1/loom/unthreaded']
	);
	const t = store.model()!.threads.find((row) => row.id === 'web')!;
	equal(t.title, 'Web');
	equal(t.board?.state, 'quiet');
	equal(t.board?.why, 'parked on ci');
	equal(t.steps?.[0].text, 'fat step');
	equal(t.strands?.[0].id, 's-ae66-webbod');
	equal(t.opened_at, 'opened');
	equal(store.model()!.wishes[0].says?.[0].quote, 'a long quote');
	asked.length = 0;
	equal(await store.sync(), false);
	deepEqual(asked, ['/v1/loom/index']);
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

test('parts: a refetched window keeps the history scrolled in before it', async () => {
	let h = 'h',
		newest = [L('l3', '3')];
	const store = new Store(async (p) => {
		if (p === '/v1/loom/index') return index({ 'thread/web': h }, [T('web')]);
		if (p === '/v1/loom/thread/web') return part('web', newest, true);
		return part('web', [L('l1', '1'), L('l2', '2')], false);
	});
	await store.sync();
	await store.older('web');
	h = 'h2';
	newest = [L('l3', '3'), L('l4', '4')];
	ok(await store.sync());
	deepEqual(
		store.threads.get('web')!.letters.map((l) => l.id),
		['l1', 'l2', 'l3', 'l4']
	);
	equal(store.threads.get('web')!.more, false);
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

test('beads: children are indexed once per part, and a patched part answers with what it brought', () => {
	const b: Beads = {
		w: [null, null, null, '1', 'wish'],
		l1: ['w', 're', 's', '3', 'a']
	};
	deepEqual(children(b, 'w'), ['l1']);
	const next = patchBeads(b, {
		since: 'h1',
		hash: 'h2',
		set: { l0: ['w', 're', 's', '2', 'z'] },
		drop: ['l1']
	});
	deepEqual(children(next, 'w'), ['l0']);
	deepEqual(children(b, 'w'), ['l1']);
	deepEqual(children(next, 'nobody'), []);
});

test('beads: a delta patches the held map; drop removes', () => {
	const held: Beads = {
		a: [null, 'person', 'person:ada', '1', 'ask'],
		gone: [null, 'person', 'person:ada', '0', 'old']
	};
	deepEqual(
		patchBeads(held, {
			since: 'b1',
			hash: 'b2',
			set: { b: ['a', 're', 's', '2', 'on it'] },
			drop: ['gone']
		}),
		{
			a: held.a,
			b: ['a', 're', 's', '2', 'on it']
		}
	);
});

test('parts: beads patch from the index delta, a letter does not refetch the part', async () => {
	const row: Beads[string] = [null, 'person', 'person:ada', '1', 'ask'];
	const beads: Beads = { a: row };
	let idx = index({ 'thread/web': 'h1', beads: 'b1' });
	const asked: string[] = [];
	const store = new Store(async (p) => {
		asked.push(p);
		if (p === '/v1/loom/index') return idx;
		if (p === '/v1/loom/thread/web') return part('web', []);
		return beads;
	});
	await store.sync();
	await store.loadBeads();
	asked.length = 0;
	const added: Beads[string] = ['a', 're', 's', '2', 'on it'];
	idx = index({ 'thread/web': 'h1', beads: 'b2' });
	idx.beads_delta = { since: 'b1', hash: 'b2', set: { b: added }, drop: [] };
	ok(await store.sync());
	deepEqual(asked, ['/v1/loom/index']);
	deepEqual(store.beads, { a: row, b: added });
	equal(store.held.get('beads'), 'b2');
});

test('parts: two letters between steps, one sync from the first hash holds the current', async () => {
	const a: Beads[string] = [null, 'person', 'person:ada', '1', 'ask'];
	const b: Beads[string] = ['a', 're', 's', '2', 'two'];
	const c: Beads[string] = ['a', 're', 's', '3', 'three'];
	let idx = index({ 'thread/web': 'h1', beads: 'b1' });
	const asked: string[] = [];
	const store = new Store(async (p) => {
		asked.push(p);
		if (p === '/v1/loom/index') return idx;
		if (p === '/v1/loom/thread/web') return part('web', []);
		if (p === '/v1/loom/beads?since=b1')
			return { since: 'b1', hash: 'b3', set: { b, c }, drop: [] };
		return { a };
	});
	await store.sync();
	await store.loadBeads();
	asked.length = 0;
	idx = index({ 'thread/web': 'h1', beads: 'b3' });
	idx.beads_delta = { since: 'b2', hash: 'b3', set: { c }, drop: [] };
	ok(await store.sync());
	deepEqual(asked, ['/v1/loom/index', '/v1/loom/beads?since=b1']);
	equal(store.held.get('beads'), 'b3');
	deepEqual(store.beads, { a, b, c });
});

test('from the inbox: person letters in the chain, oldest first; another thread is marked', () => {
	const chain: Beads = {
		ask: [null, 'person', 'person:ada', '1', 'please make alpha'],
		'w-alpha0000000': ['ask', 'says', 'person:ada', '2', 'alpha happens'],
		other: [null, 'person', 'person:ada', '0', 'from charlie'],
		reply: ['other', 're', 's-x', '3', 'on it']
	};
	const byId = new Map<string, Letter>([
		[
			'ask',
			L('ask', '1', {
				person: true,
				from: 'person:ada',
				threads: ['inbox'],
				body: 'please make alpha'
			})
		],
		[
			'other',
			L('other', '0', {
				person: true,
				from: 'person:ada',
				threads: ['charlie'],
				body: 'from charlie'
			})
		]
	]);
	const rows = fromInbox(chain, byId, 'alpha');
	deepEqual(
		rows.map((r) => [r.id, r.who, r.thread, r.line]),
		[
			['other', 'ada', 'charlie', 'from charlie'],
			['ask', 'ada', undefined, 'please make alpha']
		]
	);
});

// The bead sheet walks the beads part in the client (no /bead on the loopback): chain up to the source, what hangs below.
import { fromInbox, kindOf, panelIds, refOf, resolveRef, timeline, trace } from './bead.ts';
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

test('a selection links to its bead panel: the chain up, the item, what hangs below; nothing beside it', () => {
	const beads = {
		ask: [null, 'person', 'person:ada', '2026-10-27T09:00:00Z', 'please'],
		'w-alpha0000000': ['ask', 'wish', 'alpha', '2026-10-27T09:01:00Z', 'alpha'],
		note: ['w-alpha0000000', 'link', 's-x', '2026-10-27T09:02:00Z', 'a note'],
		reply: ['note', 're', 's-y', '2026-10-27T09:03:00Z', 'ok'],
		sibling: ['ask', 're', 's-x', '2026-10-27T09:04:00Z', 'on it']
	} as Beads;
	deepEqual([...panelIds(beads, 'note')].sort(), ['ask', 'note', 'reply', 'w-alpha0000000']);
	deepEqual([...panelIds(beads, 'gone')], ['gone']);
	equal(panelIds(beads, null).size, 0);
});

test('a wish trace: stamps with a ref or a line, a result once, the verdict, beads interleaved by time', () => {
	const beads = {
		'w-aaaaaaaaaaaa': [null, 'wish', 'x', '2026-10-27T10:00:00Z', 'the board'],
		note: ['w-aaaaaaaaaaaa', 'link', 's-x', '2026-10-27T10:03:00Z', 'a note']
	} as Beads;
	const w = W('w-aaaaaaaaaaaa', {
		state: 'accepted',
		stamps: [
			{ stamp: 'heard', at: '2026-10-27T10:00:00Z' },
			{ stamp: 'read', at: '2026-10-27T10:01:00Z' },
			{ stamp: 'result', at: '2026-10-27T10:02:00Z', ref: 'brnrd#1' },
			{ stamp: 'delivered', at: '2026-10-27T10:04:00Z', ref: 'abc1234', line: 'out' }
		],
		results: [{ ref: 'brnrd#1', at: '2026-10-27T10:02:00Z' }, { ref: 'threads/x/README.md' }],
		verdict: { as: 'accepted', at: '2026-10-27T10:05:00Z' }
	});
	const rows = trace(w, timeline(beads, null, 'w-aaaaaaaaaaaa'));
	deepEqual(
		rows.map((r) => (r.t === 'stamp' ? r.k : r.t === 'bead' ? r.bead.id : r.t)),
		['read', 'granting', 'note', 'delivered', 'verdict', 'result']
	);
	const steps = [{ text: 'write the `parser`', wishes: [], receipt: 'abc1234' }];
	deepEqual(refOf('brnrd#1', beads, steps), {
		kind: 'url',
		href: 'https://github.com/hugimuni-labs/brnrd/pull/1',
		text: 'brnrd#1'
	});
	deepEqual(refOf('note', beads, steps), { kind: 'bead', id: 'note', text: 'a note' });
	deepEqual(refOf('abc1234', beads, steps), {
		kind: 'step',
		sha: 'abc1234',
		text: 'write the parser'
	});
	deepEqual(refOf('nope', beads, steps), { kind: 'text', text: 'nope' });
});

test('the rail logs a moved strand once, the first load moves none, and a working strand plays the running face', () => {
	const th = (state: string) =>
		[{ id: 'web', strands: [{ id: 's-old' }, { id: 's-new', state }] }] as Thread[];
	const at = '2026-10-27T10:21:05.123Z';
	deepEqual([...moves(null, seen(th('idle')), at).changed], []);
	const m = moves(seen(th('idle')), seen(th('working')), at);
	deepEqual([...m.changed], ['s-new']);
	deepEqual(m.lines, ['10:21:05 web idle→working']);
	deepEqual(moves(seen(th('working')), seen(th('working')), at).lines, []);
	deepEqual(moves({}, seen(th('idle')), at).lines, ['10:21:05 web new→idle']);
	const faces = { running: ['a', 'b'], idle: ['c'] };
	equal(face(faces, 'working', 3), 'b');
	equal(face(faces, 'holding', 3), 'c');
	equal(face(undefined, 'working', 3), 'brnrd');
	equal(waitOf(T('bravo', { board: { state: 'your-call' } }), 'idle'), 'waits on you');
	equal(
		waitOf(
			T('echo', { board: { state: 'waiting', why: 'waiting: the forge to come back' } }),
			'parked'
		),
		'waits: the forge to come back'
	);
	equal(
		waitOf(
			T('long', {
				board: {
					state: 'waiting',
					why: 'waiting: ' + 'x'.repeat(60)
				}
			}),
			'parked'
		),
		'waits: ' + 'x'.repeat(39) + '…'
	);
	equal(waitOf(T('charlie', { board: { state: 'mine' } }), 'idle'), 'idle');
});

test('a letter stamps who picked it up first, then its disposition, its send and its wishes in board.html order', () => {
	const l = L('l1', '2026-10-27T10:00:00Z', {
		shown: [{ strand: 's-ae66-inbox1', at: '2026-10-27T10:01:00Z' }],
		chatter: ['s-x'],
		sent: { state: 'failed', at: '2026-10-27T10:02:00Z' },
		wishes: [
			{
				wish: 'w-abc123',
				thread: 'web-main',
				line: 'make it',
				state: 'read',
				stamps: [
					{ stamp: 'heard', at: '2026-10-27T09:00:00Z' },
					{ stamp: 'read', at: '2026-10-27T10:03:00Z', ref: 'brnrd#1' },
					{ stamp: 'read', at: '2026-10-27T10:04:00Z' }
				]
			},
			// the same wish again, as two citing letters list it (10010): one line, not its history twice
			{ wish: 'w-abc123', thread: 'web-main', line: 'make it', state: 'read', stamps: [] }
		],
		wish: 'w-zzz999'
	});
	deepEqual(
		stamps(l).map((s) => [s.cls, s.text]),
		[
			['read', 'picked up · inbox1 10-27 10:01'],
			['heard', 'chatter'],
			['heard', 'to channel · failed'],
			['read', 'read · abc123 · web-main'],
			['heard', 'stamps zzz999']
		]
	);
	equal(
		stamps(l)[3].title,
		'make it\nheard 10-27 09:00\nread 10-27 10:03 brnrd#1\nread 10-27 10:04'
	);
	deepEqual(stamps(L('l2', '2026-10-27T10:00:00Z')), []);
});

test('pending: a sent letter resolves by id alone, even rerouted to its wish thread', () => {
	// brnrd.dev answers the native tail; the loom files it under its connector and routes it by the &wish
	const sent = L('loom:arseni:ltr_1', '2026-11-01T17:00:00Z', {
		to: 'thread:inbox',
		threads: ['inbox'],
		pendingNote: true
	});
	const local = L('letter:board:arseni:board-x', '2026-11-01T17:00:01Z', { pendingNote: true });
	const other = L('loom:arseni:ltr_2', '2026-11-01T17:00:02Z', { pendingNote: true });
	const handled = L('letter:relay:loom:arseni:ltr_1', '2026-11-01T17:00:05Z', {
		to: 'thread:setup-flow',
		threads: ['setup-flow']
	});
	const pend = [sent, local, other];
	deepEqual(
		unechoed(pend, [handled, L('letter:board:arseni:board-x', '2026-11-01T17:00:06Z')]).map(
			(l) => l.id
		),
		['loom:arseni:ltr_2']
	);
	// an id that only shares a prefix is not the letter
	deepEqual(unechoed([sent], [L('letter:relay:loom:arseni:ltr_10', 'x')]), [sent]);
	equal(unechoed(pend, []), pend);
});

test('colour is the thread: a strand letter takes its thread, his letters the neutral; current strand solid, earlier dashed', () => {
	const threads = [
		T('setup-flow', {
			strands: [{ id: 's-ae66-old111' }, { id: 's-ae66-new222' }] as Thread['strands']
		}),
		T('web-main', { strands: [{ id: 's-ae66-web333' }] as Thread['strands'] })
	];
	const from = (f: string, o: Partial<Letter> = {}) =>
		L('x', '2026-11-01T00:00:00Z', { from: f, ...o });
	deepEqual(letterMark(from('s-ae66-new222/a'), 'inbox', threads), {
		thread: 'setup-flow',
		line: 'solid'
	});
	deepEqual(letterMark(from('s-ae66-old111/b'), 'inbox', threads), {
		thread: 'setup-flow',
		line: 'dashed'
	});
	// read in another thread's column, it keeps its own thread's colour
	deepEqual(letterMark(from('s-ae66-web333/c'), 'setup-flow', threads), {
		thread: 'web-main',
		line: 'solid'
	});
	deepEqual(letterMark(from('person:arseni', { person: true }), 'web-main', threads), {
		thread: null
	});
	// a strand no thread lists falls back to where the letter went, then the column
	deepEqual(letterMark(from('s-ae66-gone44/d', { threads: ['loom-cpu'] }), 'inbox', threads), {
		thread: 'loom-cpu',
		line: 'solid'
	});
	deepEqual(letterMark(from('s-ae66-gone44/d'), 'inbox', threads), {
		thread: 'inbox',
		line: 'solid'
	});
});

test('the header names the leading machine and lists every machine on hover', () => {
	const m = [
		{
			id: 'ae66',
			name: 'iMac',
			lead: true,
			here: false,
			gen: 12,
			seen: '2026-11-08T22:17:00+00:00'
		},
		{ id: '46a2', name: 'MacBook Air', lead: false, here: true }
	];
	equal(leadName(m), 'iMac');
	equal(leadName(m.slice(0, 1)), '');
	equal(leadName([]), '');
	equal(leadName(undefined), '');
	equal(
		machinesTitle(m),
		'iMac: leads gen 12, beat 22:17:00Z\nMacBook Air (the loom serving this page): follows'
	);
	equal(machinesTitle(undefined), '');
});
