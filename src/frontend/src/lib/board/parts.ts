// The board's data, as parts (design §D1): GET /v1/loom/index carries every other part's hash; a thread part or beads
// is fetched again only when its hash in the index moves. Beads then patch from the index's beads_delta or
// GET /v1/loom/beads?since=<hash>, so a letter does not refetch the whole part. /v1/loom/events sends the index hash
// whenever it moves.
import type {
	BeadsDelta,
	Fuel,
	Index,
	Letter,
	Machine,
	Model,
	Thread,
	ThreadPart,
	Topic,
	Wish
} from './types.ts';
import { byAt, dated, time } from './text.ts';
import type { Held } from './cache.ts';

export const BASE = '/v1/loom';
export type Beads = Record<
	string,
	[string | null, string | null, string | null, string | null, string]
>;
export type { BeadsDelta };
type Get = (path: string) => Promise<unknown>;

export function isDelta(got: unknown): got is BeadsDelta {
	return (
		!!got &&
		typeof got === 'object' &&
		'set' in got &&
		'drop' in got &&
		Array.isArray((got as BeadsDelta).drop) &&
		typeof (got as BeadsDelta).set === 'object'
	);
}

export function patchBeads(held: Beads, delta: BeadsDelta): Beads {
	const next = { ...held, ...delta.set };
	for (const id of delta.drop) delete next[id];
	return next;
}

export async function getJSON(path: string): Promise<unknown> {
	const r = await fetch(path, { cache: 'no-store' });
	if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
	return r.json();
}

// The index row is short. Steps, strands and calls ride on the thread part; join puts them back on the column.
const PART_ONLY = new Set(['letters', 'wishes', 'tools', 'live', 'chain', 'more', 'count', 'id']);

// The header line. One shell per provider, in the index's order (name order, the status line's without a seat
// pulled forward). A collector error with no window is the word the index carries (`fails`); the error text stays off.
export function headerFuel(fuel: Fuel[] | undefined): string {
	return (fuel ?? [])
		.map((f) => {
			const gauges = (f.gauges ?? []).map((g) => `${g.window} ${g.left_pct}%`).join(' ');
			const word = gauges || f.error || '';
			return word ? `${f.shell} ${word}` : '';
		})
		.filter((line) => line)
		.join('   ');
}

// The leading machine, and on hover every machine this install has heard of. `seen` is the newest router beat
// that reached this install, not liveness: a peer's facts come through the shuttle a couple of minutes late.
export function leadName(machines: Machine[] | undefined): string {
	if (!machines || machines.length < 2) return '';
	return machines.find((m) => m.lead)?.name || 'Other computer';
}

export function machinesTitle(machines: Machine[] | undefined): string {
	return (machines ?? [])
		.map((m) => {
			const beat = m.seen ? `, beat ${m.seen.slice(11, 19)}Z` : '';
			const gen = m.gen !== undefined ? ` gen ${m.gen}` : '';
			return `${m.name || 'Other computer'}${m.here ? ' (the loom serving this page)' : ''}: ${m.lead ? 'leads' : 'follows'}${gen}${beat}`;
		})
		.join('\n');
}

export function join(row: Thread, part?: ThreadPart): Thread {
	if (!part) return row;
	const head: Partial<Thread> = {};
	for (const [k, v] of Object.entries(part))
		if (!PART_ONLY.has(k)) (head as Record<string, unknown>)[k] = v;
	return { ...head, ...row, board: { ...(head.board || {}), ...(row.board || {}) } };
}

// One letter may sit in several thread parts (its `threads`); the board holds it once.
// `loose` is the unthreaded part. When the caller omits it, a fixture's index.unthreaded still counts.
export function assemble(index: Index, threads: Map<string, ThreadPart>, loose?: Wish[]): Model {
	const byId = new Map<string, Letter>(),
		tools: Model['tools'] = {},
		live: Model['live'] = {},
		wishes = [...(loose ?? index.unthreaded ?? [])];
	for (const p of threads.values()) {
		for (const l of p.letters)
			if (dated(l) && !byId.has(l.id))
				byId.set(l.id, {
					...l,
					// The loom annotates a letter once per say; several spans can cite the same wish.
					wishes: l.wishes ? [...new Map(l.wishes.map((w) => [w.wish, w])).values()] : undefined
				});
		wishes.push(...p.wishes);
		// Drop undated calls rather than inventing an ordering or a duration for them.
		for (const [id, calls] of Object.entries(p.tools || {})) tools[id] = calls.filter(dated);
		for (const [id, v] of Object.entries(p.live || {}))
			live[id] = {
				...v,
				open: (v.open || []).filter(dated),
				last_end: time(v.last_end) || undefined,
				paused: v.paused,
				paused_at: time(v.paused_at) || undefined
			};
	}
	return {
		threads: index.threads.map((t) => join(t, threads.get(t.id))),
		letters: [...byId.values()].sort(byAt),
		byId,
		wishes,
		tools,
		live,
		fuel: [...new Map((index.fuel || []).map((s) => [s.shell, s])).values()],
		machines: index.machines || [],
		faces: index.faces,
		person: index.person,
		person_names: index.person_names,
		via: index.via,
		topics: []
	};
}

// The thread parts whose hash moved since `held`, and the ones the index no longer names.
export function moved(
	index: Index,
	held: Map<string, string>
): { fetch: string[]; drop: string[] } {
	const names = Object.keys(index.parts).filter((n) => n.startsWith('thread/'));
	return {
		fetch: names.filter((n) => held.get(n) !== index.parts[n]),
		drop: [...held.keys()].filter((n) => n.startsWith('thread/') && !(n in index.parts))
	};
}

// A refetched part is the newest window again; the older letters scrolled in before it stay, so a new letter
// does not drop the history a reader loaded.
export function keep(was: ThreadPart | undefined, got: ThreadPart): ThreadPart {
	const first = got.letters[0];
	if (!was || !first || !got.more) return got;
	const have = new Set(got.letters.map((l) => l.id));
	const prior = was.letters.filter(
		(l) => !have.has(l.id) && (l.at < first.at || (l.at === first.at && l.id < first.id))
	);
	return prior.length ? { ...got, letters: [...prior, ...got.letters], more: was.more } : got;
}

export class Store {
	index: Index | null = null;
	threads = new Map<string, ThreadPart>();
	beads: Beads | null = null;
	topics: Topic[] = [];
	unthreaded: Wish[] = [];
	held = new Map<string, string>();
	private get: Get;
	private loading: Promise<Beads> | null = null;
	constructor(get: Get) {
		this.get = get;
	}

	// Primed from the last page's cache (cache.ts): model() answers at once, and the first sync fetches only the parts
	// whose hash moved since. A part the index names but the cache lacks is left unheld, so sync fetches it.
	prime(held: Held) {
		this.index = held.index;
		this.threads = new Map(held.threads);
		for (const [n, h] of Object.entries(held.index.parts))
			if (n.startsWith('thread/') && held.threads.has(n.slice(7))) this.held.set(n, h);
		if (held.topics && held.index.parts.topics) {
			this.topics = held.topics;
			this.held.set('topics', held.index.parts.topics);
		}
		if (held.unthreaded && held.index.parts.unthreaded) {
			this.unthreaded = held.unthreaded;
			this.held.set('unthreaded', held.index.parts.unthreaded);
		}
		if (held.beads && held.index.parts.beads) {
			this.beads = held.beads;
			this.held.set('beads', held.index.parts.beads);
		}
	}

	// Bring every part to the index's hashes; true when anything changed. Thread parts load in parallel.
	async sync(): Promise<boolean> {
		const index = (await this.get(`${BASE}/index`)) as Index;
		if (this.index && JSON.stringify(this.index) === JSON.stringify(index)) return false;
		const { fetch, drop } = moved(index, this.held);
		const got = await Promise.all(
			fetch.map((n) => this.get(`${BASE}/${n}`) as Promise<ThreadPart>)
		);
		fetch.forEach((n, i) => {
			this.threads.set(n.slice(7), keep(this.threads.get(n.slice(7)), got[i]));
			this.held.set(n, index.parts[n]);
		});
		for (const n of drop) {
			this.threads.delete(n.slice(7));
			this.held.delete(n);
		}
		if (this.beads && this.held.get('beads') !== index.parts.beads) await this.patchBeads(index);
		if (index.parts.topics && this.held.get('topics') !== index.parts.topics) {
			this.topics = (await this.get(`${BASE}/topics`)) as Topic[];
			this.held.set('topics', index.parts.topics);
		}
		if (!index.parts.topics) {
			this.topics = [];
			this.held.delete('topics');
		}
		if (index.parts.unthreaded && this.held.get('unthreaded') !== index.parts.unthreaded) {
			this.unthreaded = (await this.get(`${BASE}/unthreaded`)) as Wish[];
			this.held.set('unthreaded', index.parts.unthreaded);
		}
		if (!index.parts.unthreaded) {
			this.unthreaded = [];
			this.held.delete('unthreaded');
		}
		this.index = index;
		return true;
	}

	// Apply the index's last delta when we hold its `since`; otherwise GET ?since= and fall back to the whole part.
	private async patchBeads(index: Index) {
		const since = this.held.get('beads');
		const want = index.parts.beads;
		if (!this.beads || !since || since === want) return;
		const delta = index.beads_delta;
		if (delta && delta.since === since) {
			this.beads = patchBeads(this.beads, delta);
			this.held.set('beads', delta.hash || want);
			return;
		}
		const got = await this.get(`${BASE}/beads?since=${encodeURIComponent(since)}`);
		if (isDelta(got)) {
			this.beads = patchBeads(this.beads, got);
			this.held.set('beads', got.hash || want);
			return;
		}
		this.beads = got as Beads;
		this.held.set('beads', want);
	}

	// Beads load on the first press, then follow the index by patch. One in-flight GET: select and openBead
	// both ask, and a second 1 MB fetch is the thing this diet exists to stop.
	async loadBeads(): Promise<Beads> {
		if (this.beads) return this.beads;
		if (this.loading) return this.loading;
		if (!this.index) return {};
		const index = this.index;
		this.loading = this.get(`${BASE}/beads`)
			.then((got) => {
				this.beads = got as Beads;
				this.held.set('beads', index.parts.beads);
				return this.beads;
			})
			.finally(() => {
				this.loading = null;
			});
		return this.loading;
	}

	// History: the 200 letters before the oldest one held, or back to `until` (a verdict, bead or link's letter).
	async older(thread: string, opts: { until?: string } = {}): Promise<boolean> {
		const p = this.threads.get(thread);
		if (!p || !p.more) return false;
		const q = opts.until
			? `until=${encodeURIComponent(opts.until)}`
			: `before=${encodeURIComponent(p.letters[0]?.id || '')}`;
		const cut = (await this.get(`${BASE}/thread/${encodeURIComponent(thread)}?${q}`)) as ThreadPart;
		const have = new Set(p.letters.map((l) => l.id));
		this.threads.set(thread, {
			...p,
			letters: [...cut.letters.filter((l) => !have.has(l.id)), ...p.letters],
			more: cut.more
		});
		return true;
	}

	model(): Model | null {
		if (!this.index) return null;
		const loose =
			this.held.has('unthreaded') || this.index.parts.unthreaded ? this.unthreaded : undefined;
		const m = assemble(this.index, this.threads, loose);
		m.topics = this.topics;
		return m;
	}
}

// The chain up from an item to its source (the beads' labelled parent map, design §D3), nearest first.
export function ancestors(beads: Beads, id: string): { id: string; edge: string | null }[] {
	const out: { id: string; edge: string | null }[] = [],
		seen = new Set([id]);
	let cur = beads[id];
	while (cur && cur[0] && !seen.has(cur[0])) {
		out.push({ id: cur[0], edge: cur[1] });
		seen.add(cur[0]);
		cur = beads[cur[0]];
	}
	return out;
}

// What hangs directly below an item, oldest first. The parent map is inverted once per beads object (a patch makes a
// new one): a scan of every bead on each press and each selection was most of the sheet's walk.
const below = new WeakMap<Beads, Map<string, string[]>>();
export function children(beads: Beads, id: string): string[] {
	let m = below.get(beads);
	if (!m) {
		m = new Map();
		for (const [k, b] of Object.entries(beads)) {
			if (!b[0]) continue;
			const l = m.get(b[0]);
			if (l) l.push(k);
			else m.set(b[0], [k]);
		}
		for (const l of m.values())
			l.sort((a, c) => String(beads[a][3] || '').localeCompare(String(beads[c][3] || '')));
		below.set(beads, m);
	}
	return m.get(id) || [];
}
