// Slice 3 (kb/design-dashboard-live-surface.md "Reconsidered 2026-07-06"):
// account-scoped live/coexisting-runs view. Types mirror the JSON
// `GET /v1/dashboard/live-runs` returns (`src/brnrd/routers/dashboard.py::
// dashboard_live_runs_api`), sourced from the local presence registry
// (`src/brr/presence.py`) via the daemon's `PUT /v1/daemons/live-runs` publish.

export interface LiveRunRunner {
	name?: string;
	shell?: string;
	core?: string;
	class?: string;
}

export interface LiveRun {
	id: string;
	kind: string;
	stream: string;
	label: string;
	name: string;
	run_id: string;
	repo_label: string;
	started_at: string | null;
	last_seen: string | null;
	// Same join key as the closed-run ledger's `parent_run_id`/`is_subspawn`
	// (run_ledger.py) — a concurrent `spawn:` child carries these while
	// still live, so a peer card can be told apart from a resident thought
	// before it ever reaches the ledger
	// (kb/design-multi-workstream-concurrency.md "Ranked moves" #1).
	parent_run_id: string | null;
	is_subspawn: boolean;
	// Which Shell+Core this thought is running on
	// (`cloud.py::_runner_payload`, same shape Activity/respawn rows already
	// carry) — sourced from the presence entry now that `presence.register`
	// records it at registration time. `{}` on an entry from before this
	// field shipped, or an ad-hoc session that never selected a Runner.
	runner: LiveRunRunner;
	// #200's remaining slice (progress-card richness): the run's current
	// lifecycle phase (`queued`/`preparing`/`running`/`finalizing`/...,
	// `src/brr/run_progress.py::PHASES`) and the live `.card` note text,
	// folded into the same publish tick via `run_progress.project_run`.
	// `null` when there's no conversation record yet or the run hasn't
	// written a card note. Budget/keepalive posture is a separate,
	// not-yet-built slice — nothing persists that state today.
	phase: string | null;
	card_text: string | null;
	card_updated_at: string | null;
	// #342 relics-so-far: counts of the run's attested produce mid-flight
	// (`{commit: 2, kb: 1}`), from the daemon's heartbeat-refreshed portal
	// capsule via `cloud.py::_live_runs_snapshot`. `null`/absent = nothing
	// attested (ad-hoc session, pre-upgrade daemon); `{}` = known, no
	// produce yet. Render via `liveRelicChips` below.
	relics_counts?: Record<string, number> | null;
	// #476 wyrd §3: a stop the account owner has parked for this run, not yet
	// consumed by the daemon. Server-side (rather than a fact the client holds
	// in memory) so the cell keeps saying "stopping" across a reload — and so
	// it says only that: the run is still running until the daemon's next sync
	// finalizes it as `stopped`.
	stop_requested?: boolean;
}

export interface LiveRunsResponse {
	generated_at: string;
	runs: LiveRun[];
	stale: boolean;
	reported_at: string | null;
	// Configured `spawn:` pool width (`spawn.max_concurrent`), piggybacked
	// on this same publish tick — loom-envelope Phase 1's one piece of data
	// the slice-1 publish didn't already carry. `null` before any daemon
	// has reported it (pre-upgrade daemon, or never published yet).
	spawn_max_concurrent: number | null;
}

/** Resident-authored name wins; the waking-message excerpt remains a fallback. */
export function liveRunDisplayName(run: Pick<LiveRun, 'name' | 'label' | 'kind'>): string {
	return run.name || run.label || run.kind || 'run';
}

// Render order for relics-so-far chips — produce first, chatter last.
// Mirrors `brr.relics._TAIL_NOUNS` order (hand-mirrored, same precedent as
// RELIC_ICONS in runLedger.ts). `branch` is deliberately absent: mid-flight
// every commit-bearing run has exactly one branch, so a branch chip only
// restates the commits chip (#329's family logic makes the same call on
// receipts). `summary` is prose, not produce.
const RELIC_CHIP_ORDER = [
	'commit',
	'merge',
	'pr',
	'issue',
	'kb',
	'file',
	'comment',
	'message',
	'reply'
];
const RELIC_CHIP_EXCLUDE = new Set(['branch', 'summary']);

export interface LiveRelicChip {
	kind: string;
	count: number;
}

/** Relics-so-far counts → ordered chips for the expanded live-run card
 * (#342). Zero/absent counts → `[]`, so the card renders no relics row at
 * all. Unknown kinds trail in alphabetical order rather than vanishing —
 * the backend's relic vocabulary is meant to grow without a frontend
 * round trip (same posture as `RelicRecord`). */
export function liveRelicChips(counts: Record<string, number> | null | undefined): LiveRelicChip[] {
	if (!counts) return [];
	const chips: LiveRelicChip[] = [];
	for (const kind of RELIC_CHIP_ORDER) {
		const count = counts[kind] ?? 0;
		if (count > 0) chips.push({ kind, count });
	}
	for (const kind of Object.keys(counts).sort()) {
		if (RELIC_CHIP_ORDER.includes(kind) || RELIC_CHIP_EXCLUDE.has(kind)) continue;
		const count = counts[kind] ?? 0;
		if (count > 0) chips.push({ kind, count });
	}
	return chips;
}

export class LiveRunsAuthError extends Error {}

/** Fetches the account-scoped live-runs snapshot. Throws `LiveRunsAuthError`
 * on a 401 (no session cookie), same shape as `fetchQuota`. */
export async function fetchLiveRuns(fetchImpl: typeof fetch = fetch): Promise<LiveRunsResponse> {
	const res = await fetchImpl('/v1/dashboard/live-runs', { credentials: 'include' });
	if (res.status === 401) {
		throw new LiveRunsAuthError('not signed in');
	}
	if (!res.ok) {
		throw new Error(`live-runs fetch failed: ${res.status}`);
	}
	return (await res.json()) as LiveRunsResponse;
}

/** A parked run stop (#476 wyrd §3). Not cancelable: by the time the row
 * exists the only thing between it and a dead process is one daemon sync. */
export interface RunStopRequest {
	request_id: string;
	run_id: string;
	requested_at: string | null;
	status: string;
}

/** Ask the daemon to stop a burning run. Async by nature — this parks the
 * request; the daemon consumes it on its next sync and the run finalizes as
 * `stopped` with partial work salvaged. */
export async function requestRunStop(
	runId: string,
	fetchImpl: typeof fetch = fetch
): Promise<RunStopRequest> {
	const res = await fetchImpl(`/v1/dashboard/runs/${encodeURIComponent(runId)}/stop`, {
		method: 'POST',
		credentials: 'include'
	});
	if (res.status === 401) {
		throw new LiveRunsAuthError('not signed in');
	}
	if (!res.ok) {
		throw new Error(
			res.status === 404 ? 'that run is no longer live' : `stop failed: ${res.status}`
		);
	}
	return ((await res.json()) as { stop_request: RunStopRequest }).stop_request;
}

/** Heartbeat freshness → lifecycle temperature. A heartbeat lands roughly
 * every 30s (`daemon.py`'s watch loop); three missed beats reads as genuinely
 * stalling rather than one slow tick. The registry itself only prunes at 300s
 * (`presence.DEFAULT_STALE_AFTER_S`), so a run can sit "stalling" for a while
 * before it's gone — that gap is real and worth seeing. Shared by the
 * LiveRuns grid and the inline node panel so the two surfaces cannot disagree
 * about whether one run is alive. */
export const STALL_AFTER_MS = 90_000;

export type HeartbeatLevel = 'running' | 'stalling' | 'unknown';

export function heartbeatLevel(
	lastSeen: string | null,
	now: number,
	stale: boolean
): HeartbeatLevel {
	if (stale) return 'unknown';
	const seen = lastSeen ? Date.parse(lastSeen) : NaN;
	if (Number.isNaN(seen)) return 'unknown';
	return now - seen > STALL_AFTER_MS ? 'stalling' : 'running';
}

/** "3m ago" / "just now" — a live run's age since it started, ticking off
 * `now` the same way `timeUntil` ticks the quota window's countdown. */
export function ageSince(startedAt: string | null, now: number): string | null {
	if (!startedAt) return null;
	const started = Date.parse(startedAt);
	if (Number.isNaN(started)) return null;
	const deltaS = Math.max(0, Math.floor((now - started) / 1000));
	if (deltaS < 60) return 'just now';
	const minutes = Math.floor(deltaS / 60);
	if (minutes < 60) return `${minutes}m ago`;
	const hours = Math.floor(minutes / 60);
	return `${hours}h ${minutes % 60}m ago`;
}
