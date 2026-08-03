import {
	quotaLevel,
	quotaWindowReading,
	type QuotaLevel,
	type QuotaShell,
	type QuotaWindow
} from './quota.ts';
import { isCollapsed } from './collapse.ts';
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

export interface SlotChip {
	/** `1/4 slots` — active over configured ceiling; `1/? slots` when the
	 *  daemon published no ceiling (a fact worth a character, not a guess). */
	label: string;
	/** Quota-level word for the headroom left, or null while the reading is
	 *  merely a configured ceiling (utilization < 80%) — neutral chrome, the
	 *  same convention Limits.svelte carried before it folded in here. */
	level: QuotaLevel | null;
	title: string;
}

/** The spawn-slot capacity chip (#972 machine round: LIMITS stops being a
 * section). Same reading the section made — headroom, contention at ≥80%
 * utilization — compressed to a chip beside fuel; the raw config key demotes
 * from caption to tooltip. */
export function slotChip(activeSpawns: number, maxSpawns: number | null): SlotChip {
	const title = 'spawn slots — concurrent worker-stack children (spawn.max_concurrent)';
	if (maxSpawns === null || maxSpawns <= 0) {
		return { label: `${activeSpawns}/? slots`, level: null, title };
	}
	const headroomPct = Math.max(0, ((maxSpawns - activeSpawns) / maxSpawns) * 100);
	const contention = activeSpawns / maxSpawns >= 0.8;
	return {
		label: `${activeSpawns}/${maxSpawns} slots`,
		level: contention ? quotaLevel(headroomPct) : null,
		title
	};
}

export function dialDasharray(fraction: number): string {
	const clamped = Math.max(0, Math.min(1, fraction));
	return `${(clamped * DIAL_CIRCUMFERENCE).toFixed(3)} ${DIAL_CIRCUMFERENCE.toFixed(3)}`;
}

export function quotaWindowCountLabel(shells: QuotaShell[]): string {
	const count = shells.reduce((total, shell) => total + shell.windows.length, 0);
	return `${count} quota window${count === 1 ? '' : 's'}`;
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

function resetLabel(window: QuotaWindow | ReturnType<typeof quotaWindowReading>): string | null {
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
			const reading = quotaWindowReading(window);
			const compact = compactWindowName(window);
			const owner = compact.owner ?? shell.shell.toLowerCase();
			const percent =
				reading.percent === null || reading.percent === undefined
					? null
					: Math.max(0, Math.min(100, reading.percent));
			const asOf =
				percent !== null && shell.status === 'stale' && shell.as_of
					? new Date(shell.as_of).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
					: null;
			const percentLabel =
				percent === null ? '?' : `${Math.round(percent)}%${asOf ? ` · as of ${asOf}` : ''}`;
			const label = `${owner} · ${compact.window}`;
			const reset = resetLabel(reading);

			// Reset visibility (2026-07-18 ask): the fuel bar answers "how
			// much is left", the countdown + time track answer "how long
			// until it refills". Both derive from `resets_at`; a report
			// without it (older daemon) keeps the bar and drops the clock.
			const secondsLeft =
				reading.resets_at === null || reading.resets_at === undefined
					? null
					: reading.resets_at - nowMs / 1000;
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

/**
 * Does the rail render as its one-line slim bar?
 *
 * THE PICKER YOU CANNOT REACH (2026-08-02). This used to be
 * `condensed && !pinnedOpen`, spelled inline in the component — and it let the
 * page's *scroll verdict* take back a panel the reader had opened by hand.
 * Expanding the rack and scrolling one pixel past the sentinel unmounted the
 * whole strip, spool rack included; since the rack is the last block of that
 * panel and the strong cores are its last rows, the bottom spool could not be
 * tapped at all. Reaching it needed the page scroll that deleted it.
 *
 * The rule, and the reason this is a function rather than an expression: a
 * reader's own open outranks the scroll verdict, and both ways of opening —
 * pinning the slim bar, or expanding the rack — are equally the reader's.
 * Enumerating them inline is how the second one got left out.
 *
 * A thin wrapper over `collapse.isCollapsed` (2026-08-03, the rack answers
 * everywhere): the rule above generalises past this component — it is the
 * same one the machine's dock answers — so the verdict itself now lives in
 * `collapse.ts` and this function only translates the rail's own vocabulary
 * into it.
 */
export function railIsSlim(state: {
	condensed: boolean;
	pinnedOpen: boolean;
	expanded: boolean;
}): boolean {
	return isCollapsed({
		open: state.expanded,
		scrolledPast: state.condensed,
		pinnedOpen: state.pinnedOpen
	});
}

/**
 * The rail's condense verdict, with hysteresis.
 *
 * THE BOUNDARY THAT FLICKERED (2026-08-02, his touchpad report: "it glitches
 * real hard between the collapsed and normal unless I scroll fast enough to
 * go past the head of the warp"). The old verdict was a single
 * IntersectionObserver threshold on the sentinel above the rail: one shared
 * boundary for condensing and un-condensing. A slow touchpad scroll sits *at*
 * that boundary for many frames, and every 1px of jitter toggled a ~140px
 * layout change plus a 260ms glitch reveal — the flicker was the trigger's
 * geometry, not the animation's.
 *
 * Second defect, same boundary: the spacer that holds the rail's flow
 * footprint is documented as "only ever non-zero while off-screen", but at
 * the old threshold the rail condensed the moment its *top* left the
 * viewport — inflating the spacer while the freed area was still on screen,
 * a visible blank band exactly where the rail had been.
 *
 * The rule: a form change earns a dead band at least as tall as the form
 * change itself. Condense only once the reader has scrolled past the whole
 * full rail (the freed space is then provably off-screen; the sticky slim
 * bar takes over seamlessly). Un-condense only back near the rail's natural
 * top, where the full form belongs. Between the two thresholds the verdict
 * holds its last state — jitter has nothing to toggle.
 */
export const RAIL_UNCONDENSE_SLACK_PX = 8;
export const RAIL_CONDENSE_FLOOR_PX = 48;

export function railScrollVerdict(state: {
	scrollY: number;
	railTop: number;
	railFullHeight: number;
	condensed: boolean;
}): boolean {
	const condenseAt = state.railTop + Math.max(state.railFullHeight, RAIL_CONDENSE_FLOOR_PX);
	const releaseAt = state.railTop + RAIL_UNCONDENSE_SLACK_PX;
	if (!state.condensed) return state.scrollY > condenseAt;
	return state.scrollY >= releaseAt;
}
