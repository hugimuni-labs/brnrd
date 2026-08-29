// ROOM REGIONS — the ground is assigned before the painters arrive.
//
// The defect this closes, measured on the live board 2026-08-29: `RoomLayout`
// assigned points to topology nodes, and `asciiCamera` independently derived
// the terminal's 50×7 box from the camp's point and painted it afterwards
// with claiming writes. Two owners, one cell each, and paint order decided
// which truth disappeared — a `prompts/` label byte-identical to a directory
// that was never observed.
//
// Design: `design-room-operational-topology.md` §Space building. Four passes:
//
//   1. compile spatial requests  — an owner, a lifetime, a minimum extent in
//      world units, a district. Never an absolute coordinate.
//   2. assign districts          — the island owns stable directional growth
//      domains around its root. Disjoint by construction, not by looking at
//      which side currently seems empty.
//   3. lay out locally           — each district allocates inside itself,
//      append-only, through the shelf below.
//   4. compose, then paint       — layout returns points *and* rectangles;
//      the camera transforms them and invents nothing.
//
// The one constraint that rules out a general rectangle packer: **atlas
// memory**. A packer repacks when a request grows, and repacking moves
// chambers a reader has already explored. So the shelf here is append-only:
// it finds a free slot for a new request and never relocates an old one. A
// future compaction is an explicit atlas migration, never an incidental
// consequence of one more node.

export interface Rect {
	x: number;
	y: number;
	w: number;
	h: number;
}

/** The island's stable growth domains, in island-relative world units.
 *  Disjoint: no two districts share a cell, so no two owners can. */
export type DistrictName = 'terrain' | 'camp' | 'labour' | 'forge';

/**
 * A subsystem asking for ground. It declares what it is and how big it must
 * be; it does not declare where. `attach` names a district-local port (today
 * only the camp's, which is the only instrument anchored to a moving thing).
 */
export interface SpatialRequest {
	id: string;
	owner: string;
	district: DistrictName;
	/** `durable` survives the run (terrain, camps, docks); `ephemeral` is a
	 *  live instrument and its slot is reclaimable by a later atlas
	 *  migration — never mid-run, which would be the reflow this forbids. */
	lifetime: 'durable' | 'ephemeral';
	/** Minimum extent in world units. The allocator may give more, never less. */
	w: number;
	h: number;
	/** Island-relative x the request would like to start at — a preference the
	 *  allocator honours when the slot is free, never a coordinate. */
	preferX?: number;
	preferY?: number;
}

// ── the districts ───────────────────────────────────────────────────────────
//
// Island-relative. The root sits at (0, 0); north is −y, east is +x.
//
//   labour   y ≤ LABOUR_FLOOR          the control band above everything
//   ─────────────────────────────────────────────────────────────────────
//   forge    x ≤ SHORE_EDGE  |  camp   |  terrain   x ≥ 0
//            the outward shore  the west shore  the tree, growing east+south
//
// The band boundary is what makes the terminal safe: terrain never grows
// north of TERRAIN_TOP, so a 25×7 window sitting at y = camp.y − 8 cannot be
// reached by a hundred-chamber tree.

/** Terrain may never claim a row north of the root's own row. It does not
 *  want to: the tree walks depth-first *downward* (`roomLayout`), so this is
 *  a floor the allocator never approaches from above — which is exactly what
 *  lets the labour band sit close enough to the camp to read as standing on
 *  it. A district that had to leave room for terrain growing both ways would
 *  have pushed the terminal ten rows into open sea. */
export const TERRAIN_TOP = 0;
/** The camps' station cluster reaches three rows north of its camp
 *  (`STATION_OFFSETS`), so the west shore's own northmost row is this. */
export const CAMP_TOP = -3;
/** The labour band's southern floor: one row of air above the northmost
 *  station. The terminal's rectangle hangs from here, growing north. */
export const LABOUR_FLOOR = CAMP_TOP - 1; // −4
/** West of this is the outward shore: forge dock, HOME's approaches. */
export const SHORE_EDGE = -13;

export function districtOf(name: DistrictName, origin: { x: number; y: number }): Rect {
	// `Infinity` extents are honest: terrain and the labour band are
	// unbounded in their growth direction. A finite number here would be a
	// cap nobody chose, discovered by the first run that exceeded it.
	switch (name) {
		case 'terrain':
			return { x: origin.x, y: origin.y + TERRAIN_TOP, w: Infinity, h: Infinity };
		case 'camp':
			return {
				x: origin.x + SHORE_EDGE + 1,
				y: origin.y + TERRAIN_TOP,
				w: -SHORE_EDGE - 1,
				h: Infinity
			};
		case 'labour':
			return {
				x: origin.x - 34,
				y: -Infinity,
				w: Infinity,
				h: origin.y + LABOUR_FLOOR - -Infinity
			};
		case 'forge':
			return { x: -Infinity, y: origin.y + CAMP_TOP, w: Infinity, h: Infinity };
	}
}

/** Does `p` fall inside the district a given owner may claim? Used by the
 *  tests to prove the disjointness the design asserts, and by the layout to
 *  clamp a lane that would wander out of its own domain. */
export function inDistrict(
	name: DistrictName,
	origin: { x: number; y: number },
	p: { x: number; y: number }
): boolean {
	const relX = p.x - origin.x;
	const relY = p.y - origin.y;
	switch (name) {
		case 'terrain':
			return relX >= 0 && relY >= TERRAIN_TOP;
		case 'camp':
			return relX > SHORE_EDGE && relX < 0 && relY >= CAMP_TOP;
		case 'labour':
			return relY <= LABOUR_FLOOR;
		case 'forge':
			return relX <= SHORE_EDGE && relY >= CAMP_TOP;
	}
}

// ── the append-only shelf ───────────────────────────────────────────────────

/** One claimed span on one world row: `[x, x + w)`. */
interface Span {
	x: number;
	w: number;
}

/**
 * An append-only occupancy index over world rows.
 *
 * Two users, one mechanism — which is the point. Terrain nodes claim their
 * painted extent so no two labels can be addressed at one cell; the labour
 * band claims the terminal's rectangle so the tree can grow a hundred
 * chambers east without reaching it.
 *
 * Append-only means: `claim` finds a free slot for a *new* extent and never
 * moves an existing one. A claim that cannot fit anywhere in its preferred
 * direction walks further out; it never evicts.
 */
export class Shelf {
	private rows = new Map<number, Span[]>();

	/** Record an extent that already has a home (a remembered atlas
	 *  coordinate, a fixed fixture). Always succeeds — memory outranks the
	 *  allocator, which is what makes reload stable. */
	occupy(x: number, y: number, w: number, h = 1): void {
		for (let r = y; r < y + h; r++) {
			const spans = this.rows.get(r) ?? [];
			spans.push({ x, w: Math.max(1, w) });
			this.rows.set(r, spans);
		}
	}

	free(x: number, y: number, w: number, h = 1): boolean {
		const width = Math.max(1, w);
		for (let r = y; r < y + h; r++) {
			for (const s of this.rows.get(r) ?? []) {
				if (x < s.x + s.w && s.x < x + width) return false;
			}
		}
		return true;
	}

	/**
	 * Claim `w × h` at `x`, searching rows outward from `preferY` in the
	 * given direction, and occupy it. `minY`/`maxY` clamp the search to the
	 * caller's own district so an allocation can never wander into another
	 * owner's domain.
	 */
	claim(
		x: number,
		w: number,
		opts: {
			preferY: number;
			h?: number;
			/** `'south'` walks +1, +2…; `'north'` walks −1, −2…; `'both'`
			 *  alternates, nearest-first. */
			direction?: 'south' | 'north' | 'both';
			minY?: number;
			maxY?: number;
			limit?: number;
		}
	): { x: number; y: number } {
		const h = opts.h ?? 1;
		const dir = opts.direction ?? 'south';
		const minY = opts.minY ?? -Infinity;
		const maxY = opts.maxY ?? Infinity;
		const limit = opts.limit ?? 256;
		for (let i = 0; i < limit; i++) {
			const y = opts.preferY + step(i, dir);
			if (y < minY || y + h - 1 > maxY) continue;
			if (this.free(x, y, w, h)) {
				this.occupy(x, y, w, h);
				return { x, y };
			}
		}
		// Exhausted: place at the far end rather than refuse. A layout that
		// declines to place a node renders as a node that was never observed,
		// which is the narrowing this whole module exists to stop.
		const y = clamp(opts.preferY + step(limit, dir), minY, maxY - h + 1);
		this.occupy(x, y, w, h);
		return { x, y };
	}
}

function step(i: number, dir: 'south' | 'north' | 'both'): number {
	if (dir === 'south') return i;
	if (dir === 'north') return -i;
	if (i === 0) return 0;
	const k = Math.ceil(i / 2);
	return i % 2 === 1 ? k : -k;
}

function clamp(v: number, lo: number, hi: number): number {
	return Math.max(lo, Math.min(hi, v));
}
