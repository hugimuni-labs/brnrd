// The board's data, as parts (design §D1): GET /v1/loom/index carries every other part's hash; a thread part or beads
// is fetched again only when its hash in the index moves. /v1/loom/events sends the index hash whenever it moves.
import type { Index, Letter, Model, ThreadPart } from './types.ts';
import { byAt } from './text.ts';

export const BASE = '/v1/loom';
export type Beads = Record<
	string,
	[string | null, string | null, string | null, string | null, string]
>;
type Get = (path: string) => Promise<unknown>;

export async function getJSON(path: string): Promise<unknown> {
	const r = await fetch(path, { cache: 'no-store' });
	if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
	return r.json();
}

// One letter may sit in several thread parts (its `threads`); the board holds it once.
export function assemble(index: Index, threads: Map<string, ThreadPart>): Model {
	const byId = new Map<string, Letter>(),
		tools: Model['tools'] = {},
		live: Model['live'] = {},
		wishes = [...index.unthreaded];
	for (const p of threads.values()) {
		for (const l of p.letters) if (!byId.has(l.id)) byId.set(l.id, l);
		wishes.push(...p.wishes);
		Object.assign(tools, p.tools);
		Object.assign(live, p.live);
	}
	return {
		threads: index.threads,
		letters: [...byId.values()].sort(byAt),
		byId,
		wishes,
		tools,
		live,
		fuel: index.fuel || [],
		person: index.person,
		via: index.via
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

export class Store {
	index: Index | null = null;
	threads = new Map<string, ThreadPart>();
	beads: Beads | null = null;
	held = new Map<string, string>();
	private get: Get;
	constructor(get: Get) {
		this.get = get;
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
			this.threads.set(n.slice(7), got[i]);
			this.held.set(n, index.parts[n]);
		});
		for (const n of drop) {
			this.threads.delete(n.slice(7));
			this.held.delete(n);
		}
		if (this.beads && this.held.get('beads') !== index.parts.beads) this.beads = null;
		this.index = index;
		return true;
	}

	// Beads load on the first press, then follow the index.
	async loadBeads(): Promise<Beads> {
		if (!this.beads && this.index) {
			this.beads = (await this.get(`${BASE}/beads`)) as Beads;
			this.held.set('beads', this.index.parts.beads);
		}
		return this.beads || {};
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
		return this.index ? assemble(this.index, this.threads) : null;
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

// What hangs directly below an item, oldest first.
export function children(beads: Beads, id: string): string[] {
	return Object.entries(beads)
		.filter(([, b]) => b[0] === id)
		.sort((a, b) => String(a[1][3] || '').localeCompare(String(b[1][3] || '')))
		.map(([k]) => k);
}
