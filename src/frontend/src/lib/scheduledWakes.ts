// Loom slice 4 (kb/design-continuous-presence.md §3.2.1): the schedule
// lane — queued intent, rendered. The daemon has published scheduled wakes
// all along (`cloud.py::_schedule_activity_records`, kind="scheduled",
// carrying `scheduled_for` + at/every phase) into the same activity feed
// the /activity page filters; the main dashboard just never surfaced them.
// This helper narrows the existing `GET /v1/dashboard/activity` to that
// kind — zero new backend data, the same shape slices 1-3 kept to.

export interface ScheduledWake {
	id: string;
	kind: string;
	source: string;
	status: string | null; // "recurring" | "scheduled"
	phase: string | null; // "every" | "at" — the trigger kind
	bucket: string;
	summary: string;
	repo_label: string | null;
	daemon_name: string | null;
	conversation_key: string | null;
	scheduled_for: string | null; // next-fire instant, ISO; null = anchoring
	reported_at: string | null;
}

export interface ScheduledWakesResponse {
	generated_at: string;
	rows: ScheduledWake[];
	total: number;
}

export class ScheduledWakesAuthError extends Error {}

/** Fetches the account's scheduled/queued wakes. Throws
 * `ScheduledWakesAuthError` on a 401, same shape as `fetchLiveRuns`. */
export async function fetchScheduledWakes(
	fetchImpl: typeof fetch = fetch
): Promise<ScheduledWakesResponse> {
	const res = await fetchImpl('/v1/dashboard/activity?kind=scheduled&limit=50', {
		credentials: 'include'
	});
	if (res.status === 401) {
		throw new ScheduledWakesAuthError('not signed in');
	}
	if (!res.ok) {
		throw new Error(`scheduled-wakes fetch failed: ${res.status}`);
	}
	return (await res.json()) as ScheduledWakesResponse;
}

/** "in 42m" / "due now" / "overdue 3m" — countdown to a wake's next fire,
 * ticking off the shared `now` the same way `ageSince` does. `null` when
 * the instant is unknown (an `every:` entry still anchoring its first
 * cycle — the daemon records first-sight before ever computing a fire). */
export function untilText(scheduledFor: string | null, now: number): string | null {
	if (!scheduledFor) return null;
	const t = Date.parse(scheduledFor);
	if (Number.isNaN(t)) return null;
	const deltaS = Math.floor((t - now) / 1000);
	if (deltaS <= -60) {
		const m = Math.floor(-deltaS / 60);
		return m < 60 ? `overdue ${m}m` : `overdue ${Math.floor(m / 60)}h ${m % 60}m`;
	}
	if (deltaS < 60) return 'due now';
	const minutes = Math.floor(deltaS / 60);
	if (minutes < 60) return `in ${minutes}m`;
	const hours = Math.floor(minutes / 60);
	if (hours < 24) return `in ${hours}h ${minutes % 60}m`;
	const days = Math.floor(hours / 24);
	return `in ${days}d ${hours % 24}h`;
}
