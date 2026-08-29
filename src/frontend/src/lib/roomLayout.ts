// RoomLayout — deterministic, incremental logical coordinates for the place
// graph (#1652 slice 1), now with the ground assigned before the painters
// arrive (`design-room-operational-topology.md` §Space building, 2026-08-29).
// Coordinates are logical world units, not pixels or character cells: the
// ASCII camera rasterizes one unit as a character-cell multiple; an SVG
// camera may transform the same units.
//
// The invariants (spec: "the exact spacing constants may be tuned; these
// invariants may not"):
//   - repository root is local (0, 0) in island space;
//   - **one node, one row.** No node shares a character row with a node whose
//     painted label would reach it. Reworked 2026-08-29: the old rule let the
//     first child continue its parent's lane, which meant every level had to
//     advance far enough east to clear the parent's *label* — 10 to 28
//     characters of corridor per level, and a five-deep path burned the whole
//     board. `tree(1)` is compact for exactly one reason: entries do not
//     share lines, so the indent can be two characters. Same trade here;
//     depth now advances a constant DEPTH_DX and the row is what separates.
//   - siblings claim the nearest free row via the alternation 0,+1,-1,+2…
//     from the parent, biased south and clamped to the terrain district;
//   - once assigned, a node's coordinate never changes (atlas memory);
//   - adding a chamber may expand world bounds but must not move an
//     existing chamber;
//   - **districts are disjoint, and no allocation crosses one** — terrain
//     grows east and south of the root, the camps hold the west shore, the
//     labour band owns everything north of TERRAIN_TOP, the outward shore
//     holds the forge. A live instrument (the terminal) receives a
//     *rectangle* from its district's append-only shelf, so a hundred-chamber
//     tree can never be addressed at the same cell as the window;
//   - two renderers given the same topology/layout snapshot receive the
//     same coordinates.

import type { PlaceId, PlaceNode, RoomTopology } from './roomTopology.ts';
import {
	CAMP_TOP,
	LABOUR_FLOOR,
	Shelf,
	TERRAIN_TOP,
	districtOf,
	inDistrict,
	type DistrictName,
	type Rect,
	type SpatialRequest
} from './roomRegions.ts';

export type { Rect, SpatialRequest, DistrictName };

export interface Point {
	x: number;
	y: number;
}

export interface RoomLayout {
	nodes: Record<PlaceId, Point>;
	/** Polyline per edge, keyed `${from}->${to}` — vertical run at the
	 *  parent's x, then east along the child's lane. */
	edgeRoutes: Record<string, Point[]>;
	/** Allocated rectangles, keyed by request id (`labour:terminal:<runId>`).
	 *  The camera reads these; it no longer derives an instrument's location
	 *  from a node's point and paints it afterwards. */
	regions: Record<string, Rect>;
	/** The island growth domains, keyed `${rootId}#${district}`. Finite where
	 *  the district is bounded; unbounded edges are omitted rather than
	 *  reported as a number nobody chose. */
	districts: Record<string, Rect>;
	worldBounds: { minX: number; minY: number; maxX: number; maxY: number };
}

/** The persisted half: coordinates and rectangles already assigned. The
 *  caller owns persistence (localStorage today, a server-side atlas later);
 *  layoutRoom never mutates the object it was given. */
export interface AtlasMemory {
	nodes: Record<PlaceId, Point>;
	/** Allocated instrument rectangles. Persisted for the same reason node
	 *  coordinates are: a terminal that moved on reload would be a reflow
	 *  wearing a refresh. Optional so a `-v1` blob still loads. */
	regions?: Record<string, Rect>;
}

export function emptyAtlas(): AtlasMemory {
	return { nodes: {}, regions: {} };
}

// ── spacing constants (tunable) ─────────────────────────────────────────────

const NOMINAL_CHARS_PER_UNIT = 2; // island scale (asciiCamera SCALE.island.x)
/** `tree(1)`'s indent, in world units: 2 units = 4 characters. Constant on
 *  purpose — a label-aware advance is what a shared row costs, and rows are
 *  no longer shared. */
const DEPTH_DX = 2;
/** The label the camera will paint for a tree node, in characters — the
 *  camera clips directory labels at MAX_DIR_LABEL_CHARS (asciiCamera), so
 *  an extent never reserves more than the reader will see. */
export const MAX_DIR_LABEL_CHARS = 24;
function labelChars(node: PlaceNode): number {
	if (node.kind === 'repo-root') {
		const short = node.label.includes('/')
			? (node.label.split('/').pop() ?? node.label)
			: node.label;
		return short.length + 2; // `⌂ `
	}
	if (node.kind === 'file') return node.label.length + 2;
	return Math.min(node.label.length + 1, MAX_DIR_LABEL_CHARS); // `label/`
}
/** A node's painted extent in world units — what it claims on its row so no
 *  other node's label can be addressed at the same cell. One unit of air
 *  past the text, so two labels never touch. */
function extentUnits(node: PlaceNode): number {
	return Math.ceil(labelChars(node) / NOMINAL_CHARS_PER_UNIT) + 1;
}
const ISLAND_DY = 44; // vertical distance between island origins
const HOME_POS: Point = { x: -26, y: 0 };
const CAMP_DX = -9; // camps sit west of the root, home-facing shore
const CAMP_LANE_STEP = 5;
const FORGE_OFFSET: Point = { x: -16, y: 5 }; // the outward shore dock
const STATION_OFFSETS: Record<string, Point> = {
	'portal-rack': { x: -4, y: -2 },
	'chart-table': { x: 0, y: -3 },
	'strand-bay': { x: 4, y: -2 },
	'watch-perch': { x: -4, y: 1 },
	'wake-dock': { x: 4, y: 1 },
	'cut-loom': { x: 0, y: 3 },
	'work-bench': { x: 4, y: 3 }
};
const HOME_FIXTURE_OFFSETS: Record<string, Point> = {
	gate: { x: 0, y: -4 },
	watch: { x: -4, y: -2 },
	clockwork: { x: -4, y: 2 },
	garage: { x: 0, y: 4 },
	library: { x: -3, y: -6 }
};
const RIG_OFFSET: Point = { x: 2, y: 2 }; // off the lane grid

// ── the allocator ───────────────────────────────────────────────────────────

/**
 * Assign coordinates to every node the memory does not already hold, in a
 * deterministic order (islands in topology order; nodes in each island by
 * depth, parents before children by construction of the walk). Returns the
 * layout plus the extended memory; nothing already in the memory moves.
 *
 * `requests` are live spatial requests — instruments that need a rectangle
 * rather than a point (the terminal). They are allocated inside their own
 * district, after every node in that island has claimed its ground, so an
 * instrument never lands on terrain and terrain never lands on it.
 */
export function layoutRoom(
	topo: RoomTopology,
	memory: AtlasMemory = emptyAtlas(),
	requests: SpatialRequest[] = []
): { layout: RoomLayout; memory: AtlasMemory } {
	const coords: Record<PlaceId, Point> = { ...memory.nodes };
	const regions: Record<string, Rect> = { ...(memory.regions ?? {}) };
	// drop remembered coords for nodes that no longer exist? No — atlas
	// placement survives reload and re-observation; stale entries are
	// harmless and keep a returning chamber where it stood.

	const claim = (id: PlaceId, p: Point) => {
		if (!coords[id]) coords[id] = p;
		return coords[id];
	};

	// HOME and its fixtures
	if (topo.nodes[topo.homeId]) {
		const home = claim(topo.homeId, HOME_POS);
		for (const node of childrenOf(topo, topo.homeId)) {
			const off = HOME_FIXTURE_OFFSETS[node.label] ?? { x: -2, y: 6 };
			claim(node.id, { x: home.x + off.x, y: home.y + off.y });
		}
	}

	// island origins: stable slots in first-seen order; a new island claims
	// the next free slot south of everything already placed
	for (const rootId of topo.islandRoots) {
		if (coords[rootId]) continue;
		const taken = topo.islandRoots.filter((r) => coords[r]).map((r) => coords[r].y);
		const y = taken.length === 0 ? 0 : Math.max(...taken) + ISLAND_DY;
		claim(rootId, { x: 0, y });
	}

	const districts: Record<string, Rect> = {};

	for (const rootId of topo.islandRoots) {
		const root = coords[rootId];
		if (!root) continue;

		for (const name of ['terrain', 'camp', 'labour', 'forge'] as DistrictName[]) {
			districts[`${rootId}#${name}`] = districtOf(name, root);
		}

		// Pass 3 · lay out locally. One shelf per island holds the terrain
		// district's occupancy: every already-remembered node re-occupies its
		// own extent first, so a new chamber packs against the remembered
		// board rather than against a board that forgot it.
		const shelf = new Shelf();
		const islandNodes = Object.values(topo.nodes).filter(
			(n) => n.repoId === topo.nodes[rootId].repoId
		);
		for (const node of islandNodes) {
			const p = coords[node.id];
			if (p) shelf.occupy(p.x, p.y, extentUnits(node));
		}
		if (!islandNodes.some((n) => n.id === rootId))
			shelf.occupy(root.x, root.y, extentUnits(topo.nodes[rootId]));

		// camps on the west shore, stable lanes
		const camps = childrenOf(topo, rootId).filter((n) => n.kind === 'camp');
		for (const campNode of camps) {
			if (!coords[campNode.id]) {
				// south-only: a camp's stations reach CAMP_TOP rows north of it,
				// and north of that is the labour band's ground. The old
				// alternation put the second camp five rows north — inside the
				// terminal's rectangle, which is the collision this pass exists
				// to make impossible rather than unlikely.
				const lane = nextFreeLane(
					camps.map((c) => coords[c.id]).filter(Boolean),
					root.y,
					CAMP_LANE_STEP
				);
				coords[campNode.id] = { x: root.x + CAMP_DX, y: root.y + lane };
			}
			const camp = coords[campNode.id];
			for (const st of childrenOf(topo, campNode.id)) {
				const off = STATION_OFFSETS[stationSuffix(st.id)] ?? { x: 0, y: 2 };
				claim(st.id, { x: camp.x + off.x, y: camp.y + off.y });
			}
		}

		// the forge dock on the outward shore
		claim(`${rootId}#forge-dock`, { x: root.x + FORGE_OFFSET.x, y: root.y + FORGE_OFFSET.y });

		// The tree, in DFS pre-order — the tree(1) walk. Depth is a constant
		// indent and the *row* is what separates a child from its parent, so a
		// pre-order walk claiming rows downward reproduces `tree`'s shape
		// exactly: a subtree occupies a contiguous block, and a sibling that
		// comes after it starts below it.
		//
		// Append-only is what this costs. A directory observed later cannot be
		// inserted into the middle of its parent's block without moving every
		// row beneath it, which is the reflow atlas memory forbids — so it
		// packs into the nearest free row instead. The first observation of a
		// tree draws it as `tree` would; the hundredth draws a tree that grew.
		const byParent = new Map<PlaceId, PlaceNode[]>();
		for (const n of islandNodes) {
			if (n.kind !== 'directory' && n.kind !== 'file') continue;
			const key = n.parentId ?? rootId;
			(byParent.get(key) ?? byParent.set(key, []).get(key)!).push(n);
		}
		const walk = (parentId: PlaceId) => {
			const parent = coords[parentId];
			if (!parent) return;
			for (const node of byParent.get(parentId) ?? []) {
				if (!coords[node.id]) {
					coords[node.id] = shelf.claim(parent.x + DEPTH_DX, extentUnits(node), {
						preferY: parent.y + 1,
						direction: 'south',
						minY: root.y + TERRAIN_TOP
					});
				}
				walk(node.id);
			}
		};
		walk(rootId);

		// rigs and island-scoped fixtures hang off their owner at a fixed offset
		for (const node of islandNodes) {
			const parent = node.parentId ? coords[node.parentId] : undefined;
			if (!parent) continue;
			if (node.kind === 'test-rig' && !coords[node.id]) {
				coords[node.id] = { x: parent.x + RIG_OFFSET.x, y: parent.y + RIG_OFFSET.y };
			}
			if (node.kind === 'home-fixture' && !coords[node.id]) {
				const off = HOME_FIXTURE_OFFSETS[node.label] ?? { x: -3, y: -6 };
				coords[node.id] = { x: parent.x + off.x, y: parent.y + off.y };
			}
		}

		// Pass 3b · the instruments. A rectangle, allocated in the requesting
		// district's own band, from a shelf of its own — the labour band is
		// north of everything terrain may ever claim, so this allocation and
		// the tree are disjoint by construction rather than by paint order.
		const labourShelf = new Shelf();
		for (const rect of Object.values(regions)) {
			labourShelf.occupy(rect.x, rect.y, rect.w, rect.h);
		}
		for (const req of requests) {
			if (regions[req.id]) continue;
			if (req.district !== 'labour') continue;
			const x = req.preferX ?? root.x + CAMP_DX;
			// the window stands one row of air above the labour floor and
			// grows north from there, so it reads as *over* the camp the actor
			// walks into rather than as a panel pinned somewhere.
			const top = req.preferY ?? root.y + LABOUR_FLOOR - req.h + 1;
			const slot = labourShelf.claim(x, req.w, {
				preferY: top,
				h: req.h,
				direction: 'north',
				maxY: root.y + LABOUR_FLOOR
			});
			regions[req.id] = { x: slot.x, y: slot.y, w: req.w, h: req.h };
		}
	}

	// any node still unplaced (defensive): pin beside home so it exists
	for (const id of Object.keys(topo.nodes)) {
		if (!coords[id]) coords[id] = { x: HOME_POS.x, y: HOME_POS.y + 8 };
	}

	// Edge polylines. One turn, and **which** turn is a district question, not
	// a style one: the default (vertical at the origin's x, then along the
	// target's row) is right for the tree, where the vertical run *is* the
	// parent's trunk. It is wrong for an edge leaving terrain — the shore
	// rail from the root to the forge dock ran four rows straight down the
	// tree's own trunk column before turning west, which is the same
	// cross-district claim the terminal's box was, one layer down: placement
	// was allocated, routing was not.
	//
	// So an edge whose ends sit in different districts turns at its *source's*
	// row instead, leaving the source's district immediately and travelling
	// the rest of the way through the destination's own column.
	//
	// No `kind !== 'tree'` guard, deliberately. It was here and it is
	// redundant — a tree edge joins a parent and a child that are both in
	// terrain, so the predicate is already false for it — and a mutation test
	// proved nothing could tell the two versions apart. A guard the run
	// cannot be proven wrong about is a claim, not a check.
	const edgeRoutes: Record<string, Point[]> = {};
	for (const e of topo.edges) {
		const a = coords[e.from];
		const b = coords[e.to];
		if (!a || !b) continue;
		let pts: Point[];
		if (a.y === b.y || a.x === b.x) pts = [a, b];
		else if (leavesTerrain(topo, coords, e.from, a, b)) pts = [a, { x: b.x, y: a.y }, b];
		else pts = [a, { x: a.x, y: b.y }, b];
		edgeRoutes[`${e.from}->${e.to}`] = pts;
	}

	// bounds over placed nodes actually present in this topology, plus any
	// allocated instrument — a window off the top of the world would be a
	// bound that lies about what the camera can reach.
	let minX = Infinity,
		minY = Infinity,
		maxX = -Infinity,
		maxY = -Infinity;
	for (const id of Object.keys(topo.nodes)) {
		const p = coords[id];
		minX = Math.min(minX, p.x);
		minY = Math.min(minY, p.y);
		maxX = Math.max(maxX, p.x);
		maxY = Math.max(maxY, p.y);
	}
	for (const r of Object.values(regions)) {
		minX = Math.min(minX, r.x);
		minY = Math.min(minY, r.y);
		maxX = Math.max(maxX, r.x + r.w);
		maxY = Math.max(maxY, r.y + r.h);
	}
	if (!Number.isFinite(minX)) {
		minX = minY = maxX = maxY = 0;
	}

	return {
		layout: {
			nodes: Object.fromEntries(Object.keys(topo.nodes).map((id) => [id, coords[id]])),
			edgeRoutes,
			regions,
			districts,
			worldBounds: { minX: minX - 2, minY: minY - 2, maxX: maxX + 2, maxY: maxY + 2 }
		},
		memory: { nodes: coords, regions }
	};
}

/** The terminal's spatial request — its rendered box converted to world units
 *  and handed to the labour district. The camera used to do this conversion
 *  itself, at paint time, against a node's point; that is the seam this
 *  closes. */
export function terminalRequest(
	runId: string,
	cols: number,
	rows: number,
	charsPerUnit = NOMINAL_CHARS_PER_UNIT
): SpatialRequest {
	return {
		id: `labour:terminal:${runId}`,
		owner: runId,
		district: 'labour',
		lifetime: 'ephemeral',
		w: Math.ceil(cols / charsPerUnit),
		h: rows
	};
}

function childrenOf(topo: RoomTopology, parentId: PlaceId): PlaceNode[] {
	return Object.values(topo.nodes).filter((n) => n.parentId === parentId);
}

function stationSuffix(id: PlaceId): string {
	const i = id.lastIndexOf('#');
	return i >= 0 ? id.slice(i + 1) : '';
}

/** The nearest free lane around `originY`: walk the alternation until a
 *  lane no sibling occupies. Occupancy derives from already-assigned
 *  sibling coordinates, so it is reload-stable via the atlas memory. */
function nextFreeLane(placedSiblings: Point[], originY: number, step: number): number {
	const taken = new Set(placedSiblings.map((p) => p.y - originY));
	for (let i = 0; i < 64; i++) {
		const lane = i * step;
		if (!taken.has(lane)) return lane;
	}
	return 64 * step;
}

/** True when this edge starts inside a terrain district and ends outside it.
 *  Deliberately asked of the *source's own island origin* rather than of the
 *  world: districts are island-relative, and a second island's terrain is not
 *  this one's. */
function leavesTerrain(
	topo: RoomTopology,
	coords: Record<PlaceId, Point>,
	fromId: PlaceId,
	a: Point,
	b: Point
): boolean {
	const repoId = topo.nodes[fromId]?.repoId;
	const rootId = topo.islandRoots.find((r) => topo.nodes[r]?.repoId === repoId);
	const origin = rootId ? coords[rootId] : undefined;
	if (!origin) return false;
	return inDistrict('terrain', origin, a) && !inDistrict('terrain', origin, b);
}
