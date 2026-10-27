// The bead panel's timeline, built in the client from the beads part (design §D3): the loopback has no /bead and no
// /resolve, so the chain up, what hangs below and the composer's ref check all walk `id → [parent, edge, from, at,
// label]`, with the model adding what the beads do not carry (a letter's body, a wish's stamps and verdict).
import type { Model } from './types.ts';
import { ancestors, children, type Beads } from './parts.ts';

export type Kind = 'letter' | 'wish' | 'strand' | 'thread' | 'gone';
export type Root = 'person' | 'loom' | 'unconfirmed' | 'dangling';
export type Bead = { id: string; kind: Kind; from: string | null; at: string | null; line: string };
export type Timeline = {
	me: Bead;
	root: Root;
	// the source first, then each step down to the pressed item; edge is how the next one hangs from it
	chain: (Bead & { edge: string | null })[];
	// what came out of it, by time
	out: Bead[];
};

const STRAND = /^s-[a-z0-9]{4}-[a-z0-9]{6}$/;
export function kindOf(id: string, beads: Beads): Kind {
	if (id.startsWith('thread:')) return 'thread';
	if (/^w-[a-z0-9]{6,16}$/.test(id)) return 'wish';
	if (STRAND.test(id)) return 'strand';
	return id in beads ? 'letter' : 'gone';
}

export function bead(beads: Beads, id: string): Bead {
	const b = beads[id];
	return {
		id,
		kind: kindOf(id, beads),
		from: b?.[2] ?? null,
		at: b?.[3] ?? null,
		line: b?.[4] ?? ''
	};
}

// A bare thread name in a body (&product-shape) is that thread, not a letter id (ko9os b).
export function normalize(id: string, model: Model | null): string {
	id = String(id).replace(/^&/, '');
	if (!id.startsWith('thread:') && model?.threads.some((t) => t.id === id)) return 'thread:' + id;
	return id;
}

export function timeline(beads: Beads, model: Model | null, id: string): Timeline {
	const me = bead(beads, id),
		up = ancestors(beads, id).reverse();
	const chain = up.map((a, i) => ({
		...bead(beads, a.id),
		edge: (up[i + 1] || { edge: null }).edge
	}));
	const top = chain[0] || me;
	const wish = model?.wishes.find((w) => w.wish === top.id);
	const root: Root = !beads[top.id]
		? 'dangling'
		: top.kind === 'letter'
			? top.from === 'loom' || !String(top.from).startsWith('person:')
				? 'loom'
				: 'person'
			: top.kind === 'wish' && wish?.origin === 'derived' && !wish.verdict
				? 'unconfirmed'
				: 'loom';
	const out = children(beads, id).map((c) => bead(beads, c));
	return { me, root, chain, out };
}

// The composer's ref check: an &token names a bead when the beads hold it whole, as a thread name, or as the tail of
// one letter id (refs.py's short form, `&4954` → `letter:<channel>:4954`). Two letters ending the same way stay unresolved.
export function resolveRef(beads: Beads, model: Model | null, token: string): string | null {
	const id = normalize(token, model);
	if (id in beads) return id;
	const tail = Object.keys(beads).filter((k) => k.endsWith(':' + token) || k.endsWith('/' + token));
	return tail.length === 1 ? tail[0] : null;
}

// The ids a selected item links to on the board (53vuyk): the bead panel's, so the chain up to its source, the item
// and what hangs below it. A letter whose chain crosses a wish links to that wish's cards.
export function panelIds(beads: Beads, id: string | null): ReadonlySet<string> {
	if (!id) return new Set();
	return new Set([...ancestors(beads, id).map((a) => a.id), id, ...children(beads, id)]);
}
