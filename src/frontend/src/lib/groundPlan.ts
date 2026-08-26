// The generated ground — space creation for the room (2026-08-26, his
// steer: "the actual space creation (likely fog of war, roguelike, but from
// clear structure … we could have very rigid structure as well)").
//
// The resolution is a hybrid, and this module is the generated half:
//
// - **The civic frame stays rigid** (gate, garage, plaza — isoField.ts):
//   every room shares it, so a reader always knows where the door is.
// - **The ground is generated from clear structure**: districts derived
//   deterministically from the repository's own tree — top-level working
//   directories as chambers, sized by weight, laid out by a seeded BSP over
//   the plate. Roguelike in character, never random in fact: the same repo
//   deals the same map every time (the seed is the repo label; jitter comes
//   from hashing, not clocks), so the space is *learnable*.
// - **Fog of war is attention**: a district is LIT where recorded acts are
//   currently touching it (edge.dir), EXPLORED where they once did, VOID
//   where no run has ever stood. The being is the light source; the atlas
//   grows as the work explores — which is the loom again: exploring is
//   weaving the map.
//
// Pure geometry + pure state transitions; the route draws. No filesystem
// access here: nodes arrive from the caller (the demo carries this repo's
// real shape as fixture; live mode accumulates districts from observed
// `edge.dir`s until a ground wire exists — the gap is named in
// design-the-loom-being.md).

export interface GroundNode {
	/** Repo-relative dir path, '/'-separated, no leading './'. */
	path: string;
	/** Relative size — file count, recency-weighted churn, anything
	 *  monotone; only ratios matter. Non-positive weights are clamped. */
	weight: number;
}

export interface District {
	path: string;
	/** Last path segment — the painted name. */
	name: string;
	x: number;
	y: number;
	w: number;
	d: number;
}

export interface GroundArea {
	x: number;
	y: number;
	w: number;
	d: number;
}

/** FNV-1a, 32-bit — the same stable hash the face modules use. */
function fnv1a(text: string): number {
	let h = 0x811c9dc5;
	for (let i = 0; i < text.length; i++) {
		h ^= text.charCodeAt(i);
		h = Math.imul(h, 0x01000193) >>> 0;
	}
	return h >>> 0;
}

/** Deterministic jitter in [−amp, +amp] from seed+key — roguelike character
 *  without randomness (Date/Math.random would break the learnable-map
 *  contract and every replay). */
function jitter(seed: string, key: string, amp: number): number {
	const h = fnv1a(`${seed}::${key}`);
	return ((h % 1000) / 1000 - 0.5) * 2 * amp;
}

/** The street between districts, tiles — enough negative space that two
 *  floors never read as one. */
const STREET = 0.14;

/**
 * Seeded BSP over `area`: split the weight-sorted list into two halves of
 * roughly equal weight, split the area along its longer axis at the weight
 * ratio (jittered by the seed for character), recurse. Deterministic for a
 * given (nodes, area, seed); output rects tile the area minus streets.
 */
export function planGround(nodes: GroundNode[], area: GroundArea, seed: string): District[] {
	const cleaned = nodes
		.filter((n) => n.path.length > 0)
		.map((n) => ({ ...n, weight: Math.max(n.weight, 0.0001) }));
	// Weight-desc then path: the ordering is part of the determinism contract.
	cleaned.sort((a, b) => b.weight - a.weight || (a.path < b.path ? -1 : 1));
	const out: District[] = [];
	const place = (list: GroundNode[], rect: GroundArea) => {
		if (list.length === 0) return;
		if (list.length === 1) {
			const n = list[0];
			out.push({
				path: n.path,
				name: n.path.split('/').pop() ?? n.path,
				x: rect.x + STREET / 2,
				y: rect.y + STREET / 2,
				w: Math.max(rect.w - STREET, 0.2),
				d: Math.max(rect.d - STREET, 0.2)
			});
			return;
		}
		const total = list.reduce((acc, n) => acc + n.weight, 0);
		// Greedy halving: walk the sorted list until half the weight is taken.
		let acc = 0;
		let cut = 0;
		for (let i = 0; i < list.length - 1; i++) {
			acc += list[i].weight;
			cut = i + 1;
			if (acc >= total / 2) break;
		}
		const a = list.slice(0, cut);
		const b = list.slice(cut);
		const wa = a.reduce((s, n) => s + n.weight, 0);
		let ratio = wa / total;
		ratio += jitter(seed, a[0].path, 0.05);
		ratio = Math.min(0.82, Math.max(0.18, ratio));
		if (rect.w >= rect.d) {
			const wSplit = rect.w * ratio;
			place(a, { x: rect.x, y: rect.y, w: wSplit, d: rect.d });
			place(b, { x: rect.x + wSplit, y: rect.y, w: rect.w - wSplit, d: rect.d });
		} else {
			const dSplit = rect.d * ratio;
			place(a, { x: rect.x, y: rect.y, w: rect.w, d: dSplit });
			place(b, { x: rect.x, y: rect.y + dSplit, w: rect.w, d: rect.d - dSplit });
		}
	};
	place(cleaned, area);
	return out;
}

// ── fog ─────────────────────────────────────────────────────────────────────

export type Fog = 'void' | 'explored' | 'lit';

/** How long a touched district stays lit after its last recorded act. */
export const LIT_MS = 8 * 60_000;

/**
 * The district a working directory stands in: the *deepest* district whose
 * path is a '/'-boundary prefix of `dir`. `'.'`, `''`, and the repo root
 * belong to the plaza, not to any district — null.
 */
export function districtFor(
	districts: District[],
	dir: string | null | undefined
): District | null {
	if (!dir || dir === '.' || dir === './') return null;
	const clean = dir.replace(/^\.\//, '').replace(/\/+$/, '');
	if (!clean) return null;
	let best: District | null = null;
	for (const t of districts) {
		if (clean === t.path || clean.startsWith(t.path + '/')) {
			if (!best || t.path.length > best.path.length) best = t;
		}
	}
	return best;
}

/**
 * Fog state from the atlas — the map of district path → last-touched epoch
 * ms. The atlas is the caller's accumulated observation (state); this is a
 * pure read of it. A district someone works *now* is lit; one the account
 * has ever worked is explored (the roguelike memory state — outlines, no
 * light); one never visited is void, and the route draws it as darkness,
 * not as an empty room. Not guessing and not drawing are different acts.
 */
export function fogOf(district: District, atlas: Record<string, number>, now: number): Fog {
	const seen = atlas[district.path];
	if (seen === undefined) return 'void';
	return now - seen <= LIT_MS ? 'lit' : 'explored';
}

/** Fold one observed working dir into the atlas at `now`. Returns the same
 *  reference when nothing changed, so `$state` diffing stays cheap. */
export function markAtlas(
	atlas: Record<string, number>,
	districts: District[],
	dir: string | null | undefined,
	now: number
): Record<string, number> {
	const hit = districtFor(districts, dir);
	if (!hit) return atlas;
	if (atlas[hit.path] === now) return atlas;
	return { ...atlas, [hit.path]: now };
}

/**
 * The atlas, derived: raw observed dirs (dir → last-seen ms) folded onto a
 * district plan. Pure, so the same sightings re-fold correctly when the
 * plan itself changes shape (live accretion regrows the map under the
 * marks). Each district keeps the newest timestamp among its dirs.
 */
export function atlasFromDirs(
	districts: District[],
	dirs: Record<string, number>
): Record<string, number> {
	const out: Record<string, number> = {};
	for (const [dir, ts] of Object.entries(dirs)) {
		const hit = districtFor(districts, dir);
		if (hit && (out[hit.path] ?? 0) < ts) out[hit.path] = ts;
	}
	return out;
}

/**
 * Live mode has no tree wire yet: the ground grows from what the runs have
 * been *seen* to touch. Fold an observed dir into the node list — first
 * segment (or first two for `src/…`, whose top level is a grouping shell in
 * most repos) becomes a district; repeat sightings gain weight. Deliberately
 * coarse: the demo carries the real tree; this keeps the live floor honest
 * rather than empty until the ground wire exists.
 */
export function growNodes(nodes: GroundNode[], dir: string | null | undefined): GroundNode[] {
	if (!dir || dir === '.' || dir === './') return nodes;
	const clean = dir.replace(/^\.\//, '').replace(/\/+$/, '');
	if (!clean) return nodes;
	const parts = clean.split('/');
	const key = parts[0] === 'src' && parts.length > 1 ? `src/${parts[1]}` : parts[0];
	const existing = nodes.find((n) => n.path === key);
	if (existing) {
		return nodes.map((n) => (n.path === key ? { ...n, weight: n.weight + 1 } : n));
	}
	return [...nodes, { path: key, weight: 1 }];
}
