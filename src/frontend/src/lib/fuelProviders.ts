import { fuelRows, type FuelRow } from './railGauge.ts';
import type { QuotaShell } from './quota.ts';

// design-resident-field.md §"Settings, fuel, and the next dispatch": fuel
// groups by **harness provider** (`claude`, `codex` — the Shell family a
// daemon actually runs), not by a flat list of quota windows.
//
// `primary` is the **binding** window: the provider-scope reading with the
// least left, because that is the one that stops a dispatch first. It used
// to be whichever window was labelled `week`, which was right only by
// coincidence — a burned 5h session under a comfortable weekly ceiling
// rendered the weekly number over a machine that could not take a run at
// all. Ties go to the window with more time left to run, since a tie is a
// coincidence and the longer ceiling is the one you cannot wait out.
//
// The other readings are not drawn behind it. Overlaying several fills on
// one track (the "ghost" stack this replaces, 2026-08-28) put two different
// quantities — remaining fuel, and which window each belonged to — on one
// axis with no key, so the headline number and the longest visible fill
// routinely disagreed in front of the reader. Every other window keeps its
// own number in the collapsed row's ledger line, and its own full bar in
// the bench's Resources list. One bar, one quantity, always named.
//
// The grouping key is the QuotaShell's own `shell` field, which is the same
// string RunnerProfile.shell carries (`groupByShell` in spoolRack.ts already
// groups the runner catalog by it) — one provider id, read the same way on
// both the fuel side and the dispatch side. No normalization or averaging
// crosses that boundary: a weekly ceiling, a rolling five-hour window, and a
// core-specific allowance answer different questions, so they stay separate
// `FuelRow`s, merely grouped and ranked.

export type FuelMeterScope = 'provider' | 'core';

/** One reported meter, with the scope fact `fuelRows` computes internally
 *  (provider-scope, e.g. `claude · week` or `claude · 5h`; core-scope, e.g.
 *  `fable · week`) surfaced for a caller that needs to render or label the
 *  difference — the ghost-bar stack and the expanded Resources list both do. */
export interface FuelMeter extends FuelRow {
	scope: FuelMeterScope;
	/** The core name when `scope === 'core'` (e.g. `fable`); null otherwise. */
	coreId: string | null;
	/** The window half of `label` on its own (`week`, `5h`) — what the
	 *  reading *measures*, so a percentage never has to be rendered next to
	 *  a bar without saying which ceiling it is a percentage of. */
	windowName: string;
}

export interface FuelProviderGroup {
	/** The harness provider id — `QuotaShell.shell` / `RunnerProfile.shell`. */
	provider: string;
	/** The binding provider-scope reading — least left of the windows that
	 *  gate every run on this shell, and therefore the one number the
	 *  collapsed row shows. Null when this provider has reported no
	 *  provider-scope window at all (still a legitimate state: render the
	 *  group from `meters` alone rather than fabricating a track). */
	primary: FuelMeter | null;
	/** Every other observed meter for this provider — the collapsed row's
	 *  ledger line, and the full bar list the expanded Resources view reads.
	 *  Ordered: provider-scope windows first (e.g. `5h`), then core-scope
	 *  allowances, each group preserving report order. */
	secondary: FuelMeter[];
	/** `primary` followed by `secondary`, unfiltered — the expanded view's
	 *  "every observed meter" reading in one list. */
	meters: FuelMeter[];
}

/** True when `row` is this provider's own reading (its 5h/week windows),
 *  false when it is a core-specific allowance attached to the provider
 *  (Fable's weekly under Claude). Mirrors `fuelRows`' own `owner` derivation
 *  without duplicating it: a core-scoped row is the one case where the
 *  label's leading token diverges from the provider it was grouped under. */
function meterScope(
	row: FuelRow,
	provider: string
): { scope: FuelMeterScope; coreId: string | null; windowName: string } {
	const [owner = '', window = ''] = row.label.split(' · ');
	// A label brnrd never compacted (an unrecognised window name arrives
	// whole) has no ` · ` to split on — fall back to the label itself rather
	// than rendering a bar labelled with the empty string.
	const windowName = window || row.label;
	if (owner === provider.toLowerCase()) return { scope: 'provider', coreId: null, windowName };
	return { scope: 'core', coreId: owner, windowName };
}

/** Least-left wins; a tie goes to the window with more time still to run.
 *  Reads only provider-scope meters — a core allowance constrains one core,
 *  not the shell, so it can never be the shell's binding ceiling. */
function bindingIndex(meters: FuelMeter[]): number {
	let chosen = -1;
	let fallback = -1;
	for (let index = 0; index < meters.length; index++) {
		const meter = meters[index];
		if (meter.scope !== 'provider') continue;
		if (fallback === -1) fallback = index;
		if (meter.percent === null) continue;
		if (chosen === -1) {
			chosen = index;
			continue;
		}
		const best = meters[chosen];
		const bestPercent = best.percent as number;
		if (meter.percent < bestPercent) chosen = index;
		else if (
			meter.percent === bestPercent &&
			(meter.timeRemaining ?? 0) > (best.timeRemaining ?? 0)
		)
			chosen = index;
	}
	// Every provider-scope window reported an unreadable percentage: still
	// name one, so the row says *which* ceiling it cannot read rather than
	// falling silent about the provider entirely.
	return chosen !== -1 ? chosen : fallback;
}

/**
 * Groups an already-fetched quota snapshot by provider. Callers that need
 * to drop disconnected shells first (a subscription with every current
 * profile verified unavailable) should filter with `availableQuotaShells`
 * (`railGauge.ts`) before calling this — same contract `fuelRows` itself
 * expects, unchanged here.
 */
export function fuelProviderGroups(
	shells: QuotaShell[],
	nowMs: number = Date.now()
): FuelProviderGroup[] {
	return shells.map((shell) => {
		const provider = shell.shell;
		const rows = fuelRows([shell], nowMs);
		const meters: FuelMeter[] = rows.map((row) => ({ ...row, ...meterScope(row, provider) }));

		const chosenIndex = bindingIndex(meters);

		const primary = chosenIndex === -1 ? null : meters[chosenIndex];
		const rest = meters.filter((_, index) => index !== chosenIndex);
		// Ledger order: this provider's own remaining windows (5h, say) ahead
		// of core-scoped allowances (Fable's week).
		const secondary = [
			...rest.filter((meter) => meter.scope === 'provider'),
			...rest.filter((meter) => meter.scope === 'core')
		];

		return { provider, primary, secondary, meters };
	});
}
