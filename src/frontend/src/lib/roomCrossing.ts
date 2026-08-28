// THE CROSSING — a letter's delivery, as one motion with one origin.
//
// design-the-crossing.md: a message arriving is *one event*, and the room
// drew it as three co-occurring facts that never touched — the waiting count
// at the gate, the `✉>>>` pulse on the actor's line, and the reading tether.
// Each true, none of them the story.
//
// The maintainer's image supplies what the framing lacked: "the pager
// ceremony could be a claw taking a message out of the pager and delivering
// it to the actor." A claw has a **direction**, and a direction has a
// **source** — which is HOME finally doing something rather than being a
// fixture cluster. That is the mothership: not a seventh station, but the
// counterpart traffic departs from and returns to.
//
// Movement doctrine, unchanged from #1652 and inherited whole: **a change may
// animate only when canonical input attests it.** A crossing is minted from
// one published boundary that carried an injection (`LiveRun.crossings`,
// brnrd#1679), identified by that boundary's own `at`. No polling tick,
// elapsed clock or decorative loop mints one, and a crossing already
// delivered never re-animates.
//
// Pure data — the caller owns time, exactly like a Walk.
//
// One property worth stating because it is the reason to build the ceremony
// before fixing anything else: **a claw that reaches and finds nothing to
// carry is a frame you can watch fail.** Until #1679 the wire published a
// *cursor* — whichever boundary was current — so a crossing that landed
// between polls was never published at all, and its absence was
// indistinguishable from no crossing. A reach that visibly comes back empty
// is the difference between "the animation seems random" and "the animation
// is telling you it missed one."

import type { Point, RoomLayout } from './roomLayout.ts';
import type { PlaceId } from './roomTopology.ts';

/** Beats, at the page's own 160ms motion ticker. Sized so the whole
 *  ceremony reads as one gesture (~3.4s) rather than a cutscene: events do
 *  not arrive often, and the reader is meant to *see* this one. */
export const REACH_TICKS = 8;
export const CARRY_TICKS = 8;
export const SETTLE_TICKS = 5;
export const CROSSING_TICKS = REACH_TICKS + CARRY_TICKS + SETTLE_TICKS;

/** Max world-units per densified hop — matches `roomMotion.STEP_UNITS`, so
 *  a claw and a walk cross the same board at the same apparent speed. */
const STEP_UNITS = 3;

/** One crossing mid-ceremony. `at` is the attested boundary's own timestamp
 *  and therefore the crossing's identity: two ceremonies can never be minted
 *  for one boundary, and a re-published tail cannot replay one. */
export interface Crossing {
	actorRunId: string;
	at: string;
	/** World-unit waypoints from the source (HOME) to the actor, densified. */
	points: Point[];
	/** Ticks elapsed; the ceremony ends at `CROSSING_TICKS`. */
	tick: number;
}

/** What the renderer draws this tick. `arm` is the claw's extent from the
 *  source outward; `letter` is the carried glyph's position, or null when
 *  nothing is being carried — the empty reach, which is a real state. */
export interface CrossingFrame {
	actorRunId: string;
	arm: Point[];
	letter: Point | null;
	/** True once the letter has arrived and the arm is withdrawing — the
	 *  beat where the actor's own `✉>>>` becomes true. */
	settling: boolean;
}

function densify(from: Point, to: Point): Point[] {
	const dist = Math.max(Math.abs(to.x - from.x), Math.abs(to.y - from.y));
	const hops = Math.max(1, Math.ceil(dist / STEP_UNITS));
	const out: Point[] = [from];
	for (let h = 1; h <= hops; h++) {
		out.push({
			x: Math.round(from.x + ((to.x - from.x) * h) / hops),
			y: Math.round(from.y + ((to.y - from.y) * h) / hops)
		});
	}
	return out;
}

/**
 * Mint ceremonies for crossings not yet seen.
 *
 * `seen` is mutated: the caller owns it across ticks, the same contract
 * `roomPager.recordPages` keeps for its page store. A boundary's `at` is the
 * key, so the bounded tail the wire republishes every poll (up to 8 rows,
 * the same rows) mints each ceremony exactly once.
 *
 * Returns nothing when the source or the actor has no laid-out position —
 * a claw with no origin is not a claw, and a delivery to nowhere is the kind
 * of fabricated motion the doctrine exists to forbid.
 */
export function crossingsFor(
	attested: { actorRunId: string; at: string }[],
	seen: Set<string>,
	sourcePlaceId: PlaceId | null,
	actorPlaces: Record<string, PlaceId>,
	layout: RoomLayout
): Crossing[] {
	if (!sourcePlaceId) return [];
	const source = layout.nodes[sourcePlaceId];
	if (!source) return [];
	const out: Crossing[] = [];
	for (const row of attested) {
		const key = `${row.actorRunId}@${row.at}`;
		if (seen.has(key)) continue;
		const placeId = actorPlaces[row.actorRunId];
		const dest = placeId ? layout.nodes[placeId] : null;
		// Mark it seen either way: an unplaceable actor's crossing is not a
		// ceremony we owe later, and re-trying it every tick would mint one
		// the moment the camera happened to place the actor — motion from a
		// layout change rather than from an attested event.
		seen.add(key);
		if (!dest) continue;
		out.push({ actorRunId: row.actorRunId, at: row.at, points: densify(source, dest), tick: 0 });
	}
	return out;
}

/** Advance every ceremony one beat; finished ones are dropped. */
export function advanceCrossings(list: Crossing[]): Crossing[] {
	const alive: Crossing[] = [];
	for (const c of list) {
		const tick = c.tick + 1;
		if (tick >= CROSSING_TICKS) continue;
		alive.push({ ...c, tick });
	}
	return alive;
}

/**
 * The frames to draw, without advancing — paints between ticks read this.
 *
 * Three beats, in order:
 *
 * - **reach** — the arm extends from the source toward the actor. Nothing is
 *   carried yet; the claw is going to fetch.
 * - **carry** — the arm holds its full extent and the letter travels back
 *   along it. This is the beat that says *where the message came from*.
 * - **settle** — the letter has arrived, the arm withdraws from the source
 *   end, and the actor's own pulse is true.
 */
export function crossingFrames(list: Crossing[]): CrossingFrame[] {
	const out: CrossingFrame[] = [];
	for (const c of list) {
		const n = c.points.length;
		if (n === 0) continue;
		const t = c.tick;
		if (t < REACH_TICKS) {
			const extent = Math.max(1, Math.round((n * (t + 1)) / REACH_TICKS));
			out.push({
				actorRunId: c.actorRunId,
				arm: c.points.slice(0, extent),
				letter: null,
				settling: false
			});
			continue;
		}
		if (t < REACH_TICKS + CARRY_TICKS) {
			const p = (t - REACH_TICKS) / CARRY_TICKS;
			// The letter rides outward along the arm to the actor. The arm is
			// at full extent behind it — the reader can see the whole path the
			// message travelled, which is the point of a direction.
			const idx = Math.min(n - 1, Math.round(p * (n - 1)));
			out.push({
				actorRunId: c.actorRunId,
				arm: c.points,
				letter: c.points[idx],
				settling: false
			});
			continue;
		}
		// settle: withdraw from the source end, letter delivered
		const p = (t - REACH_TICKS - CARRY_TICKS) / SETTLE_TICKS;
		const cut = Math.min(n, Math.round(p * n));
		out.push({
			actorRunId: c.actorRunId,
			arm: c.points.slice(cut),
			letter: null,
			settling: true
		});
	}
	return out;
}
