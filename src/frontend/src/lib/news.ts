// The news lane (the-user-hears-it-first): typed, receipted facts about the
// product itself — a newer brnrd release today, a shell version or model
// availability change once a sibling producer exists. Types mirror the JSON
// `GET /v1/dashboard/news` returns (`src/brnrd/routers/dashboard.py::
// dashboard_news_api`), itself a merge of every daemon's own
// `PUT /v1/daemons/news` report (`src/brr/gates/cloud_publisher.py::
// _publish_news`, reading `brr.news_lane.collect` locally) — see
// `brr/news_lane.py`'s module docstring for the daemon-side shape this
// mirrors field for field.

export interface NewsItem {
	kind: string;
	subject: string;
	prior: string | null;
	current: string;
	source: string | null;
	expires_at: string | null;
	daemon_reported_at: string | null;
	daemon_stale: boolean;
}

export interface NewsWithheld {
	lane: string;
	unrecorded?: string[];
	unrecorded_ids?: string[];
	opted_out?: string[];
	opted_out_ids?: string[];
}

export interface NewsResponse {
	generated_at: string | null;
	items: NewsItem[];
	withheld?: NewsWithheld;
}

export class NewsAuthError extends Error {}

/** Fetches the account-scoped news lane. Throws `NewsAuthError` on a 401 (no
 * session cookie), same shape as the other dashboard fetchers. */
export async function fetchNews(fetchImpl: typeof fetch = fetch): Promise<NewsResponse> {
	const res = await fetchImpl('/v1/dashboard/news', { credentials: 'include' });
	if (res.status === 401) {
		throw new NewsAuthError('not signed in');
	}
	if (!res.ok) {
		throw new Error(`news fetch failed: ${res.status}`);
	}
	return (await res.json()) as NewsResponse;
}

/** One line per item, in the same "subject: prior → current" shape
 * `brr.news_lane.NewsItem.render()` uses server-side — kept in sync by hand
 * since chat and the dashboard are two different renderers of one fact,
 * not one shared code path. */
export function renderNewsItem(item: NewsItem): string {
	if (item.expires_at) {
		return `${item.subject}: ${item.current} (retires ${item.expires_at})`;
	}
	if (item.prior && item.prior !== item.current) {
		return `${item.subject} update available: ${item.prior} → ${item.current}`;
	}
	return `${item.subject}: ${item.current}`;
}
