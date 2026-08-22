import type { RunnerProfile } from './runners';

// #328 spool rack. w-68 rework (2026-08-19, the gauge/bench split): two
// design instructions landed mid-flight, both his, both taken —
//
// 1. **Shell first, then core.** `groupByShell` already computed the
//    two-axis structure (shell, then its cores); the row list used to throw
//    it away by flattening every profile back into one column. The rack now
//    renders a shell selector and the selected shell's cores below it — see
//    `SpoolRack.svelte`.
// 2. **`stale` must never reach the reader.** A row's availability used to
//    render as three visually distinct states — available, verified
//    unavailable, "we don't know" — with the third state's own doubt
//    ("outdated report", a dashed border, a `?` mark) presented as if it
//    meant something to act on. It doesn't: a reader can offer a wake on
//    this profile or not, and "we last checked a while ago" is not a third
//    option. `offerabilityOf` below collapses the tri-state availability
//    plus every staleness signal to that binary at the edge; only the
//    binary reaches the component.
export type Availability = 'available' | 'unavailable' | 'unverified';

/**
 * A profile's raw availability, failed closed. `available !== false` used
 * to mean "render as available" — a row simply *missing* the field (an
 * older daemon report, a partial merge) rendered identically to a
 * verified-live one. Three states, and only one of them is live:
 * `true` → available, `false` → unavailable, anything else → unverified.
 *
 * Kept exported and distinct from `offerabilityOf` on purpose: the tri-state
 * *is* the fact the daemon reports, and it is real — `groupByShell`'s dead
 * vs unverified distinction still depends on it. What must not happen is a
 * *row* rendering the middle state as its own visual identity; that
 * collapse happens one layer up, in `offerabilityOf`.
 */
export function availabilityOf(profile: RunnerProfile): Availability {
	if (profile.available === true) return 'available';
	if (profile.available === false) return 'unavailable';
	return 'unverified';
}

export type Offerability = 'offerable' | 'off';

/**
 * The binary a row actually renders. Resolves `availabilityOf` plus every
 * staleness signal (the account-wide report, this row's own `daemon_stale`)
 * to one answer: can a tap here park a wake, or not. There is no third
 * value — "verified available but the report is old" and "we don't know"
 * both resolve to `off`, because neither is a fact a reader can act on
 * differently from the other. `offReason` is where the two are told apart
 * again, for the row that already knows it is off and wants to say why.
 */
export function offerabilityOf(profile: RunnerProfile, reportStale: boolean): Offerability {
	if (reportStale || profile.daemon_stale === true) return 'off';
	return availabilityOf(profile) === 'available' ? 'offerable' : 'off';
}

/** Can this row's tap actually park a wake request? Exactly `offerabilityOf
 *  === 'offerable'` — kept as its own name because "tappable" is the
 *  question the component's markup asks, and reads better at the call
 *  site than the binary's own name would. */
export function isTappable(profile: RunnerProfile, reportStale: boolean): boolean {
	return offerabilityOf(profile, reportStale) === 'offerable';
}

/**
 * The reason an off row is off, when there is one honest to give. Verified
 * unavailable (`available === false`) carries a real reason off the
 * catalog's own `availability` string — shell not installed, auth not
 * configured — and that is the *only* case this returns a specific claim.
 * Every other off path (unverified, or available-but-stale) returns the
 * generic reading: not confirmed available right now, no invented
 * specifics. This is the answer to the maintainer's question of whether
 * "verified unavailable" survives to the client distinguishably from "we
 * don't know" — it does, one layer up in `availabilityOf` — but a row's own
 * rendering only ever shows the concrete reason when the daemon actually
 * gave one; report-staleness on an otherwise-available row never borrows
 * the unavailable copy either, so a reader is never told a wrong reason.
 */
export function offReasonOf(
	profile: RunnerProfile,
	reportStale: boolean
): { known: boolean; text: string } {
	if (availabilityOf(profile) === 'unavailable') {
		return { known: true, text: reasonText(profile.availability ?? null) };
	}
	if (reportStale || profile.daemon_stale === true) {
		return { known: false, text: 'not confirmed available right now' };
	}
	return { known: false, text: 'not confirmed available yet' };
}

function reasonText(availability: string | null): string {
	if (availability === 'shell-not-found') return 'not installed on this daemon';
	if (availability === 'auth-env-missing') return 'auth not configured on this daemon';
	if (availability === 'auth-error') return 'authentication failed; log in again';
	if (availability === 'subscription-unavailable') return 'subscription is not available';
	return 'unavailable on this daemon';
}

export interface ShellGroup {
	shell: string;
	profiles: RunnerProfile[];
	/** Every profile in the group is *verified* unavailable (not merely
	 *  unverified) — the group's tab renders off, deliberately, rather than
	 *  a live selector into dead cores. */
	allUnavailable: boolean;
	/** A representative reason for the tab's own off state, straight off
	 *  the first row that carries one. */
	reason: string | null;
}

/**
 * Groups the rack by shell — now the rack's own first stage (his 2026-08-19
 * steer: "add a separate shell selector which renders available cores for
 * it below" — a small, stable set of shells, then the chosen one's cores,
 * instead of every `shell-core` compound flattened into one list that grows
 * multiplicatively). Preserves the incoming order both across shells and
 * within one: the catalog already arrives cost_rank-ascending
 * (economy → strong), so grouping by shell over that order keeps each group
 * internally economy-first for free. Available shells sort first; shells
 * with nothing live sort after, in their own first-seen order — still
 * selectable, per "unavailable is legitimate and stays", just last.
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

/** The off-tab's own reason text — one shell standing in for N dead cores. */
export function deadShellReason(group: ShellGroup): string {
	return reasonText(group.reason);
}

/** Which shell a two-stage picker should open on: the shell backing whoever
 *  wakes next (the pin, the sticky, or the parked request), falling back to
 *  the first live shell, and only then to whatever shell sorts first —
 *  never landing the reader on a dead tab by default when a live one
 *  exists. */
export function defaultShell(groups: ShellGroup[], nextWakeProfile: string | null): string {
	if (nextWakeProfile) {
		const owner = groups.find((group) =>
			group.profiles.some((profile) => profile.name === nextWakeProfile)
		);
		if (owner) return owner.shell;
	}
	return (groups.find((group) => !group.allUnavailable) ?? groups[0])?.shell ?? '';
}
