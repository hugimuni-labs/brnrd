// A synthetic home as loom parts (brr.loom.runtime.board.compute's shapes, lib/board/types.ts): one thread per board
// state, the inbox's call stack (an open ask, an open verdict, a closed one), an assumed wish, steps with and without a
// receipt, tapes and a live body. Nobody's letters: the committed e2e never carries a recording.
// `fixture()` returns { name: json }; `node e2e/fixture.mjs <dir>` writes them as parts-server reads them.
import { createHash } from 'node:crypto';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

export const T0 = Date.parse('2026-10-27T09:00:00Z');
export const at = (min) => new Date(T0 + min * 60_000).toISOString().replace('.000Z', 'Z');
const hash = (v) => createHash('sha256').update(JSON.stringify(v)).digest('hex').slice(0, 14);
const stamps = (...ks) => ks.map(([stamp, m]) => ({ stamp, at: at(m) }));

// id, board, strand state (null = no body), wish state, extra
const THREADS = [
	['alpha', { state: 'seated', strand: 'working' }, 'working', 'granting'],
	[
		'bravo',
		{ state: 'your-call', since: at(20), waits: true, why: 'delivered: your verdict' },
		'holding',
		'delivered'
	],
	['charlie', { state: 'mine', why: 'mine: building the parser' }, 'idle', 'granting'],
	[
		'delta',
		{ state: 'mine', stall: true, why: 'no body, 1 open wish (no body yet): wake or settle' },
		null,
		'read'
	],
	['echo', { state: 'waiting', why: 'waiting: the forge to come back' }, 'parked', 'read'],
	['foxtrot', { state: 'quiet' }, 'stopped', 'accepted'],
	['golf', { state: 'settled' }, 'stopped', 'accepted'],
	['hotel', { state: 'settled' }, 'stopped', 'dropped']
];

export function fixture() {
	const letters = [],
		wishes = {},
		threads = [],
		tools = {},
		live = {};
	let n = 0;
	const letter = (l) => (letters.push({ threads: ['inbox'], ...l }), l);
	THREADS.forEach(([id, board, state, wstate], i) => {
		const m = i * 6,
			sid = `s-test-${id.slice(0, 4)}${i}`,
			wid = `w-${id}0000000`;
		const ask = letter({
			id: `letter:mine:${100 + i}`,
			at: at(m),
			from: 'person:ada',
			to: 'inbox',
			person: true,
			body: `please make ${id} happen`
		});
		const ks = { heard: m, read: m + 1, granting: m + 2, delivered: m + 4, accepted: m + 5 };
		const upto = ['heard', 'read', 'granting', 'delivered', 'accepted'];
		const got = wstate === 'dropped' ? ['heard', 'read'] : upto.slice(0, upto.indexOf(wstate) + 1);
		const wish = {
			wish: wid,
			line: `${id} happens`,
			state: wstate,
			origin: 'derived',
			thread: id,
			at: at(m),
			stamps: stamps(...got.map((k) => [k, ks[k]])),
			says: [{ id: ask.id, quote: `make ${id} happen`, cites: 'whole' }],
			letters: [ask.id],
			results: ['delivered', 'accepted'].includes(wstate)
				? [
						{
							ref: `threads/${id}/README.md`,
							kind: 'doc',
							line: `${id} done`,
							at: at(m + 4),
							delivered: true
						}
					]
				: [],
			verdict:
				wstate === 'accepted'
					? { as: 'accepted', at: at(m + 5) }
					: wstate === 'dropped'
						? { as: 'dropped', at: at(m + 5) }
						: null
		};
		wishes[id] = [wish];
		const reply = letter({
			id: `${sid}/a${n++}`,
			at: at(m + 1),
			from: sid,
			to: 'thread:inbox',
			re: ask.id,
			body: `on it: thread ${id}`,
			threads: ['inbox', id],
			wishes: [{ wish: wid, thread: id, line: wish.line, state: wstate, stamps: wish.stamps }]
		});
		ask.answers = [reply.id];
		if (id === 'alpha') {
			// an assumed wish beside the derived one, a tape, a body at work right now
			wishes[id].push({
				wish: 'w-alphaassumed',
				line: 'alpha keeps its tests green',
				state: 'granting',
				origin: 'assumed',
				thread: id,
				at: at(m + 2),
				stamps: stamps(['granting', m + 2])
			});
			letter({
				id: `${sid}/a${n++}`,
				at: at(m + 3),
				from: sid,
				to: 'thread:alpha',
				body: 'step 1 landed',
				threads: [id]
			});
			tools[sid] = [
				{
					tool: 'Bash',
					at: at(m + 2),
					end: at(m + 2.5),
					arg: 'npm test',
					cmd: 'npm test',
					failed: false
				},
				{ tool: 'Edit', at: at(m + 2.6), end: at(m + 2.7), arg: 'src/a.ts', failed: false },
				{ tool: 'Bash', at: at(m + 4), arg: 'npm run build', cmd: 'npm run build' }
			];
			live[sid] = { open: [tools[sid][2]], say: 'building alpha' };
		}
		threads.push({
			id,
			title: `${id} happens`,
			status: board.state === 'settled' ? 'settled' : 'open',
			url: `https://example.test/threads/${id}`,
			opened_at: at(m),
			last_at: at(m + 3),
			opened_for: [{ wish: wid }],
			// a thread keeps its strands' history: a no-body thread lists the one that stopped
			strands: [{ id: sid, state: state || 'stopped', body: 'claude/opus' }],
			steps:
				id === 'alpha'
					? [
							{ text: 'write the parser', wishes: [wid], receipt: 'abc1234' },
							{ text: 'wire the parser', wishes: [wid], receipt: null }
						]
					: [],
			plan: id === 'alpha' ? { done: 1, total: 2, bytes: 2048, cap: 8192 } : undefined,
			board: { ...board, strand: state || undefined }
		});
	});
	// the inbox's call stack: an ask on charlie, a verdict on bravo, a verdict foxtrot closed
	const inbox = {
		id: 'inbox',
		title: 'inbox',
		status: 'open',
		strands: [{ id: 's-test-inbx', state: 'idle', body: 'claude/opus' }],
		board: {
			state: 'seated',
			calls: [
				{
					kind: 'ask',
					wish: 'w-charlie0000000',
					line: 'which parser grammar?',
					at: at(15),
					waits: 'ada',
					ref: 's-test-char2/a9'
				},
				{
					kind: 'verdict',
					wish: 'w-bravo0000000',
					line: 'bravo happens',
					at: at(10),
					result: 'threads/bravo/README.md'
				},
				{
					kind: 'verdict',
					wish: 'w-foxtrot0000000',
					line: 'foxtrot happens',
					at: at(34),
					closed: { verdict: 'accepted', letter: 'letter:mine:199', at: at(36) }
				}
			]
		}
	};
	const parts = {};
	const counts = {};
	for (const t of threads) {
		const ls = letters.filter((l) => l.threads.includes(t.id));
		const sid = t.strands[0]?.id;
		parts[`thread/${t.id}`] = {
			id: t.id,
			count: ls.length,
			letters: ls,
			wishes: wishes[t.id],
			tools: sid && tools[sid] ? { [sid]: tools[sid] } : {},
			live: sid && live[sid] ? { [sid]: live[sid] } : {}
		};
		counts[t.id] = {};
		for (const w of wishes[t.id]) counts[t.id][w.state] = (counts[t.id][w.state] || 0) + 1;
	}
	const ib = letters.filter((l) => l.threads.includes('inbox'));
	parts['thread/inbox'] = {
		id: 'inbox',
		count: ib.length,
		letters: ib,
		wishes: [],
		tools: {},
		live: {}
	};
	const beads = {};
	for (const l of letters)
		beads[l.id] = [
			l.re || null,
			l.re ? 're' : l.person ? 'person' : null,
			l.from,
			l.at,
			l.body.slice(0, 40)
		];
	for (const [id, ws] of Object.entries(wishes))
		for (const w of ws) beads[w.wish] = [w.letters?.[0] || null, 'wish', id, w.at, w.line];
	parts.beads = beads;
	return reindex(parts, {
		v: 1,
		threads: [inbox, ...threads],
		fuel: [
			{
				shell: 'claude',
				gauges: [
					{ window: 'session', left_pct: 64 },
					{ window: 'week', left_pct: 41 }
				]
			}
		],
		wishes: counts,
		unthreaded: [],
		person: 'ada',
		via: 'channel:mine/42'
	});
}

// The index names every other part's hash, as compute does; call it again after changing a part.
export function reindex(parts, index = parts.index) {
	const hashes = {};
	for (const [k, v] of Object.entries(parts)) if (k !== 'index') hashes[k] = hash(v);
	const ix = { ...index, parts: hashes };
	ix.parts.index = hash(ix);
	return { ...parts, index: ix };
}

if (import.meta.url === `file://${process.argv[1]}`) {
	const dir = process.argv[2];
	mkdirSync(dir, { recursive: true });
	for (const [k, v] of Object.entries(fixture()))
		writeFileSync(join(dir, k.replaceAll('/', '__') + '.json'), JSON.stringify(v));
	console.log(`parts in ${dir}`);
}
