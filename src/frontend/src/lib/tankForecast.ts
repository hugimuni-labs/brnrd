// Control strip slice 2 (design-wyrd.md §4 band 1): "what fits in the tank" —
// supply against draw.
//
// The design doc speced this as *planned* draw, priced from "median spend per
// classification", on the stated ground that the run-shape slug
// "has been feeding this for weeks". Going to derive it killed that premise:
// 165 distinct slugs across 270 ledger rows, 141 of them appearing exactly
// once. The slug is coined fresh by each run with no vocabulary to conform to,
// so its median is mostly a population of one. It is a label, not a statistic.
//
// The maintainer then cut the right way (2026-07-19): "actual Usage is the most
// important anyway." So this module measures rather than projects, and the
// measurement needs no ledger join at all — the quota window already carries
// both halves. `100 - percent` is exactly how much of the window has been
// drawn, and `resets_at` minus the window's own duration says how long that
// took. Rate is the quotient of two numbers the provider already reports.
//
// The strip has in fact rendered both halves since slice 1 — the fuel bar and
// the elapsed track underneath it, with a comment inviting the reader to
// compare them ("time ahead of fuel = burning slow, fuel ahead of time =
// burning hot"). Slice 2 is the instrument saying it out loud instead of
// leaving it as an eyeball exercise on two 3px bars.
//
// What the ledger adds is the half the window genuinely cannot know: what is
// *queued* to draw next. That is priced only for scheduled wakes, and only off
// `source_system`, which the daemon writes and no run can improvise.

import type { QuotaBurn, QuotaShell, QuotaWindow } from './quota';
import type { RunLedgerRow } from './runLedger';
import type { ScheduledWake } from './scheduledWakes';

/** Window lengths by compact name. A window we cannot size yields no rate —
 *  never a fabricated one. Mirrors `controlStrip.ts`'s table deliberately:
 *  both read the same provider vocabulary, and neither owns the other. */
const WINDOW_DURATION_S: Record<string, number> = {
	'5h': 5 * 3600,
	week: 7 * 86400
};

/** Below this much of a window elapsed, a rate is noise: a window three
 *  minutes old divides by ~nothing and projects wildly. `recent_burn` refuses
 *  under 30 minutes for the same reason; this is the fractional equivalent,
 *  so it scales to both the 5h and the weekly window. */
const MIN_ELAPSED_FRACTION = 0.04;

export type TankVerdict = 'sustainable' | 'tight' | 'exhausting' | 'unknown';

/** Headroom below this at the window's close reads as `tight`: not a
 *  projected dry-out, but no slack for anything unplanned either. */
const TIGHT_HEADROOM_PERCENT = 10;

export interface Tank {
	id: string;
	/** `claude · week`, matching the fuel row it sits under. */
	label: string;
	remainingPercent: number;
	usedPercent: number;
	/** How far through this window's own clock we are, 0..1. */
	elapsedFraction: number | null;
	hoursLeft: number | null;
	/** Percent of the window drawn per hour. Null when unmeasurable. */
	ratePerHour: number | null;
	/** Where the rate came from. `measured` is the short-horizon burn series
	 *  (`usage_samples.recent_burn`, sampled readings over the last ~3h);
	 *  `window` is whole-window arithmetic (used ÷ elapsed), a lifetime
	 *  average that answers "what has this window drawn" but lags the current
	 *  pace by hours. The strip says which, because the two can genuinely
	 *  disagree and a reader deciding whether to dispatch needs the current
	 *  pace, not the average of a week that mostly already happened. */
	rateSource: 'measured' | 'window' | null;
	/** Horizon the measured rate was read over, in minutes. Null for `window`. */
	rateSpanMinutes: number | null;
	/** Where this rate lands the window at reset. Can go negative — that *is*
	 *  the reading, and the caller renders it as a dry-out, not a clamp. */
	projectedRemainingAtReset: number | null;
	/** Hours until zero at this rate; null when not burning or unmeasurable. */
	exhaustsInHours: number | null;
	verdict: TankVerdict;
	/** One line, already unfolded: the whole verdict without a legend. */
	headline: string;
	/** Queued scheduled draw before this window resets, in percent. Null when
	 *  there is no measured per-wake cost to price it with. */
	committedDraw: number | null;
	committedWakes: number;
	stale: boolean;
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

function median(values: number[]): number | null {
	if (values.length === 0) return null;
	const sorted = [...values].sort((a, b) => a - b);
	const mid = Math.floor(sorted.length / 2);
	return sorted.length % 2 === 0 ? (sorted[mid - 1] + sorted[mid]) / 2 : sorted[mid];
}

/** Shell match for calibration rows. Compared case-insensitively because the
 *  quota snapshot's `shell` and the ledger's `runner_shell` are written by
 *  different producers; the strip already lowercases the former for display. */
function rowIsShell(row: RunLedgerRow, shell: string | undefined): boolean {
	if (shell === undefined) return true;
	return (row.runner_shell ?? '').toLowerCase() === shell.toLowerCase();
}

function rowTokens(row: RunLedgerRow): number | null {
	const input = row.tokens_input;
	const output = row.tokens_output;
	if (input === null && output === null) return null;
	return (input ?? 0) + (output ?? 0);
}

/**
 * Tokens per one percent of the weekly window, calibrated from closed runs.
 *
 * Calibrated **per shell**, because a percent is not a unit. Claude's weekly
 * window and codex's are different budgets of different size, so a percent of
 * one buys a different number of tokens than a percent of the other: measured
 * on this account 2026-07-19, claude reads ~48,330 tokens per weekly percent
 * against codex's ~30,375, a 1.6× spread across 107 and 17 samples. A blended
 * median would price whichever window leads the strip with a ratio dominated by
 * whichever shell simply ran more often. A shell with too few samples yields
 * null and the caller shows the wake count without a cost — falling back to the
 * other shell's ratio would be the invented number this module refuses.
 *
 * Only rows with a strictly positive delta are used. A zero is the
 * quantization floor (the provider reports whole percents, so any run under
 * ~46k tokens books 0 and would divide to infinity), and a negative is a
 * window that reset mid-run — the bug `run_ledger._delta` now nulls at the
 * source, but rows written before that fix are still on disk and this must not
 * trust them. Median rather than mean for the same reason: one surviving
 * outlier should not move the calibration.
 */
export function tokensPerWeeklyPercent(rows: RunLedgerRow[], shell?: string): number | null {
	const ratios: number[] = [];
	for (const row of rows) {
		if (!rowIsShell(row, shell)) continue;
		const delta = row.weekly_pct_delta;
		if (delta === null || delta === undefined || delta <= 0) continue;
		const tokens = rowTokens(row);
		if (tokens === null || tokens <= 0) continue;
		ratios.push(tokens / delta);
	}
	// Four is thin, but the alternative is no reading at all on a young
	// account; the caller shows the sample count so the number is never
	// mistaken for more evidence than it is.
	return ratios.length >= 4 ? median(ratios) : null;
}

export interface ScheduledCost {
	/** Runs the daemon itself tagged `source_system=schedule`. */
	samples: number;
	medianTokens: number;
	/** Median cost of one scheduled firing, as percent of the weekly window.
	 *  Null when the token→percent calibration has too little to stand on. */
	percentOfWeek: number | null;
}

/**
 * What one scheduled wake actually costs, measured.
 *
 * The join key is `source_system`: the daemon writes it, a run cannot
 * improvise it, and it is filled on every row. That distinction is the whole
 * reason this function exists — it is why the resident-coined run-shape slug
 * was cut (2026-07-19) and this measures off a daemon-written field instead.
 */
export function scheduledCost(rows: RunLedgerRow[], shell?: string): ScheduledCost | null {
	const tokens: number[] = [];
	for (const row of rows) {
		if (row.source_system !== 'schedule') continue;
		if (!rowIsShell(row, shell)) continue;
		const t = rowTokens(row);
		if (t !== null && t > 0) tokens.push(t);
	}
	const medianTokens = median(tokens);
	if (medianTokens === null || tokens.length < 3) return null;
	const perPercent = tokensPerWeeklyPercent(rows, shell);
	return {
		samples: tokens.length,
		medianTokens,
		percentOfWeek: perPercent === null ? null : medianTokens / perPercent
	};
}

/** Scheduled wakes whose next firing lands before *beforeMs*. Only wakes with
 *  a known instant count — an `every:` entry still anchoring its first cycle
 *  has no fire time yet, and guessing one would be inventing draw. */
export function wakesBefore(wakes: ScheduledWake[], nowMs: number, beforeMs: number): number {
	return wakes.filter((wake) => {
		if (!wake.scheduled_for) return false;
		const t = Date.parse(wake.scheduled_for);
		if (Number.isNaN(t)) return false;
		return t >= nowMs - 60_000 && t <= beforeMs;
	}).length;
}

function shortHours(hours: number): string {
	if (hours < 1) return `${Math.max(1, Math.round(hours * 60))}m`;
	if (hours < 48) {
		const h = Math.floor(hours);
		const m = Math.round((hours - h) * 60);
		return m === 0 ? `${h}h` : `${h}h${m}m`;
	}
	return `${Math.round(hours / 24)}d`;
}

/**
 * The one line the strip exists to say.
 *
 * Deliberately written as a sentence rather than a legend plus glyphs: this is
 * the *user's* face of the object, and the density rule that governs my
 * working register explicitly does not govern here.
 */
function headlineFor(
	verdict: TankVerdict,
	remaining: number,
	projected: number | null,
	exhaustsInHours: number | null,
	hoursLeft: number | null
): string {
	if (verdict === 'unknown') {
		return hoursLeft === null
			? 'not enough of this window has elapsed to read a rate'
			: `${Math.round(remaining)}% left · too early in the window to project`;
	}
	if (verdict === 'exhausting' && exhaustsInHours !== null) {
		const resetIn = hoursLeft === null ? '' : `, ${shortHours(hoursLeft)} before it resets`;
		return `dry in ~${shortHours(exhaustsInHours)} at this rate${resetIn}`;
	}
	if (verdict === 'tight') {
		return `holds, barely — ~${Math.round(projected ?? 0)}% left at reset`;
	}
	return `holds — ~${Math.round(projected ?? 0)}% still in the tank at reset`;
}

/**
 * Read one window as a tank: supply, measured draw rate, and where that rate
 * lands before the window refills.
 *
 * `nowMs` is passed rather than read so the whole reading is a pure function
 * of its inputs and can be pinned in a test.
 */
export function readTank(
	shell: QuotaShell,
	window: QuotaWindow,
	index: number,
	nowMs: number,
	options: {
		scheduledWakes?: ScheduledWake[];
		scheduledCost?: ScheduledCost | null;
		burn?: QuotaBurn | null;
	} = {}
): Tank | null {
	const percent = window.percent;
	if (percent === null || percent === undefined) return null;

	const compact = compactWindowName(window);
	const owner = compact.owner ?? shell.shell.toLowerCase();
	const remaining = Math.max(0, Math.min(100, percent));
	const used = 100 - remaining;

	const duration = WINDOW_DURATION_S[compact.window];
	const resetsAt = window.resets_at;
	const secondsLeft =
		resetsAt === null || resetsAt === undefined ? null : resetsAt - nowMs / 1000;

	let elapsedFraction: number | null = null;
	let hoursLeft: number | null = null;
	if (secondsLeft !== null && duration) {
		elapsedFraction = Math.max(0, Math.min(1, 1 - secondsLeft / duration));
		hoursLeft = Math.max(0, secondsLeft) / 3600;
	}

	let ratePerHour: number | null = null;
	let rateSource: 'measured' | 'window' | null = null;
	let rateSpanMinutes: number | null = null;
	let projected: number | null = null;
	let exhaustsInHours: number | null = null;
	let verdict: TankVerdict = 'unknown';

	// The measured burn is the preferred rate source (#493: burn was published,
	// typed, and rendered nowhere while this line derived its own rate from
	// window arithmetic — two measurements of one quantity). The series is
	// per-shell and pinned to one window (`burn.window_minutes` names it, the
	// longest on record for that shell), so it only speaks for the window it
	// was measured against. `recent_burn` already refuses spans under 30
	// minutes at the source, so a present burn is a usable one.
	const burn = options.burn ?? null;
	if (
		burn &&
		duration &&
		burn.window_minutes * 60 === duration &&
		burn.span_minutes > 0 &&
		burn.burned_percent >= 0
	) {
		ratePerHour = burn.burned_percent / (burn.span_minutes / 60);
		rateSource = 'measured';
		rateSpanMinutes = burn.span_minutes;
	} else if (elapsedFraction !== null && duration && elapsedFraction >= MIN_ELAPSED_FRACTION) {
		const elapsedHours = (elapsedFraction * duration) / 3600;
		ratePerHour = used / elapsedHours;
		rateSource = 'window';
	}

	if (ratePerHour !== null) {
		projected = remaining - ratePerHour * (hoursLeft ?? 0);
		if (ratePerHour > 0) {
			const toZero = remaining / ratePerHour;
			// Only a dry-out *before* the window refills is a dry-out. A rate
			// that would exhaust the tank in a week, on a window that resets in
			// three hours, is a pace you can keep — the reading `recent_burn`
			// calls `sustainable`, and the one the old flat bar never gave.
			if (hoursLeft !== null && toZero < hoursLeft) exhaustsInHours = toZero;
		}
		if (exhaustsInHours !== null) verdict = 'exhausting';
		else if (projected < TIGHT_HEADROOM_PERCENT) verdict = 'tight';
		else verdict = 'sustainable';
	}

	// Committed draw: only meaningful against the weekly window, where a
	// recurring wake actually fires several times before the refill. Priced
	// from measured scheduled-run cost, and omitted entirely when that
	// measurement is missing rather than defaulted to a plausible number.
	let committedWakes = 0;
	let committedDraw: number | null = null;
	if (compact.window === 'week' && resetsAt !== null && resetsAt !== undefined) {
		committedWakes = wakesBefore(options.scheduledWakes ?? [], nowMs, resetsAt * 1000);
		const cost = options.scheduledCost?.percentOfWeek ?? null;
		if (cost !== null && committedWakes > 0) committedDraw = committedWakes * cost;
	}

	return {
		id: `${shell.shell}:${window.label}:${index}`,
		label: `${owner} · ${compact.window}`,
		remainingPercent: remaining,
		usedPercent: used,
		elapsedFraction,
		hoursLeft,
		ratePerHour,
		rateSource,
		rateSpanMinutes,
		projectedRemainingAtReset: projected,
		exhaustsInHours,
		verdict,
		headline: headlineFor(verdict, remaining, projected, exhaustsInHours, hoursLeft),
		committedDraw,
		committedWakes,
		stale: shell.status === 'stale'
	};
}

/**
 * Every readable window across every shell, worst verdict first.
 *
 * Ordering is the point: the strip has room for one line at a glance, and the
 * window that is about to run dry is the one that should occupy it — not
 * whichever shell the provider happened to list first.
 */
export function readTanks(
	shells: QuotaShell[],
	rows: RunLedgerRow[] | null,
	wakes: ScheduledWake[] | null,
	nowMs: number = Date.now()
): Tank[] {
	const tanks: Tank[] = [];
	for (const shell of shells) {
		// Priced against this shell's own rows: see `tokensPerWeeklyPercent`.
		const cost = rows ? scheduledCost(rows, shell.shell) : null;
		shell.windows.forEach((window, index) => {
			const tank = readTank(shell, window, index, nowMs, {
				scheduledWakes: wakes ?? [],
				scheduledCost: cost,
				burn: shell.burn ?? null
			});
			if (tank) tanks.push(tank);
		});
	}
	const order: Record<TankVerdict, number> = {
		exhausting: 0,
		tight: 1,
		sustainable: 2,
		unknown: 3
	};
	return tanks.sort((a, b) => {
		const byVerdict = order[a.verdict] - order[b.verdict];
		if (byVerdict !== 0) return byVerdict;
		return a.remainingPercent - b.remainingPercent;
	});
}
