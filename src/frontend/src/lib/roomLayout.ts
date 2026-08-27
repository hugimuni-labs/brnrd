// RoomLayout — deterministic, incremental logical coordinates for the place
// graph (#1652 slice 1). Coordinates are logical world units, not pixels or
// character cells: the ASCII camera rasterizes one unit as a character-cell
// multiple; an SVG camera may transform the same units.
//
// The invariants (spec: "the exact spacing constants may be tuned; these
// invariants may not"):
//   - repository root is local (0, 0) in island space;
//   - directory depth advances east by a fixed spacing;
//   - the first child may continue its parent's lane; additional children
//     claim the nearest free lane via the stable alternation 0,-4,+4,-8,+8…;
//   - once assigned, a node's coordinate never changes (atlas memory);
//   - file leaves occupy a small stable offset from their directory;
//   - adding a chamber may expand world bounds but must not move an
//     existing chamber;
//   - two renderers given the same topology/layout snapshot receive the
//     same coordinates.

import type { PlaceId, PlaceNode, RoomTopology } from './roomTopology.ts';

export interface Point {
	x: number;
	y: number;
}

export interface RoomLayout {
	nodes: Record<PlaceId, Point>;
	/** Polyline per edge, keyed `${from}->${to}` — vertical run at the
	 *  parent's x, then east along the child's lane. */
	edgeRoutes: Record<string, Point[]>;
	worldBounds: { minX: number; minY: number; maxX: number; maxY: number };
}

/** The persisted half: coordinates already assigned. The caller owns
 *  persistence (localStorage today, a server-side atlas later); layoutRoom
 *  never mutates the object it was given. */
export interface AtlasMemory {
	nodes: Record<PlaceId, Point>;
}

export function emptyAtlas(): AtlasMemory {
	return { nodes: {} };
}

// ── spacing constants (tunable) ─────────────────────────────────────────────

const DEPTH_DX = 11; // east per directory depth
const LANE_STEP = 4; // lane alternation unit: 0, -4, +4, -8, +8 …
const ISLAND_DY = 44; // vertical distance between island origins
const HOME_POS: Point = { x: -26, y: 0 };
const CAMP_DX = -9; // camps sit west of the root, home-facing shore
const CAMP_LANE_STEP = 5;
const FORGE_OFFSET: Point = { x: -16, y: 5 }; // the outward shore dock
const FILE_DX = 4; // file leaves: small stable offset east of their dir
const FILE_DY0 = 2; // first file slot below the dir, then +1 per file
const STATION_OFFSETS: Record<string, Point> = {
	'portal-rack': { x: -4, y: -2 },
	'chart-table': { x: 0, y: -3 },
	'strand-bay': { x: 4, y: -2 },
	'watch-perch': { x: -4, y: 1 },
	'wake-dock': { x: 4, y: 1 },
	'cut-loom': { x: 0, y: 3 }
};
const HOME_FIXTURE_OFFSETS: Record<string, Point> = {
	gate: { x: 0, y: -4 },
	watch: { x: -4, y: -2 },
	clockwork: { x: -4, y: 2 },
	garage: { x: 0, y: 4 },
	library: { x: -3, y: -6 }
};
const RIG_OFFSET: Point = { x: 2, y: 2 }; // off the lane grid (lanes are ×4)

/** The stable alternation the spec names: 0, -1, +1, -2, +2 … (scaled by a
 *  step). Index → offset. */
function laneOffset(i: number, step: number): number {
	if (i === 0) return 0;
	const k = Math.ceil(i / 2);
	return (i % 2 === 1 ? -k : k) * step;
}

// ── the allocator ───────────────────────────────────────────────────────────

/**
 * Assign coordinates to every node the memory does not already hold, in a
 * deterministic order (islands in topology order; nodes in each island by
 * id, parents before children by construction of the walk). Returns the
 * layout plus the extended memory; nothing already in the memory moves.
 */
export function layoutRoom(
	topo: RoomTopology,
	memory: AtlasMemory = emptyAtlas()
): { layout: RoomLayout; memory: AtlasMemory } {
	const coords: Record<PlaceId, Point> = { ...memory.nodes };
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

	for (const rootId of topo.islandRoots) {
		const root = coords[rootId];
		if (!root) continue;

		// camps on the west shore, stable lanes
		const camps = childrenOf(topo, rootId).filter((n) => n.kind === 'camp');
		for (const campNode of camps) {
			if (!coords[campNode.id]) {
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

		// the tree: walk directories in depth order so parents are always
		// placed first; within one parent, first-observed child first
		// (object insertion order in topo.nodes is observation order).
		const dirs = Object.values(topo.nodes)
			.filter((n) => n.kind === 'directory' && n.repoId === topo.nodes[rootId].repoId)
			.sort((a, b) => (a.depth ?? 0) - (b.depth ?? 0));
		for (const dir of dirs) {
			if (coords[dir.id]) continue;
			const parent = coords[dir.parentId ?? rootId];
			if (!parent) continue;
			const siblings = childrenOf(topo, dir.parentId ?? rootId).filter(
				(n) => n.kind === 'directory'
			);
			const lane = nextFreeLane(
				siblings.map((s) => coords[s.id]).filter(Boolean),
				parent.y,
				LANE_STEP
			);
			coords[dir.id] = { x: parent.x + DEPTH_DX, y: parent.y + lane };
		}

		// file leaves + rigs: stable offsets from their owning node
		for (const node of Object.values(topo.nodes)) {
			if (node.repoId !== topo.nodes[rootId].repoId) continue;
			const parent = node.parentId ? coords[node.parentId] : undefined;
			if (!parent) continue;
			if (node.kind === 'file' && !coords[node.id]) {
				const slots = childrenOf(topo, node.parentId!)
					.filter((n) => n.kind === 'file')
					.map((n) => coords[n.id])
					.filter(Boolean);
				coords[node.id] = { x: parent.x + FILE_DX, y: parent.y + FILE_DY0 + slots.length };
			}
			if (node.kind === 'test-rig' && !coords[node.id]) {
				coords[node.id] = { x: parent.x + RIG_OFFSET.x, y: parent.y + RIG_OFFSET.y };
			}
			if (node.kind === 'home-fixture' && !coords[node.id]) {
				// island-scoped fixtures (the library)
				const off = HOME_FIXTURE_OFFSETS[node.label] ?? { x: -3, y: -6 };
				coords[node.id] = { x: parent.x + off.x, y: parent.y + off.y };
			}
		}
	}

	// any node still unplaced (defensive): pin beside home so it exists
	for (const id of Object.keys(topo.nodes)) {
		if (!coords[id]) coords[id] = { x: HOME_POS.x, y: HOME_POS.y + 8 };
	}

	// edge polylines: vertical at the origin's x, then east on the target lane
	const edgeRoutes: Record<string, Point[]> = {};
	for (const e of topo.edges) {
		const a = coords[e.from];
		const b = coords[e.to];
		if (!a || !b) continue;
		const pts: Point[] = a.y === b.y || a.x === b.x ? [a, b] : [a, { x: a.x, y: b.y }, b];
		edgeRoutes[`${e.from}->${e.to}`] = pts;
	}

	// bounds over placed nodes actually present in this topology
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
	if (!Number.isFinite(minX)) {
		minX = minY = maxX = maxY = 0;
	}

	return {
		layout: {
			nodes: Object.fromEntries(Object.keys(topo.nodes).map((id) => [id, coords[id]])),
			edgeRoutes,
			worldBounds: { minX: minX - 2, minY: minY - 2, maxX: maxX + 2, maxY: maxY + 2 }
		},
		memory: { nodes: coords }
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
		const lane = laneOffset(i, step);
		if (!taken.has(lane)) return lane;
	}
	return laneOffset(64, step);
}
