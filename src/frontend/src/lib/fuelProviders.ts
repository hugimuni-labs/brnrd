import { fuelRows, type FuelRow } from './railGauge.ts';
import type { QuotaShell } from './quota.ts';

// design-resident-field.md §"Settings, fuel, and the next dispatch": fuel
// groups by **harness provider** (`claude`, `codex` — the Shell family a
// daemon actually runs), not by a flat list of quota windows. A provider's
// weekly quota is the primary, readable reading; every other window it
// reports (a rolling 5h ceiling, a model-specific allowance like Fable's own
// weekly) renders as a minimized "ghost" behind it — enough topology to say
// "there is more here" without asking the reader to decode every meter
// before choosing whether to open the group.
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
}

export interface FuelProviderGroup {
	/** The harness provider id — `QuotaShell.shell` / `RunnerProfile.shell`. */
	provider: string;
	/** The provider's own weekly reading, when reported — the primary,
	 *  full-opacity bar. Null when this provider has never reported a
	 *  provider-scope weekly window (still a legitimate state: render the
	 *  group from `meters` alone rather than fabricating a track). */
	primary: FuelMeter | null;
	/** Every other observed meter for this provider — the layered ghost
	 *  bars in the collapsed row, and the full list the expanded Resources
	 *  view reads. Ordered: provider-scope windows first (e.g. `5h`), then
	 *  core-scope allowances, each group preserving report order. */
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
): { scope: FuelMeterScope; coreId: string | null } {
	const owner = row.label.split(' · ')[0] ?? '';
	if (owner === provider.toLowerCase()) return { scope: 'provider', coreId: null };
	return { scope: 'core', coreId: owner };
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

		const primaryIndex = meters.findIndex(
			(meter) => meter.scope === 'provider' && meter.label.endsWith(' · week')
		);
		// No reported weekly window for the provider itself (a shell that has
		// only ever surfaced a 5h ceiling, say) — the design still wants one
		// readable reading rather than an empty collapsed row, so the first
		// provider-scope meter stands in; only when the provider has reported
		// *no* provider-scope meter at all (every row is core-attributed) does
		// the group fall back to no primary, `meters` alone carrying the truth.
		const fallbackIndex =
			primaryIndex === -1 ? meters.findIndex((meter) => meter.scope === 'provider') : -1;
		const chosenIndex = primaryIndex !== -1 ? primaryIndex : fallbackIndex;

		const primary = chosenIndex === -1 ? null : meters[chosenIndex];
		const rest = meters.filter((_, index) => index !== chosenIndex);
		// Ghost order: this provider's own remaining windows (5h, say) ahead
		// of core-scoped allowances (Fable's week) — the design's own example
		// reads `[behind: 5h 93% · fable/week 91%]` in that order.
		const secondary = [
			...rest.filter((meter) => meter.scope === 'provider'),
			...rest.filter((meter) => meter.scope === 'core')
		];

		return { provider, primary, secondary, meters };
	});
}
