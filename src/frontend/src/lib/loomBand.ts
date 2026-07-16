/**
 * The loom band maps time onto two independent logarithmic half-axes.
 * One minute is the logarithm's unit: it keeps the curve numerically
 * readable while still giving recent events more room than old ones.
 */
export const LOOM_LOG_UNIT_MS = 60_000;
export const LOOM_PAST_WINDOW_MS = 24 * 60 * 60 * 1000;
export const LOOM_MIN_FUTURE_HORIZON_MS = 6 * 60 * 60 * 1000;
export const LOOM_CENTER_ZONE_PX = 120;
export const LOOM_DUE_SOON_MS = 15 * 60 * 1000;

/**
 * Scrollback stops for the past half-axis ("can't scroll back",
 * 2026-07-16). Discrete windows, not continuous zoom: each step is a
 * legible unit a reader can name, and the log curve re-fits the new span.
 */
export const LOOM_PAST_WINDOWS_MS = [
	6 * 60 * 60 * 1000,
	12 * 60 * 60 * 1000,
	24 * 60 * 60 * 1000,
	3 * 24 * 60 * 60 * 1000,
	7 * 24 * 60 * 60 * 1000
] as const;

export function loomPastWindowLabel(windowMs: number): string {
	const hours = Math.round(windowMs / 3_600_000);
	return hours < 48 ? `${hours}h` : `${Math.round(hours / 24)}d`;
}

function clamp(value: number, minimum: number, maximum: number): number {
	return Math.max(minimum, Math.min(maximum, value));
}

function logFraction(valueMs: number, limitMs: number): number {
	const value = clamp(valueMs, 0, limitMs);
	return Math.log1p(value / LOOM_LOG_UNIT_MS) / Math.log1p(limitMs / LOOM_LOG_UNIT_MS);
}

/** Position inside the past half: 0 = window edge, 1 = NOW boundary. */
export function loomPastPosition(ageMs: number, windowMs: number = LOOM_PAST_WINDOW_MS): number {
	if (!Number.isFinite(ageMs)) return 0;
	return 1 - logFraction(ageMs, windowMs);
}

/** Position inside the future half: 0 = NOW boundary, 1 = horizon edge. */
export function loomFuturePosition(etaMs: number, horizonMs: number): number {
	const safeHorizon = Math.max(LOOM_MIN_FUTURE_HORIZON_MS, horizonMs);
	if (!Number.isFinite(etaMs)) return 1;
	return logFraction(etaMs, safeHorizon);
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
