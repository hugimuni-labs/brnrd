import {
	quotaLevel,
	quotaWindowReading,
	type QuotaLevel,
	type QuotaShell,
	type QuotaWindow
} from './quota.ts';
import { liveSticky, type RunnerProfile, type RunnerSticky, type WakeRequest } from './runners.ts';

export type RunnerBlockKind = 'requested' | 'sticky' | 'default';

export interface RunnerBlock {
	profile: RunnerProfile;
	kind: RunnerBlockKind;
	badge: string;
	active: boolean;
	/** ISO expiry stamp, present only on the sticky block — the header's
	 *  countdown re-derives from it on every clock tick. */
	until?: string;
}

export interface FuelRow {
	id: string;
	label: string;
	percent: number | null;
	percentLabel: string;
	/** Compact time-to-reset, e.g. `4d2h` / `3h50m` / `47m`. */
	resetShort: string | null;
	/** Fraction of the window still to run, 1 → 0 as reset approaches. The
	 *  dial draws this, so it **drains**: the wedge is what is left, not what
	 *  is spent. */
	timeRemaining: number | null;
	tooltip: string;
	stale: boolean;
	/** This row's *daemon report* is stale (#1503) — distinct from `stale`
	 *  above, which is the scrape-level fact the shell payload's own
	 *  `updated_at` carries. A retired daemon's shell can merge-survive with
	 *  no fresh `updated_at` ever contradicting it; this is the report-level
	 *  fact that catches that case regardless. */
	daemonStale: boolean;
}

/** Known window lengths by compact name; a window we can't size renders
 *  its countdown text but no dial (never a fabricated fraction). */
const WINDOW_DURATION_S: Record<string, number> = {
	'5h': 5 * 3600,
	week: 7 * 86400
};

/** The reset dial is a filled pie: a circle of radius R/2 stroked at width R
 *  covers the full disc, so a stroke-dasharray arc reads as a wedge. 2026-07-22
 *  ask — the old second bar shared the fuel bar's grammar while meaning time,
 *  and nothing on screen said so; a disc reads as a clock natively.
 *
 *  2026-08-05: the wedge **drains**. It filled for two weeks, which is the
 *  progress-bar idiom — a thing being accomplished — beside a fuel bar that
 *  already means "what is left". A quota window is a reserve of time and a
 *  reserve empties; a draining disc is the cooldown/countdown idiom every
 *  reader already holds, and it now agrees with its neighbour instead of
 *  running against it. */
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
	const title = 'spawn slots — concurrent strand-stack children (spawn.max_concurrent)';
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
 * foreground intent; a live conversation-sticky (#932) is the standing
 * truth for the bound thread and outranks the pin — until it rode here the
 * header showed the config default while the sticky decided every wake
 * (the 2026-08-08 "core tap is lying" defect). The default remains visible
 * only when it is genuinely a different fallback, so duplicate blocks
 * cannot imply two competing wakes.
 */
export function runnerBlocks(
	profiles: RunnerProfile[],
	defaultProfile: string | null,
	wakeRequest: WakeRequest | null,
	sticky: RunnerSticky | null = null,
	nowMs: number = Date.now()
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

	const live = liveSticky(sticky, nowMs);
	const stuck = live ? profiles.find((profile) => profile.name === live.profile) : undefined;
	if (live && stuck) {
		const blocks: RunnerBlock[] = [
			{
				profile: stuck,
				kind: 'sticky',
				badge: `riding thread · ${stickyCountdown(live, nowMs) ?? 'until released'}`,
				active: true,
				...(live.expires_at ? { until: live.expires_at } : {})
			}
		];
		if (fallback && fallback.name !== stuck.name) {
			blocks.push({ profile: fallback, kind: 'default', badge: 'default', active: false });
		}
		return blocks;
	}

	return fallback ? [{ profile: fallback, kind: 'default', badge: 'default', active: true }] : [];
}

/** Compact time left on a sticky (`47m`, `1h13m`), or null without an
 *  expiry stamp. Same grammar as the fuel dials' reset column. */
export function stickyCountdown(
	sticky: RunnerSticky | null | undefined,
	nowMs: number = Date.now()
): string | null {
	if (!sticky?.expires_at) return null;
	const expires = Date.parse(sticky.expires_at);
	if (Number.isNaN(expires)) return null;
	return shortDelta((expires - nowMs) / 1000);
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
			const timeRemaining =
				secondsLeft === null || !duration ? null : Math.max(0, Math.min(1, secondsLeft / duration));

			return {
				id: `${shell.shell}:${window.label}:${index}`,
				label,
				percent,
				percentLabel,
				resetShort,
				timeRemaining,
				tooltip: `${label}: ${percent === null ? 'unknown' : `${Math.round(percent)}% left`}${reset ? ` · ${reset}` : ''}${timeRemaining === null ? '' : ` · ${Math.round(timeRemaining * 100)}% of window left`}${shell.daemon_stale === true ? " · this shell's own daemon report is outdated" : ''}`,
				stale: shell.status === 'stale',
				daemonStale: shell.daemon_stale === true
			};
		})
	);
}

// `railIsSlim` (THE PICKER YOU CANNOT REACH, 2026-08-02) is gone with the
// form it used to pick between. w-68's gauge has exactly one render: one
// line, fixed height, sticky forever, no disclosure — so there is no longer
// a scroll verdict to override and nothing for a reader's own `open` to
// outrank. `collapse.ts`'s `isCollapsed`/`tapVerdict` rules still stand for
// the machine dock, which keeps its own full/docked distinction; the rail
// simply stopped needing them. See `git log` on this file for the function
// this replaced.
