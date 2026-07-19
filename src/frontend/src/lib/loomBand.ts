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

/**
 * How long an armed stop stays armed before it disarms itself. Long enough
 * to read the word "stop?" and mean it, short enough that a control left
 * armed by a mis-tap can't be committed by an unrelated click a minute later.
 */
export const LOOM_STOP_ARM_WINDOW_MS = 4000;

/**
 * What does this click on a live cell's stop control mean?
 *
 * Killing a running thought is not undoable and not free — partial work is
 * salvaged, but the thought does not resume. That argues for a confirmation;
 * the loom's register argues against a modal. So: arm, then commit. The first
 * tap turns the control into the word "stop?", the second commits, and the arm
 * lapses on its own after `LOOM_STOP_ARM_WINDOW_MS`. Two deliberate taps on a
 * 20px target in a dense band is the cheapest thing that isn't a bare tap.
 *
 * The #486 lesson applies here as it did to the cell itself — split the
 * *gesture*, not the element. The stop control is its own button beside the
 * cell rather than nested inside it (nesting is invalid HTML anyway), and it
 * ignores every click shape the cell's own `loomCellClickSelects` ignores:
 * modified and non-primary clicks belong to the browser, not to us. A stop
 * must never be something a ctrl-click or a middle-click can trip into.
 *
 * Lives here, not inline in the component, for the same reason
 * `loomCellClickSelects` does: "which clicks kill a run" is the entire
 * question and deserves a test that doesn't need a browser.
 */
export function loomStopGesture(
	event: {
		button?: number;
		metaKey?: boolean;
		ctrlKey?: boolean;
		shiftKey?: boolean;
		altKey?: boolean;
		defaultPrevented?: boolean;
	},
	armedAt: number | null,
	now: number
): 'ignore' | 'arm' | 'commit' {
	if (!loomCellClickSelects(event)) return 'ignore';
	if (armedAt === null) return 'arm';
	// A lapsed arm re-arms rather than committing: the reader's second tap is
	// answering a prompt that is no longer on screen.
	if (now - armedAt > LOOM_STOP_ARM_WINDOW_MS) return 'arm';
	return 'commit';
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
