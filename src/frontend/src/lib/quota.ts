import type { WithheldLane } from './withheld';

// Slice 2: window-track live-quota view (kb/design-dashboard-live-surface.md
// "A shape for the live-flow surface"). Types mirror the JSON
// `GET /v1/dashboard/quota` returns (`src/brnrd/routers/dashboard.py::
// dashboard_quota_api`), which is a thin wrapper around `_quota_views` — the
// same data the (soon to be replaced) Jinja dashboard renders.

export interface QuotaWindowReading {
	used: number | null;
	limit: number | null;
	percent: number | null;
	reset: string | null;
	/** Unix epoch seconds — machine-parseable twin of `reset`'s display text.
	 *  Absent (not just null) on daemon builds older than 2026-07-06. */
	resets_at?: number | null;
}

export interface QuotaWindow extends QuotaWindowReading {
	label: string;
	/** Stale snapshots keep the live-reading fields null, while preserving the
	 *  measured values here so consumers cannot mistake old data for current. */
	last_known?: QuotaWindowReading | null;
}

export function quotaWindowReading(window: QuotaWindow): QuotaWindowReading {
	return window.last_known ?? window;
}

export interface QuotaCredits {
	/** Real USD, not a projection — the Shell's own result-JSON cost figure.
	 *  Only meaningfully nonzero once a subscription window is exhausted and
	 *  the account falls through to metered credits (confirmed live
	 *  2026-07-07: a run kept working straight through an exhausted 5h
	 *  window, billed ~$1). */
	total_cost_usd: number | null;
	summary: string | null;
	updated_at: string | null;
	enabled?: boolean | null;
	used_percentage?: number | null;
	remaining_percentage?: number | null;
	spent_amount?: number | null;
	limit_amount?: number | null;
	currency?: string | null;
	reset?: string | null;
	resets_at?: number | null;
	run_spend_summary?: string | null;
	/** ISO stamp of the last scrape that actually *saw* these credits. Present
	 *  only when the reading was carried across a rate-limited `/usage` panel
	 *  (see `brr/claude_usage.py::carry_forward_sections`) — the figure is real,
	 *  it just wasn't confirmed on this tick, and the panel says so. */
	carried_from?: string | null;
}

export interface QuotaSpend {
	/** `'unimplemented'` is the live case today: a shell with no cost/spend
	 *  collector at all (Codex), named explicitly with `reason` rather than
	 *  the field just being absent, which reads identically to "unknown" on
	 *  the dashboard. Claude's proven per-run figure rides the `credits`
	 *  block above instead of this field. */
	status: 'unimplemented' | string;
	reason?: string | null;
}

export interface QuotaBurn {
	/** Which window the burn is measured against — the longest on record for
	 *  that shell, since the ceiling that matters is the one you can't wait
	 *  out. (Codex has reported only the weekly window since 2026-07-12; Claude
	 *  reports both. See `brr/usage_samples.py::recent_burn`.) */
	window_minutes: number;
	/** Horizon the rate was measured over, and projected forward across. */
	hours: number;
	span_minutes: number;
	samples: number;
	from_remaining_percent: number;
	to_remaining_percent: number;
	burned_percent: number;
	/** Where the current rate lands the window `hours` from now. */
	projected_remaining_percent: number;
	/** Epoch seconds the window hits zero at this rate — null when not burning. */
	exhausts_at: number | null;
	/** True when the window resets before this rate could exhaust it: a pace you
	 *  can keep. False is the reading the old 5h bar used to give you. */
	sustainable: boolean;
}

export interface QuotaShell {
	shell: string;
	status: 'known' | 'stale' | 'unknown' | string;
	windows: QuotaWindow[];
	/** Scrape time for a stale snapshot's `last_known` window values. This is
	 *  the shell's own timestamp, never the dashboard response generation time. */
	as_of?: string | null;
	/** Derived short-horizon burn rate, **both shells** since 2026-07-19: it is
	 *  measured off brr's own quota-sample store rather than Codex's session
	 *  rollouts, so it no longer depends on which shell happens to leave
	 *  timestamped readings on disk. Absent when the evidence is too thin to
	 *  project from (fewer than two samples, or a span under 30 minutes).
	 *
	 *  Nothing renders this yet — the reading is correct and published, and the
	 *  §1 tank line still derives its own rate from window arithmetic. Those
	 *  are two measurements of one quantity and should become one. */
	burn?: QuotaBurn | null;
	/** Present only for shells with a proven per-run spend figure (Claude
	 *  today; absent, not null, on shells/builds with no such collector). */
	credits?: QuotaCredits | null;
	/** Unredeemed free "Full reset (Weekly + 5 hr)" grants on the account —
	 *  Codex only, and only since the app-server quota probe (#315) started
	 *  reading them (the session-rollout seam never carried them). A window at
	 *  4% left means something different when four resets sit unused. */
	reset_credits?: number | null;
	/** Explicit spend posture for a shell with no `credits` block — see
	 *  `QuotaSpend`. Absent on daemon builds older than 2026-07-13. */
	spend?: QuotaSpend | null;
	/** When this shell's own quota report was last received — the daemon-level
	 *  report timestamp (`Daemon.quota_updated_at`), distinct from `status ===
	 *  'stale'` (a scrape-level fact derived from the shell payload's own
	 *  optional `updated_at`, see `RailGauge.svelte`'s `fuel` rows). Shells
	 *  merge by name across every daemon on the account with no per-row expiry
	 *  (#1503, "the tank of dead quotas" — the same shape #1502 fixed for the
	 *  runner rack): a retired daemon's shell can sit on the tank looking as
	 *  fresh as the account's newest report. `daemon_reported_at` /
	 *  `daemon_stale` are server-computed (`dashboard.py::_quota_views`) so a
	 *  reader can gate a window on the report that actually produced it. */
	daemon_reported_at?: string | null;
	/** This shell's own source report is older than the freshness window —
	 *  same threshold (`_QUOTA_STALE_SECONDS`) the scrape-level `status`
	 *  already applies, scored against the daemon's report instead. */
	daemon_stale?: boolean | null;
}

export interface QuotaResponse {
	generated_at: string;
	runner_quotas: QuotaShell[];
	withheld?: WithheldLane;
}

export class QuotaAuthError extends Error {}

/** How long the auth-gate fetch may hang before the page must decide without
 * it. During a deploy cutover, `/v1/*` requests hang ~30s+ before the
 * edge gives up with a 502 — and this fetch is the dashboard's entry gate:
 * the page renders *nothing* until it settles. Unbounded, a mid-deploy visit
 * was a permanent black screen (2026-07-21 incident). A healthy endpoint
 * answers in ~0.2s; 8s is generous slack, not a tuning knob. */
export const QUOTA_GATE_TIMEOUT_MS = 8_000;

/** Fetches the live per-shell quota snapshot. Throws `QuotaAuthError` on a
 * 401 (no session cookie) so the caller can point the user at `/login`
 * instead of rendering an empty track. Any other failure — including the
 * gate timeout above — is an ordinary Error: the caller treats it as a
 * backend hiccup and renders the dashboard's own error strings. */
export async function fetchQuota(fetchImpl: typeof fetch = fetch): Promise<QuotaResponse> {
	const res = await fetchImpl('/v1/dashboard/quota', {
		credentials: 'include',
		signal: AbortSignal.timeout(QUOTA_GATE_TIMEOUT_MS)
	});
	if (res.status === 401) {
		throw new QuotaAuthError('not signed in');
	}
	if (!res.ok) {
		throw new Error(`quota fetch failed: ${res.status}`);
	}
	return (await res.json()) as QuotaResponse;
}

export type QuotaLevel = 'burning' | 'cooling' | 'spent' | 'unknown';

/** Draining-bar color threshold — matches the maintainer's own correction
 * (ledger 2026-07-05): "the track runs out, it doesn't fill up, and changes
 * color by remaining level." Percent here is *remaining*, not used. */
export function quotaLevel(percent: number | null | undefined): QuotaLevel {
	if (percent === null || percent === undefined) return 'unknown';
	if (percent <= 15) return 'spent';
	if (percent <= 40) return 'cooling';
	return 'burning';
}

/** Renders a countdown ("2h 14m", "38m", "<1m") from an epoch, ticking off
 * `now` rather than re-fetching — the track should visibly drain between
 * polls, not just jump on refresh. */
export function timeUntil(resetsAt: number | null | undefined, now: number): string | null {
	if (resetsAt === null || resetsAt === undefined) return null;
	const seconds = Math.max(0, resetsAt * 1000 - now) / 1000;
	if (seconds <= 0) return 'now';
	const hours = Math.floor(seconds / 3600);
	const minutes = Math.floor((seconds % 3600) / 60);
	if (hours > 0) return `${hours}h ${minutes}m`;
	if (minutes > 0) return `${minutes}m`;
	return '<1m';
}
