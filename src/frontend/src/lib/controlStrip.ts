import type { QuotaShell, QuotaWindow } from './quota';
import type { RunnerProfile, WakeRequest } from './runners';

export type RunnerBlockKind = 'requested' | 'default';

export interface RunnerBlock {
	profile: RunnerProfile;
	kind: RunnerBlockKind;
	badge: 'requested · next wake' | 'default';
	active: boolean;
}

export interface FuelRow {
	id: string;
	label: string;
	percent: number | null;
	percentLabel: string;
	/** Compact time-to-reset, e.g. `4d2h` / `3h50m` / `47m`. */
	resetShort: string | null;
	/** Fraction of this window already elapsed (0..1), for the time track. */
	timeFraction: number | null;
	tooltip: string;
	stale: boolean;
}

/** Known window lengths by compact name; a window we can't size renders
 *  its countdown text but no elapsed track (never a fabricated fraction). */
const WINDOW_DURATION_S: Record<string, number> = {
	'5h': 5 * 3600,
	week: 7 * 86400
};

/** The reset dial is a filled pie: a circle of radius R/2 stroked at width R
 *  covers the full disc, so a stroke-dasharray arc reads as a wedge. 2026-07-22
 *  ask — the old second bar shared the fuel bar's grammar while meaning time,
 *  and nothing on screen said so; a filling disc reads as a clock natively. */
export const DIAL_WEDGE_RADIUS = 2.75;
const DIAL_CIRCUMFERENCE = 2 * Math.PI * DIAL_WEDGE_RADIUS;

export function dialDasharray(fraction: number): string {
	const clamped = Math.max(0, Math.min(1, fraction));
	return `${(clamped * DIAL_CIRCUMFERENCE).toFixed(3)} ${DIAL_CIRCUMFERENCE.toFixed(3)}`;
}

function shortDelta(seconds: number): string {
	const s = Math.max(0, Math.floor(seconds));
	const d = Math.floor(s / 86400);
	const h = Math.floor((s % 86400) / 3600);
	const m = Math.floor((s % 3600) / 60);
	if (d > 0) return `${d}d${h}h`;
	if (h > 0) return `${h}h${m}m`;
	return `${m}m`;
}

/**
 * Reduces the rack to the one answer the header needs. A parked request is
 * foreground intent; the default remains visible only when it is genuinely a
 * different fallback, so duplicate blocks cannot imply two competing wakes.
 */
export function runnerBlocks(
	profiles: RunnerProfile[],
	defaultProfile: string | null,
	wakeRequest: WakeRequest | null
): RunnerBlock[] {
	const fallback =
		profiles.find((profile) => profile.name === defaultProfile) ??
		profiles.find((profile) => profile.selected === true);
	const requested = wakeRequest
		? profiles.find((profile) => profile.name === wakeRequest.profile)
		: undefined;

	if (requested) {
		const blocks: RunnerBlock[] = [
			{ profile: requested, kind: 'requested', badge: 'requested · next wake', active: true }
		];
		if (fallback && fallback.name !== requested.name) {
			blocks.push({ profile: fallback, kind: 'default', badge: 'default', active: false });
		}
		return blocks;
	}

	return fallback ? [{ profile: fallback, kind: 'default', badge: 'default', active: true }] : [];
}

function compactWindowName(window: QuotaWindow): { owner: string | null; window: string } {
	const modelWeek = /^weekly\s*\(([^)]+)\)$/iu.exec(window.label.trim());
	if (modelWeek) return { owner: modelWeek[1].trim().toLowerCase(), window: 'week' };

	return {
		owner: null,
		window: window.label
			.trim()
			.toLowerCase()
			.replace(/^weekly$/u, 'week')
			.replace(/\s+window$/u, '')
	};
}

function resetLabel(window: QuotaWindow): string | null {
	if (window.reset) return window.reset;
	if (window.resets_at === null || window.resets_at === undefined) return null;
	return `resets ${new Date(window.resets_at * 1000).toISOString()}`;
}

/**
 * The compact gauge follows the daemon's window list rather than naming four
 * product buckets in UI code. That keeps a changed provider window visible on
 * the very next report, while model-specific weekly pools still read as their
 * model (for example `fable · week`) instead of a misleading shell duplicate.
 */
export function fuelRows(shells: QuotaShell[], nowMs: number = Date.now()): FuelRow[] {
	return shells.flatMap((shell) =>
		shell.windows.map((window, index) => {
			const compact = compactWindowName(window);
			const owner = compact.owner ?? shell.shell.toLowerCase();
			const percent =
				window.percent === null || window.percent === undefined
					? null
					: Math.max(0, Math.min(100, window.percent));
			const percentLabel = percent === null ? '?' : `${Math.round(percent)}%`;
			const label = `${owner} · ${compact.window}`;
			const reset = resetLabel(window);

			// Reset visibility (2026-07-18 ask): the fuel bar answers "how
			// much is left", the countdown + time track answer "how long
			// until it refills". Both derive from `resets_at`; a report
			// without it (older daemon) keeps the bar and drops the clock.
			const secondsLeft =
				window.resets_at === null || window.resets_at === undefined
					? null
					: window.resets_at - nowMs / 1000;
			const resetShort = secondsLeft === null ? null : shortDelta(secondsLeft);
			const duration = WINDOW_DURATION_S[compact.window];
			const timeFraction =
				secondsLeft === null || !duration
					? null
					: Math.max(0, Math.min(1, 1 - secondsLeft / duration));

			return {
				id: `${shell.shell}:${window.label}:${index}`,
				label,
				percent,
				percentLabel,
				resetShort,
				timeFraction,
				tooltip: `${label}: ${percent === null ? 'unknown' : `${Math.round(percent)}% left`}${reset ? ` · ${reset}` : ''}${timeFraction === null ? '' : ` · window ${Math.round(timeFraction * 100)}% elapsed`}`,
				stale: shell.status === 'stale'
			};
		})
	);
}
