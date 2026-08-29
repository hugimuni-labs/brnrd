// RoomTopology — stable places and adjacency, compiled from the RoomGraph
// (#1652 slice 1). The layer between semantic facts and geometry:
//
//     RoomGraph      semantic facts (roomGraph.ts — unchanged)
//        ↓
//     RoomTopology   stable places + adjacency          ← this module
//        ↓
//     RoomLayout     logical coordinates + routes        (roomLayout.ts)
//        ↓
//     cameras        presentation only                   (asciiCamera.ts …)
//
// Rules this module owns (from the issue's spec):
//   - observed filesystem paths compile into a real shared-prefix trie;
//     intermediate prefixes are structural places, NOT claimed boundaries;
//   - ids are stable: repo identity + normalized path prefix — the same
//     observation always mints the same id;
//   - camps attach to their island at stable anchors with their own control
//     stations; multiple camps share one island without duplicating it;
//   - actors resolve to place ids; routes run through the graph (tree
//     corridors via the lowest common ancestor falls out of BFS on tree
//     edges);
//   - a node existing never implies the actor acted there — first-touch
//     facts stay on the RoomGraph's chambers, not on structural nodes.

import type { Place, RoomActor, RoomCamp, RoomGraph } from './roomGraph.ts';

export type PlaceId = string;

export type PlaceNodeKind =
	| 'repo-root'
	| 'directory'
	| 'file'
	| 'camp'
	| 'portal-rack'
	| 'chart-table'
	| 'strand-bay'
	| 'test-rig'
	| 'watch-perch'
	| 'forge-dock'
	| 'wake-dock'
	| 'cut-loom'
	| 'work-bench'
	| 'home-fixture';

export interface PlaceNode {
	id: PlaceId;
	kind: PlaceNodeKind;
	/** Short display label — a path *segment* for directories, never the
	 *  full path (the id carries that). */
	label: string;
	repoId?: string;
	campId?: PlaceId;
	parentId?: PlaceId;
	/** Directory depth from the island root (repo-root = 0). Only set on
	 *  tree nodes; the layout walks it east. */
	depth?: number;
}

export type PlaceEdgeKind = 'tree' | 'branch' | 'control' | 'shore' | 'sea-lane';

export interface PlaceEdge {
	from: PlaceId;
	to: PlaceId;
	kind: PlaceEdgeKind;
}

export interface RoomTopology {
	nodes: Record<PlaceId, PlaceNode>;
	edges: PlaceEdge[];
	/** Where each live actor stands, by run id. Every value is a real node. */
	actorPlaces: Record<string, PlaceId>;
	/** Islands in first-seen (graph) order — the layout allocates island
	 *  slots in this order, so it must be stable. */
	islandRoots: PlaceId[];
	homeId: PlaceId;
}

// ── ids ─────────────────────────────────────────────────────────────────────

export const HOME_ID: PlaceId = 'home:';

export function islandRootId(repoLabel: string): PlaceId {
	return `repo:${repoLabel}`;
}

/** Normalize an observed relative dir into its trie segments. */
export function pathSegments(dir: string): string[] {
	return dir
		.replace(/\\/g, '/')
		.split('/')
		.filter((s) => s.length > 0 && s !== '.');
}

export function dirId(repoLabel: string, segments: string[]): PlaceId {
	return segments.length === 0
		? islandRootId(repoLabel)
		: `${islandRootId(repoLabel)}/${segments.join('/')}`;
}

export function campId(repoLabel: string, camp: Pick<RoomCamp, 'branch' | 'dir'>): PlaceId {
	return `camp:${repoLabel}::${camp.branch ?? ''}::${camp.dir ?? ''}`;
}

function fileId(ownerDirId: PlaceId, name: string): PlaceId {
	return `${ownerDirId}#file:${name}`;
}

function rigId(anchorId: PlaceId): PlaceId {
	return `${anchorId}#rig`;
}

// ── compile ─────────────────────────────────────────────────────────────────

interface Builder {
	nodes: Record<PlaceId, PlaceNode>;
	edges: PlaceEdge[];
	edgeSeen: Set<string>;
}

function addNode(b: Builder, node: PlaceNode): PlaceNode {
	const existing = b.nodes[node.id];
	if (existing) return existing;
	b.nodes[node.id] = node;
	return node;
}

function addEdge(b: Builder, from: PlaceId, to: PlaceId, kind: PlaceEdgeKind) {
	const key = `${from}|${to}|${kind}`;
	if (b.edgeSeen.has(key)) return;
	b.edgeSeen.add(key);
	b.edges.push({ from, to, kind });
}

/** Ensure the trie path for `dir` exists under its island; returns the
 *  terminal directory node's id. Rule 3: intermediate prefixes are
 *  structural, derived from the attested path — never extra boundaries. */
/**
 * The chambers one camp has walked, as a trie rooted at **that camp**.
 *
 * Keyed by the camp, not by the repo — 2026-08-29, from his own screenshot:
 * terrain was already built per camp (`for (const chamber of camp.chambers)`)
 * but every chamber minted its node with `dirId(island.label, segs)`, so two
 * runs walking `src/` addressed *the same node*. The trie was island-scoped
 * while the walk that filled it was camp-scoped, and the strand's tree and
 * the resident's were one tree — not a collision, a shared key.
 *
 * The consequence, which is a real semantic choice and not a side effect:
 * **two runs in one directory draw two chambers.** That is right, because
 * this tree is a *trail* — where this run has been — and not a filesystem
 * listing. A run that has never opened `src/` has no `src/`.
 */
function ensureDirPath(b: Builder, repoLabel: string, campKey: PlaceId, dir: string): PlaceId {
	const segs = pathSegments(dir);
	let parent = campKey;
	for (let i = 0; i < segs.length; i++) {
		const id = dirId(campKey, segs.slice(0, i + 1));
		addNode(b, {
			id,
			kind: 'directory',
			label: segs[i],
			repoId: repoLabel,
			campId: campKey,
			parentId: parent,
			depth: i + 1
		});
		addEdge(b, parent, id, 'tree');
		parent = id;
	}
	return parent;
}

const CAMP_STATIONS: { kind: PlaceNodeKind; suffix: string; label: string }[] = [
	{ kind: 'portal-rack', suffix: 'portal-rack', label: 'portal' },
	{ kind: 'chart-table', suffix: 'chart-table', label: 'chart' },
	{ kind: 'strand-bay', suffix: 'strand-bay', label: 'bay' },
	{ kind: 'watch-perch', suffix: 'watch-perch', label: 'watch' },
	{ kind: 'wake-dock', suffix: 'wake-dock', label: 'wake' },
	{ kind: 'cut-loom', suffix: 'cut-loom', label: 'cut' },
	// the bench (his steer, 2026-08-28): the shell place — where work that
	// names no legible resource happens in plain sight, instead of the
	// actor dissolving into the camp marker
	{ kind: 'work-bench', suffix: 'work-bench', label: 'bench' }
];

function stationId(camp: PlaceId, suffix: string): PlaceId {
	return `${camp}#${suffix}`;
}

/**
 * Compile the stable place graph from one RoomGraph snapshot.
 *
 * Pure and total: every actor resolves to a node that exists; unknown
 * shapes fall back to the actor's camp (an actor always exists *somewhere*
 * that is real), never to a fabricated place.
 */
export function compileTopology(graph: RoomGraph): RoomTopology {
	const b: Builder = { nodes: {}, edges: [], edgeSeen: new Set() };

	// HOME — the account fixture; account-wide instruments live here, not
	// as peer blocks beside repository terrain.
	addNode(b, { id: HOME_ID, kind: 'home-fixture', label: 'HOME' });
	for (const [suffix, label] of [
		['gate', 'gate'],
		['watch', 'watch'],
		['clockwork', 'clockwork'],
		['garage', 'garage']
	]) {
		const id = `${HOME_ID}#${suffix}`;
		addNode(b, { id, kind: 'home-fixture', label, parentId: HOME_ID });
		addEdge(b, HOME_ID, id, 'control');
	}

	const islandRoots: PlaceId[] = [];
	const campsByKey = new Map<PlaceId, RoomCamp>();

	for (const island of graph.islands) {
		const rootId = islandRootId(island.label);
		islandRoots.push(rootId);
		addNode(b, {
			id: rootId,
			kind: 'repo-root',
			label: island.label,
			repoId: island.label,
			depth: 0
		});
		addEdge(b, HOME_ID, rootId, 'sea-lane');

		// the forge: a dock on the island's outward shore, one per island
		const forge = `${rootId}#forge-dock`;
		addNode(b, {
			id: forge,
			kind: 'forge-dock',
			label: 'FORGE',
			repoId: island.label,
			parentId: rootId
		});
		addEdge(b, rootId, forge, 'shore');

		for (const camp of island.camps) {
			const cid = campId(island.label, camp);
			campsByKey.set(cid, camp);
			addNode(b, {
				id: cid,
				kind: 'camp',
				label: camp.branch ?? camp.dir ?? '(camp)',
				repoId: island.label,
				parentId: rootId
			});
			addEdge(b, cid, rootId, 'branch'); // the branch spur
			for (const st of CAMP_STATIONS) {
				const sid = stationId(cid, st.suffix);
				addNode(b, {
					id: sid,
					kind: st.kind,
					label: st.label,
					repoId: island.label,
					campId: cid,
					parentId: cid
				});
				addEdge(b, cid, sid, 'control');
			}
			// terrain: the observed chambers of this camp, as a shared-prefix trie
			for (const chamber of camp.chambers) {
				const leafDir = ensureDirPath(b, island.label, cid, chamber.dir);
				// Every attested file, then the one the hand is on. Deduped by
				// id, so a file that is both git-attested and the current
				// `lastFile` mints one leaf and not two — the same node either
				// way, which is what `fileId` being a pure function of
				// (dir, name) buys.
				for (const name of [...chamber.files, chamber.lastFile]) {
					if (!name) continue;
					const fid = fileId(leafDir, name);
					addNode(b, {
						id: fid,
						kind: 'file',
						label: name,
						repoId: island.label,
						parentId: leafDir
					});
					addEdge(b, leafDir, fid, 'tree');
				}
			}
		}
	}

	// ── actor place resolution ──────────────────────────────────────────────
	const actorPlaces: Record<string, PlaceId> = {};
	for (const actor of graph.actors) {
		actorPlaces[actor.runId] = resolveActorPlace(b, graph, actor);
	}

	// ── fold the scaffolding ────────────────────────────────────────────────
	// After actors, deliberately: an actor standing in a pass-through
	// directory pins it, and folding first would drop the node its own
	// `dirId` lookup resolves to, silently demoting that actor to its camp.
	foldPassThroughDirs(b, new Set(Object.values(actorPlaces)));

	return {
		nodes: b.nodes,
		edges: b.edges,
		actorPlaces,
		islandRoots,
		homeId: HOME_ID
	};
}

/**
 * Radix-compress the directory trie: a chamber is a place, scaffolding is not.
 *
 * **The measurement** (maintainer, 2026-08-29, on the map of a run that had
 * just attested ten paths): *"the fact that the tree grows only left probably
 * points out at a poor design choice ... I think we could had a better dynamic
 * compact rendering of any 'path', not like too flat, as it currently is."*
 *
 * He is reading a real cost. Every path segment used to mint its own node, and
 * every node charges full width — `depthAdvance()` bills for its label plus a
 * corridor. So `src/frontend/src/lib` was four eastward hops to name one
 * place, and the trie read as a single long line rather than a shape. On those
 * ten paths: **nine directory nodes to express five chambers**, four of them
 * holding nothing at all.
 *
 * The rule is one line: **a directory is a place when it holds something, or
 * when it branches.** Anything else is punctuation, and it folds into its only
 * child — `brr` + `gates` becomes `brr/gates`, one node with one label.
 *
 * Note what is deliberately *kept*. A directory with two or more directory
 * children is a fork in the terrain, and a fork is exactly the structure a map
 * is for; collapsing those too would give a flat list of full paths, which is
 * legible and is not a place. Folding chains makes the trie branch *more*
 * visibly, not less.
 *
 * **The surviving node is the child, not the parent**, so `dirId()` keeps
 * addressing the chamber a caller actually names — `dirFromEdge`, actor
 * resolution and the atlas's persisted coordinates all key on that id, and a
 * fold that renamed the deep end would quietly move every one of them.
 */
function foldPassThroughDirs(b: Builder, pinned: Set<PlaceId>) {
	const childrenOf = (): Map<PlaceId, PlaceNode[]> => {
		const out = new Map<PlaceId, PlaceNode[]>();
		for (const node of Object.values(b.nodes)) {
			if (!node.parentId) continue;
			const list = out.get(node.parentId);
			if (list) list.push(node);
			else out.set(node.parentId, [node]);
		}
		return out;
	};

	// Loop to a fixpoint: folding `brr` into `gates` can leave `src` with one
	// child where it had two, and that node is now foldable in its turn.
	// Bounded by the node count — every pass removes at least one node or
	// stops.
	for (let pass = 0; pass < Object.keys(b.nodes).length; pass++) {
		const children = childrenOf();
		const victim = Object.values(b.nodes).find((node) => {
			if (node.kind !== 'directory' || pinned.has(node.id)) return false;
			if (!node.parentId) return false;
			const kids = children.get(node.id) ?? [];
			// One child, and it is terrain rather than a file or a rig: a
			// directory holding *anything* of its own is a place and stays.
			return kids.length === 1 && kids[0].kind === 'directory';
		});
		if (!victim) return;

		const heir = (children.get(victim.id) ?? [])[0];
		heir.label = `${victim.label}/${heir.label}`;
		heir.parentId = victim.parentId;
		delete b.nodes[victim.id];
		b.edges = b.edges.filter((e) => e.from !== victim.id && e.to !== victim.id);
		b.edgeSeen.clear();
		for (const e of b.edges) b.edgeSeen.add(`${e.from}|${e.to}|${e.kind}`);
		addEdge(b, victim.parentId as PlaceId, heir.id, 'tree');
	}
}

/** The camp an actor belongs to, by its glyph on the island's camp rosters. */
function actorCampId(graph: RoomGraph, actor: RoomActor): PlaceId | null {
	const island = graph.islands.find((i) => i.label === actor.islandLabel);
	if (!island) return null;
	const camp = island.camps.find((c) => c.actorGlyphs.includes(actor.glyph));
	return camp ? campId(island.label, camp) : null;
}

function resolveActorPlace(b: Builder, graph: RoomGraph, actor: RoomActor): PlaceId {
	const camp = actorCampId(graph, actor);
	const root = islandRootId(actor.islandLabel);
	const fallback = camp ?? (b.nodes[root] ? root : HOME_ID);
	const place: Place = actor.place;
	const station = (suffix: string): PlaceId => {
		if (camp) {
			const sid = stationId(camp, suffix);
			if (b.nodes[sid]) return sid;
		}
		return fallback;
	};
	switch (place.kind) {
		case 'chamber': {
			if (!place.label || place.label === 'the library') {
				// no legible resource ⇒ the camp's work-bench: uncategorized
				// shell work happens somewhere real, in plain sight
				if (!place.label) return station('work-bench');
				if (place.label === 'the library') {
					// the kb read-room: one per island, attached to the root
					const lib = `${root}#library`;
					if (b.nodes[root]) {
						addNode(b, {
							id: lib,
							kind: 'home-fixture',
							label: 'library',
							repoId: actor.islandLabel,
							parentId: root
						});
						addEdge(b, root, lib, 'control');
						return lib;
					}
				}
				return fallback;
			}
			// the actor's *own* camp's chamber — with a per-camp trie, another
			// run's identical path is a different node and standing there
			// would put this actor in someone else's trail
			const id = camp ? dirId(camp, pathSegments(place.label)) : null;
			return id && b.nodes[id] ? id : fallback;
		}
		case 'test-rig': {
			// the rig attaches to the directory actually probed when that
			// chamber exists; otherwise it is the camp's own rig
			const probed = place.label && camp ? dirId(camp, pathSegments(place.label)) : null;
			const anchorDir = probed && b.nodes[probed] ? probed : camp;
			if (!anchorDir) return fallback;
			const rid = rigId(anchorDir);
			addNode(b, {
				id: rid,
				kind: 'test-rig',
				label: 'rig',
				repoId: actor.islandLabel,
				campId: camp ?? undefined,
				parentId: anchorDir
			});
			addEdge(b, anchorDir, rid, 'control');
			return rid;
		}
		case 'forge-dock': {
			const forge = `${root}#forge-dock`;
			return b.nodes[forge] ? forge : fallback;
		}
		case 'correspondence-desk':
			return station('portal-rack');
		case 'chart-table':
			return station('chart-table');
		case 'strand-bay':
			return station('strand-bay');
		case 'watch-point':
			return station('watch-perch');
		case 'wake-dock':
			return station('wake-dock');
		case 'cut-line':
			return station('cut-loom');
	}
}

// ── routing ─────────────────────────────────────────────────────────────────

/**
 * Shortest place route from `from` to `to`, inclusive of both ends, BFS over
 * the undirected place graph. Tree corridors route through the lowest common
 * ancestor by construction (tree edges are the only tree-internal paths).
 * Returns null when either end is unknown or unreachable.
 */
export function routeBetween(topo: RoomTopology, from: PlaceId, to: PlaceId): PlaceId[] | null {
	if (!topo.nodes[from] || !topo.nodes[to]) return null;
	if (from === to) return [from];
	const adj = new Map<PlaceId, PlaceId[]>();
	for (const e of topo.edges) {
		(adj.get(e.from) ?? adj.set(e.from, []).get(e.from)!).push(e.to);
		(adj.get(e.to) ?? adj.set(e.to, []).get(e.to)!).push(e.from);
	}
	const prev = new Map<PlaceId, PlaceId>();
	const queue: PlaceId[] = [from];
	prev.set(from, from);
	while (queue.length > 0) {
		const cur = queue.shift()!;
		if (cur === to) break;
		for (const next of adj.get(cur) ?? []) {
			if (prev.has(next)) continue;
			prev.set(next, cur);
			queue.push(next);
		}
	}
	if (!prev.has(to)) return null;
	const route: PlaceId[] = [to];
	let cur = to;
	while (cur !== from) {
		cur = prev.get(cur)!;
		route.push(cur);
	}
	return route.reverse();
}
