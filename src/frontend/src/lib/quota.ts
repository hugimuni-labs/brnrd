// Slice 2: window-track live-quota view (kb/design-dashboard-live-surface.md
// "A shape for the live-flow surface"). Types mirror the JSON
// `GET /v1/dashboard/quota` returns (`src/brnrd_web/activity_dashboard.py::
// dashboard_quota_api`), which is a thin wrapper around `_quota_views` — the
// same data the (soon to be replaced) Jinja dashboard renders.

export interface QuotaWindow {
	label: string;
	used: number | null;
	limit: number | null;
	percent: number | null;
	reset: string | null;
	/** Unix epoch seconds — machine-parseable twin of `reset`'s display text.
	 *  Absent (not just null) on daemon builds older than 2026-07-06. */
	resets_at?: number | null;
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
	/** Which window the burn is measured against (Codex reports only the weekly
	 *  one since 2026-07-12 — see `brr/codex_status.py::recent_burn`). */
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
	/** Derived short-horizon burn rate — Codex only, and only on daemon builds
	 *  since 2026-07-13. Absent when the evidence is too thin to project from
	 *  (fewer than two samples, or a span under 30 minutes). */
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
}

export interface QuotaResponse {
	generated_at: string;
	runner_quotas: QuotaShell[];
}

export class QuotaAuthError extends Error {}

/** Fetches the live per-shell quota snapshot. Throws `QuotaAuthError` on a
 * 401 (no session cookie) so the caller can point the user at `/login`
 * instead of rendering an empty track. */
export async function fetchQuota(fetchImpl: typeof fetch = fetch): Promise<QuotaResponse> {
	const res = await fetchImpl('/v1/dashboard/quota', { credentials: 'include' });
	if (res.status === 401) {
		throw new QuotaAuthError('not signed in');
	}
	if (!res.ok) {
		throw new Error(`quota fetch failed: ${res.status}`);
	}
	return (await res.json()) as QuotaResponse;
}

export type QuotaLevel = 'ample' | 'low' | 'critical' | 'unknown';

/** Draining-bar color threshold — matches the maintainer's own correction
 * (ledger 2026-07-05): "the track runs out, it doesn't fill up, and changes
 * color by remaining level." Percent here is *remaining*, not used. */
export function quotaLevel(percent: number | null | undefined): QuotaLevel {
	if (percent === null || percent === undefined) return 'unknown';
	if (percent <= 15) return 'critical';
	if (percent <= 40) return 'low';
	return 'ample';
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
