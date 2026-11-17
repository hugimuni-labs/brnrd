// The operators' page-view read-out (/account/views): shapes the rows
// GET /v1/admin/page-views returns. Pure, so node --test reaches it.

export type ViewRow = {
	day: string;
	path: string;
	referer_host: string;
	views: number;
	bot_views: number;
};

export type PageViews = { since: string; until: string; rows: ViewRow[] };

export const DIRECT = '(direct)';

export function hostLabel(host: string): string {
	return host || DIRECT;
}

export function parsePageViews(body: unknown): PageViews | null {
	if (!body || typeof body !== 'object') return null;
	const b = body as Record<string, unknown>;
	if (typeof b.since !== 'string' || typeof b.until !== 'string' || !Array.isArray(b.rows))
		return null;
	const rows = b.rows.filter(
		(r): r is ViewRow =>
			!!r &&
			typeof r.day === 'string' &&
			typeof r.path === 'string' &&
			typeof r.referer_host === 'string' &&
			typeof r.views === 'number' &&
			typeof r.bot_views === 'number'
	);
	return { since: b.since, until: b.until, rows };
}

/** Rows whose referrer host contains `query` (case-insensitive); "" keeps all. */
export function filterByHost(rows: ViewRow[], query: string): ViewRow[] {
	const q = query.trim().toLowerCase();
	if (!q) return rows;
	return rows.filter((r) => hostLabel(r.referer_host).toLowerCase().includes(q));
}

/** Views per referrer host over the whole window, busiest first. */
export function hostTotals(rows: ViewRow[]): { host: string; views: number }[] {
	const totals = new Map<string, number>();
	for (const r of rows) totals.set(r.referer_host, (totals.get(r.referer_host) ?? 0) + r.views);
	return [...totals]
		.map(([host, views]) => ({ host, views }))
		.sort((a, b) => b.views - a.views || a.host.localeCompare(b.host));
}

/** Views per day for the filtered rows, newest first. */
export function dayTotals(rows: ViewRow[]): { day: string; views: number }[] {
	const totals = new Map<string, number>();
	for (const r of rows) totals.set(r.day, (totals.get(r.day) ?? 0) + r.views);
	return [...totals]
		.map(([day, views]) => ({ day, views }))
		.sort((a, b) => b.day.localeCompare(a.day));
}
