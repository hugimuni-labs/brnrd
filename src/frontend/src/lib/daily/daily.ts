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

/** Lines `renderWorld` paints below the `rows`-tall board (measured, above). */
export const SCENE_CONTROL_ROWS = 18;
/** The deck's own padding + legend toggle, in px (measured, above). */
export const SCENE_CHROME_PX = 77;

export const MAP_ROW_BOUNDS = {
	inline: { share: 0.62, min: 10, max: 22 },
	full: { share: 0.92, min: 14, max: 48 }
} as const;

export type MapPlacement = keyof typeof MAP_ROW_BOUNDS;

/** How tall the ascii scene stands, in board rows.
 *
 *  `AsciiField` takes its height as a row count and derives its width from
 *  the box it is given — so a constant here is the same number of rows on a
 *  phone and a 27" display, and 22 rows (the old standalone `/daily`) is most
 *  of a phone viewport. Inside the dashboard that pushes every section under
 *  the field below the horizon, on exactly the device the compact view exists
 *  for. So both placements read the viewport.
 *
 *  The arithmetic is not `height / lineHeight`, and that is the whole reason
 *  this is a function with a test rather than two numbers at the call sites.
 *  `rows` is the *board*; `renderWorld` paints a fixed tail of control rows
 *  under it — actor bearings, CHARTS, the cloth selvage — and the deck adds
 *  its own padding and legend toggle around the lot. Measured at both widths
 *  on 2026-08-31 (`repro/drive-daily.mjs` prints these live): 18 board rows
 *  rendered 36 lines in a 644px frame at 390x844. Ask for a share of the
 *  viewport without subtracting that, and every answer is ~300px too tall.
 *
 *  - `inline` — the live-runs slot. Bounded near two thirds of the viewport
 *    *including* the tail, so the warp is reachable with one thumb-flick.
 *  - `full` — the stage behind `↙ collapse`. The overlay's own 92svh cap.
 *
 *  The floors are load-bearing in both directions: a 0/absent viewport (SSR,
 *  the first client frame) renders the minimum rather than an empty bordered
 *  box, and on a short phone the tail alone can eat the whole budget — a
 *  small map reads as a map, a zero-row one reads as broken.
 */

export function mapRows(
	placement: MapPlacement,
	viewportHeight: number,
	lineHeightPx = 16.2
): number {
	const { share, min, max } = MAP_ROW_BOUNDS[placement];
	if (!Number.isFinite(viewportHeight) || viewportHeight <= 0) return min;
	if (!Number.isFinite(lineHeightPx) || lineHeightPx <= 0) return min;
	const painted = viewportHeight * share - SCENE_CHROME_PX;
	const lines = Math.round(painted / lineHeightPx) - SCENE_CONTROL_ROWS;
	return Math.max(min, Math.min(max, lines));
}
