// The bead panel's timeline, built in the client from the beads part (design §D3): the loopback has no /bead and no
// /resolve, so the chain up, what hangs below and the composer's ref check all walk `id → [parent, edge, from, at,
// label]`, with the model adding what the beads do not carry (a letter's body, a wish's stamps and verdict).
import type { Model, Step, Wish } from './types.ts';
import { ancestors, children, type Beads } from './parts.ts';
import { stageOf } from './stages.ts';
import { short, stepText } from './text.ts';

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

// A ref opens what it names (49phu 2, ko9os d): a URL or a PR goes to the forge, a ledger id to its bead by its first
// words, a sha or a path shows the Plan step whose receipt it is; anything else stays text.
export type Ref =
	| { kind: 'url'; href: string; text: string }
	| { kind: 'bead'; id: string; text: string }
	| { kind: 'step'; sha: string; text: string }
	| { kind: 'text'; text: string };
const words = (t: string) => {
	t = String(t || '')
		.replace(/\s+/g, ' ')
		.trim();
	return t.length > 90 ? t.slice(0, 88) + '…' : t;
};
export function refOf(r: string, beads: Beads, steps: Step[]): Ref {
	r = String(r || '');
	if (/^https?:/.test(r))
		return { kind: 'url', href: r, text: r.replace(/^https:\/\/github\.com\//, '') };
	const pr = r.match(/^(?:PR |brnrd)#(\d+)$/);
	if (pr)
		return { kind: 'url', href: `https://github.com/hugimuni-labs/brnrd/pull/${pr[1]}`, text: r };
	if (beads[r]) return { kind: 'bead', id: r, text: words(beads[r][4]) || short(r) };
	const st = r && steps.find((x) => x.receipt === r || String(x.text).includes(r));
	if (st) return { kind: 'step', sha: r, text: words(stepText(st.text)) };
	return { kind: 'text', text: r };
}

// A wish's trace under its stage rail, by time: every stamp that carries a ref or a line (and every read), the
// results no stamp named, the verdict, and the beads that came out of it.
export type Trace =
	| { t: 'stamp'; at: string; k: string; by?: string; ref?: string; line?: string }
	| { t: 'result'; at: string; ref: string; line?: string }
	| { t: 'verdict'; at: string; as: string; ref?: string }
	| { t: 'bead'; at: string; bead: Bead };
export function trace(w: Wish | undefined, T: Timeline): Trace[] {
	const rows: Trace[] = [],
		shown = new Set<string>();
	if (w) {
		for (const s of w.stamps) {
			const k = stageOf(s);
			if (!s.ref && !s.line && k !== 'read') continue;
			if (s.ref) shown.add(String(s.ref));
			rows.push({ t: 'stamp', at: s.at || '', k, by: s.by, ref: s.ref, line: s.line });
		}
		for (const r of w.results || []) {
			const ref = r.ref || '';
			if (!ref || shown.has(ref)) continue;
			shown.add(ref);
			rows.push({ t: 'result', at: r.at || '~', ref, line: r.line });
		}
		if (w.verdict)
			rows.push({ t: 'verdict', at: w.verdict.at || '', as: w.verdict.as, ref: w.verdict.ref });
	}
	for (const b of T.out) rows.push({ t: 'bead', at: b.at || '', bead: b });
	return rows.sort((a, b) => (a.at < b.at ? -1 : a.at > b.at ? 1 : 0));
}
