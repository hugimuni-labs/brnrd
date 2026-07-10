// Activity feed (#327 Jinja-removal, /activity half): the full account
// activity history — runs, scheduled wakes, parked respawns — as reported
// by connected daemons via `PUT /v1/daemons/activity`. Types mirror
// `GET /v1/dashboard/activity` (`src/brnrd_web/activity_dashboard.py::
// dashboard_activity_api`), the JSON twin of the retired Jinja page.
//
// The one deliberate improvement over the legacy page: the feed is bounded
// (`limit`, server-capped at 300) with `total` carrying the pre-limit
// count — the Jinja page rendered every record unbounded, which is what
// made 282 accumulated records unreadable in the first place.

export interface ActivityRunner {
	shell: string;
	core: string;
	summary: string;
}

export interface ActivityRow {
	id: string;
	kind: string;
	source: string;
	status: string;
	phase: string;
	/** Server-derived status folding (running/pending/scheduled/parked/
	 * failed/completed, or the raw status when none match) — backend
	 * knowledge the client doesn't re-own. */
	bucket: string;
	summary: string;
	repo_label: string;
	daemon_name: string;
	conversation_key: string;
	runner: ActivityRunner;
	branch: string;
	pr_number: string | null;
	started_at: string | null;
	updated_at: string | null;
	scheduled_for: string | null;
	defer_until: string | null;
	reported_at: string | null;
	links: Record<string, string>;
}

export interface ActivityFilters {
	repo_id?: string;
	kind?: string;
	status?: string;
	limit?: number;
}

export interface ActivityResponse {
	generated_at: string;
	rows: ActivityRow[];
	/** Pre-limit match count, so the UI can say "showing N of M". */
	total: number;
	kinds: string[];
	statuses: string[];
	repos: { id: string; label: string }[];
}

export class ActivityAuthError extends Error {}

export async function fetchActivity(
	filters: ActivityFilters = {},
	fetchImpl: typeof fetch = fetch
): Promise<ActivityResponse> {
	const params = new URLSearchParams();
	if (filters.repo_id) params.set('repo_id', filters.repo_id);
	if (filters.kind) params.set('kind', filters.kind);
	if (filters.status) params.set('status', filters.status);
	if (filters.limit) params.set('limit', String(filters.limit));
	const qs = params.toString();
	const res = await fetchImpl(`/v1/dashboard/activity${qs ? `?${qs}` : ''}`, {
		credentials: 'include'
	});
	if (res.status === 401) {
		throw new ActivityAuthError('not signed in');
	}
	if (!res.ok) {
		throw new Error(`activity fetch failed: ${res.status}`);
	}
	return (await res.json()) as ActivityResponse;
}
