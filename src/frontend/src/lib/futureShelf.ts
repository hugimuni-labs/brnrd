/**
 * The future shelf — the rack's read of the schedule (the dissolution,
 * 2026-08-02: each tense owns exactly one object, and the future's is the
 * rack). These are the loom band's own future-shelf rules, factored out
 * rather than rewritten, so the rack renders exactly what the band used
 * to: soonest wake first, the compact ETA legend, the thermal thaw toward
 * NOW, and the band's sqrt bar fraction against the same six-hour-minimum
 * horizon (`loomFutureHorizon`) — a countdown you can read as geometry.
 *
 * Value imports carry `.ts` extensions because `futureShelf.test.ts` runs
 * under node's own runner with no bundler in the loop — same rule
 * `cloth.ts` documents.
 */

import type { ScheduledWake } from './scheduledWakes.ts';
import {
	LOOM_DUE_SOON_MS,
	loomBarFraction,
	loomFutureHorizon,
	loomFutureStop
} from './loomBand.ts';
import { THERMAL_STOPS, type GlowUrgency } from './statusPalette.ts';

/** One shelf row, fully dressed: everything the component renders. */
export interface FutureShelfRow {
	wake: ScheduledWake;
	/** Signed distance to fire; negative = overdue. Always finite — a wake
	 * with no parseable instant (an `every:` entry still anchoring) never
	 * makes a row: a bar with no length would be a fabrication. */
	etaMs: number;
	/** Thermal stop: frost thaws to amber as the fire nears; a paused or
	 * overdue wake reads ash — no honest countdown to draw. */
	color: string;
	urgency: GlowUrgency;
	/** `in 42m · nightly sweep`, with the scheduler's own verdict
	 * (`quota-paused` / `quota-paced`) when it has one. */
	legend: string;
	/** `loomBarFraction(max(eta, 0), horizon)` — the band's sqrt scale and
	 * floor, so an imminent (or overdue) wake is still visibly a bar. */
	barFraction: number;
}

/** `in 42m` / `12m overdue` — the shelf's compact grammar, unchanged from
 * the band. (`scheduledWakes.untilText` speaks a longer dialect for the
 * detail sheet; the shelf keeps its own terser one.) */
export function futureEtaLabel(ms: number): string {
	const minutes = Math.round(Math.abs(ms) / 60_000);
	if (ms < 0) return `${minutes}m overdue`;
	if (minutes < 60) return `in ${minutes}m`;
	return `in ${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

function wakeLegend(wake: ScheduledWake, etaMs: number): string {
	const summary = (wake.summary || wake.conversation_key || 'wake').trim();
	if (wake.status === 'quota-paused') return `quota-paused · ${summary}`;
	if (wake.status === 'quota-paced') return `${futureEtaLabel(etaMs)} · quota-paced · ${summary}`;
	return `${futureEtaLabel(etaMs)} · ${summary}`;
}

/**
 * Dress the schedule for the shelf: parseable instants only, soonest
 * first, colors/urgency/legend/bar all computed against one shared
 * horizon so the rows compare with each other.
 */
export function futureShelfRows(
	scheduledWakes: ScheduledWake[] | null,
	now: number
): FutureShelfRow[] {
	const wakes = [...(scheduledWakes ?? [])]
		.filter((wake) => {
			const instant = wake.scheduled_for ? Date.parse(wake.scheduled_for) : Number.NaN;
			return Number.isFinite(instant);
		})
		.sort((a, b) => Date.parse(a.scheduled_for ?? '') - Date.parse(b.scheduled_for ?? ''));
	const horizon = loomFutureHorizon(
		wakes.map((wake) => wake.scheduled_for),
		now
	);
	return wakes.map((wake) => {
		const etaMs = Date.parse(wake.scheduled_for ?? '') - now;
		const paused = wake.status === 'quota-paused';
		const color =
			paused || etaMs < 0 ? THERMAL_STOPS.ash : THERMAL_STOPS[loomFutureStop(etaMs, horizon)];
		const urgency: GlowUrgency = paused
			? 'calm'
			: etaMs < 0
				? 'alarm'
				: etaMs <= LOOM_DUE_SOON_MS
					? 'attention'
					: 'calm';
		return {
			wake,
			etaMs,
			color,
			urgency,
			legend: wakeLegend(wake, etaMs),
			barFraction: loomBarFraction(Math.max(etaMs, 0), horizon)
		};
	});
}
