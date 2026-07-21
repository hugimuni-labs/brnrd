/**
 * The loom band is a shelf, not a plotted axis (2026-07-17 steer: "very
 * good as idea, very poor as implementation"). Real activity clusters in
 * time, so positioning runs by timestamp piled every bar into a corner and
 * left the strip dead. The shelf keeps the temporal *order* — newest row
 * touches the NOW seam — and spends the horizontal space on magnitude
 * instead: past bar length ∝ spend, future bar length ∝ distance-to-fire.
 * Age itself stays on the thermal color, where it always lived.
 */
export const LOOM_PAST_WINDOW_MS = 24 * 60 * 60 * 1000;
export const LOOM_MIN_FUTURE_HORIZON_MS = 6 * 60 * 60 * 1000;
export const LOOM_CENTER_ZONE_PX = 120;
export const LOOM_DUE_SOON_MS = 15 * 60 * 1000;

/**
 * Scrollback stops for the past shelf ("can't scroll back", 2026-07-16).
 * Discrete windows, not continuous zoom: each step is a legible unit a
 * reader can name; the shelf re-fills with the runs of the new span.
 */
export const LOOM_PAST_WINDOWS_MS = [
	6 * 60 * 60 * 1000,
	12 * 60 * 60 * 1000,
	24 * 60 * 60 * 1000,
	3 * 24 * 60 * 60 * 1000,
	7 * 24 * 60 * 60 * 1000
] as const;

/**
 * Does this click on a shelf cell mean "fill the frame below" rather than
 * "take me to the node page"?
 *
 * The loom is the spine (#482) *and* a closed run is a place (#478). Both
 * shipped; the second silently won, because the cell is an `<a>` and the
 * plainest click there is navigates. Resolved by splitting the gesture rather
 * than the element — the anchor stays real (right-click copies a URL,
 * ctrl/cmd/middle-click opens a tab, the status bar shows the target), and
 * only the unmodified primary click is intercepted.
 *
 * Lives here, not inline in the component, because "which clicks navigate" is
 * the entire defect and deserves a test that doesn't need a browser.
 */
export function loomCellClickSelects(event: {
	button?: number;
	metaKey?: boolean;
	ctrlKey?: boolean;
	shiftKey?: boolean;
	altKey?: boolean;
	defaultPrevented?: boolean;
}): boolean {
	if (event.defaultPrevented) return false;
	if ((event.button ?? 0) !== 0) return false;
	return !(event.metaKey || event.ctrlKey || event.shiftKey || event.altKey);
}

export function loomPastWindowLabel(windowMs: number): string {
	const hours = Math.round(windowMs / 3_600_000);
	return hours < 48 ? `${hours}h` : `${Math.round(hours / 24)}d`;
}

/**
 * Bar length for a shelf row, as a fraction of the half-band. Square-root
 * scaled: spend and ETA are both long-tailed, and a linear scale would let
 * one marathon run flatten every other bar into an unreadable sliver. The
 * floor keeps even a zero-magnitude row visibly a bar, not a dot.
 */
export function loomBarFraction(value: number, maxValue: number): number {
	if (!Number.isFinite(value) || value <= 0) return 0.06;
	if (!Number.isFinite(maxValue) || maxValue <= 0) return 0.06;
	const fraction = Math.sqrt(Math.min(value, maxValue) / maxValue);
	return 0.06 + 0.94 * fraction;
}

/**
 * The future cannot zoom a lone near wake to the edge: it is always at
 * least six hours, extending only when a real scheduled instant is later.
 */
export function loomFutureHorizon(scheduledFor: Array<string | null>, now: number): number {
	const etas = scheduledFor
		.map((instant) => (instant ? Date.parse(instant) - now : Number.NaN))
		.filter((eta) => Number.isFinite(eta) && eta > 0);
	return Math.max(LOOM_MIN_FUTURE_HORIZON_MS, ...etas);
}

export type LoomPastStop = 'amber' | 'ember-ash' | 'ash';
export type LoomFutureStop = 'frost-deep' | 'frost' | 'amber';

/** Discrete ashing — hue never interpolates through an accidental color. */
export function loomPastStop(ageMs: number): LoomPastStop {
	if (ageMs <= 4 * 60 * 60 * 1000) return 'amber';
	if (ageMs <= 12 * 60 * 60 * 1000) return 'ember-ash';
	return 'ash';
}

/** Discrete thawing toward NOW, using the shared thermal stop vocabulary. */
export function loomFutureStop(etaMs: number, horizonMs: number): LoomFutureStop {
	if (etaMs <= LOOM_DUE_SOON_MS) return 'amber';
	return etaMs >= horizonMs * 0.55 ? 'frost-deep' : 'frost';
}

/** The shelf-row facts `nestShelfChildren` needs to find and order a
 * dispatch edge — deliberately loose (extends `T`) so the caller's own row
 * shape passes through untouched save for the added `depth`. */
export interface ShelfGroupable {
	runId: string | null;
	parentRunId: string | null;
	isSubspawn: boolean;
	ageMs: number;
}

/**
 * Nest a `spawn:`-dispatched child's shelf row under the row of the run
 * that dispatched it (#539) — the loom's read of the same
 * `parent_run_id`/`is_subspawn` edge `groupWithChildren` already folds into
 * the receipt panel's relics. There the join answers "whose produce is
 * this"; here it answers "whose bar is this", so the shelf can no longer be
 * a single list sorted by age alone once children exist in it.
 *
 * A child is anything `is_subspawn` whose `parent_run_id` names a row
 * that's actually present in the same set — same "renders standalone
 * rather than silently vanishing" fallback `groupWithChildren` uses for a
 * parent that's scrolled out of the window or filtered out by the active
 * lens. Root rows keep the shelf's existing newest-first order; each root's
 * children are age-ordered immediately beneath it, so a fleet reads as one
 * bar with its workers trailing it rather than scattered wherever their own
 * age would otherwise place them.
 *
 * Single level only, matching `groupWithChildren`: a grandchild whose direct
 * parent is itself a child nests under that parent, not the root, and this
 * function does not walk further than one hop to find it.
 */
export function nestShelfChildren<T extends ShelfGroupable>(
	items: T[]
): Array<T & { depth: 0 | 1 }> {
	const byRunId = new Map<string, T>();
	for (const item of items) {
		if (item.runId) byRunId.set(item.runId, item);
	}
	const childrenByParent = new Map<string, T[]>();
	const roots: T[] = [];
	for (const item of items) {
		if (item.isSubspawn && item.parentRunId && byRunId.has(item.parentRunId)) {
			const list = childrenByParent.get(item.parentRunId) ?? [];
			list.push(item);
			childrenByParent.set(item.parentRunId, list);
		} else {
			roots.push(item);
		}
	}

	const out: Array<T & { depth: 0 | 1 }> = [];
	for (const root of [...roots].sort((a, b) => a.ageMs - b.ageMs)) {
		out.push({ ...root, depth: 0 });
		const children = childrenByParent.get(root.runId ?? '') ?? [];
		for (const child of [...children].sort((a, b) => a.ageMs - b.ageMs)) {
			out.push({ ...child, depth: 1 });
		}
	}
	return out;
}
