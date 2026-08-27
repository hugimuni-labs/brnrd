// Room motion — transition receipts and the walk schedule (#1654 slice 3).
//
// Movement doctrine, unchanged from #1652: a change may animate only when
// canonical input attests it. This module turns two successive place
// resolutions into first-class `BoundaryTransition` receipts, and a receipt
// into a *walk schedule* — the display-time waypoints an actor traverses.
// The walk is presentation: one attested boundary produces one walk; no
// polling tick, elapsed-time update or decorative loop moves an actor, and
// no intermediate waypoint is ever a fabricated boundary event.

import type { PlaceId, RoomTopology } from './roomTopology.ts';
import type { Point, RoomLayout } from './roomLayout.ts';
import { routeBetween } from './roomTopology.ts';

/** One attested relocation: the place diff of one actor between two
 *  successive compiles, with the route it travels. */
export interface BoundaryTransition {
	actorRunId: string;
	fromPlaceId: PlaceId | null;
	toPlaceId: PlaceId;
	/** Inclusive place route, ≥2 entries when fromPlaceId is known and a
	 *  path exists; [toPlaceId] otherwise (the actor appears at its place —
	 *  a wake, or an unroutable hop — rather than walking a lie). */
	route: PlaceId[];
}

/**
 * Diff two place resolutions into transition receipts.
 *
 * - an actor at the same place produces nothing (a same-place boundary
 *   changes stance, not position);
 * - a new actor (no previous place) produces an appearance: route = [to];
 * - a vanished actor produces nothing — cut removes the body, the caller
 *   drops its walk.
 */
export function diffTransitions(
	prev: Record<string, PlaceId> | null,
	next: Record<string, PlaceId>,
	topo: RoomTopology
): BoundaryTransition[] {
	const out: BoundaryTransition[] = [];
	for (const [runId, to] of Object.entries(next)) {
		const from = prev?.[runId] ?? null;
		if (from === to) continue;
		const route = (from ? routeBetween(topo, from, to) : null) ?? [to];
		out.push({ actorRunId: runId, fromPlaceId: from, toPlaceId: to, route });
	}
	return out;
}

/** A walk in progress: the waypoint list in world units and a cursor the
 *  display advances. Pure data — the caller owns time. */
export interface Walk {
	actorRunId: string;
	toPlaceId: PlaceId;
	/** World-unit waypoints, densified so each step is one short hop. */
	points: Point[];
	/** Index into `points`; the display position is points[step]. */
	step: number;
}

/** Max world-units per display step — small enough to read as travel,
 *  large enough that a cross-island crossing stays a beat, not a cutscene. */
const STEP_UNITS = 3;
/** A route longer than this many hops snaps to its last stretch: the
 *  reader needs arrival and enough route to perceive motion, not a tour. */
const MAX_STEPS = 24;

/** Densify a place route into world-unit waypoints along the layout's own
 *  edge polylines, ending exactly at the destination node. */
export function walkFor(t: BoundaryTransition, layout: RoomLayout): Walk | null {
	const pts: Point[] = [];
	const push = (p: Point) => {
		const last = pts[pts.length - 1];
		if (!last || last.x !== p.x || last.y !== p.y) pts.push(p);
	};
	for (let i = 0; i + 1 < t.route.length; i++) {
		const key = `${t.route[i]}->${t.route[i + 1]}`;
		const rev = `${t.route[i + 1]}->${t.route[i]}`;
		const seg =
			layout.edgeRoutes[key] ??
			(layout.edgeRoutes[rev] ? [...layout.edgeRoutes[rev]].reverse() : null) ??
			// no drawn edge (e.g. a sea lane): straight line between the nodes
			[layout.nodes[t.route[i]], layout.nodes[t.route[i + 1]]].filter(Boolean);
		for (const p of seg) if (p) push(p);
	}
	if (pts.length === 0) {
		const dest = layout.nodes[t.toPlaceId];
		if (!dest) return null;
		return { actorRunId: t.actorRunId, toPlaceId: t.toPlaceId, points: [dest], step: 0 };
	}
	// densify long segments into ≤STEP_UNITS hops
	const dense: Point[] = [pts[0]];
	for (let i = 1; i < pts.length; i++) {
		const a = dense[dense.length - 1];
		const b = pts[i];
		const dist = Math.max(Math.abs(b.x - a.x), Math.abs(b.y - a.y));
		const hops = Math.max(1, Math.ceil(dist / STEP_UNITS));
		for (let h = 1; h <= hops; h++) {
			dense.push({
				x: Math.round(a.x + ((b.x - a.x) * h) / hops),
				y: Math.round(a.y + ((b.y - a.y) * h) / hops)
			});
		}
	}
	const clipped = dense.length > MAX_STEPS ? dense.slice(dense.length - MAX_STEPS) : dense;
	return { actorRunId: t.actorRunId, toPlaceId: t.toPlaceId, points: clipped, step: 0 };
}

/** Advance every walk one step; completed walks are dropped. Returns the
 *  surviving walks and the current display positions. */
export function advanceWalks(walks: Walk[]): {
	walks: Walk[];
	positions: Record<string, Point>;
} {
	const positions: Record<string, Point> = {};
	const alive: Walk[] = [];
	for (const w of walks) {
		const step = w.step + 1;
		if (step >= w.points.length - 1) continue; // arrived — the node renders it
		const next = { ...w, step };
		positions[w.actorRunId] = next.points[step];
		alive.push(next);
	}
	return { walks: alive, positions };
}

/** Current display positions without advancing (for paints between ticks). */
export function walkPositions(walks: Walk[]): Record<string, Point> {
	const positions: Record<string, Point> = {};
	for (const w of walks) positions[w.actorRunId] = w.points[w.step];
	return positions;
}

/** Ease a camera center toward its target: a fixed fraction per tick,
 *  snapping when within one unit so the board settles byte-stable. */
export function easeCamera(current: Point, target: Point, fraction = 0.3): Point {
	const dx = target.x - current.x;
	const dy = target.y - current.y;
	if (Math.abs(dx) <= 1 && Math.abs(dy) <= 1) return { x: target.x, y: target.y };
	return { x: current.x + dx * fraction, y: current.y + dy * fraction };
}
