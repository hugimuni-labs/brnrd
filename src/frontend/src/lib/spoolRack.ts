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
	if (isLocked(profile)) return 'offerable';
	return availabilityOf(profile) === 'available' ? 'offerable' : 'off';
}

/**
 * A *locked* row: the shell is installed and its subscription exists, but
 * the daemon's last dispatch on this credential failed to authenticate and
 * the credential has not changed since. Distinct from every other
 * unavailable reason on purpose: the user holds the key (sign in again),
 * and a tap is legal — the attempt is the probe that clears the mark. The
 * 2026-09-08/09 deadlock was this state rendered as *absent*: no shell row,
 * no tap, no run, no clear, three manual deletions of the mark file.
 */
export function isLocked(profile: RunnerProfile): boolean {
	return profile.availability === 'auth-error';
}

/** The locked row's own line: when it failed, on which core, what to do. */
export function lockedText(profile: RunnerProfile): string {
	const mark = profile.auth_error ?? null;
	const when = mark?.since ? ` ${shortStamp(mark.since)}` : '';
	const on = mark?.seen_on ? ` on ${mark.seen_on}` : '';
	const agreed = mark?.probe === 'signed-out' ? ' · the shell agrees: signed out' : '';
	return `auth failed${when}${on}${agreed} — sign in again, then tap`;
}

/** The same fact at ledger width: `auth failed 05:06Z — sign in, then tap`. */
export function lockedShort(profile: RunnerProfile): string {
	const since = profile.auth_error?.since;
	const when = since ? ` ${shortStamp(since)}` : '';
	return `auth failed${when} · sign in`;
}

function shortStamp(iso: string): string {
	const d = new Date(iso);
	if (Number.isNaN(d.getTime())) return iso;
	const hh = String(d.getUTCHours()).padStart(2, '0');
	const mm = String(d.getUTCMinutes()).padStart(2, '0');
	return `${hh}:${mm}Z`;
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
 * generic reading: not confirmed — and why not, no invented
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
	if (isLocked(profile)) {
		return { known: true, text: lockedText(profile) };
	}
	if (availabilityOf(profile) === 'unavailable') {
		return { known: true, text: reasonText(profile.availability ?? null) };
	}
	if (reportStale || profile.daemon_stale === true) {
		return { known: false, text: 'not confirmed — daemon report is old' };
	}
	return { known: false, text: 'not confirmed — no daemon report yet' };
}

function reasonText(availability: string | null): string {
	if (availability === 'shell-not-found') return 'not installed on this daemon';
	if (availability === 'auth-env-missing') return 'auth not configured on this daemon';
	if (availability === 'auth-error') return 'auth failed — sign in again, then tap';
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
	/** Every row in the group is locked (`auth-error`) — one credential, one
	 *  line above the rows, instead of the same sentence on each of them. */
	allLocked: boolean;
}

/**
 * Groups the rack by shell — now the rack's own first stage (his 2026-08-19
 * steer: "add a separate shell selector which renders available cores for
 * it below" — a small, stable set of shells, then the chosen one's cores,
 * instead of every `shell-core` compound flattened into one list that grows
 * multiplicatively). Preserves the incoming cost_rank-ascending order within
 * each of two buckets — usable (available or unverified) first, verified
 * `unavailable` last — rather than across the whole group: a retired core
 * sitting between two runnable ones at its old cost rank read as a live
 * choice with a dashed border, not as *retired*, because "off" wasn't a
 * position, only a style. Available shells sort first; shells with nothing
 * live sort after, in their own first-seen order — the same "unavailable is
 * legitimate and stays, just last" rule, now applied consistently one level
 * down too.
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
		// A locked row (auth failed, key in the user's hand) is usable: it
		// keeps its place and its tap; only *dead* rows sink to the bottom.
		const usable = rows.filter((row) => isLocked(row) || availabilityOf(row) !== 'unavailable');
		const dead = rows.filter((row) => !isLocked(row) && availabilityOf(row) === 'unavailable');
		const allUnavailable = rows.length > 0 && dead.length === rows.length;
		const reason = rows.find((row) => row.availability)?.availability ?? null;
		const allLocked = rows.length > 0 && rows.every(isLocked);
		return { shell, profiles: [...usable, ...dead], allUnavailable, reason, allLocked };
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

// ---------------------------------------------------------------------------
// The row a stranger can read (his 2026-09-09 read of the after-shots: "the
// order feels random … imagine you are a new user"). One grammar for both
// vendors: headline = the vendor's model id, versioned · sub = tier · our
// handle · rows in a fixed tier order with the shell's own default first.

const TIER_ORDER: Record<string, number> = { economy: 0, balanced: 1, strong: 2 };

/** The shell's own default row: no core pinned, the shell decides. */
export function isShellDefault(profile: RunnerProfile): boolean {
	return !profile.model || profile.model === profile.shell;
}

/** The tier as the reader sees it — never blank: a core the catalog could
 *  not place says so. */
export function tierLabel(profile: RunnerProfile): string {
	return profile.class ? profile.class : 'unclassed';
}

/** What the row is called: the vendor's own id for the core, versioned
 *  where the shell attested one; the shell-decides row says so in words. */
export function headline(profile: RunnerProfile): string {
	// Not "default": that word is the DEFAULT badge's (who wakes next) — the
	// module doc's two-meanings rule. The shell's own name, and the sub-line
	// says the shell picks the core.
	if (isShellDefault(profile)) return profile.shell ?? profile.name;
	return profile.observed_model ?? profile.model ?? profile.name;
}

/** Fixed order: the shell's default first, then economy · balanced ·
 *  strong · unclassed; cost sorts only inside a tier; name breaks ties. The
 *  raw `cost_rank` used to be the whole order, and a feed core's rank was
 *  the vendor's *display* priority — GPT-6-Astra at 1, sorted as the
 *  bargain. Stable for equal keys, so the daemon's order survives within a
 *  tier. */
export function orderRows(rows: RunnerProfile[]): RunnerProfile[] {
	const key = (row: RunnerProfile): [number, number, number, string] => [
		isShellDefault(row) ? 0 : 1,
		row.class && row.class in TIER_ORDER ? TIER_ORDER[row.class] : 3,
		row.cost_rank ?? Number.MAX_SAFE_INTEGER,
		row.name
	];
	return rows
		.map((row, index) => ({ row, index, k: key(row) }))
		.sort((a, b) => {
			for (let i = 0; i < 4; i++) {
				if (a.k[i] < b.k[i]) return -1;
				if (a.k[i] > b.k[i]) return 1;
			}
			return a.index - b.index;
		})
		.map((entry) => entry.row);
}
