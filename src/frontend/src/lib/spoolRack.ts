import type { RunnerProfile } from './runners';

// #328 spool rack, rework (2026-08-19, "the rack of dead spools"). Pure
// logic pulled out of SpoolRack.svelte so it can be pinned by plain
// node:test assertions instead of only by rendering — the house pattern
// `controlStrip.ts` already set for this component's sibling.

export type Availability = 'available' | 'unavailable' | 'unverified';

/**
 * A profile's availability, failed closed. `available !== false` used to
 * mean "render as available" — a row simply *missing* the field (an older
 * daemon report, a partial merge) rendered identically to a verified-live
 * one. Three states now, and only one of them taps:
 * `true` → available, `false` → unavailable, anything else → unverified.
 */
export function availabilityOf(profile: RunnerProfile): Availability {
	if (profile.available === true) return 'available';
	if (profile.available === false) return 'unavailable';
	return 'unverified';
}

/**
 * Can this row's tap actually park a wake request? Verified-available is
 * necessary but not sufficient: a stale report — the account-wide chip, or
 * this row's own `daemon_stale` (a dead machine's rows can outlive the
 * account-wide staleness clock, see `runners.ts`) — must never park a wake
 * nothing will serve.
 */
export function isTappable(profile: RunnerProfile, reportStale: boolean): boolean {
	if (reportStale || profile.daemon_stale === true) return false;
	return availabilityOf(profile) === 'available';
}

export interface ShellGroup {
	shell: string;
	profiles: RunnerProfile[];
	/** Every profile in the group is *verified* unavailable (not merely
	 *  unverified) — the group is the "greyed-out rows" case and collapses
	 *  to one summary line rather than one dead row per core. */
	allUnavailable: boolean;
	/** A representative reason for the collapsed summary line, straight off
	 *  the first row that carries one. */
	reason: string | null;
}

/**
 * Groups the rack by shell — the "two-way selector" shape (shell, then its
 * cores) without adding a dropdown or changing tap semantics. Preserves the
 * incoming order both across shells and within one: the catalog already
 * arrives cost_rank-ascending (economy → strong), so grouping by shell over
 * that order keeps each group internally economy-first for free. Available
 * shells sort first; groups that collapse (every row verified unavailable)
 * sort after, in their own first-seen order.
 */
export function groupByShell(profiles: RunnerProfile[]): ShellGroup[] {
	const order: string[] = [];
	const byShell = new Map<string, RunnerProfile[]>();
	for (const profile of profiles) {
		const shell = profile.shell ?? profile.name;
		if (!byShell.has(shell)) {
			byShell.set(shell, []);
			order.push(shell);
		}
		byShell.get(shell)?.push(profile);
	}
	const groups = order.map((shell) => {
		const rows = byShell.get(shell) ?? [];
		const allUnavailable =
			rows.length > 0 && rows.every((row) => availabilityOf(row) === 'unavailable');
		const reason = rows.find((row) => row.availability)?.availability ?? null;
		return { shell, profiles: rows, allUnavailable, reason };
	});
	const live = groups.filter((group) => !group.allUnavailable);
	const dead = groups.filter((group) => group.allUnavailable);
	return [...live, ...dead];
}

/** The collapsed summary line's own text — one line standing in for N dead
 *  rows, the screenshot's 7 greyed rows made impossible by construction. */
export function collapsedShellSummary(group: ShellGroup): string {
	const count = group.profiles.length;
	const cores = count === 1 ? 'core' : 'cores';
	const reason =
		group.reason === 'shell-not-found'
			? 'not installed on this daemon'
			: group.reason === 'auth-env-missing'
				? 'auth not configured on this daemon'
				: 'unavailable on this daemon';
	return `${group.shell} — ${reason} · ${count} ${cores}`;
}
