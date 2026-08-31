// What `/daily` still owns after 2026-08-31. The route used to compile its
// own account of the world here — buoys off the warp, islands off branch
// relics, a kb reef, a cloth digest — a second, thinner telling of facts the
// main dashboard already states. The maintainer ditched that composition
// wholesale ("there is nothing to salvage there"), and it took
// `dailyBuoys`/`surfaceBuoys`/`dailyIslands`/`knowledgePageCount`/
// `hashItemId`/`dailyItemState` and `DailyItemPanel.svelte` with it. `/daily`
// wears `$lib/Dashboard.svelte` now; what is left in this file is the two
// things that view still needs and nothing else can answer.
import { edgeLine, liveRunDisplayName, runCourse, type LiveRun } from '../liveRuns.ts';

export interface DailyLiveBar {
	run: LiveRun;
	name: string;
	act: string | null;
	course: string | null;
	pending: number;
	depth: number;
}

/** Parent-first presence rows. Children whose parent has already left stay visible at root. */
export function dailyLiveBars(runs: LiveRun[]): DailyLiveBar[] {
	const children = new Map<string, LiveRun[]>();
	const ids = new Set(runs.map((run) => run.run_id || run.id));
	for (const run of runs) {
		if (!run.parent_run_id || !ids.has(run.parent_run_id)) continue;
		const rows = children.get(run.parent_run_id) ?? [];
		rows.push(run);
		children.set(run.parent_run_id, rows);
	}
	const roots = runs.filter((run) => !run.parent_run_id || !ids.has(run.parent_run_id));
	const out: DailyLiveBar[] = [];
	const add = (run: LiveRun, depth: number) => {
		const course = runCourse(run.card_text, run.course);
		out.push({
			run,
			name: liveRunDisplayName(run),
			act: edgeLine(run.edge),
			course: course ? `${course.done}/${course.total}` : null,
			pending: run.portals?.pending ?? 0,
			depth
		});
		for (const child of children.get(run.run_id || run.id) ?? []) add(child, depth + 1);
	};
	for (const root of roots) add(root, 0);
	return out;
}

/** How tall the ascii scene stands, in character rows.
 *
 *  `AsciiField` derives its *width* from the box it is given and takes its
 *  *height* as a row count — so a constant here is a constant number of rows
 *  on every screen, and 22 rows (the old standalone `/daily`) is roughly a
 *  full phone viewport. Inside the dashboard that would push every section
 *  under it below the horizon on exactly the device the compact view exists
 *  for. So the two placements read the viewport instead:
 *
 *  - `inline` — the glance in the live-runs slot. A third of the viewport,
 *    floored at something still legible as a map and capped so a tall desktop
 *    doesn't turn the glance back into the wall.
 *  - `full` — the expanded stage. Nearly the whole overlay; the cap is well
 *    past any real viewport and exists only so a bad measurement can't ask
 *    the camera to render a thousand rows.
 *
 *  A zero/absent viewport (SSR, a detached measurement) falls to the floor
 *  rather than to zero: a map with no rows renders as a blank frame, which
 *  reads as broken, while a short one reads as a small map.
 */
export const MAP_ROW_BOUNDS = {
	inline: { share: 0.34, min: 10, max: 22 },
	full: { share: 0.86, min: 14, max: 48 }
} as const;

export type MapPlacement = keyof typeof MAP_ROW_BOUNDS;

export function mapRows(
	placement: MapPlacement,
	viewportHeight: number,
	lineHeightPx = 16.2
): number {
	const { share, min, max } = MAP_ROW_BOUNDS[placement];
	if (!Number.isFinite(viewportHeight) || viewportHeight <= 0) return min;
	if (!Number.isFinite(lineHeightPx) || lineHeightPx <= 0) return min;
	return Math.max(min, Math.min(max, Math.round((viewportHeight * share) / lineHeightPx)));
}
