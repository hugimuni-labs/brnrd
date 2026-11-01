// Who a letter is from and which side of a column it sits on (4954, w-vr3li4w9onww).
import type { Letter, Thread } from './types.ts';

export const INBOX = 'inbox';

// Internal: a resident's letter to a resident or thread (notes, molts, inbox<->threads, loom notices); person
// letters and letters to a channel stay full.
export const internal = (l: Letter): boolean =>
	!l.person && !String(l.to || '').startsWith('channel:');

export const sender = (l: Letter): string =>
	l.notice ? 'loom' : l.person ? l.from.replace('person:', '') : l.from.split('/')[0];

export const ownStrands = (threads: Thread[], col: string): Set<string> =>
	new Set((threads.find((t) => t.id === col)?.strands || []).map((s) => s.id));

// On the right what came into the column: his letters on the inbox and, on a thread, every letter its own
// strands did not write; on the left what the column's strands wrote.
export const inSide = (l: Letter, col: string | null, threads: Thread[]): boolean =>
	!col || col === INBOX ? !!l.person : !ownStrands(threads, col).has(l.from);

// A person letter's routes: the threads its wishes and its answers went to.
export function routes(
	l: Letter,
	byId: Map<string, Letter>,
	has: (t: string) => boolean
): string[] {
	const out: string[] = [];
	for (const w of l.wishes || []) if (w.thread) out.push(w.thread);
	for (const id of l.answers || []) for (const t of byId.get(id)?.threads || []) out.push(t);
	return [...new Set(out)].filter((t) => t !== INBOX && has(t));
}

// The ids a tapped letter links to across columns: the letter it answers and the letters answering it.
export function linked(id: string, letters: Letter[], byId: Map<string, Letter>): Set<string> {
	const resolve = (re?: string) => (re ? byId.get(re) || byId.get('letter:' + re) : undefined);
	const l = byId.get(id),
		ids = new Set([id]);
	const r = resolve(l?.re);
	if (r) ids.add(r.id);
	for (const x of l?.answers || []) ids.add(x);
	for (const x of letters) if (x.re && resolve(x.re)?.id === id) ids.add(x.id);
	return ids;
}

// Colour means the thread, and only the thread (w-k1cbhzog8guq): a resident's letter takes the colour of the thread
// its strand works for, his letters one fixed neutral, so no hue reads as a person. The bar's line says which strand
// (10007): solid for the thread's current strand (its last), dashed for an earlier one; the short id stays as text.
export const ME = '#171717';
export function letterMark(
	l: Letter,
	col: string,
	threads: Thread[]
): { thread: string | null; line?: 'solid' | 'dashed' } {
	if (l.person) return { thread: null };
	const who = sender(l);
	for (const t of threads) {
		const s = t.strands || [];
		const i = s.findIndex((x) => x.id === who);
		if (i >= 0) return { thread: t.id, line: i === s.length - 1 ? 'solid' : 'dashed' };
	}
	return { thread: l.threads?.[0] || col, line: 'solid' };
}
