// The axonometric room — pure geometry for the `/new` diorama.
//
// The maintainer's 2026-08-26 direction (design-resident-field.md is the
// semantic contract; this file is the *spatial* one): the live system drawn
// as a place, not a feed. One isometric platform is the daemon's floor; the
// resident is the tall machine on its plinth; strands are low blocks down a
// service lane; conduits run through the floor's real negative space; the
// portal gate stands on the back edge. Hard prohibitions inherited from the
// round's brief: no full-width cards, no stacked-paper depth, topology must
// survive with every label blurred.
//
// This module is deliberately markup-free: world-space layout in tile units,
// 2:1 isometric projection, extruded-box faces, Manhattan conduit routing,
// painter ordering. The Svelte route joins points into polygons and decides
// nothing spatial.

import type { FieldRoot } from './residentField.ts';
import { fieldRunKey } from './residentField.ts';
import type { LiveRun } from './liveRuns.ts';

/** Screen px of one tile's half-width at scale 1 — the single scale knob.
 *  iso() maps world tiles → px: x−y spans horizontally at TILE px per tile,
 *  x+y descends at TILE/2 (the classic 2:1 dimetric read), z rises at
 *  TILE·Z_RISE px per tile of height. */
export const TILE = 36;
const Z_RISE = 0.82;

export interface Pt {
	x: number;
	y: number;
}

/** World (tiles, z up) → screen px. Pure, origin at world (0,0,0). */
export function iso(x: number, y: number, z = 0): Pt {
	return {
		x: (x - y) * TILE,
		y: ((x + y) * TILE) / 2 - z * TILE * Z_RISE
	};
}

// ── the scene model ─────────────────────────────────────────────────────────

export type MachineKind = 'resident' | 'strand' | 'orphan';

export interface Machine {
	key: string;
	run: LiveRun;
	kind: MachineKind;
	/** Footprint's back corner (min x, min y), tiles. */
	x: number;
	y: number;
	/** Footprint size, tiles. */
	w: number;
	d: number;
	/** Height, z tiles. The resident towers; strands hug the floor. */
	h: number;
	/** Collapsed deeper descendants (`+N hands`) — rendered as crates
	 *  beside the block, never as more machines. */
	hands: number;
	/** Assembly-overture slot, body order (resident first, then limbs). */
	order: number;
}

export interface Conduit {
	/** The limb's run key (or `gate` for the portal feed). */
	key: string;
	/** Floor waypoints, world tiles — Manhattan, elbows included. */
	points: Pt[];
}

export interface Scene {
	/** Platform extent in tiles (0..cols, 0..rows). */
	cols: number;
	rows: number;
	machines: Machine[];
	conduits: Conduit[];
	/** Portal gate site: center of the gate on the back (y=0) edge. */
	gate: Pt;
	/** Gate → resident conduit (floor waypoints), empty when no resident. */
	gatePath: Pt[];
	/** Total overture slots (machines), for staggered assembly. */
	slots: number;
}

// Layout constants, tiles. The resident sits back-left; the service lane
// runs along +x in front of it, so strands march down-right on screen —
// the diorama reads back-to-front the way the work grew.
const RESIDENT = { x: 1.2, y: 1.4, w: 1.7, d: 1.7, h: 2.1 };
const ORPHAN_ROW_Y = RESIDENT.y + 0.2;
const LANE_Y = RESIDENT.y + 3.4;
const LANE_X0 = RESIDENT.x + 2.6;
const LANE_PITCH = 2.3;
const STRAND = { w: 1.15, d: 1.15 };
const GATE_X_MARGIN = 1.4;

/** Strand block height by runner class — a strong core is a bigger machine,
 *  read at silhouette level, no text needed. */
export function strandHeight(run: LiveRun): number {
	switch (run.runner?.class) {
		case 'strong':
			return 0.72;
		case 'economy':
			return 0.42;
		default:
			return 0.55;
	}
}

/**
 * Topology → the room. Layout rules:
 * - first non-orphan root is the resident on its plinth; its limbs take
 *   lane sites in body order (oldest first, nearest the resident).
 * - orphan roots (dispatcher gone) park along the back wall beside the
 *   resident, strand-sized: present, connected to nothing — which is the
 *   truth of them. Their limbs still lane up after the resident's.
 * - the platform grows with the lane; it never clips a machine.
 */
export function buildScene(field: FieldRoot[]): Scene {
	const machines: Machine[] = [];
	const conduits: Conduit[] = [];
	let order = 0;
	let laneSlot = 0;
	let orphanSlot = 0;

	const residentRoot = field.find((root) => !root.orphan) ?? null;

	for (const root of field) {
		const isResident = root === residentRoot;
		const rootKey = fieldRunKey(root.run);
		let rootMachine: Machine;
		if (isResident) {
			rootMachine = {
				key: rootKey,
				run: root.run,
				kind: 'resident',
				...RESIDENT,
				hands: 0,
				order: order++
			};
		} else {
			// An orphan root, or a second non-orphan root (rare: two resident
			// thoughts) — both park along the back wall, strand-sized.
			const x = LANE_X0 + LANE_PITCH * orphanSlot++;
			rootMachine = {
				key: rootKey,
				run: root.run,
				kind: root.orphan ? 'orphan' : 'strand',
				x,
				y: ORPHAN_ROW_Y,
				w: STRAND.w,
				d: STRAND.d,
				h: strandHeight(root.run),
				hands: 0,
				order: order++
			};
		}
		machines.push(rootMachine);

		for (const limb of root.limbs) {
			const slot = laneSlot++;
			const machine: Machine = {
				key: fieldRunKey(limb.run),
				run: limb.run,
				kind: 'strand',
				x: LANE_X0 + LANE_PITCH * slot,
				y: LANE_Y,
				w: STRAND.w,
				d: STRAND.d,
				h: strandHeight(limb.run),
				hands: limb.hands,
				order: order++
			};
			machines.push(machine);
			conduits.push({
				key: machine.key,
				points: conduitPath(rootMachine, machine, slot)
			});
		}
	}

	const maxX = machines.reduce(
		(acc, m) => Math.max(acc, m.x + m.w),
		LANE_X0 + LANE_PITCH // floor never smaller than one empty lane site
	);
	const cols = Math.ceil(maxX + GATE_X_MARGIN);
	// Front margin holds the lane's staggered floor labels on the plate.
	const rows = Math.ceil(LANE_Y + STRAND.d + 1.7);

	const gate: Pt = { x: cols - 1.1, y: 0 };
	const gatePath: Pt[] = residentRoot
		? gateConduit(gate, machines[0] /* resident is always first */)
		: [];

	return { cols, rows, machines, conduits, gate, gatePath, slots: order };
}

/** Resident front port → corridor → strand back port. Each lane slot rides
 *  its own corridor offset, so parallel conduits comb instead of piling
 *  onto one line — cables in the floor, not a bus bar. */
function conduitPath(root: Machine, strand: Machine, slot: number): Pt[] {
	const portX = root.x + root.w * 0.62;
	const portY = root.y + root.d; // front face, floor level
	const corridorY = root.y + root.d + 0.65 + slot * 0.28;
	const strandX = strand.x + strand.w / 2;
	const strandY = strand.y; // back face
	return [
		{ x: portX, y: portY },
		{ x: portX, y: corridorY },
		{ x: strandX, y: corridorY },
		{ x: strandX, y: strandY }
	];
}

/** Portal gate → resident back-right port, hugging the back wall. */
function gateConduit(gate: Pt, resident: Machine): Pt[] {
	const wallY = 0.55;
	const portX = resident.x + resident.w;
	const portY = resident.y + resident.d * 0.4;
	return [
		{ x: gate.x, y: gate.y + 0.1 },
		{ x: gate.x, y: wallY },
		{ x: portX + 0.55, y: wallY },
		{ x: portX + 0.55, y: portY },
		{ x: portX, y: portY }
	];
}

// ── extrusion ───────────────────────────────────────────────────────────────

export interface Faces {
	/** Top face polygon, screen px. */
	top: Pt[];
	/** Left (south-west, x = min) face. */
	left: Pt[];
	/** Right (south-east, y = max… the viewer-facing front) face. */
	right: Pt[];
	/** Top face's front corner — where a lamp or plaque anchors. */
	frontCorner: Pt;
	/** Screen anchor of the footprint's front corner at floor level. */
	floorFront: Pt;
}

/** An extruded box's three visible faces for the fixed camera. `z0` lifts
 *  the box's base off the floor — a head hovering over a torso is the same
 *  box, raised. */
export function boxFaces(x: number, y: number, w: number, d: number, h: number, z0 = 0): Faces {
	const a = iso(x, y, z0 + h); // back
	const b = iso(x + w, y, z0 + h); // right
	const c = iso(x + w, y + d, z0 + h); // front
	const e = iso(x, y + d, z0 + h); // left
	const bf = iso(x + w, y, z0);
	const ef = iso(x, y + d, z0);
	const cf = iso(x + w, y + d, z0);
	return {
		top: [a, b, c, e],
		left: [e, c, cf, ef],
		right: [b, c, cf, bf],
		frontCorner: c,
		floorFront: cf
	};
}

// ── the resident's anatomy ──────────────────────────────────────────────────
//
// The entity round (2026-08-26, the maintainer's steer): a building answers
// *where*; the resident is a *who*. The warehouse becomes a figure — a slim
// torso, a hovering head that wears the run's own mood face, an act-trail on
// the torso's gate-facing face (the windows return, but now every slit is a
// recorded boundary act), and a bench in front where the current command
// lies. Two studies share this skeleton: `automaton` (boxed head with a
// visor) and `glyph` (no head — the face itself, held in a halo ring).

export type ResidentBody = 'automaton' | 'glyph';

/** How many act slits the torso's face can carry — the trail beyond this
 *  scrolls off the bottom, oldest first. */
export const TRAIL_MAX = 6;

export interface Box {
	x: number;
	y: number;
	w: number;
	d: number;
	h: number;
	z0: number;
}

export interface ResidentAnatomy {
	torso: Box;
	/** Null in the `glyph` study — the face floats instead of wearing a box. */
	head: Box | null;
	bench: Box;
	/** Screen anchor for the face (visor center / halo center). */
	faceAnchor: Pt;
	/** Screen anchor for the bench's command line (horizontal text). */
	benchAnchor: Pt;
	/** Act-trail slit endpoints on the torso's gate-facing (x = max) face,
	 *  newest slot first, each a short segment across the face. */
	trailSlits: { a: Pt; b: Pt }[];
}

/** The resident machine site → its figure. Pure geometry; the route draws.
 *
 *  The category-of-mark rule (his 2026-08-26 "the cubes are still cubes"
 *  read, resolved via the Cogmind reference): in an axonometric idiom,
 *  volume IS architecture — so a being must not be a volume. `automaton`
 *  keeps a boxed figure for contrast; `glyph` is the committed direction:
 *  structures are drawn, the entity is *written* — a dock plate on the
 *  floor (place), the face-core hovering above it (being), the act-trail
 *  hanging under it as a data spine. */
export function residentAnatomy(m: Machine, body: ResidentBody = 'automaton'): ResidentAnatomy {
	const tw = body === 'glyph' ? 1.2 : 1.0;
	const td = body === 'glyph' ? 1.2 : 1.0;
	const torso: Box = {
		x: m.x + (m.w - tw) / 2,
		y: m.y + (m.d - td) / 2,
		w: tw,
		d: td,
		// The glyph's "torso" is a dock plate, not a body — flat enough that
		// nothing about it reads as a building.
		h: body === 'glyph' ? 0.05 : 1.55,
		z0: 0
	};
	const head: Box | null =
		body === 'glyph'
			? null
			: {
					x: torso.x + (tw - 0.6) / 2,
					y: torso.y + (td - 0.6) / 2,
					w: 0.6,
					d: 0.6,
					h: 0.5,
					z0: torso.h + 0.14
				};
	const bench: Box = {
		x: torso.x - 0.08,
		y: m.y + m.d + 0.4,
		w: 1.16,
		d: 0.52,
		h: 0.22,
		z0: 0
	};
	const faceAnchor = head
		? iso(head.x + head.w, head.y + head.d / 2, head.z0 + head.h / 2)
		: iso(torso.x + tw / 2, torso.y + td / 2, 1.55);
	const benchFront = iso(bench.x + bench.w, bench.y + bench.d / 2, bench.h);
	const benchAnchor = { x: benchFront.x + 10, y: benchFront.y };
	const trailSlits: { a: Pt; b: Pt }[] = [];
	if (body === 'glyph') {
		// The data spine: act ticks hang beneath the hovering core, along
		// the beam that grounds it to its dock — written marks, no volume.
		for (let i = 0; i < TRAIL_MAX; i++) {
			const y = faceAnchor.y + 27 + i * 6.5;
			const floorY = iso(torso.x + tw / 2, torso.y + td / 2, torso.h).y;
			if (y > floorY - 5) break;
			trailSlits.push({ a: { x: faceAnchor.x - 4.5, y }, b: { x: faceAnchor.x + 4.5, y } });
		}
	} else {
		for (let i = 0; i < TRAIL_MAX; i++) {
			const z = torso.h - 0.3 - i * 0.2;
			if (z < 0.18) break;
			trailSlits.push({
				a: iso(torso.x + tw, torso.y + td * 0.2, z),
				b: iso(torso.x + tw, torso.y + td * 0.82, z)
			});
		}
	}
	return { torso, head, bench, faceAnchor, benchAnchor, trailSlits };
}

/** Painter order: back-to-front by footprint center depth (x+y). Stable for
 *  ties via key so two frames never swap sibling paint order. */
export function paintOrder(machines: Machine[]): Machine[] {
	return [...machines].sort((m, n) => {
		const dm = m.x + m.w / 2 + (m.y + m.d / 2);
		const dn = n.x + n.w / 2 + (n.y + n.d / 2);
		return dm === dn ? (m.key < n.key ? -1 : 1) : dm - dn;
	});
}

/** Floor waypoints → an SVG path in screen space. */
export function floorPath(points: Pt[]): string {
	return points
		.map((p, i) => {
			const s = iso(p.x, p.y);
			return `${i === 0 ? 'M' : 'L'} ${round2(s.x)} ${round2(s.y)}`;
		})
		.join(' ');
}

export function polyPoints(poly: Pt[]): string {
	return poly.map((p) => `${round2(p.x)},${round2(p.y)}`).join(' ');
}

function round2(n: number): number {
	return Math.round(n * 100) / 100;
}

/** Scene bounds in screen px (for the viewBox), padded. Includes platform
 *  corners and every machine's top. */
export function sceneBounds(scene: Scene): { x: number; y: number; w: number; h: number } {
	const pts: Pt[] = [
		iso(0, 0),
		iso(scene.cols, 0),
		iso(0, scene.rows),
		iso(scene.cols, scene.rows)
	];
	for (const m of scene.machines) {
		for (const p of boxFaces(m.x, m.y, m.w, m.d, m.h).top) pts.push(p);
	}
	// The gate posts rise above the floor's back edge.
	pts.push(iso(scene.gate.x, scene.gate.y, 1.3));
	const xs = pts.map((p) => p.x);
	const ys = pts.map((p) => p.y);
	const pad = 26;
	const x = Math.min(...xs) - pad;
	const y = Math.min(...ys) - pad;
	return {
		x: round2(x),
		y: round2(y),
		w: round2(Math.max(...xs) - x + pad),
		h: round2(Math.max(...ys) - y + pad)
	};
}

/** The floor-plane text transform (labels lie ON the floor, along +x).
 *  Basis: ex → (1, 0.5), ey → (−1, 0.5) — apply at a projected origin. */
export function floorTextTransform(x: number, y: number): string {
	const o = iso(x, y);
	return `matrix(0.894 0.447 -0.894 0.447 ${round2(o.x)} ${round2(o.y)})`;
}
