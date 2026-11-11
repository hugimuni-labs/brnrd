// The board's parts kept in the browser (w-kkghfg2vo6do, his 10361): the phone drops the tab on every switch to
// Telegram, and a restart that fetches 90 parts from nothing takes seconds. Every part GET is remembered in the Cache
// API under its path; the next page primes the Store from it and paints before the network answers, then syncs and
// fetches only the parts whose hash moved. A 401 forgets it all: the next person on this browser gets no board of his.
// `board.cached` in localStorage says, synchronously, that a board is held: the layout skips its curtain and the page
// draws nothing in place of the landing while the cache is read.
import { BASE, type Beads } from './parts.ts';
import type { Index, ThreadPart, Topic, Wish } from './types.ts';

const NAME = 'brnrd-board';
export const FLAG = 'board.cached';

export type Held = {
	index: Index;
	threads: Map<string, ThreadPart>;
	topics: Topic[] | null;
	unthreaded: Wish[] | null;
	beads: Beads | null;
};

const store = async () => {
	try {
		return typeof caches === 'undefined' ? null : await caches.open(NAME);
	} catch {
		return null;
	}
};

export const cached = () =>
	typeof localStorage !== 'undefined' && localStorage.getItem(FLAG) === '1';

// A path is a whole part when it has no query: a window (`?before=`) or a delta (`?since=`) is not.
export async function remember(path: string, json: unknown): Promise<void> {
	if (!path.startsWith(BASE + '/') || path.includes('?')) return;
	const c = await store();
	if (!c) return;
	try {
		await c.put(
			path,
			new Response(JSON.stringify(json), { headers: { 'content-type': 'application/json' } })
		);
		if (path === `${BASE}/index`) localStorage.setItem(FLAG, '1');
	} catch {
		/* a full or refused cache is no cache */
	}
}

// A get that remembers what it got; the Store's.
export const remembering = (get: (path: string) => Promise<unknown>) => (path: string) =>
	get(path).then((j) => (remember(path, j), j));

export async function forget(): Promise<void> {
	try {
		localStorage.removeItem(FLAG);
		if (typeof caches !== 'undefined') await caches.delete(NAME);
	} catch {
		/* nothing held */
	}
}

const read = async (c: Cache, path: string): Promise<unknown> => {
	const r = await c.match(path);
	return r ? r.json().catch(() => null) : null;
};

// What the last page held, whole: the index and every thread part it names, or nothing when any is missing.
export async function restore(): Promise<Held | null> {
	const c = await store();
	if (!c) return null;
	const index = (await read(c, `${BASE}/index`)) as Index | null;
	if (!index || typeof index !== 'object' || !index.parts) return null;
	const names = Object.keys(index.parts).filter((n) => n.startsWith('thread/'));
	const parts = await Promise.all(names.map((n) => read(c, `${BASE}/${n}`)));
	if (parts.some((p) => !p)) return null;
	const threads = new Map<string, ThreadPart>();
	names.forEach((n, i) => threads.set(n.slice(7), parts[i] as ThreadPart));
	const [topics, unthreaded, beads] = await Promise.all([
		index.parts.topics ? read(c, `${BASE}/topics`) : null,
		index.parts.unthreaded ? read(c, `${BASE}/unthreaded`) : null,
		read(c, `${BASE}/beads`)
	]);
	return {
		index,
		threads,
		topics: topics as Topic[] | null,
		unthreaded: unthreaded as Wish[] | null,
		beads: beads as Beads | null
	};
}
